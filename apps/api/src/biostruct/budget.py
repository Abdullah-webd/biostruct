"""Project budgets: reserve on submit, settle on completion.

Two properties this module exists to guarantee.

**Money never passes through a float.** The Boltz API returns costs as strings
("0.0500"), and float arithmetic would drift the running total (ADR-017).

**Reserving and settling are separate.** Boltz bills on "exact token counts
computed at run time", so the estimate is only an estimate. We reserve the
estimate when a run is submitted, so two concurrent submissions cannot both fit
inside the same remaining budget, and settle the actual afterwards.

The reservation is released on *every* terminal outcome, including failure.
Charging a researcher's budget for a run that produced nothing would be both
wrong and invisible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.db import money_to_db

log = logging.getLogger(__name__)

ZERO = Decimal("0")


class BudgetError(RuntimeError):
    """Base for budget failures."""


class BudgetExceeded(BudgetError):
    def __init__(self, *, requested: Decimal, remaining: Decimal, limit: Decimal) -> None:
        self.requested = requested
        self.remaining = remaining
        self.limit = limit
        super().__init__(
            f"this run would cost ${requested}, but only ${remaining} of the "
            f"${limit} project budget is available"
        )


@dataclass(frozen=True, slots=True)
class BudgetState:
    limit_usd: Decimal
    spent_usd: Decimal
    reserved_usd: Decimal

    @property
    def committed_usd(self) -> Decimal:
        return self.spent_usd + self.reserved_usd

    @property
    def remaining_usd(self) -> Decimal:
        return self.limit_usd - self.committed_usd

    @property
    def fraction_used(self) -> Decimal:
        if self.limit_usd <= ZERO:
            return Decimal("1")
        return self.committed_usd / self.limit_usd

    @property
    def should_warn(self) -> bool:
        """Past 80%, which is where the alert fires (ARCHITECTURE.md §12)."""
        return self.fraction_used >= Decimal("0.8")


def _money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("refusing to read money from a float")
    return Decimal(str(value))


class BudgetService:
    """Budget arithmetic against the ``projects`` collection.

    Reservation uses a single conditional update, so the check and the write are
    atomic. Checking first and writing second would let two simultaneous
    submissions each see enough budget and both proceed.
    """

    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    async def _read(self, project_id: str) -> tuple[BudgetState, dict[str, Any]] | None:
        """Return the parsed budget *and* the raw stored values.

        The compare-and-set in :meth:`reserve` conditions on the raw strings
        rather than re-deriving them. A document may hold ``"0"`` where we would
        now write ``"0.0000"``, and a re-derived condition would then match
        nothing and fail every reservation.
        """
        doc = await self.db.projects.find_one({"_id": project_id}, projection={"budget": 1})
        if not doc:
            return None
        budget = doc.get("budget") or {}
        state = BudgetState(
            limit_usd=_money(budget.get("limit_usd", "0")),
            spent_usd=_money(budget.get("spent_usd", "0")),
            reserved_usd=_money(budget.get("reserved_usd", "0")),
        )
        return state, budget

    async def get(self, project_id: str) -> BudgetState | None:
        read = await self._read(project_id)
        return read[0] if read else None

    async def reserve(self, project_id: str, amount_usd: Decimal) -> BudgetState:
        """Hold ``amount_usd`` against the budget, or raise.

        A read followed by a conditional update whose filter pins the values
        that were read, so it is a compare-and-set: if another submission
        reserved in between, this one is rejected and the caller retries
        against fresh numbers rather than overwriting them.
        """
        if amount_usd < ZERO:
            raise BudgetError("cannot reserve a negative amount")

        read = await self._read(project_id)
        if read is None:
            raise BudgetError(f"no such project: {project_id}")
        state, raw = read
        if amount_usd > state.remaining_usd:
            raise BudgetExceeded(
                requested=amount_usd,
                remaining=state.remaining_usd,
                limit=state.limit_usd,
            )

        # Conditional on the reserved amount not having moved since we read it,
        # which makes this a compare-and-set rather than a blind write.
        result = await self.db.projects.update_one(
            {
                "_id": project_id,
                # Exactly what was read, so any stored spelling matches.
                "budget.reserved_usd": raw.get("reserved_usd", "0"),
                "budget.spent_usd": raw.get("spent_usd", "0"),
            },
            {"$set": {"budget.reserved_usd": money_to_db(state.reserved_usd + amount_usd)}},
        )
        if result.modified_count != 1:
            # Someone else reserved in between. The caller retries, and on retry
            # the fresh read either fits or raises BudgetExceeded.
            raise BudgetError("the project budget changed while reserving; retry the submission")
        return BudgetState(
            limit_usd=state.limit_usd,
            spent_usd=state.spent_usd,
            reserved_usd=state.reserved_usd + amount_usd,
        )

    async def settle(
        self, project_id: str, *, reserved_usd: Decimal, actual_usd: Decimal
    ) -> BudgetState | None:
        """Release a reservation and record what was actually spent.

        Called on success *and* on failure: a failed run releases its
        reservation with ``actual_usd=0``, because billing a researcher for a
        run that produced nothing would be wrong and would not show up anywhere.
        """
        if actual_usd < ZERO or reserved_usd < ZERO:
            raise BudgetError("amounts must not be negative")

        state = await self.get(project_id)
        if state is None:
            return None

        # Never drive the reservation negative, even if bookkeeping has drifted.
        release = min(reserved_usd, state.reserved_usd)
        if release != reserved_usd:
            log.warning(
                "settling %s against a reservation of %s but only %s was held",
                project_id,
                reserved_usd,
                state.reserved_usd,
            )

        await self.db.projects.update_one(
            {"_id": project_id},
            {
                "$set": {
                    "budget.reserved_usd": money_to_db(state.reserved_usd - release),
                    "budget.spent_usd": money_to_db(state.spent_usd + actual_usd),
                }
            },
        )
        return BudgetState(
            limit_usd=state.limit_usd,
            spent_usd=state.spent_usd + actual_usd,
            reserved_usd=state.reserved_usd - release,
        )

    async def release(self, project_id: str, reserved_usd: Decimal) -> BudgetState | None:
        """Release a reservation without charging anything."""
        return await self.settle(project_id, reserved_usd=reserved_usd, actual_usd=ZERO)

"""MongoDB connection, index setup, and the replica-set check.

The replica-set check is not ceremony. Change streams carry design-run progress
to the browser (ARCHITECTURE.md §6.3) and MongoDB only offers them on a replica
set, so a standalone server would fail later, at runtime, in a way that looks
like "progress just stops". Better to say so at startup.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

log = logging.getLogger(__name__)

# Mongo cannot store Decimal directly without Decimal128; we persist money as
# strings and parse back to Decimal. Strings round-trip exactly and sort
# predictably for equal-width values, and we never aggregate money in Mongo.
MONEY_FIELDS = ("limit_usd", "spent_usd", "reserved_usd")


# Money is stored to a fixed four decimal places. Without this, subtraction
# produces "0.0000" where an initial value was "0", so zero has two spellings
# and any string comparison of amounts becomes subtly wrong.
MONEY_PLACES = Decimal("0.0001")


def money_to_db(value: Decimal) -> str:
    return str(value.quantize(MONEY_PLACES))


def money_from_db(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Should not happen; if a legacy float sneaks in, go via str not binary.
        return Decimal(str(value))
    return Decimal(str(value))


class Database:
    """Holds the client so the app has a single connection pool."""

    def __init__(self) -> None:
        self.client: AsyncIOMotorClient[dict[str, Any]] | None = None
        self.db: AsyncIOMotorDatabase[dict[str, Any]] | None = None

    async def connect(self, uri: str, db_name: str) -> AsyncIOMotorDatabase[dict[str, Any]]:
        self.client = AsyncIOMotorClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        self.db = self.client[db_name]
        await self.client.admin.command("ping")
        return self.db

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
            self.db = None

    async def supports_change_streams(self) -> bool:
        """True when this deployment is a replica set (or mongos)."""
        if self.client is None:
            return False
        try:
            hello = await self.client.admin.command("hello")
        except OperationFailure:
            return False
        # A replica set member reports setName; a sharded router reports msg=isdbgrid.
        return bool(hello.get("setName")) or hello.get("msg") == "isdbgrid"


database = Database()


async def ensure_indexes(db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
    """Create every index the app relies on. Idempotent, safe to run on each boot."""
    await db.users.create_index([("email", ASCENDING)], unique=True, name="users_email_unique")

    # Sessions expire themselves. The _id is the SHA-256 of the cookie token.
    await db.sessions.create_index(
        [("expires_at", ASCENDING)], expireAfterSeconds=0, name="sessions_ttl"
    )
    await db.sessions.create_index([("user_id", ASCENDING)], name="sessions_user")

    await db.projects.create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)], name="projects_user_created"
    )

    await db.targets.create_index(
        [("project_id", ASCENDING), ("created_at", DESCENDING)], name="targets_project"
    )
    # One target per accession per project: adding the same target twice is a
    # user mistake, not two distinct targets.
    await db.targets.create_index(
        [("project_id", ASCENDING), ("accession", ASCENDING)],
        unique=True,
        name="targets_project_accession_unique",
    )

    # The reconciler's scan. Load-bearing: this is the work list (ADR-004).
    await db.design_runs.create_index(
        [("status", ASCENDING), ("updated_at", ASCENDING)], name="runs_status_updated"
    )
    await db.design_runs.create_index(
        [("project_id", ASCENDING), ("created_at", DESCENDING)], name="runs_project_created"
    )
    # Makes a retried submission collide instead of starting a second billed run.
    await db.design_runs.create_index(
        [("idempotency_key", ASCENDING)], unique=True, name="runs_idempotency_unique"
    )
    await db.candidates.create_index(
        [("run_id", ASCENDING), ("metrics.binding_confidence", DESCENDING)],
        name="candidates_run_score",
    )
    await db.candidates.create_index([("project_id", ASCENDING)], name="candidates_project")
    # One candidate per engine result, so a resumed harvest cannot duplicate.
    await db.candidates.create_index(
        [("run_id", ASCENDING), ("engine_result_id", ASCENDING)],
        unique=True,
        name="candidates_run_result_unique",
    )

    # The source cache replaces Redis (ADR-003); documents remove themselves.
    await db.source_cache.create_index(
        [("expires_at", ASCENDING)], expireAfterSeconds=0, name="source_cache_ttl"
    )

    # Indexes for collections that arrive in later milestones are created with
    # those milestones, not pre-emptively here.


async def startup_checks(db: AsyncIOMotorDatabase[dict[str, Any]], *, strict: bool) -> None:
    """Warn or fail on a deployment that cannot support change streams.

    ``strict`` is on in staging and production. In development we only warn, so a
    contributor who starts a plain ``mongod`` still gets a working app, with
    progress falling back to client re-reads (ARCHITECTURE.md §12).
    """
    ok = await database.supports_change_streams()
    if ok:
        return
    message = (
        "MongoDB is not a replica set, so change streams are unavailable. "
        "Design-run progress will fall back to periodic re-reads. "
        "Start Mongo with: docker compose up -d mongo"
    )
    if strict:
        raise RuntimeError(f"Refusing to start: {message}")
    log.warning(message)

"""Migrate v1 ``projects`` into v2 ``research_sessions`` (M6).

The design rests on one decision, taken because the alternative is dangerous:
**the research session keeps the project's ``_id``.**

``targets``, ``design_runs`` and ``candidates`` all carry ``project_id``, with
working indexes on it. Minting new ids would mean rewriting every one of those
references, and a reference rewrite that half-completes orphans a researcher's
paid results with no way to tell which half ran. Keeping the id makes this a
document copy with nothing to re-point: the same value is now a session id, and
every existing document still finds its parent.

Properties this script is built to have, in order of how much they matter:

* **Idempotent.** Re-running it does nothing the second time. A migration that
  must be run exactly once is a migration that will be run twice.
* **Non-destructive.** ``projects`` is never written to and never dropped. If
  something is wrong we read the originals; the acceptance criterion for this
  milestone is literally "v1 projects readable".
* **Resumable.** Each project is converted in its own write, so an interruption
  leaves a consistent prefix rather than a torn batch. ``migrated_from:
  "project"`` marks what came from where, and there is an index to find it.
* **Reportable.** It returns counts and skips rather than printing and exiting,
  so it can be asserted on in a test instead of trusted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from biostruct.models.conversation import SCHEMA_VERSION, SessionStatus
from biostruct.repositories.conversations import SESSIONS

log = logging.getLogger(__name__)

PROJECTS = "projects"


@dataclass
class MigrationReport:
    """What happened, in enough detail to act on."""

    scanned: int = 0
    migrated: int = 0
    #: Already present from an earlier run. Expected, not a problem.
    already_done: int = 0
    #: Could not be converted, with the reason. Never silently dropped.
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> str:
        parts = [
            f"scanned {self.scanned}",
            f"migrated {self.migrated}",
            f"already done {self.already_done}",
        ]
        if self.failed:
            parts.append(f"FAILED {len(self.failed)}")
        return ", ".join(parts)


def session_doc_from_project(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert one project document. Pure, so it is testable without a database.

    A v1 project had a required ``name`` and a required ``infection``; a v2
    session has an optional infection and a title that may be blank. The name
    becomes the title, which is right: it is what the researcher typed to
    identify this piece of work.

    Money is copied as the stored strings, untouched. Re-parsing it through
    ``Decimal`` here would be a second place where a rounding rule lives, and
    the budget ledger's invariants are already written against these exact
    values.
    """
    now = datetime.now(UTC)
    budget = doc.get("budget") or {}
    return {
        # The same id, deliberately. See the module docstring.
        "_id": doc["_id"],
        "user_id": doc["user_id"],
        "title": doc.get("name", ""),
        "infection": doc.get("infection"),
        "status": SessionStatus.active.value,
        "ranking_weights": doc.get("ranking_weights") or {},
        "budget": {
            "limit_usd": budget.get("limit_usd", "0"),
            "spent_usd": budget.get("spent_usd", "0"),
            "reserved_usd": budget.get("reserved_usd", "0"),
        },
        # A v1 project had no conversation, so the thread starts empty. It is
        # not back-filled with a synthetic message: inventing a turn the
        # researcher never had would put words in their mouth in their own
        # transcript.
        "message_count": 0,
        "last_seq": 0,
        "last_message_at": None,
        # The original timestamps are kept. Stamping "migrated today" would
        # reorder a researcher's history in the sidebar.
        "created_at": doc.get("created_at", now),
        "updated_at": doc.get("updated_at", now),
        "schema_version": SCHEMA_VERSION,
        "migrated_from": "project",
    }


async def migrate(
    db: AsyncIOMotorDatabase[dict[str, Any]], *, dry_run: bool = False
) -> MigrationReport:
    """Copy every v1 project into ``research_sessions``.

    ``dry_run`` reports what would happen and writes nothing, which is how this
    should be run against production data the first time.
    """
    report = MigrationReport()
    cursor = db[PROJECTS].find({}).sort("created_at", 1)
    async for project in cursor:
        report.scanned += 1
        project_id = project.get("_id")
        if not project_id or not project.get("user_id"):
            report.failed.append((str(project_id), "missing _id or user_id"))
            continue

        existing = await db[SESSIONS].find_one({"_id": project_id}, {"_id": 1})
        if existing is not None:
            report.already_done += 1
            continue

        try:
            doc = session_doc_from_project(project)
        except (KeyError, TypeError, ValueError) as exc:
            report.failed.append((str(project_id), f"could not convert: {exc}"))
            continue

        if dry_run:
            report.migrated += 1
            continue

        try:
            await db[SESSIONS].insert_one(doc)
        except DuplicateKeyError:
            # Another run inserted it between the check and the write. That is
            # the outcome we wanted anyway.
            report.already_done += 1
            continue
        except Exception as exc:  # a write failure must not abandon the rest
            log.warning("could not migrate project %s: %s", project_id, exc)
            report.failed.append((str(project_id), str(exc)))
            continue
        report.migrated += 1

    log.info("project migration: %s", report.summary())
    return report


__all__ = ["PROJECTS", "MigrationReport", "migrate", "session_doc_from_project"]

"""The v1 -> v2 migration (M6).

The milestone's acceptance criterion is "v1 projects readable", and that is the
first thing asserted here: the migration never writes to ``projects``, so the
originals remain exactly as they were and a bad conversion can be re-run rather
than reconstructed.

The conversion function is pure, so most of its edge cases need no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.migrations.projects_to_sessions import (
    PROJECTS,
    migrate,
    session_doc_from_project,
)
from biostruct.models.conversation import SCHEMA_VERSION, SessionStatus
from biostruct.repositories.conversations import SESSIONS, ConversationRepository

USER = "user-abc"

CREATED = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)


def project_doc(project_id: str = "proj-1", **overrides: Any) -> dict[str, Any]:
    """A v1 project exactly as ``ProjectRepository.create`` writes one."""
    doc: dict[str, Any] = {
        "_id": project_id,
        "user_id": USER,
        "name": "Carbapenem-resistant Klebsiella",
        "infection": {
            "name": "Carbapenem-resistant Klebsiella pneumoniae",
            "pathogen": "Klebsiella pneumoniae",
            "amr_context": "ICU isolates, carbapenem non-susceptible",
        },
        "ranking_weights": {"binding_confidence": "0.40", "iptm": "0.20"},
        "budget": {
            "limit_usd": "25.0000",
            "spent_usd": "3.7500",
            "reserved_usd": "0.5000",
        },
        "created_at": CREATED,
        "updated_at": CREATED + timedelta(days=2),
        "schema_version": 1,
    }
    doc.update(overrides)
    return doc


@pytest_asyncio.fixture
async def db(settings: Settings, clean_db: None) -> Any:
    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        settings.mongodb_uri, tz_aware=True
    )
    database = client[settings.mongodb_db]
    await ensure_indexes(database)
    try:
        yield database
    finally:
        client.close()


class TestConversion:
    def test_the_session_keeps_the_projects_id(self) -> None:
        """The decision the whole migration rests on. ``targets``,
        ``design_runs`` and ``candidates`` all reference ``project_id``;
        minting a new id would mean rewriting every one of those, and a
        reference rewrite that half-completes orphans paid results with no way
        to tell which half ran."""
        doc = session_doc_from_project(project_doc("proj-42"))
        assert doc["_id"] == "proj-42"

    def test_the_project_name_becomes_the_title(self) -> None:
        doc = session_doc_from_project(project_doc())
        assert doc["title"] == "Carbapenem-resistant Klebsiella"

    def test_the_infection_is_carried_over_whole(self) -> None:
        doc = session_doc_from_project(project_doc())
        assert doc["infection"]["pathogen"] == "Klebsiella pneumoniae"
        assert "ICU isolates" in doc["infection"]["amr_context"]

    def test_money_is_copied_as_stored_without_being_reparsed(self) -> None:
        """A second place that rounds money is a second place that can disagree
        with the ledger. The strings are moved, not recomputed."""
        doc = session_doc_from_project(project_doc())
        assert doc["budget"] == {
            "limit_usd": "25.0000",
            "spent_usd": "3.7500",
            "reserved_usd": "0.5000",
        }

    def test_the_original_timestamps_are_preserved(self) -> None:
        """Stamping "migrated today" would reorder a researcher's history in
        the sidebar, so the oldest work would appear newest."""
        doc = session_doc_from_project(project_doc())
        assert doc["created_at"] == CREATED
        assert doc["updated_at"] == CREATED + timedelta(days=2)

    def test_the_thread_starts_empty_rather_than_invented(self) -> None:
        """A v1 project had no conversation. Back-filling a synthetic opening
        message would put words in the researcher's mouth in their own
        transcript."""
        doc = session_doc_from_project(project_doc())
        assert doc["message_count"] == 0
        assert doc["last_seq"] == 0
        assert doc["last_message_at"] is None

    def test_the_result_is_marked_as_migrated(self) -> None:
        doc = session_doc_from_project(project_doc())
        assert doc["migrated_from"] == "project"
        assert doc["schema_version"] == SCHEMA_VERSION

    def test_a_project_with_no_budget_block_still_converts(self) -> None:
        """Defensive: v1 always wrote one, but a hand-edited or partially
        written document must not stop the whole migration."""
        doc = session_doc_from_project(project_doc(budget=None))
        assert doc["budget"]["limit_usd"] == "0"

    def test_an_archived_v1_project_arrives_active(self) -> None:
        doc = session_doc_from_project(project_doc())
        assert doc["status"] == SessionStatus.active.value


class TestMigrationRun:
    async def test_v1_projects_remain_readable_afterwards(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """The milestone's acceptance criterion, stated as a test. Nothing is
        written to ``projects`` and nothing is dropped, so a conversion that
        turns out wrong is re-run from the originals."""
        original = project_doc()
        await db[PROJECTS].insert_one(dict(original))
        await migrate(db)
        after = await db[PROJECTS].find_one({"_id": "proj-1"})
        assert after == original

    async def test_a_migrated_session_is_readable_through_the_repository(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], tmp_path: Any
    ) -> None:
        """Converting the documents is not enough — the application has to be
        able to load one."""
        await db[PROJECTS].insert_one(project_doc())
        report = await migrate(db)
        assert report.migrated == 1
        assert report.ok

        repo = ConversationRepository(db)
        session = await repo.get_session("proj-1", user_id=USER)
        assert session is not None
        assert session.is_migrated
        assert session.title == "Carbapenem-resistant Klebsiella"
        assert session.budget.limit_usd == Decimal("25")
        assert session.budget.spent_usd == Decimal("3.75")
        assert session.budget.remaining_usd == Decimal("20.75")
        assert session.display_title() == "Carbapenem-resistant Klebsiella"

    async def test_a_migrated_session_accepts_its_first_message(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """A session nobody can write to is a read-only museum piece. The
        counters have to be in a state the append path accepts."""
        from biostruct.models.conversation import MessageCreate, Role

        await db[PROJECTS].insert_one(project_doc())
        await migrate(db)
        repo = ConversationRepository(db)
        message = await repo.append_message(
            session_id="proj-1", user_id=USER, data=MessageCreate(role=Role.user)
        )
        assert message is not None
        assert message.seq == 1

    async def test_running_it_twice_changes_nothing(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """A migration that must run exactly once is a migration that will be
        run twice."""
        await db[PROJECTS].insert_many([project_doc("p1"), project_doc("p2")])
        first = await migrate(db)
        second = await migrate(db)
        assert first.migrated == 2
        assert second.migrated == 0
        assert second.already_done == 2
        assert await db[SESSIONS].count_documents({}) == 2

    async def test_it_does_not_overwrite_a_session_someone_has_since_used(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """The dangerous version of re-running: a second pass that resets
        ``message_count`` and ``last_seq`` would strand every message already
        written, because new appends would reuse positions the unique index
        then rejects."""
        from biostruct.models.conversation import MessageCreate, Role

        await db[PROJECTS].insert_one(project_doc())
        await migrate(db)
        repo = ConversationRepository(db)
        await repo.append_message(
            session_id="proj-1", user_id=USER, data=MessageCreate(role=Role.user)
        )
        await migrate(db)
        session = await repo.get_session("proj-1", user_id=USER)
        assert session is not None
        assert session.message_count == 1
        assert session.last_seq == 1
        follow_up = await repo.append_message(
            session_id="proj-1", user_id=USER, data=MessageCreate(role=Role.user)
        )
        assert follow_up is not None
        assert follow_up.seq == 2

    async def test_a_dry_run_writes_nothing(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        """How this should be run against production data the first time."""
        await db[PROJECTS].insert_one(project_doc())
        report = await migrate(db, dry_run=True)
        assert report.migrated == 1
        assert await db[SESSIONS].count_documents({}) == 0

    async def test_one_unconvertible_project_does_not_abandon_the_rest(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Resumability in practice: a single bad document must not leave the
        other researchers' work unmigrated."""
        await db[PROJECTS].insert_many(
            [
                project_doc("good-1"),
                project_doc("broken", user_id=None),
                project_doc("good-2"),
            ]
        )
        report = await migrate(db)
        assert report.migrated == 2
        assert not report.ok
        assert report.failed[0][0] == "broken"
        assert await db[SESSIONS].count_documents({}) == 2

    async def test_the_report_says_what_happened(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        await db[PROJECTS].insert_one(project_doc())
        report = await migrate(db)
        assert "migrated 1" in report.summary()
        assert report.scanned == 1

    async def test_an_empty_database_migrates_cleanly(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        report = await migrate(db)
        assert report.ok
        assert report.scanned == 0

    async def test_migrated_sessions_are_findable_as_such(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """There is a partial index for exactly this query, so work left behind
        by an interrupted run can be found rather than guessed at."""
        await db[PROJECTS].insert_one(project_doc())
        await migrate(db)
        assert await db[SESSIONS].count_documents({"migrated_from": "project"}) == 1

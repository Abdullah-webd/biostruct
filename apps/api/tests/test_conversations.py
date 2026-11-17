"""The conversation model and its repository (M6).

These run against a real MongoDB, because the two things most worth testing
here are database behaviours and cannot be faked: sequence allocation under
concurrency, and the unique index that backs it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.models.conversation import (
    PAYLOAD_INLINE_MAX_BYTES,
    SCHEMA_VERSION,
    ArtifactCreate,
    ArtifactKind,
    Block,
    BlockKind,
    Citation,
    Message,
    MessageCreate,
    Role,
    SessionCreate,
    SessionStatus,
    SessionUpdate,
    ToolCall,
    ToolResult,
    Usage,
)
from biostruct.models.project import Infection
from biostruct.repositories.conversations import (
    ARTIFACTS,
    MESSAGES,
    SESSIONS,
    ConversationRepository,
)
from biostruct.storage import LocalDiskStorage

USER = "user-abc"
OTHER_USER = "user-xyz"


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


@pytest_asyncio.fixture
async def repo(db: AsyncIOMotorDatabase[dict[str, Any]], tmp_path: Any) -> ConversationRepository:
    return ConversationRepository(db, storage=LocalDiskStorage(tmp_path / "store"))


async def a_session(repo: ConversationRepository, *, user_id: str = USER) -> Any:
    return await repo.create_session(
        user_id=user_id,
        data=SessionCreate(title="Carbapenem-resistant Klebsiella", budget_usd="25"),
        default_budget_usd=Decimal("10"),
    )


class TestTheNameCollision:
    """The reason this model lives in its own collection."""

    async def test_a_research_session_is_not_stored_with_auth_sessions(
        self, repo: ConversationRepository, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """``sessions`` holds auth sessions and has a TTL index that deletes
        them. A research conversation written there would expire itself and take
        a researcher's work with it."""
        await a_session(repo)
        assert await db[SESSIONS].count_documents({}) == 1
        assert await db.sessions.count_documents({}) == 0

    async def test_the_auth_collection_still_expires_and_ours_does_not(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        auth = await db.sessions.index_information()
        research = await db[SESSIONS].index_information()
        assert any("expireAfterSeconds" in spec for spec in auth.values())
        assert not any("expireAfterSeconds" in spec for spec in research.values())


class TestIndexes:
    """The milestone's second acceptance criterion: indexes in place."""

    async def test_every_index_the_thread_read_needs_exists(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        assert "messages_session_seq_unique" in await db[MESSAGES].index_information()
        assert "research_sessions_user_activity" in await db[SESSIONS].index_information()
        assert "artifacts_session_created" in await db[ARTIFACTS].index_information()

    async def test_the_message_position_index_is_unique(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        info = await db[MESSAGES].index_information()
        assert info["messages_session_seq_unique"].get("unique") is True

    async def test_ensure_indexes_is_safe_to_run_twice(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """It runs on every boot. Two indexes on the same keys with different
        names would make the second boot fail, which is exactly the kind of bug
        that only appears on redeploy."""
        await ensure_indexes(db)
        await ensure_indexes(db)


class TestOrdering:
    async def test_messages_come_back_in_the_order_they_were_written(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        for i in range(5):
            await repo.append_message(
                session_id=session.id,
                user_id=USER,
                data=MessageCreate(role=Role.user, blocks=[Block(text=f"turn {i}")]),
            )
        messages = await repo.list_messages(session.id, user_id=USER)
        assert [m.seq for m in messages] == [1, 2, 3, 4, 5]
        assert [m.text for m in messages] == [f"turn {i}" for i in range(5)]

    async def test_concurrent_appends_never_share_a_position(
        self, repo: ConversationRepository
    ) -> None:
        """The reason ordering is an integer and not a timestamp. An assistant
        turn and the tool result it triggered are routinely written in the same
        millisecond; a thread rendered out of order misattributes facts to the
        wrong step."""
        session = await a_session(repo)
        written = await asyncio.gather(
            *(
                repo.append_message(
                    session_id=session.id,
                    user_id=USER,
                    data=MessageCreate(role=Role.user, blocks=[Block(text=f"#{i}")]),
                )
                for i in range(12)
            )
        )
        seqs = sorted(m.seq for m in written if m is not None)
        assert seqs == list(range(1, 13))

    async def test_the_session_counters_track_the_thread(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        assert session.message_count == 0
        assert session.last_message_at is None
        for _ in range(3):
            await repo.append_message(
                session_id=session.id, user_id=USER, data=MessageCreate(role=Role.user)
            )
        refreshed = await repo.get_session(session.id, user_id=USER)
        assert refreshed is not None
        assert refreshed.message_count == 3
        assert refreshed.last_seq == 3
        assert refreshed.last_message_at is not None

    async def test_after_seq_returns_only_what_is_new(self, repo: ConversationRepository) -> None:
        """How the UI tails a turn in progress without re-reading the thread."""
        session = await a_session(repo)
        for i in range(4):
            await repo.append_message(
                session_id=session.id,
                user_id=USER,
                data=MessageCreate(role=Role.user, blocks=[Block(text=str(i))]),
            )
        tail = await repo.list_messages(session.id, user_id=USER, after_seq=2)
        assert [m.seq for m in tail] == [3, 4]


class TestTenantIsolation:
    async def test_another_researcher_cannot_read_the_thread(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        await repo.append_message(
            session_id=session.id, user_id=USER, data=MessageCreate(role=Role.user)
        )
        assert await repo.get_session(session.id, user_id=OTHER_USER) is None
        assert await repo.list_messages(session.id, user_id=OTHER_USER) == []

    async def test_appending_to_someone_elses_session_is_refused(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        assert (
            await repo.append_message(
                session_id=session.id, user_id=OTHER_USER, data=MessageCreate(role=Role.user)
            )
            is None
        )

    async def test_a_refused_append_does_not_burn_a_sequence_number(
        self, repo: ConversationRepository
    ) -> None:
        """Authorisation and allocation are the same atomic operation, so there
        is no window where a number is consumed for a write that never lands —
        which would leave a permanent gap in someone's transcript."""
        session = await a_session(repo)
        await repo.append_message(
            session_id=session.id, user_id=OTHER_USER, data=MessageCreate(role=Role.user)
        )
        ok = await repo.append_message(
            session_id=session.id, user_id=USER, data=MessageCreate(role=Role.user)
        )
        assert ok is not None
        assert ok.seq == 1

    async def test_an_artifact_cannot_be_attached_to_another_users_session(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        assert (
            await repo.add_artifact(
                session_id=session.id,
                user_id=OTHER_USER,
                data=ArtifactCreate(kind=ArtifactKind.ranking),
            )
            is None
        )


class TestToolResults:
    async def test_a_small_result_is_stored_whole_in_the_document(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.tool,
                tool_calls=[ToolCall(call_id="c1", name="molecules.library")],
                tool_results=[
                    ToolResult(
                        call_id="c1",
                        name="molecules.library",
                        summary="20 molecules, control present",
                        payload={"entries": [{"smiles": "CCO"}]},
                    )
                ],
            ),
        )
        assert message is not None
        result = message.tool_results[0]
        assert not result.is_spilled
        assert result.payload == {"entries": [{"smiles": "CCO"}]}
        assert await repo.load_payload(result) == {"entries": [{"smiles": "CCO"}]}

    async def test_an_oversized_result_spills_to_storage_and_is_readable_back(
        self, repo: ConversationRepository
    ) -> None:
        """Mongo refuses any document over 16 MB, and a 2000-molecule library
        or a structure file gets there. A write that failed at the end of a paid
        run would lose the result we just paid for."""
        session = await a_session(repo)
        big = {"rows": ["C" * 64 for _ in range(PAYLOAD_INLINE_MAX_BYTES // 32)]}
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.tool,
                tool_results=[
                    ToolResult(call_id="c1", name="molecules.screen", summary="s", payload=big)
                ],
            ),
        )
        assert message is not None
        result = message.tool_results[0]
        assert result.is_spilled
        assert result.payload is None
        assert result.summary == "s", "the summary is what the agent reasons from"
        assert await repo.load_payload(result) == big

    async def test_the_summary_survives_when_storage_is_unavailable(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Losing the whole message because the payload would not fit is worse
        than losing the detail: the summary is what the thread needs to stay
        coherent, and the researcher is told the detail is missing."""
        repo = ConversationRepository(db, storage=None)
        session = await a_session(repo)
        big = {"rows": ["C" * 64 for _ in range(PAYLOAD_INLINE_MAX_BYTES // 32)]}
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.tool,
                tool_results=[
                    ToolResult(call_id="c1", name="molecules.screen", summary="kept", payload=big)
                ],
            ),
        )
        assert message is not None
        result = message.tool_results[0]
        assert result.summary == "kept"
        assert result.payload is None
        assert result.error, "the researcher must be told the detail is gone"

    async def test_a_spent_tool_result_records_what_it_cost(
        self, repo: ConversationRepository
    ) -> None:
        """The thread is the audit trail: money spent is visible in the
        transcript, not only in the ledger."""
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.tool,
                tool_results=[
                    ToolResult(call_id="c1", name="molecules.screen", cost_usd=Decimal("0.50"))
                ],
            ),
        )
        assert message is not None
        assert message.tool_results[0].cost_usd == Decimal("0.5000")

    def test_money_sent_as_a_float_is_refused(self) -> None:
        """Same rule as the budget ledger: a float is how an amount drifts."""
        with pytest.raises(ValueError, match="never a float"):
            ToolResult(call_id="c", name="n", cost_usd=0.5)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="never a float"):
            Block(text="t", estimate_usd=1.25)  # type: ignore[arg-type]

    async def test_a_tool_result_keeps_its_own_role(self, repo: ConversationRepository) -> None:
        """ "A tool returned this" and "the model said this" must never be
        confusable, because the whole citation guarantee rests on the
        difference."""
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id, user_id=USER, data=MessageCreate(role=Role.tool)
        )
        assert message is not None
        assert message.role is Role.tool


class TestCitationsAndFlags:
    async def test_a_verified_citation_round_trips(self, repo: ConversationRepository) -> None:
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.assistant,
                blocks=[Block(text="KPC hydrolyses carbapenems [1].")],
                citations=[
                    Citation(
                        source="MED",
                        external_id="42942151",
                        title="Carbapenemase gene profiles",
                        verified=True,
                    )
                ],
            ),
        )
        assert message is not None
        citation = message.citations[0]
        assert citation.key == "MED:42942151"
        assert citation.verified is True

    async def test_an_unsupported_claim_stays_flagged_in_storage(
        self, repo: ConversationRepository
    ) -> None:
        """M4 strips the fabricated reference and keeps the claim. If the flag
        were not persisted, reopening the session would present the claim as
        though it had been supported all along."""
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.assistant,
                blocks=[
                    Block(
                        text="Resistance has reached 90 percent in Lagos.",
                        flagged=("Resistance has reached 90 percent in Lagos.",),
                    )
                ],
            ),
        )
        assert message is not None
        reread = await repo.get_message(message.id, user_id=USER)
        assert reread is not None
        assert reread.has_unverified_claims
        assert reread.blocks[0].flagged == ("Resistance has reached 90 percent in Lagos.",)


class TestSessions:
    async def test_sessions_are_listed_by_last_activity_not_creation(
        self, repo: ConversationRepository
    ) -> None:
        """A researcher returning after a week wants the conversation they were
        in, which is rarely the newest one."""
        first = await a_session(repo)
        await a_session(repo)
        await repo.append_message(
            session_id=first.id, user_id=USER, data=MessageCreate(role=Role.user)
        )
        listed = await repo.list_sessions(USER)
        assert listed[0].id == first.id

    async def test_an_archived_session_is_kept_and_hidden(
        self, repo: ConversationRepository
    ) -> None:
        """The results are the researcher's. Archiving is not deletion."""
        session = await a_session(repo)
        await repo.update_session(
            session.id, user_id=USER, data=SessionUpdate(status=SessionStatus.archived)
        )
        assert await repo.list_sessions(USER) == []
        archived = await repo.list_sessions(USER, status=SessionStatus.archived)
        assert [s.id for s in archived] == [session.id]
        assert await repo.get_session(session.id, user_id=USER) is not None

    async def test_a_session_always_has_something_to_show_in_a_sidebar(
        self, repo: ConversationRepository
    ) -> None:
        untitled = await repo.create_session(
            user_id=USER, data=SessionCreate(), default_budget_usd=Decimal("10")
        )
        assert untitled.display_title() == "Untitled session"
        from_infection = await repo.create_session(
            user_id=USER,
            data=SessionCreate(infection=Infection(name="Resistant Klebsiella")),
            default_budget_usd=Decimal("10"),
        )
        assert from_infection.display_title() == "Resistant Klebsiella"

    async def test_the_default_budget_applies_when_none_is_given(
        self, repo: ConversationRepository
    ) -> None:
        session = await repo.create_session(
            user_id=USER, data=SessionCreate(), default_budget_usd=Decimal("10")
        )
        assert session.budget.limit_usd == Decimal("10")
        assert session.budget.remaining_usd == Decimal("10")

    async def test_usage_is_recorded_so_cost_per_turn_is_a_fact(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        message = await repo.append_message(
            session_id=session.id,
            user_id=USER,
            data=MessageCreate(
                role=Role.assistant,
                usage=Usage(input_tokens=1200, output_tokens=300, model="gpt-x"),
            ),
        )
        assert message is not None
        assert message.usage is not None
        assert message.usage.total_tokens == 1500


class TestArtifacts:
    async def test_an_artifact_is_attached_and_listed_newest_first(
        self, repo: ConversationRepository
    ) -> None:
        session = await a_session(repo)
        for kind in (ArtifactKind.structure, ArtifactKind.ranking):
            await repo.add_artifact(
                session_id=session.id,
                user_id=USER,
                data=ArtifactCreate(kind=kind, title=kind.value, run_id="run-1"),
            )
        artifacts = await repo.list_artifacts(session.id, user_id=USER)
        assert len(artifacts) == 2
        assert artifacts[0].kind is ArtifactKind.ranking
        assert [a.seq for a in artifacts] == [2, 1]
        assert all(a.schema_version == SCHEMA_VERSION for a in artifacts)

    async def test_a_structure_artifact_points_at_our_own_copy(
        self, repo: ConversationRepository
    ) -> None:
        """Boltz presigned URLs expire in seven days, so anything we intend to
        keep is downloaded and referenced by our storage key."""
        session = await a_session(repo)
        artifact = await repo.add_artifact(
            session_id=session.id,
            user_id=USER,
            data=ArtifactCreate(
                kind=ArtifactKind.structure, storage_key="structures/run-1/sample-0.cif"
            ),
        )
        assert artifact is not None
        assert artifact.storage_key.endswith(".cif")
        assert "http" not in artifact.storage_key


class TestBlocks:
    def test_a_proposal_block_carries_what_it_would_cost(self) -> None:
        block = Block(kind=BlockKind.proposal, text="Screen 20 molecules", estimate_usd="0.50")
        assert block.estimate_usd == Decimal("0.50")

    def test_only_text_blocks_form_the_readable_message(self) -> None:
        message = Message(
            id="m",
            session_id="s",
            user_id=USER,
            seq=1,
            role=Role.assistant,
            blocks=[
                Block(text="Here is the plan."),
                Block(kind=BlockKind.proposal, text="Spend $0.50"),
                Block(kind=BlockKind.error, text="internal detail"),
            ],
            created_at=datetime.now(UTC),
        )
        assert message.text == "Here is the plan."

    async def test_artifacts_from_one_turn_keep_a_stable_order(
        self, repo: ConversationRepository
    ) -> None:
        """A flaky test found this: two artifacts written in the same
        millisecond had no defined order, and one turn routinely produces
        several. A canvas that reshuffles between reads is a canvas a
        researcher cannot refer back to."""
        session = await a_session(repo)
        made = await asyncio.gather(
            *(
                repo.add_artifact(
                    session_id=session.id,
                    user_id=USER,
                    data=ArtifactCreate(kind=ArtifactKind.evidence, title=f"paper {i}"),
                )
                for i in range(8)
            )
        )
        seqs = sorted(a.seq for a in made if a is not None)
        assert seqs == list(range(1, 9))
        first = [a.title for a in await repo.list_artifacts(session.id, user_id=USER)]
        second = [a.title for a in await repo.list_artifacts(session.id, user_id=USER)]
        assert first == second

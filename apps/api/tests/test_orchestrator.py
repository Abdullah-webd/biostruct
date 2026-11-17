"""The agent loop (M7).

These tests are about the loop's hard edges, because that is what makes this an
orchestrator rather than an autonomous agent: a step cap, paid work that cannot
start itself, and every answer passing the citation gate.

The LLM is a stub that replays scripted turns. MongoDB is real, because the
turn's output is messages in a thread and asserting on a fake store would prove
nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from biostruct.agent.llm import (
    FunctionCall,
    TextDelta,
    TokenUsage,
    ToolCallStarted,
    Turn,
    TurnFinished,
)
from biostruct.agent.orchestrator import (
    HEARTBEAT,
    HEARTBEAT_FRAME,
    MAX_STEPS,
    SYSTEM_PROMPT,
    Completed,
    Delta,
    Failed,
    Orchestrator,
    ProposalRaised,
    ToolStarted,
    event_to_sse,
    with_heartbeat,
)
from biostruct.agent.tools.base import Registry, Tool, ToolContext, ToolResultData, text
from biostruct.agent.tools.science import build_registry
from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.models.conversation import Block, MessageCreate, Role, SessionCreate
from biostruct.repositories.conversations import ConversationRepository
from biostruct.sources.europepmc import Paper

USER = "u1"

PAPER = Paper(
    source="MED",
    external_id="42942151",
    title="Carbapenemase gene profiles in Klebsiella",
    journal="Int J Mol Sci",
    year="2026",
)


class ScriptedLLM:
    """Replays a list of turns, recording what it was asked."""

    def __init__(self, turns: list[Turn], model: str = "gpt-6.1-sol") -> None:
        self._turns = list(turns)
        self.model = model
        self.calls: list[dict[str, Any]] = []

    async def stream(
        self,
        *,
        instructions: str,
        input_items: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[Any]:
        self.calls.append(
            {"instructions": instructions, "input": list(input_items), "tools": list(tools)}
        )
        turn = self._turns.pop(0) if self._turns else Turn(text="(no more turns)")
        if turn.text:
            yield TextDelta(turn.text)
        for call in turn.calls:
            yield ToolCallStarted(call_id=call.call_id, name=call.name)
        yield TurnFinished(turn)


def text_turn(body: str, *, usage: TokenUsage | None = None) -> Turn:
    return Turn(text=body, usage=usage or TokenUsage(model="gpt-6.1-sol"))


def tool_turn(name: str, args: dict[str, Any], *, call_id: str = "c1") -> Turn:
    call = FunctionCall(call_id=call_id, name=name, arguments=args, item_id=f"fc_{call_id}")
    return Turn(
        calls=(call,),
        usage=TokenUsage(model="gpt-6.1-sol"),
        output_items=(
            {
                "type": "function_call",
                "id": call.item_id,
                "call_id": call_id,
                "name": name,
                "arguments": "{}",
            },
        ),
    )


class StubEuropePMC:
    def __init__(self, known: list[Paper] | None = None) -> None:
        self._known = {p.key: p for p in (known if known is not None else [PAPER])}

    async def search(self, query: str, *, limit: int = 10) -> list[Paper]:
        return list(self._known.values())

    async def resolve(self, source: str, external_id: str) -> Paper | None:
        return self._known.get(f"{source}:{external_id}")


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


async def a_thread(db: AsyncIOMotorDatabase[dict[str, Any]], question: str) -> tuple[Any, str]:
    repo = ConversationRepository(db)
    session = await repo.create_session(
        user_id=USER, data=SessionCreate(title="t"), default_budget_usd=Decimal("25")
    )
    await repo.append_message(
        session_id=session.id,
        user_id=USER,
        data=MessageCreate(role=Role.user, blocks=[Block(text=question)]),
    )
    return repo, session.id


def orchestrator_for(
    repo: ConversationRepository,
    turns: list[Turn],
    *,
    registry: Registry | None = None,
    europepmc: Any = None,
    max_steps: int = MAX_STEPS,
) -> tuple[Orchestrator, ScriptedLLM]:
    llm = ScriptedLLM(turns)
    orch = Orchestrator(
        llm=llm,  # type: ignore[arg-type]
        registry=registry or build_registry(),
        conversations=repo,
        europepmc=europepmc,
        max_steps=max_steps,
    )
    return orch, llm


async def collect(orch: Orchestrator, session_id: str, ctx: ToolContext) -> list[Any]:
    return [e async for e in orch.run_turn(session_id=session_id, user_id=USER, ctx=ctx)]


class TestTheLoop:
    async def test_a_plain_answer_is_streamed_and_stored(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "What can you do?")
        orch, _ = orchestrator_for(repo, [text_turn("I fold proteins and find molecules.")])
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))

        assert any(isinstance(e, Delta) for e in events)
        done = next(e for e in events if isinstance(e, Completed))
        assert done.text == "I fold proteins and find molecules."

        messages = await repo.list_messages(session_id, user_id=USER)
        assert [m.role for m in messages] == [Role.user, Role.assistant]
        assert messages[-1].text == "I fold proteins and find molecules."

    async def test_a_tool_call_runs_and_the_turn_continues(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "Find papers on KPC")
        orch, llm = orchestrator_for(
            repo,
            [tool_turn("literature_search", {"query": "KPC"}), text_turn("Found one paper.")],
            europepmc=StubEuropePMC(),
        )
        events = await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        assert any(isinstance(e, ToolStarted) for e in events)
        assert any(isinstance(e, Completed) for e in events)
        # Two model calls: one that asked for the tool, one that answered.
        assert len(llm.calls) == 2

    async def test_the_tool_call_item_is_echoed_back_with_its_result(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Without the call item, the model has no record of having asked and
        asks again — a loop the researcher pays for."""
        repo, session_id = await a_thread(db, "Find papers on KPC")
        orch, llm = orchestrator_for(
            repo,
            [tool_turn("literature_search", {"query": "KPC"}), text_turn("Done.")],
            europepmc=StubEuropePMC(),
        )
        await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        second = llm.calls[1]["input"]
        kinds = [i.get("type") for i in second]
        assert "function_call" in kinds
        assert "function_call_output" in kinds

    async def test_a_tool_turn_is_recorded_in_the_thread(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """So a researcher can see what was actually consulted, rather than
        taking the answer on trust."""
        repo, session_id = await a_thread(db, "Find papers on KPC")
        orch, _ = orchestrator_for(
            repo,
            [tool_turn("literature_search", {"query": "KPC"}), text_turn("Done.")],
            europepmc=StubEuropePMC(),
        )
        await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        messages = await repo.list_messages(session_id, user_id=USER)
        tool_messages = [m for m in messages if m.role is Role.tool]
        assert len(tool_messages) == 1
        assert tool_messages[0].tool_calls[0].name == "literature_search"
        assert tool_messages[0].tool_results[0].payload is not None

    async def test_the_step_cap_ends_the_turn_honestly(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """An agent that can loop forever will. The researcher is told, and the
        answer does not pretend to be an answer."""
        repo, session_id = await a_thread(db, "go round forever")
        looping = [
            tool_turn("literature_search", {"query": "x"}, call_id=f"c{i}") for i in range(10)
        ]
        orch, llm = orchestrator_for(repo, looping, europepmc=StubEuropePMC(), max_steps=3)
        events = await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        assert len(llm.calls) == 3
        done = next(e for e in events if isinstance(e, Completed))
        assert "all the steps available" in done.text

    async def test_a_model_failure_is_reported_not_swallowed(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        from biostruct.agent.llm import LLMError

        class BrokenLLM:
            model = "gpt-6.1-sol"

            async def stream(self, **kwargs: Any) -> AsyncIterator[Any]:
                raise LLMError("Rate limit reached")
                yield  # pragma: no cover

        repo, session_id = await a_thread(db, "hello")
        orch = Orchestrator(
            llm=BrokenLLM(),  # type: ignore[arg-type]
            registry=build_registry(),
            conversations=repo,
        )
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        failed = [e for e in events if isinstance(e, Failed)]
        assert failed and "Rate limit" in failed[0].message

    async def test_an_empty_thread_is_refused(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo = ConversationRepository(db)
        session = await repo.create_session(
            user_id=USER, data=SessionCreate(), default_budget_usd=Decimal("25")
        )
        orch, _ = orchestrator_for(repo, [text_turn("hi")])
        events = await collect(orch, session.id, ToolContext(user_id=USER, session_id=session.id))
        assert isinstance(events[0], Failed)


class TestMoneyCannotBeSpent:
    async def test_a_paid_tool_produces_a_proposal_event_and_no_run(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "Fold Q9F663")
        orch, _ = orchestrator_for(
            repo,
            [
                tool_turn("propose_fold", {"accession": "Q9F663", "num_samples": 1}),
                text_turn("That would cost $0.03. Shall I?"),
            ],
        )
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        proposals = [e for e in events if isinstance(e, ProposalRaised)]
        assert len(proposals) == 1
        assert proposals[0].proposal["estimate_usd"] == "0.0250"
        assert proposals[0].proposal["tool"] == "fold_structure"

    async def test_the_proposal_is_stored_as_its_own_block(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """So reopening the session shows the pending decision, not just prose
        about it."""
        repo, session_id = await a_thread(db, "Fold Q9F663")
        orch, _ = orchestrator_for(
            repo,
            [tool_turn("propose_fold", {"accession": "Q9F663"}), text_turn("About $0.03.")],
        )
        await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        assistant = (await repo.list_messages(session_id, user_id=USER))[-1]
        proposal_blocks = [b for b in assistant.blocks if b.kind.value == "proposal"]
        assert len(proposal_blocks) == 1
        assert proposal_blocks[0].estimate_usd == Decimal("0.0250")

    async def test_the_model_is_told_the_run_did_not_start(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Otherwise it tells the researcher their fold is underway."""
        repo, session_id = await a_thread(db, "Fold Q9F663")
        orch, llm = orchestrator_for(
            repo, [tool_turn("propose_fold", {"accession": "Q9F663"}), text_turn("ok")]
        )
        await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        outputs = [i for i in llm.calls[1]["input"] if i.get("type") == "function_call_output"]
        assert "NOT started" in outputs[0]["output"]


class TestCitationGate:
    async def test_a_properly_cited_answer_keeps_its_reference(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "Does KPC hydrolyse carbapenems?")
        orch, _ = orchestrator_for(
            repo,
            [
                tool_turn("literature_search", {"query": "KPC"}),
                text_turn("KPC hydrolyses carbapenems [cite:MED:42942151]."),
            ],
            europepmc=StubEuropePMC(),
        )
        events = await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        done = next(e for e in events if isinstance(e, Completed))
        assert done.text == "KPC hydrolyses carbapenems [1]."
        assert len(done.references) == 1
        assert not done.warning

    async def test_an_invented_citation_is_stripped_before_the_researcher_sees_it(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """The whole point of M4, wired into the loop. The claim survives; the
        false authority does not."""
        repo, session_id = await a_thread(db, "How bad is resistance in Lagos?")
        orch, _ = orchestrator_for(
            repo,
            [text_turn("Resistance has reached 90 percent in Lagos [cite:MED:99999999].")],
            europepmc=StubEuropePMC(),
        )
        events = await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        done = next(e for e in events if isinstance(e, Completed))
        assert "99999999" not in done.text
        assert "Resistance has reached 90 percent in Lagos" in done.text
        assert done.references == []
        assert "could not be verified" in done.warning
        assert done.flagged

    async def test_the_flag_is_persisted_on_the_message(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "x")
        orch, _ = orchestrator_for(
            repo, [text_turn("A claim [cite:MED:99999999].")], europepmc=StubEuropePMC()
        )
        await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        assistant = (await repo.list_messages(session_id, user_id=USER))[-1]
        assert assistant.has_unverified_claims

    async def test_only_verified_papers_are_stored_as_citations(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "x")
        orch, _ = orchestrator_for(
            repo,
            [
                tool_turn("literature_search", {"query": "KPC"}),
                text_turn("True [cite:MED:42942151]. False [cite:MED:111]."),
            ],
            europepmc=StubEuropePMC(),
        )
        await collect(
            orch,
            session_id,
            ToolContext(user_id=USER, session_id=session_id, europepmc=StubEuropePMC()),
        )
        assistant = (await repo.list_messages(session_id, user_id=USER))[-1]
        assert [c.key for c in assistant.citations] == ["MED:42942151"]
        assert all(c.verified for c in assistant.citations)


class TestContextStrategy:
    async def test_history_replays_summaries_not_payloads(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Replaying a 2000-molecule library on every step is how a session
        stops fitting, at $2 per million tokens."""
        repo, session_id = await a_thread(db, "Build a library")
        orch, _llm = orchestrator_for(
            repo,
            [tool_turn("molecules_library", {"ambler_class": "A", "size": 4}), text_turn("Done.")],
        )
        await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        # Start a second turn: the first turn's tool result is now history.
        await repo.append_message(
            session_id=session_id,
            user_id=USER,
            data=MessageCreate(role=Role.user, blocks=[Block(text="and now?")]),
        )
        orch2, llm2 = orchestrator_for(repo, [text_turn("Next.")])
        await collect(orch2, session_id, ToolContext(user_id=USER, session_id=session_id))
        replayed = "".join(str(i.get("content", "")) for i in llm2.calls[0]["input"])
        assert "earlier result from molecules_library" in replayed
        # The summary mentions the control; the payload's SMILES are not replayed.
        assert "Avibactam" in replayed
        assert "NC(=O)[C@@H]1CC" not in replayed

    async def test_the_system_prompt_states_what_the_product_cannot_do(self) -> None:
        """A researcher asking about toxicity or trials must be told plainly,
        not given a confident guess."""
        for phrase in ("toxicity", "clinical trials", "laboratory"):
            assert phrase in SYSTEM_PROMPT.lower()

    async def test_the_system_prompt_forbids_citing_from_memory(self) -> None:
        assert "from memory" in SYSTEM_PROMPT
        assert "[cite:SOURCE:ID]" in SYSTEM_PROMPT


class TestUsageAndCost:
    async def test_tokens_are_summed_across_every_step(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "x")
        usage = TokenUsage(input_tokens=1000, output_tokens=100, model="gpt-6.1-sol")
        orch, _ = orchestrator_for(
            repo,
            [
                Turn(
                    calls=tool_turn("molecules_library", {"ambler_class": "A"}).calls,
                    usage=usage,
                    output_items=tool_turn("molecules_library", {"ambler_class": "A"}).output_items,
                ),
                text_turn("Done.", usage=usage),
            ],
        )
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        done = next(e for e in events if isinstance(e, Completed))
        assert done.usage["input"] == 2000
        assert done.usage["output"] == 200
        # 2000 in at $2/MTok + 200 out at $10/MTok.
        assert Decimal(done.cost_usd) == Decimal("0.0060")

    async def test_the_cost_is_a_string_not_a_float(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Same rule as everywhere else money appears."""
        repo, session_id = await a_thread(db, "x")
        orch, _ = orchestrator_for(repo, [text_turn("hi")])
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        done = next(e for e in events if isinstance(e, Completed))
        assert isinstance(done.cost_usd, str)


class TestArtifacts:
    async def test_a_tool_artifact_is_attached_to_the_session(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_thread(db, "Build a library")
        orch, _ = orchestrator_for(
            repo,
            [tool_turn("molecules_library", {"ambler_class": "A", "size": 4}), text_turn("Done.")],
        )
        await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        assert len(artifacts) == 1
        assert artifacts[0].kind.value == "library"


class TestWireFormat:
    def test_each_event_is_a_named_sse_frame(self) -> None:
        """Named events, so the client switches on a name rather than sniffing
        a shape."""
        frame = event_to_sse(Delta("hello"))
        assert frame.startswith("event: delta\n")
        assert frame.endswith("\n\n")
        assert '"text": "hello"' in frame

    def test_a_failure_is_its_own_event(self) -> None:
        assert event_to_sse(Failed("broke")).startswith("event: failed\n")

    def test_a_tool_event_says_whether_it_spends(self) -> None:
        frame = event_to_sse(ToolStarted(name="propose_fold", spend="paid"))
        assert '"spend": "paid"' in frame


class TestToolFailureHandling:
    async def test_a_failing_tool_lets_the_turn_finish(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """A source being down should produce an honest answer, not a 500 in
        front of a researcher mid-turn."""

        async def explode(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
            raise RuntimeError("UniProt is unreachable")

        registry = Registry(
            [
                Tool(
                    name="flaky",
                    description="free, for testing",
                    properties={"x": text("anything")},
                    required=(),
                    handler=explode,
                )
            ]
        )
        repo, session_id = await a_thread(db, "x")
        orch, _ = orchestrator_for(
            repo,
            [tool_turn("flaky", {}), text_turn("I could not reach UniProt.")],
            registry=registry,
        )
        events = await collect(orch, session_id, ToolContext(user_id=USER, session_id=session_id))
        done = [e for e in events if isinstance(e, Completed)]
        assert done and "could not reach" in done[0].text.lower()


class TestResilience:
    """Added after a live run fell over: ChEMBL's target search began answering
    500 with a 30-second hang, three retries with backoff ran past the client's
    patience, and the stream ended with a draft on screen and no answer."""

    async def test_a_hanging_tool_is_abandoned_rather_than_eating_the_turn(self) -> None:
        async def never(args: dict[str, Any], ctx: ToolContext) -> ToolResultData:
            await asyncio.sleep(60)
            return ToolResultData(summary="too late")

        registry = Registry(
            [
                Tool(
                    name="slow",
                    description="free, for testing",
                    properties={"x": text("anything")},
                    required=(),
                    handler=never,
                )
            ]
        )
        with patch("biostruct.agent.tools.base.TOOL_TIMEOUT_SECONDS", 0.05):
            result = await registry.run("slow", {}, ToolContext(user_id="u", session_id="s"))
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert result.error == "timed out"
        assert "carry on with what" in result.summary

    async def test_a_quiet_turn_emits_a_heartbeat(self) -> None:
        """A silent connection is one a proxy is entitled to close."""

        async def slow_turn() -> AsyncIterator[Any]:
            await asyncio.sleep(0.08)
            yield Delta("finally")

        seen = [e async for e in with_heartbeat(slow_turn(), every=0.02)]
        assert seen.count(HEARTBEAT) >= 2
        assert isinstance(seen[-1], Delta)

    async def test_the_heartbeat_frame_is_a_comment_clients_ignore(self) -> None:
        assert HEARTBEAT_FRAME.startswith(":")
        assert HEARTBEAT_FRAME.endswith("\n\n")

    async def test_a_busy_turn_is_not_slowed_by_the_heartbeat(self) -> None:
        async def quick() -> AsyncIterator[Any]:
            for i in range(3):
                yield Delta(str(i))

        seen = [e async for e in with_heartbeat(quick(), every=5.0)]
        assert HEARTBEAT not in seen
        assert len(seen) == 3

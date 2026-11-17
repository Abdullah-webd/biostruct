"""The agent loop (M7).

An orchestrator with typed tools and deterministic code gates — not an
autonomous agent. The difference is the point: each scientific step costs real
money and produces output a researcher may act on, so the loop has hard edges
that the model cannot talk its way past.

Four limits, all enforced here rather than requested in the prompt:

* **Steps.** ``MAX_STEPS`` model calls per turn. An agent that can loop forever
  will, and the researcher pays for every lap.
* **Money.** Paid tools return a proposal; nothing in this file can submit a
  billed run. M8 adds the confirmation route.
* **Citations.** Every assistant answer goes through the M4 verifier before the
  researcher sees it. A reference the model invented is stripped and the claim
  flagged.
* **Context.** Tool results go into the model's context as summaries; the full
  payload is stored and re-read on demand. Replaying whole payloads every step
  is how a long session gets expensive and then stops fitting.

The turn is streamed, because a scientific turn takes tens of seconds and a
spinner for that long is indistinguishable from a hang.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from biostruct.agent.citations import reference_list, verify
from biostruct.agent.llm import (
    LLMError,
    OpenAIClient,
    TextDelta,
    TokenUsage,
    ToolCallStarted,
    TurnFinished,
)
from biostruct.agent.tools.base import Proposal, Registry, ToolContext, ToolResultData
from biostruct.models.conversation import (
    Block,
    BlockKind,
    Citation,
    MessageCreate,
    Role,
    ToolCall,
    ToolResult,
    Usage,
)
from biostruct.repositories.conversations import ConversationRepository
from biostruct.sources.europepmc import Paper

log = logging.getLogger(__name__)

#: Model calls per turn. Enough for find target -> read it -> search literature
#: -> build a library -> answer, which is the longest honest chain we have.
MAX_STEPS = 8

#: Messages replayed into context. The thread is the state, but a year-old turn
#: is not worth $2 per million tokens to re-read on every step.
HISTORY_MESSAGES = 30

#: Tool summaries are already condensed; this is the backstop for a tool that
#: returns something enormous anyway.
SUMMARY_CHARS_MAX = 6000

SYSTEM_PROMPT = """\
You are Antidote, a research assistant for scientists working on drug-resistant \
infections, with a particular focus on the resistance that is widespread across \
Africa — KPC, NDM, OXA and CTX-M carbapenemases and extended-spectrum \
beta-lactamases.

WHAT YOU CAN ACTUALLY DO
You cover two steps of drug discovery and no others:
  2. Fold a protein: turn an amino-acid sequence into a 3D structure.
  3. Find molecules: screen or invent small molecules against that structure.
You cannot do laboratory work, animal studies, clinical trials, or toxicity \
prediction. When a researcher asks about those, say plainly that they are the \
next steps and that they happen in a lab, not here.

HOW TO WORK
Use the tools. Identify the protein before discussing it, read its details \
before proposing anything, and search the literature before making a claim \
about resistance.

CITATIONS ARE MECHANICAL, NOT STYLISTIC
You may cite a paper only by copying a [cite:SOURCE:ID] marker from a tool \
result in this turn, exactly as it appeared. Never write a citation from \
memory, never invent an identifier, and never reformat a marker. Anything else \
is removed before the researcher sees it and the claim is flagged as \
unsupported, which is worse for them than no citation at all. If the \
literature tool returned nothing, say the literature is thin.

MONEY
Folding, screening and designing cost real money per molecule or per sample. \
The propose_* tools do not start anything: they produce a priced proposal that \
the researcher confirms. Always state the cost in your reply, in dollars. Never \
imply something has started when it has not.

HOW TO WRITE
Write for a working scientist: direct, specific, no preamble. Give numbers with \
their units. Say what is measured and what is predicted — a binding confidence \
is a prediction, a Ki from ChEMBL is a measurement, and conflating them is the \
most damaging thing you can do here. If you do not know, say so.
A screening library always contains a positive control, a compound known to \
inhibit that enzyme class. If the control does not rank highly, the run is not \
working: say that instead of reporting the other hits as if they meant something.
"""


# --- streamed events the HTTP layer forwards --------------------------------


@dataclass(frozen=True, slots=True)
class Started:
    message_id: str
    seq: int


@dataclass(frozen=True, slots=True)
class Delta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolStarted:
    name: str
    spend: str


@dataclass(frozen=True, slots=True)
class ToolFinished:
    name: str
    summary: str
    ok: bool


@dataclass(frozen=True, slots=True)
class ProposalRaised:
    proposal: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Completed:
    message_id: str
    text: str
    references: list[dict[str, str]]
    warning: str
    flagged: list[str]
    usage: dict[str, Any]
    cost_usd: str


@dataclass(frozen=True, slots=True)
class Failed:
    message: str


AgentEvent = Started | Delta | ToolStarted | ToolFinished | ProposalRaised | Completed | Failed


@dataclass
class _TurnState:
    """Accumulated across the steps of one turn."""

    input_items: list[dict[str, Any]] = field(default_factory=list)
    papers: list[Paper] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    usage_total: dict[str, int] = field(
        default_factory=lambda: {"input": 0, "output": 0, "cached": 0, "reasoning": 0}
    )
    cost: Decimal = Decimal("0")

    def add_usage(self, usage: TokenUsage) -> None:
        self.usage_total["input"] += usage.input_tokens
        self.usage_total["output"] += usage.output_tokens
        self.usage_total["cached"] += usage.cached_input_tokens
        self.usage_total["reasoning"] += usage.reasoning_tokens
        self.cost += usage.cost_usd()


def _truncate(text: str) -> str:
    if len(text) <= SUMMARY_CHARS_MAX:
        return text
    return text[:SUMMARY_CHARS_MAX] + "\n[summary truncated]"


class Orchestrator:
    def __init__(
        self,
        *,
        llm: OpenAIClient,
        registry: Registry,
        conversations: ConversationRepository,
        europepmc: Any = None,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._repo = conversations
        # Used only to re-resolve citations. The verifier takes a function, not
        # a client, so a turn can be tested without a network.
        self._europepmc = europepmc
        self._max_steps = max_steps

    def _history_items(self, messages: Sequence[Any]) -> list[dict[str, Any]]:
        """Replay the thread as model input.

        Tool results come back as their **summaries**, not their payloads: the
        summary is what the model reasoned from the first time, and replaying a
        2000-molecule library on every step is how a session stops fitting.
        """
        items: list[dict[str, Any]] = []
        for message in messages[-HISTORY_MESSAGES:]:
            if message.role is Role.user:
                if message.text:
                    items.append({"role": "user", "content": message.text})
            elif message.role is Role.assistant:
                if message.text:
                    items.append({"role": "assistant", "content": message.text})
            elif message.role is Role.tool:
                for result in message.tool_results:
                    items.append(
                        {
                            "role": "user",
                            "content": (
                                f"[earlier result from {result.name}]\n{_truncate(result.summary)}"
                            ),
                        }
                    )
        return items

    async def run_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        ctx: ToolContext,
    ) -> AsyncIterator[AgentEvent]:
        """Run one turn against the thread as it currently stands.

        The researcher's message is expected to be already appended, so a turn
        can be retried without duplicating what they said.
        """
        history = await self._repo.list_messages(session_id, user_id=user_id, limit=200)
        state = _TurnState(input_items=self._history_items(history))
        if not state.input_items:
            yield Failed("There is nothing to answer yet.")
            return

        final_text = ""
        try:
            for _step in range(self._max_steps):
                turn = None
                async for event in self._llm.stream(
                    instructions=SYSTEM_PROMPT,
                    input_items=state.input_items,
                    tools=self._registry.schemas(),
                ):
                    if isinstance(event, TextDelta):
                        yield Delta(event.text)
                    elif isinstance(event, ToolCallStarted):
                        tool = self._registry.get(event.name)
                        yield ToolStarted(name=event.name, spend=tool.spend if tool else "free")
                    elif isinstance(event, TurnFinished):
                        turn = event.turn

                if turn is None:
                    yield Failed("The model returned nothing.")
                    return
                state.add_usage(turn.usage)

                if not turn.wants_tools:
                    final_text = turn.text
                    break

                # Echo the call items back, or the model has no record of
                # having asked, and will ask again on the next step.
                state.input_items.extend(turn.output_items)

                tool_calls: list[ToolCall] = []
                tool_results: list[ToolResult] = []

                for call in turn.calls:
                    tool = self._registry.get(call.name)
                    tool_calls.append(
                        ToolCall(call_id=call.call_id, name=call.name, arguments=call.arguments)
                    )
                    outcome = await self._registry.run(call.name, call.arguments, ctx)

                    if isinstance(outcome, Proposal):
                        state.proposals.append(outcome)
                        summary = (
                            f"Proposal prepared, NOT started: {outcome.title}. "
                            f"Estimated ${outcome.estimate_usd:.2f}. {outcome.detail} "
                            "Tell the researcher what it costs and wait for them to confirm."
                        )
                        yield ProposalRaised(outcome.as_payload())
                        tool_results.append(
                            ToolResult(
                                call_id=call.call_id,
                                name=call.name,
                                summary=summary,
                                payload=outcome.as_payload(),
                            )
                        )
                    else:
                        summary = _truncate(outcome.summary)
                        tool_results.append(
                            ToolResult(
                                call_id=call.call_id,
                                name=call.name,
                                summary=summary,
                                payload=outcome.payload or None,
                                ok=outcome.ok,
                                error=outcome.error,
                                cost_usd=outcome.cost_usd,
                            )
                        )
                        if outcome.papers:
                            state.papers.extend(p for p in outcome.papers if isinstance(p, Paper))
                        if outcome.artifact:
                            await self._store_artifact(
                                session_id=session_id, user_id=user_id, artifact=outcome.artifact
                            )
                    yield ToolFinished(
                        name=call.name,
                        summary=summary,
                        ok=not isinstance(outcome, ToolResultData) or outcome.ok,
                    )
                    state.input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": summary,
                        }
                    )

                await self._repo.append_message(
                    session_id=session_id,
                    user_id=user_id,
                    data=MessageCreate(
                        role=Role.tool, tool_calls=tool_calls, tool_results=tool_results
                    ),
                )
            else:
                # The loop ran out of steps rather than finishing.
                final_text = (
                    "I used all the steps available for one turn without reaching an "
                    "answer. Tell me which part to focus on and I will go again."
                )
        except LLMError as exc:
            log.warning("turn failed: %s", exc)
            yield Failed(str(exc))
            return

        verified = await verify(
            final_text,
            available=state.papers,
            resolve=self._resolve_paper,
        )

        blocks = [
            Block(kind=BlockKind.text, text=verified.text, flagged=verified.flagged_sentences)
        ]
        for proposal in state.proposals:
            blocks.append(
                Block(
                    kind=BlockKind.proposal,
                    text=proposal.title,
                    estimate_usd=proposal.estimate_usd,
                    data=proposal.as_payload(),
                )
            )

        message = await self._repo.append_message(
            session_id=session_id,
            user_id=user_id,
            data=MessageCreate(
                role=Role.assistant,
                blocks=blocks,
                citations=[
                    Citation(
                        source=p.source,
                        external_id=p.external_id,
                        title=p.title,
                        citation=p.citation(),
                        url=p.url,
                        doi=p.doi,
                        verified=True,
                        is_preprint=p.is_preprint,
                    )
                    for p in verified.papers
                ],
                usage=Usage(
                    input_tokens=state.usage_total["input"],
                    output_tokens=state.usage_total["output"],
                    cached_input_tokens=state.usage_total["cached"],
                    model=self._llm.model,
                ),
            ),
        )
        if message is None:
            yield Failed("The session could not be written to.")
            return

        yield Completed(
            message_id=message.id,
            text=verified.text,
            references=reference_list(verified.papers),
            warning=verified.warning,
            flagged=list(verified.flagged_sentences),
            usage=dict(state.usage_total),
            cost_usd=f"{state.cost:.4f}",
        )

    async def _resolve_paper(self, source: str, external_id: str) -> Paper | None:
        """The existence half of the citation check (M4).

        With no client configured this returns ``None``, which the verifier
        treats as unverified and strips. That is the right direction to fail: a
        dropped real citation is recoverable, an unchecked one is not.
        """
        if self._europepmc is None:
            return None
        resolved = await self._europepmc.resolve(source, external_id)
        return resolved if isinstance(resolved, Paper) else None

    async def _store_artifact(
        self, *, session_id: str, user_id: str, artifact: dict[str, Any]
    ) -> None:
        from biostruct.models.conversation import ArtifactCreate, ArtifactKind

        try:
            kind = ArtifactKind(artifact.get("kind", "evidence"))
        except ValueError:
            return
        await self._repo.add_artifact(
            session_id=session_id,
            user_id=user_id,
            data=ArtifactCreate(
                kind=kind,
                title=str(artifact.get("title") or ""),
                payload=artifact.get("payload") or {},
            ),
        )


#: Sentinel yielded by ``with_heartbeat`` when nothing has happened for a while.
HEARTBEAT = object()

#: An SSE comment. Clients ignore it; proxies and TCP do not, which is the
#: point — a turn can spend a minute inside one slow source, and a silent
#: connection is one a proxy is entitled to close.
HEARTBEAT_FRAME = ": keepalive\n\n"


async def with_heartbeat(
    source: AsyncIterator[AgentEvent], *, every: float = 12.0
) -> AsyncIterator[Any]:
    """Yield a heartbeat whenever the turn goes quiet for ``every`` seconds.

    Implemented by racing the generator against a timer rather than by a
    background task, so there is nothing to leak if the client disconnects
    mid-turn.
    """
    iterator = source.__aiter__()
    pending: asyncio.Task[Any] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(iterator.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=every)
            if not done:
                yield HEARTBEAT
                continue
            task, pending = pending, None
            try:
                yield task.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()


def event_to_sse(event: AgentEvent) -> str:
    """Serialise one event for the wire.

    Named events rather than one blob, so the client switches on a name instead
    of sniffing a shape.
    """
    if isinstance(event, Started):
        name, data = "started", {"message_id": event.message_id, "seq": event.seq}
    elif isinstance(event, Delta):
        name, data = "delta", {"text": event.text}
    elif isinstance(event, ToolStarted):
        name, data = "tool_started", {"name": event.name, "spend": event.spend}
    elif isinstance(event, ToolFinished):
        name, data = (
            "tool_finished",
            {
                "name": event.name,
                "summary": event.summary,
                "ok": event.ok,
            },
        )
    elif isinstance(event, ProposalRaised):
        name, data = "proposal", event.proposal
    elif isinstance(event, Completed):
        name, data = (
            "completed",
            {
                "message_id": event.message_id,
                "text": event.text,
                "references": event.references,
                "warning": event.warning,
                "flagged": event.flagged,
                "usage": event.usage,
                "cost_usd": event.cost_usd,
            },
        )
    else:
        name, data = "failed", {"message": event.message}
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


__all__ = [
    "HEARTBEAT",
    "HEARTBEAT_FRAME",
    "HISTORY_MESSAGES",
    "MAX_STEPS",
    "SYSTEM_PROMPT",
    "AgentEvent",
    "Completed",
    "Delta",
    "Failed",
    "Orchestrator",
    "ProposalRaised",
    "Started",
    "ToolFinished",
    "ToolStarted",
    "event_to_sse",
    "with_heartbeat",
]

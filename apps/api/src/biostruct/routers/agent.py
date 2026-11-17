"""The agent endpoints (M7).

A turn is streamed over SSE rather than returned, because a scientific turn runs
for tens of seconds — it searches UniProt, reads Europe PMC, assembles a
library — and a spinner for that long is indistinguishable from a hang. The
researcher sees the target being looked up, the literature being searched and
the answer being written, which is also the only honest way to show that an
answer came from tools rather than from the model's memory.

The researcher's own message is committed **before** the stream opens. If the
model then fails, what they typed is still in their thread and the turn can be
retried without them retyping it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from biostruct import errors
from biostruct.agent.orchestrator import (
    HEARTBEAT,
    HEARTBEAT_FRAME,
    Completed,
    Failed,
    Started,
    event_to_sse,
    with_heartbeat,
)
from biostruct.agent.runner import AgentRun, RunRejected
from biostruct.agent.tools.base import ToolContext
from biostruct.deps import (
    ChemblDep,
    ConversationsDep,
    CsrfDep,
    CurrentUser,
    EuropePMCDep,
    OrchestratorDep,
    RunnerDep,
    SettingsDep,
    SourcesDep,
)
from biostruct.models.conversation import (
    Block,
    MessageCreate,
    ResearchSession,
    Role,
    SessionCreate,
    SessionStatus,
    SessionUpdate,
)
from biostruct.voice import VoiceUnavailable

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["agent"])

#: A researcher's message. Long enough for a pasted protein sequence, which is
#: the whole point of step 2.
MESSAGE_MAX = 20_000


class SendMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=MESSAGE_MAX)
    #: Set when the message was dictated, so the thread records that the
    #: researcher spoke Yoruba even though the answer comes back in English.
    spoken_language: str = Field(default="", max_length=16)


class SessionOut(BaseModel):
    """One research session, as the interface needs it.

    No budget fields. The ledger still exists server-side and still guards a
    run, but a per-session spending allowance is not what this product is
    about, and putting a dollar counter in a researcher's eyeline made the tool
    look like a billing console instead of an instrument.
    """

    id: str
    title: str
    status: SessionStatus
    message_count: int
    created_at: str
    last_message_at: str | None

    @classmethod
    def of(cls, session: ResearchSession) -> SessionOut:
        return cls(
            id=session.id,
            title=session.display_title(),
            status=session.status,
            message_count=session.message_count,
            created_at=session.created_at.isoformat(),
            last_message_at=(
                session.last_message_at.isoformat() if session.last_message_at else None
            ),
        )


class RunOut(BaseModel):
    """A piece of scientific work, in whatever state it has reached."""

    id: str
    kind: str
    state: str
    title: str
    percent: int
    done: int
    total: int
    error: str
    artifact_id: str
    livemode: bool

    @classmethod
    def of(cls, run: AgentRun) -> RunOut:
        return cls(
            id=run.id,
            kind=run.kind.value,
            state=run.state.value,
            title=run.title,
            percent=run.percent,
            done=run.done,
            total=run.total,
            error=run.error,
            artifact_id=run.artifact_id,
            livemode=run.livemode,
        )


class StartRun(BaseModel):
    """Confirming a proposal the agent made."""

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class MessageOut(BaseModel):
    id: str
    seq: int
    role: Role
    text: str
    blocks: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    flagged: list[str]
    created_at: str


def _message_out(message: Any) -> MessageOut:
    return MessageOut(
        id=message.id,
        seq=message.seq,
        role=message.role,
        text=message.text,
        blocks=[
            {
                "kind": b.kind.value,
                "text": b.text,
                "estimate_usd": str(b.estimate_usd) if b.estimate_usd is not None else None,
                "flagged": list(b.flagged),
                "data": b.data,
            }
            for b in message.blocks
        ],
        tool_calls=[{"name": c.name, "arguments": c.arguments} for c in message.tool_calls]
        or [{"name": r.name, "arguments": {}} for r in message.tool_results],
        citations=[
            {
                "key": c.key,
                "title": c.title,
                "citation": c.citation,
                "url": c.url,
                "is_preprint": c.is_preprint,
            }
            for c in message.citations
        ],
        flagged=[s for b in message.blocks for s in b.flagged],
        created_at=message.created_at.isoformat(),
    )


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[CsrfDep])
async def create_session(
    data: SessionCreate,
    user: CurrentUser,
    conversations: ConversationsDep,
    settings: SettingsDep,
) -> SessionOut:
    session = await conversations.create_session(
        user_id=user.id,
        data=data,
        default_budget_usd=Decimal(settings.default_project_budget_usd),
    )
    return SessionOut.of(session)


@router.get("")
async def list_sessions(user: CurrentUser, conversations: ConversationsDep) -> list[SessionOut]:
    sessions = await conversations.list_sessions(user.id)
    return [SessionOut.of(s) for s in sessions]


@router.get("/{session_id}")
async def get_session(
    session_id: str, user: CurrentUser, conversations: ConversationsDep
) -> SessionOut:
    session = await conversations.get_session(session_id, user_id=user.id)
    if session is None:
        raise errors.not_found("session")
    return SessionOut.of(session)


@router.patch("/{session_id}", dependencies=[CsrfDep])
async def update_session(
    session_id: str,
    data: SessionUpdate,
    user: CurrentUser,
    conversations: ConversationsDep,
) -> SessionOut:
    session = await conversations.update_session(session_id, user_id=user.id, data=data)
    if session is None:
        raise errors.not_found("session")
    return SessionOut.of(session)


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: str,
    user: CurrentUser,
    conversations: ConversationsDep,
    after_seq: int = 0,
) -> list[MessageOut]:
    session = await conversations.get_session(session_id, user_id=user.id)
    if session is None:
        raise errors.not_found("session")
    messages = await conversations.list_messages(session_id, user_id=user.id, after_seq=after_seq)
    return [_message_out(m) for m in messages]


#: How long a derived session name may be before it is cut.
TITLE_CHARS = 60


def _title_from(question: str) -> str:
    """A session name taken from the question that started it.

    Deliberately the researcher's own words rather than a model-generated
    summary: it costs nothing, it is instantly recognisable to the person who
    typed it, and it cannot be wrong.
    """
    cleaned = " ".join(question.split())
    if len(cleaned) <= TITLE_CHARS:
        return cleaned
    cut = cleaned[:TITLE_CHARS].rsplit(" ", 1)[0]
    return f"{cut or cleaned[:TITLE_CHARS]}…"


class TranscriptOut(BaseModel):
    """A transcript, as a draft for the researcher to check.

    Deliberately not sent anywhere. Speech models mis-hear scientific nouns —
    we measured "Klebsiella" coming back as "cleps ella" — and a mis-heard
    organism must never be the thing that starts a paid run.
    """

    text: str
    english: str
    language: str
    language_name: str
    translated: bool


@router.post("/voice/transcribe", dependencies=[CsrfDep])
async def transcribe(
    user: CurrentUser,
    request: Request,
    audio: UploadFile = File(...),
    language: str = Form(""),
) -> TranscriptOut:
    """Turn a recording into text the researcher can edit and send.

    ``language`` is optional: Spitch detects it and reports which it heard, so
    nobody has to choose from a menu before they can speak.
    """
    voice = getattr(request.app.state, "voice", None)
    if voice is None or not voice.is_configured:
        raise errors.validation_problem(
            "Speech is not available on this server. You can still type your question."
        )
    body = await audio.read()
    try:
        transcript = await voice.transcribe(
            body, filename=audio.filename or "speech.webm", language=language
        )
    except VoiceUnavailable as exc:
        raise errors.validation_problem(str(exc)) from exc
    return TranscriptOut(
        text=transcript.text,
        english=transcript.for_agent,
        language=transcript.language,
        language_name=transcript.language_name,
        translated=transcript.translated,
    )


@router.get("/{session_id}/artifacts/{artifact_id}/structure.cif")
async def get_artifact_structure(
    session_id: str,
    artifact_id: str,
    user: CurrentUser,
    conversations: ConversationsDep,
    request: Request,
    sample: int = 0,
) -> Response:
    """Serve a predicted structure from our own storage.

    Never Boltz's URL: theirs is presigned and expires, so handing it to a
    browser produces a viewer that works for half an hour and then does not.
    """
    artifacts = await conversations.list_artifacts(session_id, user_id=user.id)
    found = next((a for a in artifacts if a.id == artifact_id), None)
    if found is None:
        raise errors.not_found("Structure")
    samples = found.payload.get("samples") or []
    key = found.storage_key
    if 0 <= sample < len(samples):
        key = str(samples[sample].get("structure_key") or "") or key
    if not key:
        raise errors.not_found("Structure file")
    try:
        data = await request.app.state.storage.get_bytes(key)
    except Exception as exc:
        raise errors.ProblemError(
            code="structure_unavailable",
            title="Structure could not be read",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return Response(
        content=data,
        media_type="chemical/x-cif",
        headers={
            "content-disposition": f'inline; filename="{artifact_id}.cif"',
            "cache-control": "private, max-age=3600",
        },
    )


@router.post("/{session_id}/runs", status_code=status.HTTP_202_ACCEPTED, dependencies=[CsrfDep])
async def start_run(
    session_id: str,
    data: StartRun,
    user: CurrentUser,
    runner: RunnerDep,
) -> RunOut:
    """Confirm a proposal and start the work.

    Returns as soon as the run is recorded — a fold takes minutes, and holding
    the request open for them would time out in every proxy between here and
    the researcher. Progress is read from ``GET /runs``.
    """
    try:
        run = await runner.start(
            session_id=session_id,
            user_id=user.id,
            tool=data.tool,
            arguments=data.arguments,
        )
    except RunRejected as exc:
        raise errors.validation_problem(str(exc)) from exc
    return RunOut.of(run)


@router.get("/{session_id}/runs")
async def list_runs(session_id: str, user: CurrentUser, runner: RunnerDep) -> list[RunOut]:
    return [RunOut.of(r) for r in await runner.list_runs(session_id, user_id=user.id)]


@router.get("/{session_id}/artifacts")
async def list_artifacts(
    session_id: str, user: CurrentUser, conversations: ConversationsDep
) -> list[dict[str, Any]]:
    session = await conversations.get_session(session_id, user_id=user.id)
    if session is None:
        raise errors.not_found("session")
    artifacts = await conversations.list_artifacts(session_id, user_id=user.id)
    return [
        {
            "id": a.id,
            "kind": a.kind.value,
            "title": a.title,
            "payload": a.payload,
            "created_at": a.created_at.isoformat(),
        }
        for a in artifacts
    ]


@router.post("/{session_id}/messages", dependencies=[CsrfDep])
async def send_message(
    session_id: str,
    data: SendMessage,
    user: CurrentUser,
    conversations: ConversationsDep,
    orchestrator: OrchestratorDep,
    sources: SourcesDep,
    chembl: ChemblDep,
    europepmc: EuropePMCDep,
) -> StreamingResponse:
    """Append the researcher's message, then stream the agent's turn."""
    session = await conversations.get_session(session_id, user_id=user.id)
    if session is None:
        raise errors.not_found("session")

    # Committed before the stream opens: if the model then fails, what they
    # typed is still in their thread and the turn can be retried.
    user_message = await conversations.append_message(
        session_id=session_id,
        user_id=user.id,
        data=MessageCreate(
            role=Role.user,
            blocks=[Block(text=data.content)],
            spoken_language=data.spoken_language,
        ),
    )
    if user_message is None:
        raise errors.not_found("session")

    # Name the session after the question that started it. A rail full of
    # "Untitled session" is a rail nobody can navigate, and the researcher
    # should not have to title their work before they are allowed to do it.
    if not session.title.strip():
        await conversations.update_session(
            session_id, user_id=user.id, data=SessionUpdate(title=_title_from(data.content))
        )

    ctx = ToolContext(
        user_id=user.id,
        session_id=session_id,
        sources=sources,
        chembl=chembl,
        europepmc=europepmc,
        budget_remaining_usd=session.budget.remaining_usd,
    )

    async def body() -> AsyncIterator[str]:
        yield event_to_sse(Started(message_id=user_message.id, seq=user_message.seq))
        completed = False
        try:
            async for event in with_heartbeat(
                orchestrator.run_turn(session_id=session_id, user_id=user.id, ctx=ctx)
            ):
                if event is HEARTBEAT:
                    yield HEARTBEAT_FRAME
                    continue
                if isinstance(event, (Completed, Failed)):
                    completed = True
                yield event_to_sse(event)
        except Exception as exc:  # the stream must close with a reason, not hang
            log.exception("agent turn crashed")
            yield event_to_sse(Failed(f"The turn failed: {exc}"))
            completed = True
        if not completed:
            # The generator stopped without a terminal event. Measured once:
            # a sick upstream was retried with backoff until the stream fell
            # over, and the client was left with a draft and no answer. A
            # client that sees the stream end with no terminal event cannot
            # tell a finished turn from a dropped one, so always send one.
            yield event_to_sse(
                Failed("The turn ended without finishing. Reload the thread to see what was saved.")
            )

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this, a proxy buffers the whole stream and the researcher
            # sees nothing until the turn is over, which defeats the point.
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

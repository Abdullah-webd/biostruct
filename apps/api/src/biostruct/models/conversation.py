"""The conversation data model — what the agent writes into (M6).

Three collections arrive here, and one naming decision has to be stated up
front because it is a trap: **this project already has a ``sessions``
collection**, holding authentication sessions keyed by the SHA-256 of a cookie
token, with a TTL index that deletes them. The thing modelled in this file is a
*research* session, a conversation that must live for months. Two unrelated
concepts with one name, one of which expires itself, is how someone eventually
writes a query that logs every researcher out or quietly deletes their work.

So the collection here is ``research_sessions`` and the type is
``ResearchSession``, and nothing is renamed on the auth side.

The second decision that shapes everything below: **a research session keeps the
``_id`` of the v1 project it came from.** Targets, design runs and candidates
all reference ``project_id``, and there are working, tested indexes on those
fields. Migrating by minting new ids would mean rewriting every one of those
references — a migration that can half-succeed and orphan a researcher's
results. Keeping the id makes the migration a document copy with nothing to
re-point, and ``project_id`` stays valid as the same value under a new name.

Ordering is by an integer ``seq`` per session, not by timestamp. Two messages
written in the same millisecond — an assistant turn and the tool result it
triggered — would otherwise have no defined order, and a conversation rendered
out of order is worse than one that is late.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from biostruct.models.project import Budget, Infection, RankingWeights

#: Schema version for documents written by v2. v1 projects are version 1, and
#: the migration marks what it produced so a half-finished run is detectable.
SCHEMA_VERSION = 2

#: Mongo's hard document limit is 16 MB. A tool result can be a 2000-molecule
#: library or a structure file, so a payload above this is written to object
#: storage and the message keeps a reference. The margin below 16 MB is for the
#: rest of the document and for BSON overhead.
PAYLOAD_INLINE_MAX_BYTES = 256 * 1024

#: A turn cannot grow without bound: the context strategy injects summaries, and
#: an unbounded tool-call fan-out is how an agent loop runs away.
TOOL_CALLS_MAX = 16


class SessionStatus(StrEnum):
    active = "active"
    #: The researcher is done with it. Kept, not deleted: the results are theirs.
    archived = "archived"


class Role(StrEnum):
    user = "user"
    assistant = "assistant"
    #: A tool result. Its own role rather than an assistant block, because the
    #: provenance of every fact the agent states has to be reconstructable, and
    #: "the model said it" must never be confusable with "a tool returned it".
    tool = "tool"
    system = "system"


class BlockKind(StrEnum):
    text = "text"
    #: The agent asking to spend money. Rendered as a confirmation card; M8 makes
    #: it the only route to a paid call.
    proposal = "proposal"
    #: Something went wrong, shown in the thread rather than swallowed.
    error = "error"


class ArtifactKind(StrEnum):
    structure = "structure"
    molecule_set = "molecule_set"
    ranking = "ranking"
    library = "library"
    #: A cited literature answer, so a research turn is reviewable later.
    evidence = "evidence"


class Block(BaseModel):
    """One piece of a message's content."""

    model_config = ConfigDict(str_strip_whitespace=True)

    kind: BlockKind = BlockKind.text
    text: str = ""
    #: Set on a ``proposal`` block: what this would cost if confirmed.
    estimate_usd: Decimal | None = None
    #: Sentences whose citation failed verification (M4). Surfaced, never hidden.
    flagged: tuple[str, ...] = ()
    #: On a ``proposal`` block: the tool and arguments to run if confirmed.
    #: Stored rather than held in memory for the turn, because a proposal has
    #: to still be there when the researcher comes back to it tomorrow.
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("estimate_usd", mode="before")
    @classmethod
    def _money_is_never_a_float(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, float):
            raise ValueError("money must be a string or Decimal, never a float")
        return Decimal(str(v))


class ToolCall(BaseModel):
    """A tool the agent asked for, as it asked for it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """What a tool returned.

    Two fields, deliberately, and the split is the whole context strategy:
    ``summary`` is the condensed form injected back into the model's context,
    and ``payload`` is the result in full, kept so the agent can re-read detail
    just-in-time and so a researcher can audit what the model was actually
    shown.

    ``payload_ref`` replaces ``payload`` when the result is too large to inline.
    """

    call_id: str
    name: str
    summary: str = ""
    payload: dict[str, Any] | None = None
    payload_ref: str = ""
    ok: bool = True
    error: str = ""
    #: Set when the tool spent money, so the thread itself is an audit trail.
    cost_usd: Decimal | None = None

    @field_validator("cost_usd", mode="before")
    @classmethod
    def _money_is_never_a_float(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, float):
            raise ValueError("money must be a string or Decimal, never a float")
        return Decimal(str(v))

    @property
    def is_spilled(self) -> bool:
        """True when the full result lives in object storage, not in the document."""
        return bool(self.payload_ref)


class Usage(BaseModel):
    """Token counts, so cost per turn is a fact rather than an estimate."""

    input_tokens: int = 0
    output_tokens: int = 0
    #: Cached input is billed differently by every provider; kept separate so a
    #: later price change does not require re-deriving it.
    cached_input_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_input_tokens


class Citation(BaseModel):
    """A reference that survived verification (M4).

    ``verified`` is stored rather than implied. A citation that was checked and
    one that merely looks checkable are different things, and only the first may
    be shown as a reference.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str
    external_id: str
    title: str = ""
    citation: str = ""
    url: str = ""
    doi: str = ""
    verified: bool = False
    is_preprint: bool = False
    resolved_at: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.external_id}"


class MessageCreate(BaseModel):
    """What a caller hands the repository. ``seq`` and ids are assigned there."""

    role: Role
    blocks: list[Block] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=TOOL_CALLS_MAX)
    tool_results: list[ToolResult] = Field(default_factory=list, max_length=TOOL_CALLS_MAX)
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage | None = None
    #: A short, speakable version for read-aloud (ADR-012). Reading SMILES and
    #: accession codes out loud is noise.
    spoken: str = ""
    #: The language the researcher spoke, when the message came in by voice.
    spoken_language: str = ""


class Message(BaseModel):
    id: str
    session_id: str
    user_id: str
    seq: int
    role: Role
    blocks: list[Block] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    usage: Usage | None = None
    spoken: str = ""
    spoken_language: str = ""
    created_at: datetime
    schema_version: int = SCHEMA_VERSION

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.kind == BlockKind.text and b.text)

    @property
    def has_unverified_claims(self) -> bool:
        return any(b.flagged for b in self.blocks)


class ArtifactCreate(BaseModel):
    kind: ArtifactKind
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    message_id: str = ""
    #: Where a structure file lives, when the artifact is one. Boltz's presigned
    #: URLs expire in seven days, so anything we intend to keep is downloaded
    #: and this points at our copy.
    storage_key: str = ""


class Artifact(BaseModel):
    """What the canvas renders beside the thread."""

    id: str
    session_id: str
    user_id: str
    #: Position in the session, allocated atomically. One turn routinely
    #: produces several artifacts within the same millisecond, so a timestamp
    #: does not order them and the canvas would reshuffle on every read.
    seq: int = 0
    kind: ArtifactKind
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    message_id: str = ""
    storage_key: str = ""
    created_at: datetime
    schema_version: int = SCHEMA_VERSION


class SessionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(default="", max_length=200)
    infection: Infection | None = None
    budget_usd: Decimal | None = None

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _money_is_never_a_float(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, float):
            raise ValueError(
                "budget_usd must be sent as a string, not a number, so the amount "
                "cannot drift through binary floating point"
            )
        return Decimal(str(v))


class SessionUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, max_length=200)
    infection: Infection | None = None
    status: SessionStatus | None = None
    ranking_weights: RankingWeights | None = None


class ResearchSession(BaseModel):
    """One research conversation. The thread *is* the state (ARCHITECTURE §7).

    ``migrated_from`` records a v1 project this was converted from. It is not
    decoration: it is how a partially-completed migration is found and finished
    rather than guessed at.
    """

    id: str
    user_id: str
    title: str
    infection: Infection | None = None
    status: SessionStatus = SessionStatus.active
    ranking_weights: RankingWeights = RankingWeights()
    budget: Budget
    message_count: int = 0
    #: The high-water mark for ``seq``. Incremented atomically; see the
    #: repository. Stored on the session so appending a message is one round trip.
    last_seq: int = 0
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    schema_version: int = SCHEMA_VERSION
    migrated_from: Literal["", "project"] = ""

    @property
    def is_migrated(self) -> bool:
        return self.migrated_from == "project"

    def display_title(self) -> str:
        """Never blank. An untitled row in a sidebar is unusable."""
        if self.title.strip():
            return self.title.strip()
        if self.infection is not None and self.infection.name.strip():
            return self.infection.name.strip()
        return "Untitled session"


__all__ = [
    "PAYLOAD_INLINE_MAX_BYTES",
    "SCHEMA_VERSION",
    "TOOL_CALLS_MAX",
    "Artifact",
    "ArtifactCreate",
    "ArtifactKind",
    "Block",
    "BlockKind",
    "Citation",
    "Message",
    "MessageCreate",
    "ResearchSession",
    "Role",
    "SessionCreate",
    "SessionStatus",
    "SessionUpdate",
    "ToolCall",
    "ToolResult",
    "Usage",
]

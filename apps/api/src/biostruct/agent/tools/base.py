"""The tool contract (M7).

Every capability the agent has is one of these. The design follows from two
constraints that pull in opposite directions:

* The model must be given enough to choose correctly. A tool whose description
  is a label gets called at random.
* The model must never be the thing standing between a researcher and a bill.

So a tool declares whether it **spends money**, and a paid tool does not execute
when the agent asks for it: it returns a *proposal*, which a human confirms.
That is enforced here, in the dispatcher, rather than by asking the model
nicely — a prompt instruction is a request, and this one has a dollar value
attached.

Each result carries two things, which is the whole context strategy: a
``summary`` that goes back into the model's context, and a ``payload`` kept
whole in MongoDB for the researcher and for just-in-time re-reads. The summary
is what the model reasons from, so it is written for a reader, not as a dump.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

log = logging.getLogger(__name__)

#: How a tool is named to the model. Underscores, because the API's ``strict``
#: schema validation rejects dots in a function name.
ToolName = str

Spend = Literal["free", "paid"]

#: How long one tool may take before the turn gives up on it. Generous enough
#: for a slow-but-working source (UniProt and Europe PMC both answer in a few
#: seconds), short enough that a researcher is not left watching a stream while
#: a broken upstream is retried with backoff.
TOOL_TIMEOUT_SECONDS = 45.0


class ToolError(Exception):
    """A failure the *agent* should see and can act on.

    Deliberately not an HTTP error: "UniProt is unreachable" is something the
    agent should tell the researcher and work around, not something that should
    500 a turn they are waiting on. The message is written to be read by the
    model and then by a human, so it says what failed and what to try.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ToolResultData:
    """What a tool hands back."""

    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: Papers a literature tool returned. The citation verifier (M4) will only
    #: allow the model to cite something that appears here.
    papers: tuple[Any, ...] = ()
    #: An artifact to show on the canvas beside the thread.
    artifact: dict[str, Any] | None = None
    #: Set when the tool actually spent money.
    cost_usd: Decimal | None = None
    ok: bool = True
    error: str = ""


@dataclass(frozen=True, slots=True)
class Proposal:
    """A paid action, described and priced, awaiting a human.

    Returned *instead of* doing the work. The model cannot bypass this by
    phrasing the request differently, because the dispatcher decides, not the
    prompt.
    """

    tool: ToolName
    arguments: dict[str, Any]
    title: str
    detail: str
    estimate_usd: Decimal
    #: What the researcher gets if they say yes, in one line.
    yields: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "title": self.title,
            "detail": self.detail,
            "estimate_usd": str(self.estimate_usd),
            "yields": self.yields,
        }


@dataclass
class ToolContext:
    """Everything a tool is allowed to touch.

    Passed in rather than imported, so a tool is testable without a database and
    cannot quietly reach for a connection the caller did not grant it.
    """

    user_id: str
    session_id: str
    sources: Any = None
    chembl: Any = None
    europepmc: Any = None
    engine: Any = None
    budget_remaining_usd: Decimal = Decimal("0")
    #: Accumulates every paper any tool returned this turn. The citation
    #: verifier's provenance check reads this, and nothing else.
    papers_seen: list[Any] = field(default_factory=list)


class ToolHandler(Protocol):
    async def __call__(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResultData | Proposal: ...


@dataclass(frozen=True, slots=True)
class Tool:
    """One capability, as both a model-facing schema and a callable."""

    name: ToolName
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    handler: ToolHandler
    spend: Spend = "free"

    def schema(self) -> dict[str, Any]:
        """The flat declaration the Responses API expects.

        ``strict: true`` requires ``additionalProperties: false`` *and* every
        property listed in ``required``. The API rejects a schema that omits
        either, so this is built here rather than hand-written per tool — a
        detail that cost a probe to find and would otherwise be rediscovered
        once per tool.
        """
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.properties),
                "additionalProperties": False,
            },
            "strict": True,
        }

    @property
    def optional(self) -> tuple[str, ...]:
        """Properties that are nominally optional.

        ``strict`` mode forces every property into ``required``, so "optional"
        means the model is told to pass an empty value rather than omit the key.
        The tool has to treat an empty string as absent, and this names which
        those are so a test can check the descriptions say so.
        """
        return tuple(p for p in self.properties if p not in self.required)


def numeric(
    description: str, *, minimum: int | None = None, maximum: int | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer", "description": description}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


def text(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


class Registry:
    """The set of tools a turn may use, and the only way to run one."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"two tools named {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    async def run(
        self, name: str, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResultData | Proposal:
        """Execute a tool, or refuse in a way the model can act on.

        An unknown name is a model mistake and comes back as a tool *result*
        rather than an exception, so the agent can correct itself instead of the
        researcher seeing a crash.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResultData(
                summary=(
                    f"There is no tool called {name!r}. Available tools: {', '.join(self.names)}."
                ),
                ok=False,
                error="unknown tool",
            )
        try:
            result = await asyncio.wait_for(tool.handler(args, ctx), timeout=TOOL_TIMEOUT_SECONDS)
        except TimeoutError:
            # One sick upstream must not consume a whole turn. Measured the case
            # this exists for: ChEMBL's target search started answering 500 with
            # a 30-second hang, and three retries with backoff ran well past any
            # reasonable time for a researcher to sit watching a stream.
            log.warning("tool %s timed out after %ss", name, TOOL_TIMEOUT_SECONDS)
            return ToolResultData(
                summary=(
                    f"The {name} tool took longer than {TOOL_TIMEOUT_SECONDS} seconds and was "
                    "given up on, so that source is probably having trouble. Tell the "
                    "researcher that this particular lookup failed, and carry on with what "
                    "can be done without it."
                ),
                ok=False,
                error="timed out",
            )
        except ToolError as exc:
            return ToolResultData(summary=str(exc), ok=False, error=str(exc))
        except Exception as exc:  # a tool bug must not take the turn down
            log.exception("tool %s failed", name)
            return ToolResultData(
                summary=(
                    f"The {name} tool failed unexpectedly: {exc}. "
                    "Tell the researcher plainly rather than guessing the answer."
                ),
                ok=False,
                error=str(exc),
            )
        if isinstance(result, ToolResultData) and result.papers:
            ctx.papers_seen.extend(result.papers)
        return result


ToolFactory = Callable[[], Awaitable[Registry]]

__all__ = [
    "TOOL_TIMEOUT_SECONDS",
    "Proposal",
    "Registry",
    "Spend",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolHandler",
    "ToolName",
    "ToolResultData",
    "numeric",
    "text",
]

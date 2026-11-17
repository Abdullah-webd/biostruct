"""The OpenAI Responses client (M7).

Raw ``httpx`` rather than the ``openai`` SDK, for the same reason the Boltz SDK
is pinned to an exact version: this layer is a wire protocol we have verified by
hand, and a generated client that renames a field in a minor release would break
the streaming path at runtime. Every field and event name here was confirmed
against the live API on 2026-11-17 — see ``docs/openai-api-observed.md``.

Three facts shape the code:

* **Tools are declared flat.** ``{"type": "function", "name": ..., "parameters":
  ...}``, not the Chat Completions ``{"type": "function", "function": {...}}``.
* **``arguments`` is a JSON string** and arrives in fragments. A parallel call
  interleaves two items' deltas in one stream, so fragments are accumulated per
  ``item_id``, never into one buffer.
* **There is no cost field.** Dollars are our arithmetic from the token counts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.openai.com/v1"
RESPONSES_PATH = "/responses"

#: Default model. "Near-Astra performance for complex work at a lower cost".
#: The orchestrator picks which scientific tool to call and a wrong call spends
#: real money on the wrong protein, so this is not a place for the cheapest
#: model. Overridable by configuration.
DEFAULT_MODEL = "gpt-6.1-sol"

Effort = Literal["low", "medium", "high", "xhigh", "max"]

#: Measured from the published price table on 2026-11-17, in dollars per token.
#: Cached input is billed at roughly a twentieth, and reasoning tokens are
#: billed as output, so a turn's cost cannot be derived from ``total_tokens``.
PRICES_PER_MTOK: dict[str, tuple[str, str, str]] = {
    # model: (input, output, cached input)
    "gpt-6-astra": ("10.00", "50.00", "1.00"),
    "gpt-6.1-sol": ("2.00", "10.00", "0.10"),
    "gpt-6.1-terra": ("1.00", "5.00", "0.05"),
    "gpt-6-luna": ("0.10", "0.50", "0.01"),
}

_MILLION = Decimal("1000000")


class LLMError(RuntimeError):
    """The model could not be reached, or refused the request.

    Separate from a tool failing: a tool failure is something the agent can
    reason about and report, while this ends the turn.
    """


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def cost_usd(self) -> Decimal:
        """What this turn cost, to the cent we can actually justify.

        Returns zero for an unpriced model rather than guessing: a made-up
        number in a spend ledger is worse than a visible gap.
        """
        prices = PRICES_PER_MTOK.get(self.model)
        if prices is None:
            log.warning("no price known for model %r; reporting $0", self.model)
            return Decimal("0")
        price_in, price_out, price_cached = (Decimal(p) for p in prices)
        uncached = max(0, self.input_tokens - self.cached_input_tokens)
        return (
            uncached * price_in
            + self.cached_input_tokens * price_cached
            # Reasoning tokens are already counted inside output_tokens.
            + self.output_tokens * price_out
        ) / _MILLION


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """A tool the model asked for."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    #: The item id, needed to echo the call back on the next request.
    item_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class Turn:
    """One completed model turn."""

    text: str = ""
    calls: tuple[FunctionCall, ...] = ()
    usage: TokenUsage = TokenUsage()
    #: The output items exactly as returned, to be echoed back next request.
    #: The model has no record of having asked unless the call item goes back.
    output_items: tuple[dict[str, Any], ...] = ()
    incomplete_reason: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.calls)


# --- streamed events, as the orchestrator sees them ------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    call_id: str
    name: str


@dataclass(frozen=True, slots=True)
class TurnFinished:
    turn: Turn


StreamEvent = TextDelta | ToolCallStarted | TurnFinished


def _parse_arguments(raw: str, *, name: str) -> dict[str, Any]:
    """``arguments`` is a JSON string. A malformed one is the model's bug.

    Returning an empty mapping rather than raising lets the orchestrator hand
    the tool's own validation error back to the model, which it can correct —
    raising would end a turn the researcher is waiting on.
    """
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.warning("model sent malformed arguments for %s: %r", name, raw[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_from(payload: dict[str, Any] | None, *, model: str) -> TokenUsage:
    if not payload:
        return TokenUsage(model=model)
    input_details = payload.get("input_tokens_details") or {}
    output_details = payload.get("output_tokens_details") or {}
    return TokenUsage(
        input_tokens=int(payload.get("input_tokens") or 0),
        output_tokens=int(payload.get("output_tokens") or 0),
        cached_input_tokens=int(input_details.get("cached_tokens") or 0),
        reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
        model=model,
    )


def _turn_from_response(payload: dict[str, Any]) -> Turn:
    model = str(payload.get("model") or "")
    text_parts: list[str] = []
    calls: list[FunctionCall] = []
    items = [i for i in payload.get("output") or [] if isinstance(i, dict)]
    for item in items:
        kind = item.get("type")
        if kind == "function_call":
            calls.append(
                FunctionCall(
                    call_id=str(item.get("call_id") or ""),
                    name=str(item.get("name") or ""),
                    arguments=_parse_arguments(
                        str(item.get("arguments") or ""), name=str(item.get("name") or "")
                    ),
                    item_id=str(item.get("id") or ""),
                    raw=item,
                )
            )
        elif kind == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(str(part.get("text") or ""))
    incomplete = payload.get("incomplete_details") or {}
    return Turn(
        text="".join(text_parts),
        calls=tuple(calls),
        usage=_usage_from(payload.get("usage"), model=model),
        output_items=tuple(items),
        incomplete_reason=str(incomplete.get("reason") or ""),
    )


class OpenAIClient:
    """Thin, typed access to ``POST /v1/responses``."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        effort: Effort = "medium",
        client: httpx.AsyncClient | None = None,
        base_url: str = BASE_URL,
        timeout: float = 180.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.effort = effort
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        """False when no key is set, so the app can start and say so."""
        return bool(self._api_key)

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _body(
        self,
        *,
        instructions: str,
        input_items: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        max_output_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": list(input_items),
            # Nested, not a top-level reasoning_effort. "medium" is the default
            # the API applies when this is omitted.
            "reasoning": {"effort": self.effort},
            # The thread is our state and lives in MongoDB, so OpenAI does not
            # need to retain it. This also means no researcher's unpublished
            # work sits in someone else's conversation store.
            "store": False,
        }
        if tools:
            body["tools"] = list(tools)
            body["tool_choice"] = "auto"
        if max_output_tokens:
            body["max_output_tokens"] = max_output_tokens
        if stream:
            body["stream"] = True
        return body

    async def complete(
        self,
        *,
        instructions: str,
        input_items: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
        max_output_tokens: int | None = None,
    ) -> Turn:
        if not self.is_configured:
            raise LLMError("no OpenAI API key is configured")
        http = await self._http()
        body = self._body(
            instructions=instructions,
            input_items=input_items,
            tools=tools,
            max_output_tokens=max_output_tokens,
            stream=False,
        )
        try:
            response = await http.post(
                f"{self._base_url}{RESPONSES_PATH}", headers=self._headers(), json=body
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"could not reach the model: {exc}") from exc
        if response.status_code >= 400:
            raise LLMError(self._error_detail(response))
        payload = response.json()
        if not isinstance(payload, dict):
            raise LLMError("unexpected response shape")
        return _turn_from_response(payload)

    async def stream(
        self,
        *,
        instructions: str,
        input_items: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] = (),
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield text as it is written, and a finished ``Turn`` at the end.

        Tool-call arguments are accumulated **per ``item_id``**: a parallel call
        interleaves two items' fragments in one stream, and concatenating them
        into one buffer produces two corrupt payloads rather than one error.
        """
        if not self.is_configured:
            raise LLMError("no OpenAI API key is configured")
        http = await self._http()
        body = self._body(
            instructions=instructions,
            input_items=input_items,
            tools=tools,
            max_output_tokens=max_output_tokens,
            stream=True,
        )

        text_parts: list[str] = []
        # item_id -> accumulated argument fragments
        pending_args: dict[str, list[str]] = {}
        items_by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        usage = TokenUsage(model=self.model)
        incomplete_reason = ""
        final_items: list[dict[str, Any]] = []

        try:
            async with http.stream(
                "POST",
                f"{self._base_url}{RESPONSES_PATH}",
                headers=self._headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise LLMError(self._error_detail(response))
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        event = json.loads(chunk)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type", "")

                    if kind == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            text_parts.append(delta)
                            yield TextDelta(delta)

                    elif kind == "response.output_item.added":
                        item = event.get("item") or {}
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            item_id = str(item.get("id") or "")
                            items_by_id[item_id] = dict(item)
                            pending_args.setdefault(item_id, [])
                            if item_id not in order:
                                order.append(item_id)
                            # Announced before the arguments finish, so the
                            # researcher sees "looking up the target" straight
                            # away rather than after a silent pause.
                            yield ToolCallStarted(
                                call_id=str(item.get("call_id") or ""),
                                name=str(item.get("name") or ""),
                            )

                    elif kind == "response.function_call_arguments.delta":
                        item_id = str(event.get("item_id") or "")
                        pending_args.setdefault(item_id, []).append(str(event.get("delta") or ""))

                    elif kind == "response.function_call_arguments.done":
                        item_id = str(event.get("item_id") or "")
                        complete = event.get("arguments")
                        if isinstance(complete, str):
                            pending_args[item_id] = [complete]

                    elif kind == "response.output_item.done":
                        item = event.get("item") or {}
                        if isinstance(item, dict):
                            item_id = str(item.get("id") or "")
                            if item.get("type") == "function_call":
                                items_by_id[item_id] = dict(item)
                                if item_id not in order:
                                    order.append(item_id)
                            final_items.append(dict(item))

                    elif kind in ("response.completed", "response.incomplete"):
                        payload = event.get("response") or {}
                        if isinstance(payload, dict):
                            usage = _usage_from(
                                payload.get("usage"),
                                model=str(payload.get("model") or self.model),
                            )
                            details = payload.get("incomplete_details") or {}
                            incomplete_reason = str(details.get("reason") or "")

                    elif kind == "error":
                        raise LLMError(str(event.get("message") or "the model stream failed"))
        except httpx.HTTPError as exc:
            raise LLMError(f"the model stream failed: {exc}") from exc

        calls: list[FunctionCall] = []
        for item_id in order:
            item = items_by_id.get(item_id, {})
            name = str(item.get("name") or "")
            raw_args = "".join(pending_args.get(item_id) or [])
            # The streamed item carries no arguments of its own; the accumulated
            # fragments are the only copy, so they are written back onto the
            # item that gets echoed to the model next request.
            item = {**item, "arguments": raw_args}
            items_by_id[item_id] = item
            calls.append(
                FunctionCall(
                    call_id=str(item.get("call_id") or ""),
                    name=name,
                    arguments=_parse_arguments(raw_args, name=name),
                    item_id=item_id,
                    raw=item,
                )
            )

        output_items = [items_by_id[i] for i in order] or final_items
        yield TurnFinished(
            Turn(
                text="".join(text_parts),
                calls=tuple(calls),
                usage=usage,
                output_items=tuple(output_items),
                incomplete_reason=incomplete_reason,
            )
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """A message worth showing, without leaking the key or a wall of JSON."""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and error.get("message"):
                    return f"the model refused the request: {error['message']}"
        except ValueError:
            pass
        return f"the model returned HTTP {response.status_code}"


__all__ = [
    "DEFAULT_MODEL",
    "PRICES_PER_MTOK",
    "Effort",
    "FunctionCall",
    "LLMError",
    "OpenAIClient",
    "StreamEvent",
    "TextDelta",
    "TokenUsage",
    "ToolCallStarted",
    "Turn",
    "TurnFinished",
]

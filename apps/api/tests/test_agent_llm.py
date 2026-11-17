"""The OpenAI Responses client (M7).

Fixtures are shapes captured from the live API on 2026-11-17, so a field rename
fails the build rather than breaking a streamed turn in front of a researcher.
No network.

The streaming tests matter most. A non-streaming response is one JSON document;
a stream is nine event types and the tool arguments arrive in fragments, which
is where the real bugs live.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from biostruct.agent.llm import (
    PRICES_PER_MTOK,
    LLMError,
    OpenAIClient,
    TextDelta,
    TokenUsage,
    ToolCallStarted,
    Turn,
    TurnFinished,
    _turn_from_response,
)

MODEL = "gpt-6.1-sol"


def client_for(handler: Any) -> OpenAIClient:
    return OpenAIClient(
        api_key="sk-test",
        model=MODEL,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def sse(*events: dict[str, Any]) -> str:
    """Build a stream the way the API sends one."""
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def stream_handler(body: str) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return handler


async def turn_of(body: str) -> Turn:
    """Drain a scripted stream and return the finished turn."""
    events = [
        e
        async for e in client_for(stream_handler(body)).stream(
            instructions="i", input_items=[{"role": "user", "content": "x"}]
        )
    ]
    return next(e for e in events if isinstance(e, TurnFinished)).turn


#: A tool-call response, as captured live.
TOOL_CALL_RESPONSE = {
    "model": MODEL,
    "status": "completed",
    "output": [
        {
            "type": "function_call",
            "id": "fc_0532ebb",
            "call_id": "call_qJdEZntGw2BSRXmh5kghil5U",
            "name": "target_find",
            "arguments": '{"query":"carbapenem resistance in Klebsiella"}',
            "status": "completed",
        }
    ],
    "usage": {
        "input_tokens": 74,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 32,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 106,
    },
}


class TestRequestShape:
    @pytest.mark.anyio
    async def test_tools_are_declared_flat_not_nested(self) -> None:
        """The Chat Completions form ``{"type":"function","function":{...}}`` is
        rejected by this endpoint. Verified against the live API."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=TOOL_CALL_RESPONSE)

        await client_for(handler).complete(
            instructions="be useful",
            input_items=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "name": "t", "parameters": {}}],
        )
        assert seen["tools"][0]["name"] == "t"
        assert "function" not in seen["tools"][0]

    @pytest.mark.anyio
    async def test_reasoning_effort_is_nested(self) -> None:
        """``reasoning: {effort}``, not a top-level ``reasoning_effort``."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=TOOL_CALL_RESPONSE)

        client = OpenAIClient(
            api_key="k",
            model=MODEL,
            effort="high",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        await client.complete(instructions="i", input_items=[{"role": "user", "content": "x"}])
        assert seen["reasoning"] == {"effort": "high"}
        assert "reasoning_effort" not in seen

    @pytest.mark.anyio
    async def test_the_thread_is_not_stored_on_their_servers(self) -> None:
        """The thread is our state and lives in MongoDB, so no researcher's
        unpublished work needs to sit in someone else's conversation store."""
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=TOOL_CALL_RESPONSE)

        await client_for(handler).complete(
            instructions="i", input_items=[{"role": "user", "content": "x"}]
        )
        assert seen["store"] is False

    @pytest.mark.anyio
    async def test_no_key_is_refused_before_a_request_is_made(self) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        client = OpenAIClient(
            api_key="", client=httpx.AsyncClient(transport=httpx.MockTransport(explode))
        )
        assert not client.is_configured
        with pytest.raises(LLMError, match="no OpenAI API key"):
            await client.complete(instructions="i", input_items=[])


class TestResponseParsing:
    def test_a_captured_tool_call_parses(self) -> None:
        turn = _turn_from_response(TOOL_CALL_RESPONSE)
        assert turn.wants_tools
        call = turn.calls[0]
        assert call.name == "target_find"
        assert call.call_id == "call_qJdEZntGw2BSRXmh5kghil5U"
        assert call.arguments == {"query": "carbapenem resistance in Klebsiella"}

    def test_arguments_are_a_json_string_not_an_object(self) -> None:
        """The API sends a string. Treating it as a dict silently yields no
        arguments at all, and the tool then runs on its defaults."""
        assert isinstance(TOOL_CALL_RESPONSE["output"][0]["arguments"], str)  # type: ignore[index]
        turn = _turn_from_response(TOOL_CALL_RESPONSE)
        assert isinstance(turn.calls[0].arguments, dict)

    def test_malformed_arguments_do_not_end_the_turn(self) -> None:
        """A researcher is waiting. An empty mapping lets the tool's own
        validation error go back to the model, which can correct it."""
        payload = json.loads(json.dumps(TOOL_CALL_RESPONSE))
        payload["output"][0]["arguments"] = '{"query": '
        turn = _turn_from_response(payload)
        assert turn.calls[0].arguments == {}

    def test_text_is_read_from_output_text_parts(self) -> None:
        turn = _turn_from_response(
            {
                "model": MODEL,
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "KPC-2 is the enzyme."}],
                    }
                ],
            }
        )
        assert turn.text == "KPC-2 is the enzyme."
        assert not turn.wants_tools

    def test_the_call_items_are_kept_to_echo_back(self) -> None:
        """The model has no record of having asked unless the function_call
        item goes back with the result, and it asks again."""
        turn = _turn_from_response(TOOL_CALL_RESPONSE)
        assert turn.output_items[0]["type"] == "function_call"


class TestCost:
    def test_cost_is_computed_from_the_published_prices(self) -> None:
        """There is no cost field in the response; dollars are our arithmetic."""
        usage = TokenUsage(input_tokens=1_000_000, output_tokens=0, model=MODEL)
        assert usage.cost_usd() == Decimal("2.00")

    def test_cached_input_is_charged_at_the_cached_rate(self) -> None:
        """A fifth of a cent against ten cents. Billing all input at the full
        rate would overstate a long session's cost by an order of magnitude."""
        cached = TokenUsage(
            input_tokens=1_000_000, cached_input_tokens=1_000_000, model=MODEL
        ).cost_usd()
        assert cached == Decimal("0.10")

    def test_an_unknown_model_reports_zero_rather_than_guessing(self) -> None:
        """A made-up number in a spend ledger is worse than a visible gap."""
        assert TokenUsage(input_tokens=1000, model="gpt-does-not-exist").cost_usd() == Decimal("0")

    def test_the_default_model_is_priced(self) -> None:
        from biostruct.agent.llm import DEFAULT_MODEL

        assert DEFAULT_MODEL in PRICES_PER_MTOK


class TestStreaming:
    @pytest.mark.anyio
    async def test_text_arrives_as_deltas_and_the_turn_closes(self) -> None:
        body = sse(
            {"type": "response.created"},
            {"type": "response.output_text.delta", "delta": "KPC"},
            {"type": "response.output_text.delta", "delta": "-2"},
            {
                "type": "response.completed",
                "response": {
                    "model": MODEL,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            },
        )
        events = [
            e
            async for e in client_for(stream_handler(body)).stream(
                instructions="i", input_items=[{"role": "user", "content": "x"}]
            )
        ]
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["KPC", "-2"]
        finished = next(e for e in events if isinstance(e, TurnFinished))
        assert finished.turn.text == "KPC-2"
        assert finished.turn.usage.input_tokens == 10

    @pytest.mark.anyio
    async def test_a_tool_call_is_announced_before_its_arguments_finish(self) -> None:
        """So the researcher sees "looking up the target" straight away rather
        than after a silent pause while 19 fragments arrive."""
        body = sse(
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "target_find",
                },
            },
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"q'},
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": 'uery":"KPC"}',
            },
            {"type": "response.completed", "response": {"model": MODEL, "usage": {}}},
        )
        events = [
            e
            async for e in client_for(stream_handler(body)).stream(
                instructions="i", input_items=[{"role": "user", "content": "x"}]
            )
        ]
        started = [e for e in events if isinstance(e, ToolCallStarted)]
        assert started[0].name == "target_find"
        # Announced before any arguments arrived.
        assert events.index(started[0]) == 0
        turn = next(e for e in events if isinstance(e, TurnFinished)).turn
        assert turn.calls[0].arguments == {"query": "KPC"}

    @pytest.mark.anyio
    async def test_parallel_calls_do_not_corrupt_each_others_arguments(self) -> None:
        """The reason fragments are accumulated per item_id. Interleaved deltas
        concatenated into one buffer produce two corrupt payloads rather than
        one visible error."""
        body = sse(
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "a", "call_id": "ca", "name": "one"},
            },
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "b", "call_id": "cb", "name": "two"},
            },
            {"type": "response.function_call_arguments.delta", "item_id": "a", "delta": '{"x":'},
            {"type": "response.function_call_arguments.delta", "item_id": "b", "delta": '{"y":'},
            {"type": "response.function_call_arguments.delta", "item_id": "a", "delta": "1}"},
            {"type": "response.function_call_arguments.delta", "item_id": "b", "delta": "2}"},
            {"type": "response.completed", "response": {"model": MODEL, "usage": {}}},
        )
        turn = await turn_of(body)
        assert [c.name for c in turn.calls] == ["one", "two"]
        assert turn.calls[0].arguments == {"x": 1}
        assert turn.calls[1].arguments == {"y": 2}

    @pytest.mark.anyio
    async def test_the_done_event_is_authoritative_over_the_fragments(self) -> None:
        body = sse(
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "a", "call_id": "ca", "name": "one"},
            },
            {"type": "response.function_call_arguments.delta", "item_id": "a", "delta": "junk"},
            {
                "type": "response.function_call_arguments.done",
                "item_id": "a",
                "arguments": '{"x":9}',
            },
            {"type": "response.completed", "response": {"model": MODEL, "usage": {}}},
        )
        turn = await turn_of(body)
        assert turn.calls[0].arguments == {"x": 9}

    @pytest.mark.anyio
    async def test_accumulated_arguments_are_written_onto_the_echoed_item(self) -> None:
        """The streamed item carries no arguments of its own. If the fragments
        are not written back, the model is handed its own call with an empty
        argument list and asks again — an infinite loop the researcher pays for."""
        body = sse(
            {
                "type": "response.output_item.added",
                "item": {"type": "function_call", "id": "a", "call_id": "ca", "name": "one"},
            },
            {"type": "response.function_call_arguments.delta", "item_id": "a", "delta": '{"x":1}'},
            {"type": "response.completed", "response": {"model": MODEL, "usage": {}}},
        )
        turn = await turn_of(body)
        assert turn.output_items[0]["arguments"] == '{"x":1}'

    @pytest.mark.anyio
    async def test_an_http_error_becomes_a_readable_message(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, json={"error": {"message": "Rate limit reached for gpt-6.1-sol"}}
            )

        with pytest.raises(LLMError, match="Rate limit reached"):
            async for _ in client_for(handler).stream(
                instructions="i", input_items=[{"role": "user", "content": "x"}]
            ):
                pass

    @pytest.mark.anyio
    async def test_an_error_event_mid_stream_raises(self) -> None:
        body = sse({"type": "error", "message": "the model stream broke"})
        with pytest.raises(LLMError, match="stream broke"):
            async for _ in client_for(stream_handler(body)).stream(
                instructions="i", input_items=[{"role": "user", "content": "x"}]
            ):
                pass

    @pytest.mark.anyio
    async def test_the_error_message_never_contains_the_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="Incorrect API key provided: sk-test")

        try:
            await client_for(handler).complete(instructions="i", input_items=[])
        except LLMError as exc:
            assert "sk-test" not in str(exc)
        else:
            raise AssertionError("expected an LLMError")

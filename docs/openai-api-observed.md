# OpenAI API — observed behaviour

Verified against the live API with our own key on **2026-11-17**. Every model
ID, field name and event name below was confirmed by a real call, not read from
a doc page. Both matter: the published docs moved host mid-check
(`platform.openai.com/docs/*` now 301s to `developers.openai.com/api/docs/*`)
and two doc pages disagreed with each other about which model to use.

## 1. The model landscape

`GET /v1/models` on our account is the authoritative list of what we can call.
The current families, with the official one-line descriptions and Standard-tier
prices per million tokens:

| Model ID | Description | In | Out | Cached in |
|---|---|---|---|---|
| `gpt-6-astra` | "Our most capable model for the most demanding work" | $10.00 | $50.00 | $1.00 |
| `gpt-6.1-sol` | "Near-Astra performance for complex work at a lower cost" | **$2.00** | **$10.00** | $0.10 |
| `gpt-6.1-terra` | "A fast, efficient model for visual reasoning, precise computer use, and clear, natural writing" | $1.00 | $5.00 | $0.05 |
| `gpt-6-luna` | "Our most efficient model for focused, high-volume tasks" | $0.10 | $0.50 | $0.01 |

Context is 1.05M with 128K max output on Astra and Sol. Knowledge cutoffs are
April–June 2026 — **after** the resistance literature a researcher will ask
about, which is one more reason the agent cites Europe PMC rather than its own
recall.

**We use `gpt-6.1-sol`.** The orchestrator decides which scientific tool to
call, and a wrong call here spends real money on the wrong protein, so this is
not a place for the cheapest model. Astra at 5× the price is reserved for
nothing yet; `ANTIDOTE_AGENT_MODEL` changes it without a deploy.

## 2. The Responses API is not optional for this model

> "GPT-6 Astra and GPT-6.1 Sol require the Responses API for tool calling. The
> Chat Completions examples use GPT-5.6 Terra with reasoning disabled."

So `POST /v1/responses`, not `/v1/chat/completions`. Confirmed working.

## 3. Tools are declared flat, not nested

This is the shape that works. There is **no** `{"type": "function", "function":
{...}}` wrapper — the older Chat Completions form:

```json
{
  "type": "function",
  "name": "target_find",
  "description": "Find a resistance enzyme by plain-language description.",
  "parameters": {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": false
  },
  "strict": true
}
```

`strict: true` requires `additionalProperties: false` **and** every property
listed in `required`. A schema that omits either is rejected, which is why the
tool registry builds the schema rather than letting each tool hand-write one.

## 4. The tool-call round trip

A tool call comes back as an item in `response.output`:

```json
{"type": "function_call", "id": "fc_...", "call_id": "call_qJdEZ...",
 "name": "target_find", "arguments": "{\"query\":\"...\"}", "status": "completed"}
```

`arguments` is a **JSON string**, not an object. The result goes back as its own
input item, and the `call_id` — not the `id` — is what pairs them:

```json
{"type": "function_call_output", "call_id": "call_qJdEZ...", "output": "{...}"}
```

The function-call item itself must be echoed back in `input` alongside its
output, or the model has no record of having asked.

## 5. Streaming event names

Confirmed by capturing a real SSE stream of each kind. These are the names the
orchestrator switches on:

**Text turn**

| Event | Carries |
|---|---|
| `response.created` | — |
| `response.output_item.added` | `item.type == "message"` |
| `response.output_text.delta` | `delta` — the token |
| `response.output_text.done` | the full text |
| `response.completed` | `response.usage` |

**Tool-call turn**

| Event | Carries |
|---|---|
| `response.output_item.added` | `item.type == "function_call"`, with `name` |
| `response.function_call_arguments.delta` | `delta`, `item_id`, `output_index` |
| `response.function_call_arguments.done` | the complete `arguments` string |
| `response.output_item.done` | the finished `function_call` item |

Arguments stream in 19 fragments for a one-field call, so they must be
concatenated per `item_id` — a parallel call interleaves two items' deltas in
one stream. Deltas also carry an `obfuscation` field, which is padding; ignore it.

## 6. Reasoning effort

`reasoning: {"effort": "low" | "medium" | "high" | "xhigh" | "max"}`, nested —
not a top-level `reasoning_effort`. The response echoes the full object:
`{"context": "all_turns", "effort": "medium", "mode": "standard", "summary": null}`,
so **`medium` is the default** when nothing is sent. `none` is accepted only by
Terra and Luna, not by Sol or Astra.

## 7. Usage, and what it does not tell you

```json
{"input_tokens": 74,
 "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0,
   "cache_write_tokens_details": {"ttl_12h_tokens": 0, "ttl_30m_tokens": 0}},
 "output_tokens": 32,
 "output_tokens_details": {"reasoning_tokens": 0},
 "total_tokens": 106}
```

Cached input is billed at about 1/20th, and reasoning tokens are billed as
output, so a turn's cost cannot be derived from `total_tokens` alone. The three
numbers are stored separately on every message for that reason.

**There is no cost field in the response.** Dollars are our arithmetic, from
these counts and the price table above, which is why the prices are a constant
in the code with this file as their provenance.

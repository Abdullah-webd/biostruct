/**
 * The agent transport: a streamed turn, and the citation filter that goes with it.
 *
 * Two things here are not obvious.
 *
 * **Why `fetch` and not `EventSource`.** The turn is a POST (it carries the
 * message and needs the CSRF header), and `EventSource` can only issue a GET
 * with no custom headers. So the SSE frames are parsed by hand off the response
 * body, which is a dozen lines and avoids inventing a GET endpoint that would
 * mutate a thread.
 *
 * **Why the text is filtered as it streams.** The model writes citations as
 * `[cite:MED:42942151]` markers and the backend replaces each verified one with
 * a number — but that happens when the turn completes, after the text has
 * already streamed past the reader. Showing raw markers looks broken, and if a
 * citation turns out to be fabricated the reader has already seen a reference
 * that is about to be withdrawn. So markers are withheld during the stream and
 * the authoritative verified text replaces the draft on `completed`.
 */

import { CSRF_COOKIE } from "@/lib/api";

export type SessionSummary = {
  id: string;
  title: string;
  status: "active" | "archived";
  message_count: number;
  budget_limit_usd: string;
  budget_remaining_usd: string;
  created_at: string;
  last_message_at: string | null;
};

export type MessageBlock = {
  kind: "text" | "proposal" | "error";
  text: string;
  estimate_usd: string | null;
  flagged: string[];
};

export type Citation = {
  key: string;
  title: string;
  citation: string;
  url: string;
  is_preprint: boolean;
};

export type AgentMessage = {
  id: string;
  seq: number;
  role: "user" | "assistant" | "tool" | "system";
  text: string;
  blocks: MessageBlock[];
  tool_calls: { name: string; arguments: Record<string, unknown> }[];
  citations: Citation[];
  flagged: string[];
  created_at: string;
};

export type Artifact = {
  id: string;
  kind: string;
  title: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type Reference = {
  n: string;
  key: string;
  title: string;
  citation: string;
  url: string;
  doi: string;
  note: string;
};

export type Proposal = {
  tool: string;
  arguments: Record<string, unknown>;
  title: string;
  detail: string;
  estimate_usd: string;
  yields: string;
};

export type TurnEvent =
  | { type: "started"; message_id: string; seq: number }
  | { type: "delta"; text: string }
  | { type: "tool_started"; name: string; spend: "free" | "paid" }
  | { type: "tool_finished"; name: string; summary: string; ok: boolean }
  | { type: "proposal"; proposal: Proposal }
  | {
      type: "completed";
      message_id: string;
      text: string;
      references: Reference[];
      warning: string;
      flagged: string[];
      usage: Record<string, number>;
      cost_usd: string;
    }
  | { type: "failed"; message: string };

/** A marker, or the beginning of one that has not finished arriving. */
const MARKER = /\[cite:[A-Za-z]{2,6}:[A-Za-z0-9]{1,32}\]/g;
const PARTIAL = /\[(?:c(?:i(?:t(?:e(?::[A-Za-z]{0,6}(?::[A-Za-z0-9]{0,32})?)?)?)?)?)?$/;

/**
 * Hides citation markers from text that is still arriving.
 *
 * A marker is split across deltas — `[cit`, `e:MED:4294`, `2151]` — so a naive
 * replace on each chunk shows the fragments. This holds back any trailing text
 * that could still become a marker and releases it once it either completes (and
 * is dropped) or turns out to be something else (and is emitted).
 */
export function createCitationFilter() {
  let held = "";
  return {
    push(chunk: string): string {
      const combined = held + chunk;
      const cleaned = combined.replace(MARKER, "");
      const partial = cleaned.match(PARTIAL);
      if (partial) {
        held = partial[0];
        return cleaned.slice(0, cleaned.length - held.length);
      }
      held = "";
      return cleaned;
    },
    /** Anything still held was never a marker, so it is real text. */
    flush(): string {
      const rest = held;
      held = "";
      return rest;
    },
  };
}

/** Strip markers from a complete string (for text that is not streaming). */
export function stripMarkers(text: string): string {
  return text.replace(MARKER, "").replace(/\s+([.,;:])/g, "$1");
}

function readCsrf(): string {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

/**
 * POST a message and yield the turn's events as they arrive.
 *
 * `signal` lets the page abandon a turn when the reader navigates away. The
 * server keeps going and writes the result to the thread, so nothing is lost —
 * reopening the session shows the finished answer.
 */
export async function* sendMessage(
  sessionId: string,
  content: string,
  options: { spokenLanguage?: string; signal?: AbortSignal } = {},
): AsyncGenerator<TurnEvent> {
  const response = await fetch(`/api/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "x-csrf-token": readCsrf(),
    },
    credentials: "same-origin",
    body: JSON.stringify({
      content,
      spoken_language: options.spokenLanguage ?? "",
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    let detail = `The request failed (HTTP ${response.status}).`;
    try {
      const problem = (await response.json()) as { detail?: string };
      if (problem.detail) detail = problem.detail;
    } catch {
      /* a non-JSON error body is not worth surfacing verbatim */
    }
    yield { type: "failed", message: detail };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. A partial frame stays in the
    // buffer: parsing one would drop half an event.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = parseFrame(frame);
      if (event) yield event;
      split = buffer.indexOf("\n\n");
    }
  }
}

function parseFrame(frame: string): TurnEvent | null {
  let name = "";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!name || !data) return null;
  try {
    const payload = JSON.parse(data) as Record<string, unknown>;
    if (name === "proposal") {
      return { type: "proposal", proposal: payload as unknown as Proposal };
    }
    return { type: name, ...payload } as TurnEvent;
  } catch {
    return null;
  }
}

export const agentApi = {
  sessions: () => fetchJson<SessionSummary[]>("/sessions"),
  session: (id: string) => fetchJson<SessionSummary>(`/sessions/${id}`),
  messages: (id: string) =>
    fetchJson<AgentMessage[]>(`/sessions/${id}/messages`),
  artifacts: (id: string) => fetchJson<Artifact[]>(`/sessions/${id}/artifacts`),
  create: (body: { title?: string }) =>
    fetchJson<SessionSummary>("/sessions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  archive: (id: string) =>
    fetchJson<SessionSummary>(`/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "archived" }),
    }),
};

async function fetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    accept: "application/json",
    ...(init.body ? { "content-type": "application/json" } : {}),
  };
  if (init.method && init.method !== "GET") headers["x-csrf-token"] = readCsrf();
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const problem = parsed as { detail?: string; title?: string };
    throw new Error(
      problem.detail ?? problem.title ?? `HTTP ${response.status}`,
    );
  }
  return parsed as T;
}

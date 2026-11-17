"use client";

/**
 * The agent workbench: a thread, the work being done, and the evidence.
 *
 * Motion is dial 4 — app-screen calm. Messages arrive with a short, staggered
 * entrance, tool rows slide in as the work happens, and the composer responds
 * to the press. Nothing scroll-triggered: this is a working instrument, and a
 * reveal animation on a result a researcher is waiting for would be an
 * obstruction rather than a flourish.
 *
 * Three decisions worth stating:
 *
 * - **Tool activity is shown, not hidden behind a spinner.** "Searching Europe
 *   PMC" is the difference between an answer a researcher can trust and one
 *   they have to take on faith.
 * - **The streamed draft is replaced by the verified text.** The backend strips
 *   unverifiable citations when the turn completes, so the draft is explicitly
 *   provisional and marked as such.
 * - **A paid proposal is a card with a price and a disabled button**, not a
 *   sentence. The confirmation route lands in M8; until then the UI says so
 *   rather than offering a button that silently does nothing.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  type AgentMessage,
  type Artifact,
  type Proposal,
  type Reference,
  type SessionSummary,
  agentApi,
  createCitationFilter,
  sendMessage,
  stripMarkers,
} from "@/lib/agent";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import Markdown from "@/components/Markdown";
import { Button, Notice, Panel, SkeletonRows } from "@/components/ui";

const TOOL_LABELS: Record<string, string> = {
  target_find: "Searching UniProt for the protein",
  target_detail: "Reading the protein and its active site",
  literature_search: "Searching Europe PMC",
  resistance_literature: "Searching the resistance literature",
  chembl_target_lookup: "Looking up the target in ChEMBL",
  molecules_library: "Assembling a molecule library",
  propose_fold: "Pricing a structure prediction",
  propose_screen: "Pricing a screen",
  propose_design: "Pricing a design run",
};

type ToolRow = { name: string; spend: "free" | "paid"; done: boolean; ok: boolean };

type Live = {
  draft: string;
  tools: ToolRow[];
  proposals: Proposal[];
  references: Reference[];
  warning: string;
  flagged: string[];
  cost: string;
  failed: string;
};

const EMPTY: Live = {
  draft: "",
  tools: [],
  proposals: [],
  references: [],
  warning: "",
  flagged: [],
  cost: "",
  failed: "",
};

const EXAMPLES = [
  "Our ICU has Klebsiella pneumoniae that no longer responds to meropenem. What enzyme is likely responsible?",
  "What does the literature say about NDM-1 in Nigeria?",
  "Build me a molecule library for a class A carbapenemase and tell me the cost.",
];

export default function Workbench({ sessionId }: { sessionId: string }) {
  const [session, setSession] = useState<SessionSummary | null>(null);
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState<Live>(EMPTY);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement | null>(null);

  const reload = useCallback(async () => {
    // Yield before the first setState, so this is safe to call straight out of
    // an effect as well as out of a click (the precedent in TargetStep).
    await Promise.resolve();
    const [s, m, a] = await Promise.all([
      agentApi.session(sessionId),
      agentApi.messages(sessionId),
      agentApi.artifacts(sessionId),
    ]);
    setSession(s);
    setMessages(m);
    setArtifacts(a);
  }, [sessionId]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      // Yields first, so no state is set synchronously inside the effect.
      await Promise.resolve();
      try {
        await reload();
      } catch (e: unknown) {
        if (alive) setError(String(e));
      } finally {
        if (alive) setLoading(false);
      }
    };
    void load();
    return () => {
      alive = false;
      // A turn in flight is abandoned on the client only. The server finishes
      // it and writes the answer to the thread, so reopening the session shows
      // the result rather than losing it.
      abort.current?.abort();
    };
  }, [reload]);

  // Follow the turn as it is written, the way a terminal does.
  useEffect(() => {
    tail.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, live.draft, live.tools.length]);

  const ask = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || running) return;
      setInput("");
      setError("");
      setLive(EMPTY);
      setRunning(true);

      // Shown immediately: the researcher should see what they asked while the
      // agent works, not an empty thread with a spinner.
      const optimistic: AgentMessage = {
        id: `pending-${Date.now()}`,
        seq: (messages.at(-1)?.seq ?? 0) + 1,
        role: "user",
        text: content,
        blocks: [{ kind: "text", text: content, estimate_usd: null, flagged: [] }],
        tool_calls: [],
        citations: [],
        flagged: [],
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, optimistic]);

      const controller = new AbortController();
      abort.current = controller;
      const filter = createCitationFilter();
      let terminated = false;

      try {
        for await (const event of sendMessage(sessionId, content, {
          signal: controller.signal,
        })) {
          switch (event.type) {
            case "delta":
              setLive((l) => ({ ...l, draft: l.draft + filter.push(event.text) }));
              break;
            case "tool_started":
              setLive((l) => ({
                ...l,
                tools: [
                  ...l.tools,
                  { name: event.name, spend: event.spend, done: false, ok: true },
                ],
              }));
              break;
            case "tool_finished":
              setLive((l) => ({
                ...l,
                tools: l.tools.map((t, i) =>
                  t.name === event.name && !t.done && i === l.tools.findIndex((x) => x.name === event.name && !x.done)
                    ? { ...t, done: true, ok: event.ok }
                    : t,
                ),
              }));
              break;
            case "proposal":
              setLive((l) => ({ ...l, proposals: [...l.proposals, event.proposal] }));
              break;
            case "completed":
              terminated = true;
              setLive((l) => ({
                ...l,
                draft: l.draft + filter.flush(),
                references: event.references,
                warning: event.warning,
                flagged: event.flagged,
                cost: event.cost_usd,
              }));
              break;
            case "failed":
              terminated = true;
              setLive((l) => ({ ...l, failed: event.message }));
              break;
          }
        }
        if (!terminated && !controller.signal.aborted) {
          // The stream ended with neither a completion nor a failure, so the
          // client cannot tell a finished turn from a dropped connection. The
          // server may still have written the answer, so reload and say so
          // rather than leaving a draft on screen as though it were final.
          setError(
            "The connection to the turn dropped. Anything the agent finished has been saved below.",
          );
        }
        await reload();
      } catch (e: unknown) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        setRunning(false);
        setLive(EMPTY);
        abort.current = null;
      }
    },
    [messages, reload, running, sessionId],
  );

  const visible = useMemo(
    () => messages.filter((m) => m.role === "user" || m.role === "assistant"),
    [messages],
  );

  if (loading) {
    return (
      <Panel className="p-4">
        <SkeletonRows rows={5} />
      </Panel>
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="flex min-h-0 flex-col gap-4 pb-2">
        {error ? <Notice tone="danger" title="Something went wrong">{error}</Notice> : null}

        {visible.length === 0 ? (
          <Panel className="p-4">
            <h2 className="font-serif text-xl text-ink">
              Describe the infection you are working on
            </h2>
            <p className="mt-2 max-w-prose text-sm text-muted">
              In your own words — the organism, the antibiotic that stopped
              working, what you are seeing on the ward. Antidote will find the
              protein, read the literature and tell you what a screen would
              cost before spending anything.
            </p>
            <ul className="mt-5 flex flex-col gap-2">
              {EXAMPLES.map((example, i) => (
                <motion.li
                  key={example}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: DUR.panel, delay: stagger(i, 0.06), ease: EASE_OUT }}
                >
                  <button
                    type="button"
                    onClick={() => void ask(example)}
                    className="w-full rounded-md border border-line bg-wash px-3 py-2 text-left text-sm text-ink transition-colors duration-[var(--dur-ui)] hover:border-signal hover:bg-signal-dim"
                  >
                    {example}
                  </button>
                </motion.li>
              ))}
            </ul>
          </Panel>
        ) : null}

        <AnimatePresence initial={false}>
          {visible.map((message) => (
            <motion.div
              key={message.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: DUR.panel, ease: EASE_OUT }}
            >
              <MessageCard message={message} />
            </motion.div>
          ))}
        </AnimatePresence>

        <AnimatePresence>
          {running ? (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: DUR.ui, ease: EASE_OUT }}
            >
              <LiveTurn live={live} />
            </motion.div>
          ) : null}
        </AnimatePresence>

        {/* The composer is sticky, so without this the last proposal card
            sits underneath it and a researcher cannot read the price they are
            being asked to approve. */}
        <div ref={tail} className="h-2" />

        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => void ask(input)}
          running={running}
          onStop={() => abort.current?.abort()}
        />
      </div>

      <SidePanel session={session} artifacts={artifacts} />
    </div>
  );
}

function MessageCard({ message }: { message: AgentMessage }) {
  const isUser = message.role === "user";
  const proposals = message.blocks.filter((b) => b.kind === "proposal");
  return (
    <article
      className={
        isUser
          ? "rounded-lg border border-line bg-wash p-4"
          : "rounded-lg border border-line bg-panel p-5"
      }
    >
      <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.08em] text-faint">
        {isUser ? "You" : "Antidote"}
      </p>
      <Prose text={message.text} flagged={message.flagged} />

      {proposals.map((block, i) => (
        <div
          key={i}
          className="mt-3 rounded-md border border-dashed border-caution bg-[#fff9ef] p-3"
        >
          <p className="text-sm font-medium text-ink">{block.text}</p>
          <p className="mt-1 text-xs text-muted">
            Nothing has been spent. Confirmation arrives in the next milestone.
          </p>
        </div>
      ))}

      {message.citations.length > 0 ? (
        <ol className="mt-4 flex flex-col gap-1.5 border-t border-line pt-3">
          {message.citations.map((citation, i) => (
            <li key={citation.key} className="text-xs leading-relaxed text-muted">
              <span className="font-mono text-faint">[{i + 1}]</span>{" "}
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer"
                className="text-signal underline decoration-line underline-offset-2 transition-colors duration-[var(--dur-ui)] hover:decoration-signal"
              >
                {citation.citation || citation.title}
              </a>
              {citation.is_preprint ? (
                <span className="ml-1 text-caution">
                  (preprint, not peer reviewed)
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  );
}

/**
 * Renders the answer, marking any sentence whose citation did not survive
 * verification. The claim stays visible — a researcher is entitled to see what
 * was asserted *and* that it was unsupported.
 */
function Prose({ text, flagged }: { text: string; flagged: string[] }) {
  const clean = stripMarkers(text);
  if (flagged.length === 0) {
    return <div className="flex flex-col gap-3">{paragraphs(clean)}</div>;
  }
  return (
    <div className="flex flex-col gap-3">
      {paragraphs(clean)}
      <div className="rounded-md border border-caution bg-[#fff9ef] p-3">
        <p className="text-xs font-medium text-caution">
          {flagged.length === 1
            ? "One statement here could not be supported by a verified source:"
            : `${flagged.length} statements here could not be supported by a verified source:`}
        </p>
        <ul className="mt-1.5 flex flex-col gap-1">
          {flagged.map((sentence) => (
            <li key={sentence} className="text-xs italic text-muted">
              “{sentence}”
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * The model writes markdown — headings, bullets, bold, and a table whenever it
 * is pricing two steps — so it is rendered as markdown. Printing it raw was the
 * ugliest thing in the first working build.
 */
function paragraphs(text: string) {
  return <Markdown>{text}</Markdown>;
}

/**
 * Money for a reader. The API stores four decimal places because a screen is
 * priced per molecule at $0.0250, but "$25.0000" on a dashboard reads as a
 * mistake. Sub-cent amounts keep their precision rather than rounding to
 * "$0.00", which would be worse than useless on a per-molecule price.
 */
function money(value: string | undefined): string {
  if (!value) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return `$${value}`;
  if (n !== 0 && Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function LiveTurn({ live }: { live: Live }) {
  return (
    <article className="rounded-lg border border-line bg-panel p-5">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-[0.08em] text-faint">
        Antidote
      </p>

      <AnimatePresence initial={false}>
        {live.tools.map((tool, i) => (
          <motion.div
            key={`${tool.name}-${i}`}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: DUR.ui, ease: EASE_OUT }}
            className="mb-2 flex items-center gap-2 text-sm"
          >
            <span
              aria-hidden
              className={
                tool.done
                  ? tool.ok
                    ? "h-1.5 w-1.5 rounded-full bg-good"
                    : "h-1.5 w-1.5 rounded-full bg-danger"
                  : "h-1.5 w-1.5 animate-pulse rounded-full bg-signal"
              }
            />
            <span className={tool.done ? "text-muted" : "text-ink"}>
              {TOOL_LABELS[tool.name] ?? tool.name}
            </span>
            {tool.spend === "paid" ? (
              <span className="font-mono text-[10px] uppercase tracking-wide text-caution">
                pricing only
              </span>
            ) : null}
          </motion.div>
        ))}
      </AnimatePresence>

      {live.draft ? (
        <div className="mt-3 flex flex-col gap-3">
          {paragraphs(live.draft)}
          <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-faint">
            Draft — citations are verified when the turn finishes
          </p>
        </div>
      ) : live.tools.length === 0 ? (
        <p className="text-sm text-muted">Thinking…</p>
      ) : null}

      {live.proposals.map((proposal, i) => (
        <div
          key={i}
          className="mt-3 rounded-md border border-dashed border-caution bg-[#fff9ef] p-3"
        >
          <p className="text-sm font-medium text-ink">{proposal.title}</p>
          <p className="mt-1 text-xs text-muted">{proposal.detail}</p>
          <p className="mt-2 font-mono text-sm text-ink">
            ${proposal.estimate_usd}
          </p>
        </div>
      ))}

      {live.failed ? (
        <div className="mt-3">
          <Notice tone="danger" title="The turn failed">{live.failed}</Notice>
        </div>
      ) : null}
    </article>
  );
}

function Composer({
  value,
  onChange,
  onSubmit,
  running,
  onStop,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  running: boolean;
  onStop: () => void;
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="sticky bottom-0 rounded-lg border border-line bg-panel p-3 shadow-[0_-8px_24px_-20px_rgba(0,0,0,0.3)]"
    >
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends; shift+enter is a newline, because a pasted protein
          // sequence is a legitimate multi-line message.
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            onSubmit();
          }
        }}
        rows={3}
        disabled={running}
        placeholder="Describe the infection, paste a sequence, or ask what the literature says…"
        className="w-full resize-none bg-transparent text-[15px] leading-relaxed text-ink outline-none placeholder:text-faint disabled:opacity-60"
      />
      <div className="mt-2 flex items-center justify-between">
        <p className="text-xs text-muted">
          Enter to send · Shift+Enter for a new line
        </p>
        {running ? (
          <Button type="button" variant="ghost" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button type="submit" disabled={!value.trim()}>
            Ask
          </Button>
        )}
      </div>
    </form>
  );
}

function SidePanel({
  session,
  artifacts,
}: {
  session: SessionSummary | null;
  artifacts: Artifact[];
}) {
  return (
    <aside className="flex flex-col gap-4">
      <Panel className="p-4">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-faint">
          Budget
        </h3>
        <p className="mt-2 font-mono text-2xl text-ink">
          {money(session?.budget_remaining_usd)}
        </p>
        <p className="text-xs text-muted">
          remaining of {money(session?.budget_limit_usd)} for this session
        </p>
        <p className="mt-3 border-t border-line pt-3 text-xs text-muted">
          Reading the literature, finding a protein and assembling a library are
          free. Folding and screening cost money and are never started without
          you confirming a price.
        </p>
      </Panel>

      <Panel className="p-4">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-faint">
          Work so far
        </h3>
        {artifacts.length === 0 ? (
          <p className="mt-2 text-xs text-muted">
            Nothing yet. Structures, libraries and evidence collected in this
            session will appear here.
          </p>
        ) : (
          <ul className="mt-2 flex flex-col gap-2">
            <AnimatePresence initial={false}>
              {artifacts.map((artifact, i) => (
                <motion.li
                  key={artifact.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: DUR.ui, delay: stagger(i, 0.04), ease: EASE_OUT }}
                  className="rounded-sm border border-line bg-wash px-2.5 py-2"
                >
                  <p className="font-mono text-[10px] uppercase tracking-wide text-faint">
                    {artifact.kind.replace("_", " ")}
                  </p>
                  <p className="mt-0.5 text-xs leading-snug text-ink">
                    {artifact.title}
                  </p>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </Panel>
    </aside>
  );
}

"use client";

/**
 * The workbench: a conversation, the work it set off, and the results.
 *
 * One surface. A researcher describes an infection in their own words — typed
 * or spoken in their own language — and the agent finds the protein, reads the
 * literature, and offers to fold it or screen molecules against it. Everything
 * it produces is a thing they can open.
 *
 * Decisions that are not obvious:
 *
 * - **Tool activity is shown, not hidden.** Current practice calls hiding tool
 *   calls the most common failure in production agent interfaces, and here it
 *   is also the only honest way to show an answer came from Europe PMC rather
 *   than from the model's memory. Each call is a row; finished ones collapse.
 * - **The streamed draft is provisional and says so.** Citations are verified
 *   when the turn completes, so the text on screen mid-turn is explicitly
 *   labelled a draft and replaced by the verified version.
 * - **A proposal is a button, not a sentence.** The agent never starts paid
 *   work; the researcher does, by pressing confirm.
 * - **No money on screen.** The ledger still guards a run server-side, but a
 *   dollar counter in the eyeline made this read as a billing console.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import ArtifactCanvas from "@/components/ArtifactCanvas";
import Markdown from "@/components/Markdown";
import { Button, Notice, SkeletonRows } from "@/components/ui";
import {
  type AgentMessage,
  type Artifact,
  type Proposal,
  type Reference,
  type Run,
  agentApi,
  createCitationFilter,
  sendMessage,
  stripMarkers,
  transcribe,
} from "@/lib/agent";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import { useRecorder } from "@/lib/useRecorder";

const TOOL_LABELS: Record<string, string> = {
  target_find: "Searching UniProt for the protein",
  target_detail: "Reading the protein and its active site",
  literature_search: "Searching Europe PMC",
  resistance_literature: "Searching the resistance literature",
  chembl_target_lookup: "Looking up the target in ChEMBL",
  molecules_library: "Assembling a molecule library",
  propose_fold: "Working out what folding would involve",
  propose_screen: "Working out what a screen would involve",
  propose_design: "Working out what designing molecules would involve",
};

const EXAMPLES = [
  "Our ICU has Klebsiella pneumoniae that no longer responds to meropenem. What enzyme is likely responsible?",
  "What does the literature say about NDM-1 carbapenemase in Nigeria?",
  "Fold KPC-2 for me and find molecules that could block it.",
];

type ToolRow = { key: string; name: string; done: boolean; ok: boolean };

type Live = {
  draft: string;
  tools: ToolRow[];
  references: Reference[];
  warning: string;
  flagged: string[];
  failed: string;
};

const EMPTY: Live = {
  draft: "",
  tools: [],
  references: [],
  warning: "",
  flagged: [],
  failed: "",
};

export default function Workbench({ sessionId }: { sessionId: string }) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [openArtifact, setOpenArtifact] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState<Live>(EMPTY);
  const [error, setError] = useState("");
  const [heard, setHeard] = useState("");
  const abort = useRef<AbortController | null>(null);
  const tail = useRef<HTMLDivElement | null>(null);

  const reload = useCallback(async () => {
    await Promise.resolve();
    const [m, a, r] = await Promise.all([
      agentApi.messages(sessionId),
      agentApi.artifacts(sessionId),
      agentApi.runs(sessionId),
    ]);
    setMessages(m);
    setArtifacts(a);
    setRuns(r);
  }, [sessionId]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
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
      // Abandoned on the client only: the server finishes the turn and writes
      // the answer, so reopening the session shows it rather than losing it.
      abort.current?.abort();
    };
  }, [reload]);

  // While work is in flight, keep the progress honest without hammering.
  const active = runs.some((r) => r.state !== "complete" && r.state !== "failed");
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => void reload().catch(() => {}), 4000);
    return () => clearInterval(id);
  }, [active, reload]);

  useEffect(() => {
    tail.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, live.draft, live.tools.length]);

  const ask = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || running) return;
      setInput("");
      setHeard("");
      setError("");
      setLive(EMPTY);
      setRunning(true);

      const optimistic: AgentMessage = {
        id: `pending-${Date.now()}`,
        seq: (messages.at(-1)?.seq ?? 0) + 1,
        role: "user",
        text: content,
        blocks: [{ kind: "text", text: content, estimate_usd: null, flagged: [], data: {} }],
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
                  { key: `${event.name}-${l.tools.length}`, name: event.name, done: false, ok: true },
                ],
              }));
              break;
            case "tool_finished":
              setLive((l) => {
                const i = l.tools.findIndex((t) => t.name === event.name && !t.done);
                if (i === -1) return l;
                const tools = [...l.tools];
                tools[i] = { ...tools[i], done: true, ok: event.ok };
                return { ...l, tools };
              });
              break;
            case "proposal":
              // Shown as a card only once it is stored on the message, so the
              // button is never offered for something a reload would lose.
              break;
            case "completed":
              terminated = true;
              setLive((l) => ({
                ...l,
                draft: l.draft + filter.flush(),
                references: event.references,
                warning: event.warning,
                flagged: event.flagged,
              }));
              break;
            case "failed":
              terminated = true;
              setLive((l) => ({ ...l, failed: event.message }));
              break;
          }
        }
        if (!terminated && !controller.signal.aborted) {
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

  const confirm = useCallback(
    async (proposal: Proposal) => {
      setError("");
      try {
        await agentApi.startRun(sessionId, proposal.tool, proposal.arguments);
        await reload();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [reload, sessionId],
  );

  const onAudio = useCallback(async (blob: Blob) => {
    const result = await transcribe(blob);
    // A draft, never a send. The researcher reads what we heard first.
    setInput((prev) => (prev ? `${prev} ${result.english}` : result.english));
    setHeard(
      result.translated
        ? `Heard ${result.language_name}: “${result.text}” — translated for the agent. Edit before sending.`
        : `Heard ${result.language_name}. Edit before sending.`,
    );
  }, []);

  const recorder = useRecorder(onAudio);

  const visible = useMemo(
    () => messages.filter((m) => m.role === "user" || m.role === "assistant"),
    [messages],
  );
  const shown = artifacts.find((a) => a.id === openArtifact) ?? null;

  /**
   * Proposals the researcher has not acted on.
   *
   * Read from the stored message blocks, not from the streaming state: the
   * turn's state is cleared when it finishes, and a proposal has to still be
   * there when someone comes back to it tomorrow. Anything already started is
   * dropped, because the button has done its job.
   */
  const pending = useMemo(() => {
    const started = new Set(
      runs.map((r) => ({ fold: "fold_structure", screen: "screen_molecules", design: "design_molecules" })[r.kind]),
    );
    const out: { key: string; proposal: Proposal }[] = [];
    for (const message of messages) {
      for (const [i, block] of message.blocks.entries()) {
        if (block.kind !== "proposal") continue;
        const proposal = block.data as unknown as Proposal;
        if (!proposal?.tool || started.has(proposal.tool)) continue;
        out.push({ key: `${message.id}-${i}`, proposal });
      }
    }
    return out;
  }, [messages, runs]);

  if (loading) {
    return (
      <div className="p-6">
        <SkeletonRows rows={5} />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6 sm:px-6">
            {error ? (
              <Notice tone="danger" title="Something went wrong">
                {error}
              </Notice>
            ) : null}

            {visible.length === 0 ? <Welcome onPick={(q) => void ask(q)} /> : null}

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

            {pending.map(({ key, proposal }) => (
              <ProposalCard
                key={key}
                proposal={proposal}
                onConfirm={() => void confirm(proposal)}
              />
            ))}

            {runs.map((run) => (
              <RunCard
                key={run.id}
                run={run}
                onOpen={() => setOpenArtifact(run.artifact_id)}
              />
            ))}

            {artifacts.length > 0 ? (
              <ArtifactStrip
                artifacts={artifacts}
                openId={openArtifact}
                onOpen={setOpenArtifact}
              />
            ) : null}

            <div ref={tail} className="h-1" />
          </div>
        </div>

        <Composer
          value={input}
          heard={heard}
          onChange={(v) => {
            setInput(v);
            if (heard) setHeard("");
          }}
          onSubmit={() => void ask(input)}
          running={running}
          onStop={() => abort.current?.abort()}
          recorder={recorder}
        />
      </div>

      {/* Side by side when there is room; a sheet over the thread when there
          is not. Below xl the canvas used to be hidden entirely, so "Open
          result" did nothing at all on a laptop — a dead button is worse than
          a cramped panel. */}
      <AnimatePresence>
        {shown ? (
          <>
            <motion.button
              type="button"
              aria-label="Close this result"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: DUR.ui }}
              onClick={() => setOpenArtifact("")}
              className="fixed inset-0 z-40 bg-ink/20 xl:hidden"
            />
            <div className="fixed inset-y-0 right-0 z-50 w-full max-w-[420px] xl:static xl:z-auto xl:w-[400px] xl:max-w-none xl:shrink-0">
              <ArtifactCanvas
                sessionId={sessionId}
                artifact={shown}
                onClose={() => setOpenArtifact("")}
              />
            </div>
          </>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="py-6">
      <h1 className="font-serif text-2xl text-ink">
        What infection are you working on?
      </h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted">
        Describe it in your own words — the organism, the antibiotic that
        stopped working, what you are seeing on the ward. Or press the
        microphone and say it in Yorùbá, Hausa, Igbo, Amharic or Pidgin.
        Antidote will find the protein behind the resistance, read the
        literature with citations you can check, and fold it so you can look
        for a molecule that blocks it.
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
              onClick={() => onPick(example)}
              className="w-full rounded-md border border-line bg-wash px-3 py-2.5 text-left text-sm leading-relaxed text-ink transition-colors duration-[var(--dur-ui)] hover:border-signal hover:bg-signal-dim"
            >
              {example}
            </button>
          </motion.li>
        ))}
      </ul>
    </div>
  );
}

function MessageCard({ message }: { message: AgentMessage }) {
  const isUser = message.role === "user";
  if (isUser) {
    return (
      <article className="ml-auto max-w-[85%] rounded-lg border border-line bg-wash px-4 py-3">
        <p className="whitespace-pre-wrap text-[15px] leading-relaxed text-ink">
          {message.text}
        </p>
      </article>
    );
  }
  return (
    <article>
      <Prose text={message.text} flagged={message.flagged} />
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
                <span className="ml-1 text-caution">(preprint, not peer reviewed)</span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  );
}

function Prose({ text, flagged }: { text: string; flagged: string[] }) {
  const clean = stripMarkers(text);
  return (
    <div className="flex flex-col gap-3">
      <Markdown>{clean}</Markdown>
      {flagged.length > 0 ? (
        <div className="rounded-md border-l-[3px] border-caution bg-[#fdf8ef] px-3 py-2">
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
      ) : null}
    </div>
  );
}

function LiveTurn({ live }: { live: Live }) {
  return (
    <article>
      <AnimatePresence initial={false}>
        {live.tools.map((tool) => (
          <motion.div
            key={tool.key}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: DUR.ui, ease: EASE_OUT }}
            className="mb-2 flex items-center gap-2 text-[13px]"
          >
            <span
              aria-hidden
              className={
                tool.done
                  ? tool.ok
                    ? "h-1.5 w-1.5 shrink-0 rounded-full bg-good"
                    : "h-1.5 w-1.5 shrink-0 rounded-full bg-danger"
                  : "h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-signal"
              }
            />
            <span className={tool.done ? "text-muted" : "text-ink"}>
              {TOOL_LABELS[tool.name] ?? tool.name}
              {tool.done && !tool.ok ? " — failed" : ""}
            </span>
          </motion.div>
        ))}
      </AnimatePresence>

      {live.draft ? (
        <div className="mt-3 flex flex-col gap-2">
          <Markdown>{live.draft}</Markdown>
          <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-faint">
            Draft — citations are verified when the turn finishes
          </p>
        </div>
      ) : live.tools.length === 0 ? (
        <p className="text-[13px] text-muted">Thinking…</p>
      ) : null}

      {live.failed ? (
        <div className="mt-3">
          <Notice tone="danger" title="The turn failed">
            {live.failed}
          </Notice>
        </div>
      ) : null}
    </article>
  );
}

function ProposalCard({
  proposal,
  onConfirm,
}: {
  proposal: Proposal;
  onConfirm: () => void;
}) {
  const [sent, setSent] = useState(false);
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR.panel, ease: EASE_OUT }}
      className="rounded-lg border border-signal bg-signal-dim/50 p-4"
    >
      <p className="text-sm font-medium text-ink">{proposal.title}</p>
      <p className="mt-1 text-[13px] leading-relaxed text-muted">{proposal.detail}</p>
      {proposal.yields ? (
        <p className="mt-1.5 text-[13px] leading-relaxed text-muted">
          <span className="text-faint">You get:</span> {proposal.yields}
        </p>
      ) : null}
      <div className="mt-3 flex items-center gap-2">
        <Button
          variant="primary"
          disabled={sent}
          onClick={() => {
            setSent(true);
            onConfirm();
          }}
        >
          {sent ? "Started" : "Run it"}
        </Button>
        <span className="text-xs text-muted">
          Nothing runs until you press this.
        </span>
      </div>
    </motion.div>
  );
}

function RunCard({ run, onOpen }: { run: Run; onOpen: () => void }) {
  const done = run.state === "complete";
  const failed = run.state === "failed";
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: DUR.panel, ease: EASE_OUT }}
      className="rounded-lg border border-line bg-panel p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink">{run.title}</p>
        {done && run.artifact_id ? (
          <Button onClick={onOpen} className="shrink-0 px-2.5 py-1 text-[13px]">
            Open result
          </Button>
        ) : null}
      </div>

      {failed ? (
        <p className="mt-1.5 text-[13px] leading-relaxed text-danger">{run.error}</p>
      ) : done ? (
        <p className="mt-1.5 text-[13px] text-muted">
          Finished{run.total ? ` — ${run.done} of ${run.total}` : ""}.
          {run.livemode ? "" : " Test mode: the results are synthetic."}
        </p>
      ) : (
        <>
          <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-wash">
            <motion.div
              className="h-full bg-signal"
              initial={{ width: 0 }}
              animate={{ width: `${Math.max(4, run.percent)}%` }}
              transition={{ duration: DUR.panel, ease: EASE_OUT }}
            />
          </div>
          <p className="mt-1.5 text-[13px] text-muted">
            {run.state === "harvesting"
              ? "Collecting the results…"
              : run.total
                ? `${run.done} of ${run.total} done`
                : "Running — this takes a few minutes."}
          </p>
        </>
      )}
    </motion.div>
  );
}

function ArtifactStrip({
  artifacts,
  openId,
  onOpen,
}: {
  artifacts: Artifact[];
  openId: string;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="border-t border-line pt-3">
      <p className="pb-2 font-mono text-[10px] uppercase tracking-[0.1em] text-faint">
        Results in this session
      </p>
      <ul className="flex flex-wrap gap-2">
        {artifacts.map((artifact, i) => (
          <motion.li
            key={artifact.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: DUR.ui, delay: stagger(i, 0.04), ease: EASE_OUT }}
          >
            <button
              type="button"
              onClick={() => onOpen(openId === artifact.id ? "" : artifact.id)}
              className={`max-w-[260px] truncate rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors duration-[var(--dur-ui)] ${
                openId === artifact.id
                  ? "border-signal bg-signal-dim text-ink"
                  : "border-line bg-wash text-muted hover:border-faint hover:text-ink"
              }`}
            >
              {artifact.title}
            </button>
          </motion.li>
        ))}
      </ul>
    </div>
  );
}

function Composer({
  value,
  heard,
  onChange,
  onSubmit,
  running,
  onStop,
  recorder,
}: {
  value: string;
  heard: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  running: boolean;
  onStop: () => void;
  recorder: ReturnType<typeof useRecorder>;
}) {
  const listening = recorder.state === "recording";
  const working = recorder.state === "working";
  return (
    <div className="border-t border-line bg-ground px-4 py-3 sm:px-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit();
        }}
        className="mx-auto w-full max-w-3xl"
      >
        {heard ? (
          <p className="mb-2 rounded-md border-l-[3px] border-signal bg-signal-dim px-3 py-1.5 text-xs leading-relaxed text-signal">
            {heard}
          </p>
        ) : null}
        {recorder.error ? (
          <p className="mb-2 text-xs text-danger">{recorder.error}</p>
        ) : null}

        <div className="rounded-lg border border-line bg-panel p-3">
          <textarea
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              // Shift+Enter is a newline: a pasted protein sequence is a
              // legitimate multi-line message.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                onSubmit();
              }
            }}
            rows={2}
            disabled={running}
            placeholder="Describe the infection, paste a sequence, or ask what the literature says…"
            className="w-full resize-none bg-transparent text-[15px] leading-relaxed text-ink outline-none placeholder:text-faint disabled:opacity-60"
          />
          <div className="mt-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => (listening ? recorder.stop() : void recorder.start())}
                disabled={running || working || recorder.state === "unsupported"}
                aria-label={listening ? "Stop recording" : "Ask by voice"}
                aria-pressed={listening}
                className={`grid size-8 place-items-center rounded-full border transition-colors duration-[var(--dur-ui)] active:scale-95 disabled:opacity-40 ${
                  listening
                    ? "border-danger bg-danger text-white"
                    : "border-line text-muted hover:border-faint hover:text-ink"
                }`}
              >
                {listening ? (
                  <span className="block size-2.5 rounded-[2px] bg-white" />
                ) : (
                  <span aria-hidden className="text-[13px]">
                    ●
                  </span>
                )}
              </button>
              <span className="text-xs text-muted">
                {listening
                  ? "Listening — press again to stop"
                  : working
                    ? "Transcribing…"
                    : "Speak Yorùbá, Hausa, Igbo, Amharic, Pidgin or English"}
              </span>
            </div>
            {running ? (
              <Button type="button" variant="ghost" onClick={onStop}>
                Stop
              </Button>
            ) : (
              <Button type="submit" variant="primary" disabled={!value.trim()}>
                Ask
              </Button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}

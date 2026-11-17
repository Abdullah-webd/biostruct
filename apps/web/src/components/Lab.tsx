"use client";

/**
 * The product, on one page.
 *
 * Step 1 — paste a sequence, get a 3D structure.
 * Step 2 — look at it, press one button, get ten molecules to test.
 *
 * Deliberately linear. The second step is disabled until the first finishes,
 * because there is no useful thing to do out of order, and an interface that
 * offers choices which cannot work is how a demo goes wrong in front of
 * people.
 *
 * Motion dial 4: steps arrive as they unlock, progress is real, controls are
 * tactile. Nothing decorative — someone is watching a job they are paying for.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import { StructureViewer } from "@/components/StructureViewer";
import { Button, Notice } from "@/components/ui";
import {
  AMBLER_CLASSES,
  EXAMPLE,
  type Hit,
  type Prediction,
  type Screen,
  lab,
  shortlistUrl,
  structureUrl,
} from "@/lib/lab";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

/** How often a running job is re-checked. Folds take minutes. */
const POLL_MS = 4000;

export default function Lab() {
  const [sequence, setSequence] = useState("");
  const [name, setName] = useState("");
  const [amblerClass, setAmblerClass] = useState("A");
  const [prediction, setPrediction] = useState<Prediction | null>(null);
  const [screen, setScreen] = useState<Screen | null>(null);
  const [history, setHistory] = useState<Prediction[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshHistory = useCallback(async () => {
    await Promise.resolve();
    try {
      setHistory(await lab.predictions());
    } catch {
      /* the history list is a convenience; never block the work on it */
    }
  }, []);

  useEffect(() => {
    const load = async () => {
      // Yields first, so nothing sets state synchronously inside the effect.
      await Promise.resolve();
      await refreshHistory();
    };
    void load();
  }, [refreshHistory]);

  // One poller for whichever job is in flight.
  useEffect(() => {
    const predictionRunning =
      prediction && prediction.state !== "complete" && prediction.state !== "failed";
    const screenRunning =
      screen && screen.state !== "complete" && screen.state !== "failed";
    if (!predictionRunning && !screenRunning) {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
      return;
    }
    timer.current = setInterval(() => {
      void (async () => {
        try {
          if (predictionRunning && prediction) {
            const next = await lab.prediction(prediction.id);
            setPrediction(next);
            if (next.state === "complete") void refreshHistory();
          }
          if (screenRunning && screen) {
            const all = await lab.screens(screen.prediction_id);
            const mine = all.find((s) => s.id === screen.id);
            if (mine) setScreen(mine);
          }
        } catch {
          /* a dropped poll is not a failure; the next one will catch up */
        }
      })();
    }, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
      timer.current = null;
    };
  }, [prediction, screen, refreshHistory]);

  const predict = useCallback(async () => {
    setError("");
    setScreen(null);
    setBusy(true);
    try {
      setPrediction(await lab.predict(sequence, name));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [sequence, name]);

  const runScreen = useCallback(async () => {
    if (!prediction) return;
    setError("");
    setBusy(true);
    try {
      setScreen(await lab.screen(prediction.id, amblerClass));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [prediction, amblerClass]);

  const open = useCallback(async (id: string) => {
    setError("");
    setScreen(null);
    const found = await lab.prediction(id);
    setPrediction(found);
    const screens = await lab.screens(id);
    if (screens.length) setScreen(screens[0]);
  }, []);

  const reset = useCallback(() => {
    setPrediction(null);
    setScreen(null);
    setSequence("");
    setName("");
    setError("");
  }, []);

  const folded = prediction?.state === "complete";

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-8 sm:px-8">
      <header className="mb-8">
        <h1 className="font-serif text-[clamp(1.6rem,3vw,2.2rem)] leading-tight text-ink">
          Fold a protein, then find what blocks it.
        </h1>
        <p className="mt-2 max-w-2xl text-[15px] leading-relaxed text-muted">
          Paste the amino-acid sequence of the enzyme making an infection
          drug-resistant. Antidote predicts its 3D structure, then ranks
          molecules that might jam it — a shortlist for your lab to test.
        </p>
      </header>

      {error ? (
        <div className="mb-5">
          <Notice tone="danger" title="That did not work">
            {error}
          </Notice>
        </div>
      ) : null}

      <Step n={1} title="Paste the sequence" done={Boolean(folded)}>
        {prediction ? (
          <PredictionPanel prediction={prediction} onReset={reset} />
        ) : (
          <div className="flex flex-col gap-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="What is it called? (optional — e.g. KPC-2 from our ICU isolate)"
              className="w-full rounded-md border border-line bg-panel px-3 py-2 text-[14px] text-ink outline-none transition-colors duration-[var(--dur-ui)] placeholder:text-faint focus:border-signal"
            />
            <textarea
              value={sequence}
              onChange={(e) => setSequence(e.target.value)}
              rows={7}
              spellCheck={false}
              placeholder="MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSR…"
              className="w-full resize-y rounded-md border border-line bg-panel px-3 py-2.5 font-mono text-[12.5px] leading-relaxed break-all text-ink outline-none transition-colors duration-[var(--dur-ui)] placeholder:text-faint focus:border-signal"
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="primary"
                onClick={() => void predict()}
                disabled={busy || !sequence.trim()}
              >
                {busy ? "Starting…" : "Predict the structure"}
              </Button>
              <button
                type="button"
                onClick={() => {
                  setSequence(EXAMPLE.sequence);
                  setName(EXAMPLE.name);
                }}
                className="text-[13px] text-signal underline decoration-line underline-offset-2 transition-colors duration-[var(--dur-ui)] hover:decoration-signal"
              >
                Use a real example
              </button>
              <span className="text-xs text-faint">
                {sequence.trim()
                  ? `${sequence.replace(/[^A-Za-z]/g, "").length} residues pasted`
                  : "FASTA headers and line breaks are fine"}
              </span>
            </div>
          </div>
        )}
      </Step>

      <AnimatePresence>
        {folded ? (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: DUR.panel, ease: EASE_OUT }}
          >
            <Step n={2} title="Find molecules that could block it" done={screen?.state === "complete"}>
              {screen ? (
                <ScreenPanel screen={screen} />
              ) : (
                <div className="flex flex-col gap-3">
                  <p className="max-w-2xl text-[14px] leading-relaxed text-muted">
                    Which family is this enzyme? It decides the known inhibitor
                    we include as a control — the molecule that tells you
                    whether the run worked at all.
                  </p>
                  <div className="flex flex-wrap items-center gap-3">
                    <select
                      value={amblerClass}
                      onChange={(e) => setAmblerClass(e.target.value)}
                      className="rounded-md border border-line bg-panel px-3 py-2 text-[14px] text-ink outline-none transition-colors duration-[var(--dur-ui)] focus:border-signal"
                    >
                      {AMBLER_CLASSES.map((c) => (
                        <option key={c.value} value={c.value}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="primary"
                      onClick={() => void runScreen()}
                      disabled={busy}
                    >
                      {busy ? "Starting…" : "Rank molecules"}
                    </Button>
                  </div>
                </div>
              )}
            </Step>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {history.length > 0 && !prediction ? (
        <section className="mt-10 border-t border-line pt-5">
          <h2 className="pb-2 font-mono text-[10px] uppercase tracking-[0.1em] text-faint">
            Earlier structures
          </h2>
          <ul className="flex flex-col gap-1.5">
            {history.map((p, i) => (
              <motion.li
                key={p.id}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: DUR.ui, delay: stagger(i, 0.04), ease: EASE_OUT }}
              >
                <button
                  type="button"
                  onClick={() => void open(p.id)}
                  className="flex w-full items-center justify-between gap-4 rounded-md border border-line bg-panel px-3 py-2 text-left transition-colors duration-[var(--dur-ui)] hover:border-signal hover:bg-signal-dim"
                >
                  <span className="min-w-0 truncate text-[14px] text-ink">{p.name}</span>
                  <span className="shrink-0 font-mono text-xs text-faint">
                    {p.length} aa · {p.state}
                  </span>
                </button>
              </motion.li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function Step({
  n,
  title,
  done,
  children,
}: {
  n: number;
  title: string;
  done?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 rounded-lg border border-line bg-panel">
      <header className="flex items-center gap-3 border-b border-line bg-wash px-4 py-2.5">
        <span
          className={`grid size-5 shrink-0 place-items-center rounded-full font-mono text-[11px] transition-colors duration-[var(--dur-panel)] ${
            done ? "bg-good text-white" : "bg-ink text-white"
          }`}
        >
          {done ? "✓" : n}
        </span>
        <h2 className="text-sm font-medium text-ink">{title}</h2>
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

function PredictionPanel({
  prediction,
  onReset,
}: {
  prediction: Prediction;
  onReset: () => void;
}) {
  if (prediction.state === "failed") {
    return (
      <div className="flex flex-col gap-3">
        <Notice tone="danger" title="The prediction failed">
          {prediction.error}
        </Notice>
        <div>
          <Button onClick={onReset}>Try another sequence</Button>
        </div>
      </div>
    );
  }

  if (prediction.state !== "complete") {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2.5 text-[14px] text-ink">
          <span className="size-2 animate-pulse rounded-full bg-signal" />
          Folding {prediction.name} — {prediction.length} residues.
        </div>
        <p className="text-[13px] text-muted">
          This takes a few minutes. You can leave the page open; it keeps
          running on the server either way.
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-5 md:grid-cols-[320px_1fr] md:items-start">
      <div className="overflow-hidden rounded-md border border-line bg-specimen">
        {prediction.has_structure ? (
          <StructureViewer
            url={structureUrl(prediction.id)}
            targetChains={["A"]}
            className="aspect-square w-full"
          />
        ) : (
          <div className="grid aspect-square place-items-center px-6 text-center">
            <p className="text-xs leading-relaxed text-white/70">
              The structure file could not be stored, so there is nothing to
              display. The scores are still the engine&rsquo;s.
            </p>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <div>
          <p className="text-[15px] text-ink">{prediction.name}</p>
          <p className="text-[13px] text-muted">{prediction.length} residues</p>
        </div>

        {!prediction.livemode ? (
          <p className="rounded-md border-l-[3px] border-caution bg-[#fdf8ef] px-3 py-2 text-xs leading-relaxed text-caution">
            Test mode — the engine returned a stand-in rather than folding your
            sequence, and nothing was charged. Switch to a live key for a real
            prediction.
          </p>
        ) : null}

        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-[13px]">
          <div>
            <dt className="text-[11px] text-faint">Structure confidence</dt>
            <dd className="font-mono text-ink">
              {prediction.structure_confidence?.toFixed(3) ?? "—"}
            </dd>
          </div>
          <div>
            <dt className="text-[11px] text-faint">pTM</dt>
            <dd className="font-mono text-ink">{prediction.ptm?.toFixed(3) ?? "—"}</dd>
          </div>
        </dl>

        <p className="text-xs leading-relaxed text-muted">
          A predicted structure is a hypothesis about shape. It is the input to
          finding a molecule that fits — not evidence that one will.
        </p>

        <div>
          <Button onClick={onReset}>Start another</Button>
        </div>
      </div>
    </div>
  );
}

function ScreenPanel({ screen }: { screen: Screen }) {
  if (screen.state === "failed") {
    return (
      <Notice tone="danger" title="The screen failed">
        {screen.error}
      </Notice>
    );
  }

  if (screen.state !== "complete") {
    return (
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2.5 text-[14px] text-ink">
          <span className="size-2 animate-pulse rounded-full bg-signal" />
          Docking {screen.submitted || "a library of"} molecules against the structure.
        </div>
        <p className="text-[13px] text-muted">A few minutes. It keeps running if you leave.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <ControlVerdict screen={screen} />

      {screen.submitted > screen.returned ? (
        <p className="text-xs leading-relaxed text-muted">
          {screen.submitted - screen.returned} of {screen.submitted} molecules
          were rejected by the engine before scoring and never appear below.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="bg-wash text-left">
              <th className="border border-line px-2.5 py-1.5 font-medium">#</th>
              <th className="border border-line px-2.5 py-1.5 font-medium">Molecule</th>
              <th
                className="border border-line px-2.5 py-1.5 text-right font-medium"
                title="Predicted by the model. Not a measurement."
              >
                Binding <span className="text-faint">(predicted)</span>
              </th>
              <th
                className="border border-line px-2.5 py-1.5 text-right font-medium"
                title="Measured in a laboratory and recorded in ChEMBL."
              >
                Ki <span className="text-faint">(measured)</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {screen.hits.map((hit, i) => (
              <Row key={hit.rank} hit={hit} index={i} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <a href={shortlistUrl(screen.id)} download>
          <Button variant="primary">Download the shortlist (CSV)</Button>
        </a>
        <span className="text-xs text-muted">
          Predicted and measured columns stay labelled in the file.
        </span>
      </div>

      <p className="border-t border-line pt-3 text-xs leading-relaxed text-muted">
        Computational predictions for a laboratory to test, not evidence of
        therapeutic effect. Nothing here says a molecule is safe — no part of
        this stack predicts toxicity.
      </p>
    </div>
  );
}

function ControlVerdict({ screen }: { screen: Screen }) {
  if (!screen.livemode) {
    return (
      <p className="rounded-md border-l-[3px] border-caution bg-[#fdf8ef] px-3 py-2 text-xs leading-relaxed text-caution">
        Test mode — the engine replaced the molecules we submitted with its own.
        These are <strong>not</strong> your library and the scores mean nothing.
        Nothing was charged.
      </p>
    );
  }
  if (screen.control_looks_right === false) {
    return (
      <p className="rounded-md border-l-[3px] border-danger bg-[#fdf3f2] px-3 py-2 text-xs leading-relaxed text-danger">
        <strong>{screen.control_name}</strong> is known to inhibit this enzyme,
        and it only ranked #{screen.control_rank}. This run is not reliable —
        treat the molecules below with suspicion rather than as findings.
      </p>
    );
  }
  if (screen.control_looks_right === true) {
    return (
      <p className="rounded-md border-l-[3px] border-good bg-[#f0f8f4] px-3 py-2 text-xs leading-relaxed text-good">
        <strong>{screen.control_name}</strong>, a known inhibitor of this
        enzyme, ranked #{screen.control_rank}. That is the evidence this screen
        is working.
      </p>
    );
  }
  // Live, but the control never came back. Measured on the live API: the
  // engine's structural filters can discard molecules silently, and the only
  // trace is a shortfall in the count. A ranked list with no control behind it
  // looks exactly like a result and is not one, so it is labelled before it is
  // shown.
  return (
    <p className="rounded-md border-l-[3px] border-danger bg-[#fdf3f2] px-3 py-2 text-xs leading-relaxed text-danger">
      {screen.control_name ? <strong>{screen.control_name}</strong> : "The positive control"}{" "}
      did not come back from this run, so there is nothing to check the ranking
      against. Treat the molecules below as unverified.
    </p>
  );
}

function Row({ hit, index }: { hit: Hit; index: number }) {
  const [open, setOpen] = useState(false);
  return (
    <motion.tr
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: DUR.ui, delay: stagger(index, 0.03, 0.3) }}
      onClick={() => setOpen((v) => !v)}
      className={`cursor-pointer transition-colors duration-[var(--dur-ui)] hover:bg-panel-hi ${
        hit.is_control ? "bg-signal-dim/40" : ""
      }`}
    >
      <td className="border border-line px-2.5 py-1.5 font-mono">{hit.rank}</td>
      <td className="border border-line px-2.5 py-1.5">
        <span className="block truncate text-ink">
          {hit.name || hit.external_id || "unnamed compound"}
        </span>
        {hit.is_control ? (
          <span className="font-mono text-[10px] uppercase tracking-wide text-signal">
            positive control
          </span>
        ) : null}
        {open ? (
          <span className="mt-1 block break-all font-mono text-[10px] text-muted">
            {hit.smiles}
          </span>
        ) : null}
      </td>
      <td className="border border-line px-2.5 py-1.5 text-right font-mono">
        {hit.binding_confidence === null ? "—" : hit.binding_confidence.toFixed(3)}
      </td>
      <td className="border border-line px-2.5 py-1.5 text-right font-mono text-muted">
        {hit.known_potency_nm === null ? "—" : `${hit.known_potency_nm} nM`}
      </td>
    </motion.tr>
  );
}

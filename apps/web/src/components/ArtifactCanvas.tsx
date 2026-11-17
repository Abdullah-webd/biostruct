"use client";

/**
 * The canvas: what a run produced, beside the conversation that asked for it.
 *
 * This is the pattern every agent interface converged on in 2026 — output is a
 * first-class object you can return to, not a wall of text scrolled past. For
 * this product it matters more than most: a predicted structure and a ranked
 * molecule table are the deliverables. A researcher takes them to a lab. They
 * cannot live in a transcript.
 *
 * Two judgements are built in and worth stating:
 *
 * - **The positive control's rank is the headline, not a footnote.** If a
 *   compound known to inhibit this enzyme class did not rank near the top, the
 *   run did not work, and reporting the other hits as findings would be
 *   actively misleading. The table says so before it says anything else.
 * - **Predicted and measured are never mixed.** A binding confidence is a
 *   prediction; a Ki from ChEMBL is a measurement someone made at a bench.
 *   They sit in different columns and the header says which is which.
 */

import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { StructureViewer } from "@/components/StructureViewer";
import { type Artifact, type MoleculeRow, structureUrl } from "@/lib/agent";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

export default function ArtifactCanvas({
  sessionId,
  artifact,
  onClose,
}: {
  sessionId: string;
  artifact: Artifact;
  onClose: () => void;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 16 }}
      transition={{ duration: DUR.panel, ease: EASE_OUT }}
      className="flex h-full min-h-0 w-full flex-col border-l border-line bg-panel"
      aria-label="Result"
    >
      <header className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-faint">
            {artifact.kind.replace("_", " ")}
          </p>
          <h2 className="mt-0.5 truncate text-sm text-ink">{artifact.title}</h2>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close this result"
          className="shrink-0 text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink"
        >
          ✕
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {artifact.kind === "structure" ? (
          <StructurePanel sessionId={sessionId} artifact={artifact} />
        ) : artifact.kind === "molecule_set" ? (
          <MoleculePanel artifact={artifact} />
        ) : (
          <GenericPanel artifact={artifact} />
        )}
      </div>
    </motion.section>
  );
}

function StructurePanel({
  sessionId,
  artifact,
}: {
  sessionId: string;
  artifact: Artifact;
}) {
  const samples = (artifact.payload.samples as Record<string, unknown>[]) ?? [];
  const [sample, setSample] = useState(0);
  const current = samples[sample] ?? {};
  const confidence = current.structure_confidence as number | undefined;
  const stored = Boolean(current.structure_key);
  const livemode = artifact.payload.livemode === true;

  return (
    <div className="flex flex-col">
      {stored ? (
        <StructureViewer
          url={structureUrl(sessionId, artifact.id, sample)}
          targetChains={["A"]}
          className="aspect-square w-full"
        />
      ) : (
        <div className="grid aspect-square w-full place-items-center bg-specimen px-6 text-center">
          <p className="text-xs leading-relaxed text-white/70">
            The structure file could not be stored, so there is nothing to
            display. The confidence scores below are still the engine&rsquo;s.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-3 p-4">
        {!livemode ? (
          <p className="rounded-md border-l-[3px] border-caution bg-[#fdf8ef] px-3 py-2 text-xs leading-relaxed text-caution">
            Test mode. This structure is synthetic — the engine returned a
            stand-in rather than folding your sequence. Nothing was charged.
          </p>
        ) : null}

        {samples.length > 1 ? (
          <div className="flex flex-wrap gap-1.5">
            {samples.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setSample(i)}
                className={`rounded-sm border px-2 py-1 font-mono text-[11px] transition-colors duration-[var(--dur-ui)] ${
                  i === sample
                    ? "border-signal bg-signal-dim text-ink"
                    : "border-line text-muted hover:border-faint hover:text-ink"
                }`}
              >
                sample {i + 1}
              </button>
            ))}
          </div>
        ) : null}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
          <Metric
            label="Structure confidence"
            value={confidence}
            hint="How sure the model is about this fold, 0–1. Predicted, not measured."
          />
          <Metric label="pTM" value={current.ptm as number | undefined} />
          <Metric label="ipTM" value={current.iptm as number | undefined} />
          <Metric
            label="Residues"
            value={artifact.payload.length as number | undefined}
            raw
          />
        </dl>

        <p className="border-t border-line pt-3 text-xs leading-relaxed text-muted">
          A predicted structure is a hypothesis about shape. It is the input to
          finding a molecule that fits — not evidence that one will.
        </p>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  hint,
  raw = false,
}: {
  label: string;
  value: number | undefined;
  hint?: string;
  raw?: boolean;
}) {
  return (
    <div>
      <dt className="text-[11px] text-faint" title={hint}>
        {label}
      </dt>
      <dd className="font-mono text-ink">
        {value === undefined || value === null
          ? "—"
          : raw
            ? value
            : value.toFixed(3)}
      </dd>
    </div>
  );
}

function MoleculePanel({ artifact }: { artifact: Artifact }) {
  const rows = (artifact.payload.results as MoleculeRow[]) ?? [];
  const controlRank = artifact.payload.control_rank as number | null;
  const requested = artifact.payload.requested as number | undefined;
  const returned = artifact.payload.returned as number | undefined;
  const livemode = artifact.payload.livemode === true;
  const [open, setOpen] = useState<string>("");

  // The run judges itself. A control that did not rank highly means the screen
  // is not telling us anything, and that has to be said before the hits are.
  const controlVerdict =
    controlRank === null || controlRank === undefined
      ? null
      : controlRank <= Math.max(3, Math.ceil(rows.length * 0.1))
        ? "good"
        : "bad";

  return (
    <div className="flex flex-col gap-3 p-4">
      {!livemode ? (
        <p className="rounded-md border-l-[3px] border-caution bg-[#fdf8ef] px-3 py-2 text-xs leading-relaxed text-caution">
          Test mode. The engine replaced the molecules we submitted with its
          own, so these are <strong>not</strong> your library and the scores
          mean nothing. Nothing was charged.
        </p>
      ) : controlVerdict === "bad" ? (
        <p className="rounded-md border-l-[3px] border-danger bg-[#fdf3f2] px-3 py-2 text-xs leading-relaxed text-danger">
          The positive control ranked #{controlRank} of {rows.length}. A
          compound known to inhibit this enzyme should be near the top, so this
          run is not reliable — treat the other hits with suspicion rather than
          as findings.
        </p>
      ) : controlVerdict === "good" ? (
        <p className="rounded-md border-l-[3px] border-good bg-[#f0f8f4] px-3 py-2 text-xs leading-relaxed text-good">
          The positive control ranked #{controlRank}. A compound known to
          inhibit this enzyme came out near the top, which is the evidence that
          this screen is working.
        </p>
      ) : null}

      {requested && returned && returned < requested ? (
        <p className="text-xs text-muted">
          {requested - returned} of {requested} molecules were rejected by the
          structural filters and never scored.
        </p>
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="bg-wash text-left">
              <th className="border border-line px-2 py-1.5 font-medium">#</th>
              <th className="border border-line px-2 py-1.5 font-medium">Molecule</th>
              <th
                className="border border-line px-2 py-1.5 text-right font-medium"
                title="Predicted by the model. Not a measurement."
              >
                Binding (pred.)
              </th>
              <th
                className="border border-line px-2 py-1.5 text-right font-medium"
                title="Measured in a laboratory and recorded in ChEMBL."
              >
                Ki (measured)
              </th>
            </tr>
          </thead>
          <tbody>
            <AnimatePresence initial={false}>
              {rows.map((row, i) => (
                <motion.tr
                  key={row.result_id || i}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: DUR.ui, delay: stagger(i, 0.015, 0.3) }}
                  onClick={() => setOpen(open === row.result_id ? "" : row.result_id)}
                  className={`cursor-pointer transition-colors duration-[var(--dur-ui)] hover:bg-panel-hi ${
                    row.is_control ? "bg-signal-dim/40" : ""
                  }`}
                >
                  <td className="border border-line px-2 py-1.5 font-mono">{row.rank}</td>
                  <td className="border border-line px-2 py-1.5">
                    <span className="block truncate text-ink">
                      {row.name || row.external_id || "unnamed"}
                    </span>
                    {row.is_control ? (
                      <span className="font-mono text-[10px] uppercase tracking-wide text-signal">
                        positive control
                      </span>
                    ) : null}
                    {open === row.result_id ? (
                      <span className="mt-1 block break-all font-mono text-[10px] text-muted">
                        {row.smiles}
                      </span>
                    ) : null}
                  </td>
                  <td className="border border-line px-2 py-1.5 text-right font-mono">
                    {row.binding_confidence === null
                      ? "—"
                      : row.binding_confidence.toFixed(3)}
                  </td>
                  <td className="border border-line px-2 py-1.5 text-right font-mono text-muted">
                    {row.known_potency_nm === null || row.known_potency_nm === undefined
                      ? "—"
                      : `${row.known_potency_nm} nM`}
                  </td>
                </motion.tr>
              ))}
            </AnimatePresence>
          </tbody>
        </table>
      </div>

      <p className="border-t border-line pt-3 text-xs leading-relaxed text-muted">
        These are computational predictions for a laboratory to test, not
        evidence of therapeutic effect. Nothing here says a molecule is safe —
        no part of this stack predicts toxicity.
      </p>
    </div>
  );
}

function GenericPanel({ artifact }: { artifact: Artifact }) {
  const entries = Object.entries(artifact.payload).filter(
    ([, v]) => typeof v === "string" || typeof v === "number",
  );
  return (
    <dl className="flex flex-col gap-2 p-4 text-[13px]">
      {entries.length === 0 ? (
        <p className="text-xs text-muted">Nothing further to show.</p>
      ) : (
        entries.map(([key, value]) => (
          <div key={key} className="flex justify-between gap-4 border-b border-line pb-1.5">
            <dt className="text-muted">{key.replace(/_/g, " ")}</dt>
            <dd className="text-right font-mono text-ink">{String(value)}</dd>
          </div>
        ))
      )}
    </dl>
  );
}

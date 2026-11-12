"use client";

/**
 * Results and ranking. The place where a cheerful UI would actively mislead, so
 * the honesty is designed in rather than set in grey 11px type.
 *
 * Scores are normalised *within a run*, so the best of ten hopeless designs
 * still scores near 1.0. A real run against KPC-2 came back with predicted
 * binding confidences around 2e-6. The absolute number therefore gets the same
 * visual weight as the rank, and a run where nothing is predicted to bind says
 * so across the top instead of presenting a tidy ordered list.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  BAND_TONE,
  endpoints,
  formatMetric,
  type DesignRun,
  type RankedCandidate,
  type Ranking,
  VERDICT_LABEL,
} from "@/lib/api";
import { Button, Empty, Notice, Panel, Readout, SkeletonRows } from "@/components/ui";
import { StructureViewer } from "@/components/StructureViewer";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

const STATUS_LABEL: Record<DesignRun["status"], string> = {
  pending: "queued",
  running: "generating designs",
  harvesting: "collecting results",
  succeeded: "finishing",
  complete: "complete",
  failed: "failed",
  stopped: "stopped",
  submit_failed: "never started",
  harvest_partial: "complete, with missing structures",
};

export default function ResultsStep({
  projectId,
  runs,
  activeRunId,
  onSelectRun,
  onRunUpdated,
  isRunActive,
}: {
  projectId: string;
  runs: DesignRun[];
  activeRunId: string | null;
  onSelectRun: (id: string) => void;
  onRunUpdated: () => Promise<void>;
  isRunActive: boolean;
}) {
  // Live updates arriving over SSE. The run itself is derived from props, so
  // there is no second copy to keep in step.
  const [liveRun, setLiveRun] = useState<DesignRun | null>(null);
  const [ranking, setRanking] = useState<Ranking | null>(null);
  const [selected, setSelected] = useState<RankedCandidate | null>(null);
  const [error, setError] = useState("");
  const [loadingRanking, setLoadingRanking] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  const fromProps = runs.find((r) => r.id === activeRunId) ?? null;
  const run = liveRun?.id === activeRunId ? liveRun : fromProps;

  const loadRanking = useCallback(
    async (runId: string) => {
      // Yield first, so the flag is not set synchronously inside an effect.
      await Promise.resolve();
      setLoadingRanking(true);
      try {
        const result = await endpoints.ranking(projectId, runId);
        setRanking(result);
        setSelected(result.items[0] ?? null);
      } catch (e) {
        setError(e instanceof ApiError ? e.message_for_user : "Could not load the ranking");
      } finally {
        setLoadingRanking(false);
      }
    },
    [projectId],
  );

  /* Live progress over server-sent events. The server sends current state
     first and the stream carries only deltas, so a dropped connection makes
     progress slower rather than wrong. */
  useEffect(() => {
    esRef.current?.close();
    esRef.current = null;
    if (!activeRunId || !isRunActive) return;

    const source = new EventSource(endpoints.runEventsUrl(projectId, activeRunId));
    esRef.current = source;

    source.addEventListener("state", (event) => {
      try {
        const next = JSON.parse((event as MessageEvent).data) as DesignRun;
        setLiveRun(next);
      } catch {
        /* a malformed frame is not worth tearing the stream down for */
      }
    });
    source.addEventListener("complete", () => {
      source.close();
      void onRunUpdated();
      void loadRanking(activeRunId);
    });
    source.onerror = () => {
      // EventSource reconnects on its own; closing here would stop that.
    };

    return () => {
      source.close();
      esRef.current = null;
    };
  }, [activeRunId, isRunActive, projectId, loadRanking, onRunUpdated]);

  const rankableRunId =
    run && !isRunActive && (run.status === "complete" || run.status === "harvest_partial")
      ? run.id
      : null;

  useEffect(() => {
    if (!rankableRunId || ranking || loadingRanking) return;
    // loadRanking yields before setting its loading flag, so this is not a
    // synchronous setState; the rule just cannot follow it across the call.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadRanking(rankableRunId);
  }, [rankableRunId, ranking, loadingRanking, loadRanking]);

  if (runs.length === 0) {
    return (
      <div className="p-4 sm:p-6">
        <Empty title="No runs yet">Submit a design run in step 04.</Empty>
      </div>
    );
  }

  const progress = run?.progress;
  const pct =
    progress?.total_to_generate && progress.total_to_generate > 0
      ? Math.min(100, Math.round((progress.generated / progress.total_to_generate) * 100))
      : null;

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[10px] uppercase tracking-widest text-signal">step 05</p>
          <h2 className="display mt-1 text-2xl">Candidates</h2>
        </div>
        {runs.length > 1 && (
          <label className="text-xs text-muted">
            <span className="mr-2 uppercase tracking-widest">run</span>
            <select
              value={activeRunId ?? ""}
              onChange={(e) => onSelectRun(e.target.value)}
              className="border border-line bg-ground px-2 py-1.5 text-sm text-ink focus:border-signal"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {new Date(r.created_at).toLocaleString()} · {r.num_proteins} designs ·{" "}
                  {STATUS_LABEL[r.status]}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      {error && <Notice tone="danger" title={error} />}

      {run && (
        <Panel
          title={`run · ${STATUS_LABEL[run.status]}`}
          aside={
            isRunActive ? (
              <span className="flex items-center gap-2 text-[11px] text-signal">
                <span className="block size-1.5 animate-pulse rounded-full bg-signal" />
                live
              </span>
            ) : null
          }
        >
          <div className="p-4">
            {isRunActive && (
              <>
                <div className="mb-3 h-1 w-full overflow-hidden bg-ground">
                  {/* Tracks the actual job rather than animating hopefully. */}
                  <motion.div
                    className="h-full bg-signal"
                    initial={{ width: 0 }}
                    animate={{ width: `${pct ?? 0}%` }}
                    transition={{ duration: DUR.panel, ease: EASE_OUT }}
                  />
                </div>
                <p className="tnum text-xs text-muted">
                  {progress?.generated ?? 0} of {progress?.total_to_generate ?? "?"}{" "}
                  designs generated
                  {progress && progress.harvested > 0 &&
                    ` · ${progress.harvested} collected`}
                </p>
              </>
            )}

            <div className="mt-2 grid gap-x-8 gap-y-0 divide-line sm:grid-cols-2">
              <Readout label="Designs requested" value={run.num_proteins} />
              <Readout label="Modality" value={run.modality} />
              <Readout
                label="Epitope"
                value={`${run.epitope_pdb_id} · ${run.epitope_chain_id}`}
              />
              <Readout
                label="Cost"
                value={run.cost_actual_usd ?? run.cost_estimate_usd}
                unit="USD"
              />
            </div>

            {!run.livemode && (
              <div className="mt-4">
                <Notice tone="caution" title="Test-mode run — these results are synthetic">
                  The metrics below were not produced by the real model and the
                  structure is a placeholder. Useful for checking the workflow.
                  Not findings, and not exportable as such.
                </Notice>
              </div>
            )}

            {run.status === "harvest_partial" && (
              <div className="mt-4">
                <Notice tone="caution" title="Some structures could not be saved">
                  {run.error}
                </Notice>
              </div>
            )}

            {(run.status === "failed" || run.status === "submit_failed") && (
              <div className="mt-4">
                <Notice tone="danger" title="This run did not produce results">
                  {run.error} Your budget was not charged.
                </Notice>
              </div>
            )}
          </div>
        </Panel>
      )}

      {loadingRanking && <SkeletonRows rows={4} />}

      {/* The honest headline: stated before the ordered list, not after it. */}
      {ranking && ranking.assessment && (
        <Notice
          tone={ranking.nothing_predicted_to_bind ? "caution" : "signal"}
          title={
            ranking.nothing_predicted_to_bind
              ? "Nothing in this run is predicted to bind"
              : "Run assessment"
          }
        >
          {ranking.assessment}
        </Notice>
      )}

      {ranking && ranking.items.length > 0 && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,560px)]">
          <Panel title={`${ranking.total} ranked`}>
            <ul className="divide-y divide-line">
              {ranking.items.map((item, index) => {
                const active = selected?.candidate.id === item.candidate.id;
                return (
                  <motion.li
                    key={item.candidate.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: DUR.ui, delay: stagger(index), ease: EASE_OUT }}
                  >
                    <button
                      type="button"
                      onClick={() => setSelected(item)}
                      aria-pressed={active}
                      className={`w-full px-4 py-3 text-left transition-colors duration-[var(--dur-ui)] ${
                        active ? "bg-panel-hi" : "hover:bg-panel-hi"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="flex items-center gap-3">
                          <span className="tnum text-sm text-faint">
                            {String(item.rank).padStart(2, "0")}
                          </span>
                          <span className={`text-xs ${BAND_TONE[item.binding_band] ?? "text-muted"}`}>
                            {VERDICT_LABEL[item.verdict]}
                          </span>
                        </span>
                        <span className="tnum text-xs text-muted">
                          binding{" "}
                          <span className={BAND_TONE[item.binding_band] ?? "text-ink"}>
                            {formatMetric(item.binding_confidence)}
                          </span>
                        </span>
                      </div>

                      {/* The relative score, visibly labelled as relative. */}
                      <div className="mt-2 flex items-center gap-2">
                        <div className="h-0.5 flex-1 overflow-hidden bg-ground">
                          <motion.div
                            className="h-full bg-faint"
                            initial={{ width: 0 }}
                            animate={{ width: `${Number(item.score) * 100}%` }}
                            transition={{
                              duration: DUR.reveal,
                              delay: stagger(index),
                              ease: EASE_OUT,
                            }}
                          />
                        </div>
                        <span className="tnum text-[10px] text-faint">
                          rel. {item.score}
                        </span>
                      </div>
                    </button>
                  </motion.li>
                );
              })}
            </ul>
          </Panel>

          <AnimatePresence mode="wait">
            {selected && (
              <motion.div
                key={selected.candidate.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: DUR.panel, ease: EASE_OUT }}
                className="space-y-4 xl:sticky xl:top-[65px] xl:self-start"
              >
                <Panel
                  title={`rank ${selected.rank} of ${ranking.total}`}
                  aside={
                    <span
                      className={`text-[11px] ${BAND_TONE[selected.binding_band] ?? "text-muted"}`}
                    >
                      {selected.binding_band} band
                    </span>
                  }
                >
                  <div className="relative aspect-[4/3]">
                    <StructureViewer
                      url={endpoints.structureUrl(projectId, selected.candidate.id)}
                      binderChains={selected.candidate.binder_chain_ids}
                      targetChains={selected.candidate.target_chain_ids}
                      epitopeIndices={run?.epitope_design_indices ?? []}
                    />
                  </div>

                  <div className="divide-y divide-line p-4">
                    <Readout
                      label="Predicted binding confidence"
                      value={formatMetric(selected.binding_confidence)}
                      tone={BAND_TONE[selected.binding_band] ?? "text-ink"}
                    />
                    <Readout
                      label="Interface confidence (ipTM)"
                      value={formatMetric(selected.candidate.metrics.iptm)}
                    />
                    <Readout
                      label="Interface error (PAE)"
                      value={formatMetric(selected.candidate.metrics.min_interaction_pae)}
                      title="Lower is better for this metric."
                    />
                    <Readout
                      label="Fold confidence"
                      value={formatMetric(selected.candidate.metrics.structure_confidence)}
                    />
                    <Readout
                      label="Relative score (this run only)"
                      value={selected.score}
                      tone="text-muted"
                    />
                  </div>
                </Panel>

                <Panel title="How this score was reached">
                  <ul className="divide-y divide-line">
                    {selected.contributions.map((c) => (
                      <li key={c.metric} className="px-4 py-2.5">
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="text-xs text-muted">
                            {c.label}
                            {c.is_constant && (
                              <span className="ml-2 text-[10px] text-faint">
                                identical across the run
                              </span>
                            )}
                          </span>
                          <span className="tnum text-xs text-ink">
                            {formatMetric(c.raw)}
                          </span>
                        </div>
                        <div className="mt-1.5 flex items-center gap-2">
                          <div className="h-0.5 flex-1 overflow-hidden bg-ground">
                            <motion.div
                              className="h-full bg-signal/60"
                              initial={{ width: 0 }}
                              animate={{ width: `${Number(c.normalised) * 100}%` }}
                              transition={{ duration: DUR.reveal, ease: EASE_OUT }}
                            />
                          </div>
                          <span className="tnum text-[10px] text-faint">
                            {c.normalised} × {c.weight} = {c.contribution}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                  {selected.weight_redistributed && (
                    <p className="border-t border-line px-4 py-2.5 text-[11px] leading-relaxed text-muted">
                      {selected.missing_metrics.join(", ")} was not reported, so its
                      weight was shared across the metrics that were present rather
                      than counted as zero.
                    </p>
                  )}
                </Panel>

                <Panel title="In words">
                  <p className="p-4 text-xs leading-relaxed text-muted">
                    {selected.explanation}
                  </p>
                </Panel>

                <Panel title="Designed sequence">
                  <div className="p-4">
                    {selected.candidate.binder_chain_ids.length > 0 && (
                      <p className="mb-2 text-[11px] text-faint">
                        chain {selected.candidate.binder_chain_ids.join(", ")} ·{" "}
                        <span className="tnum">
                          {selected.candidate.sequences[0]?.length ?? 0} aa
                        </span>
                      </p>
                    )}
                    <p className="tnum max-h-40 overflow-y-auto break-all text-[11px] leading-relaxed text-binder">
                      {selected.candidate.sequences[0] ?? "—"}
                    </p>
                  </div>
                </Panel>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {ranking && ranking.items.length === 0 && !loadingRanking && (
        <Empty title="No candidates to rank">
          {ranking.excluded.length > 0
            ? ranking.excluded[0].reason
            : "This run produced no scoreable results."}
        </Empty>
      )}

      {ranking && (
        <p className="max-w-3xl text-[11px] leading-relaxed text-faint">
          {ranking.caveat}
        </p>
      )}

      {run && isRunActive && (
        <Button
          variant="danger"
          onClick={async () => {
            await endpoints.stopRun(projectId, run.id).catch(() => {});
            await onRunUpdated();
          }}
        >
          Stop this run
        </Button>
      )}
    </div>
  );
}

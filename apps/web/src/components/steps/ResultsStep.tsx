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
import {
  Button,
  Empty,
  Notice,
  Panel,
  Readout,
  SkeletonRows,
  StepHeader,
  Term,
} from "@/components/ui";
import { StructureViewer } from "@/components/StructureViewer";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import { BAND_WORDS, METRIC_WORDS, STEP_INTRO } from "@/lib/words";

const STATUS_LABEL: Record<DesignRun["status"], string> = {
  pending: "waiting to start",
  running: "designing proteins",
  harvesting: "collecting the results",
  succeeded: "finishing up",
  complete: "finished",
  failed: "failed",
  stopped: "stopped",
  submit_failed: "never started",
  harvest_partial: "finished, but some 3D shapes are missing",
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
      <div>
        <Empty title="Nothing has been designed yet">
          Go back to step 3 and start a run.
        </Empty>
      </div>
    );
  }

  const progress = run?.progress;
  const pct =
    progress?.total_to_generate && progress.total_to_generate > 0
      ? Math.min(100, Math.round((progress.generated / progress.total_to_generate) * 100))
      : null;

  return (
    <div className="space-y-6">
      <StepHeader
        title="What came back"
        aside={
          runs.length > 1 ? (
          <label className="text-[13px] text-muted">
            <span className="mr-2">Showing</span>
            <select
              value={activeRunId ?? ""}
              onChange={(e) => onSelectRun(e.target.value)}
              className="rounded-md border border-line bg-ground px-2 py-1.5 text-sm text-ink focus:border-signal"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {new Date(r.created_at).toLocaleString()} — {r.num_proteins}{" "}
                  designs, {STATUS_LABEL[r.status]}
                </option>
              ))}
            </select>
          </label>
          ) : undefined
        }
      >
        {STEP_INTRO.results}
      </StepHeader>

      {error && <Notice tone="danger" title={error} />}

      {run && (
        <Panel
          title={`This run: ${STATUS_LABEL[run.status]}`}
          aside={
            isRunActive ? (
              <span className="flex items-center gap-2 text-[13px] text-signal">
                <span className="block size-1.5 animate-pulse rounded-full bg-signal" />
                running now
              </span>
            ) : null
          }
        >
          <div className="p-4">
            {isRunActive && (
              <>
                <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-panel-hi">
                  {/* Tracks the actual job rather than animating hopefully. */}
                  <motion.div
                    className="h-full rounded-full bg-signal"
                    initial={{ width: 0 }}
                    animate={{ width: `${pct ?? 0}%` }}
                    transition={{ duration: DUR.panel, ease: EASE_OUT }}
                  />
                </div>
                <p className="text-[13px] text-muted">
                  <span className="tnum">{progress?.generated ?? 0}</span> of{" "}
                  <span className="tnum">{progress?.total_to_generate ?? "?"}</span>{" "}
                  proteins designed so far
                  {progress && progress.harvested > 0 && (
                    <>
                      {" · "}
                      <span className="tnum">{progress.harvested}</span> collected
                    </>
                  )}
                </p>
              </>
            )}

            <div className="mt-2 grid gap-x-8 gap-y-0 divide-line sm:grid-cols-2">
              <Readout label="Proteins designed" value={run.num_proteins} />
              <Readout label="Shape designed" value={run.modality} />
              <Readout
                label="Attacked on"
                value={`${run.epitope_pdb_id.toUpperCase()}, chain ${run.epitope_chain_id}`}
              />
              <Readout
                label="Spent"
                value={run.cost_actual_usd ?? run.cost_estimate_usd}
                unit="USD"
              />
            </div>

            {!run.livemode && (
              <div className="mt-4">
                <Notice tone="caution" title="This was a practice run — the numbers below are made up">
                  No real model produced these, and the 3D shape is a stand-in. It
                  shows you how the tool works. Do not read anything into the
                  results, and do not take them to a lab.
                </Notice>
              </div>
            )}

            {run.status === "harvest_partial" && (
              <div className="mt-4">
                <Notice tone="caution" title="Some 3D shapes could not be saved">
                  {run.error}
                </Notice>
              </div>
            )}

            {(run.status === "failed" || run.status === "submit_failed") && (
              <div className="mt-4">
                <Notice tone="danger" title="This run produced nothing">
                  {run.error} You were not charged.
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
              ? "Nothing in this batch is predicted to stick"
              : "What this batch amounts to"
          }
        >
          {ranking.assessment}
        </Notice>
      )}

      {ranking && ranking.items.length > 0 && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,560px)]">
          <Panel
            title={
              ranking.total === 1
                ? "1 designed protein, best first"
                : `${ranking.total} designed proteins, best first`
            }
          >
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
                            {item.rank}
                          </span>
                          <span className={`text-xs ${BAND_TONE[item.binding_band] ?? "text-muted"}`}>
                            {VERDICT_LABEL[item.verdict]}
                          </span>
                        </span>
                        <span className="text-[13px] text-muted">
                          <span className={BAND_TONE[item.binding_band] ?? "text-ink"}>
                            {BAND_WORDS[item.binding_band] ?? item.binding_band}
                          </span>{" "}
                          <span className="tnum text-faint">
                            ({formatMetric(item.binding_confidence)})
                          </span>
                        </span>
                      </div>

                      {/* The relative score, visibly labelled as relative. */}
                      <div className="mt-2 flex items-center gap-2">
                        <div className="h-1 flex-1 overflow-hidden rounded-full bg-panel-hi">
                          <motion.div
                            className="h-full rounded-full bg-faint"
                            initial={{ width: 0 }}
                            animate={{ width: `${Number(item.score) * 100}%` }}
                            transition={{
                              duration: DUR.reveal,
                              delay: stagger(index),
                              ease: EASE_OUT,
                            }}
                          />
                        </div>
                        <span className="text-[12px] text-faint">
                          <span className="tnum">{item.score}</span> within this batch
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
                  title={`No. ${selected.rank} of ${ranking.total}`}
                  aside={
                    <span
                      className={`text-[13px] ${BAND_TONE[selected.binding_band] ?? "text-muted"}`}
                    >
                      {BAND_WORDS[selected.binding_band] ?? selected.binding_band}
                    </span>
                  }
                >
                  <div className="specimen relative aspect-[4/3] overflow-hidden">
                    <StructureViewer
                      url={endpoints.structureUrl(projectId, selected.candidate.id)}
                      binderChains={selected.candidate.binder_chain_ids}
                      targetChains={selected.candidate.target_chain_ids}
                      epitopeIndices={run?.epitope_design_indices ?? []}
                    />
                  </div>

                  <div className="divide-y divide-line p-4">
                    <Readout
                      label={<Term k="bindingConfidence" />}
                      value={formatMetric(selected.binding_confidence)}
                      tone={BAND_TONE[selected.binding_band] ?? "text-ink"}
                    />
                    <Readout
                      label={<Term k="iptm" />}
                      value={formatMetric(selected.candidate.metrics.iptm)}
                      title="Higher is better."
                    />
                    <Readout
                      label={<Term k="pae" />}
                      value={formatMetric(selected.candidate.metrics.min_interaction_pae)}
                      title="Lower is better for this one."
                    />
                    <Readout
                      label={<Term k="structureConfidence" />}
                      value={formatMetric(selected.candidate.metrics.structure_confidence)}
                      title="Higher is better."
                    />
                    <Readout
                      label={<Term k="relativeScore" />}
                      value={selected.score}
                      tone="text-muted"
                    />
                  </div>
                </Panel>

                <Panel title="Why it ranked where it did">
                  <ul className="divide-y divide-line">
                    {selected.contributions.map((c) => (
                      <li key={c.metric} className="px-4 py-2.5">
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="text-[13px] text-muted">
                            {c.label}
                            {c.is_constant && (
                              <span className="ml-2 text-[12px] text-faint">
                                the same for every design here, so it changed nothing
                              </span>
                            )}
                          </span>
                          <span className="tnum text-[13px] text-ink">
                            {formatMetric(c.raw)}
                          </span>
                        </div>
                        <div className="mt-1.5 flex items-center gap-2">
                          <div className="h-1 flex-1 overflow-hidden rounded-full bg-panel-hi">
                            <motion.div
                              className="h-full rounded-full bg-signal/60"
                              initial={{ width: 0 }}
                              animate={{ width: `${Number(c.normalised) * 100}%` }}
                              transition={{ duration: DUR.reveal, ease: EASE_OUT }}
                            />
                          </div>
                          <span className="tnum text-[12px] text-faint">
                            {c.normalised} × {c.weight} = {c.contribution}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                  {selected.weight_redistributed && (
                    <p className="border-t border-line px-4 py-2.5 text-[13px] leading-relaxed text-muted">
                      The model did not report{" "}
                      {selected.missing_metrics
                        .map((m) => METRIC_WORDS[m] ?? m)
                        .join(", ")}
                      , so that share of the score was spread across the
                      measurements we did get, rather than counted as a zero it did
                      not earn.
                    </p>
                  )}
                </Panel>

                <Panel title="In plain terms">
                  <p className="p-4 text-sm leading-relaxed text-ink">
                    {selected.explanation}
                  </p>
                </Panel>

                <Panel title="The protein we designed">
                  <div className="p-4">
                    {selected.candidate.binder_chain_ids.length > 0 && (
                      <p className="mb-2 text-[13px] text-muted">
                        <span className="tnum">
                          {selected.candidate.sequences[0]?.length ?? 0}
                        </span>{" "}
                        building blocks long. This is the recipe a lab would
                        synthesise.
                      </p>
                    )}
                    <p className="tnum max-h-40 overflow-y-auto break-all rounded-md border border-line bg-wash p-3 text-[13px] leading-relaxed text-ink">
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
        <Empty title="Nothing here could be scored">
          {ranking.excluded.length > 0
            ? ranking.excluded[0].reason
            : "This run came back without anything we could measure."}
        </Empty>
      )}

      {ranking && (
        <p className="max-w-3xl text-[13px] leading-relaxed text-muted">
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

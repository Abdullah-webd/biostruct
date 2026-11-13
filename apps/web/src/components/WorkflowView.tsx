"use client";

/**
 * The workflow.
 *
 * v1 used a left rail of seven items, four of which were real steps and three
 * decoration, labelled with the system's words (Target, Epitope, Design). v2
 * shows the four real steps as one horizontal line across the top: it is a
 * sequence, so numbering is honest here, and a line reads as progress in a way
 * a vertical list of nouns does not. Budget sits in the same bar, because
 * spending is a first-class concern but not a step.
 */

import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";

import {
  ACTIVE_STATUSES,
  ApiError,
  endpoints,
  formatMoney,
  type DesignRun,
  type Project,
  type Target,
} from "@/lib/api";
import { Notice, Spinner } from "@/components/ui";
import { DUR, EASE_OUT } from "@/lib/motion";
import { STEPS } from "@/lib/words";
import TargetStep from "@/components/steps/TargetStep";
import EpitopeStep from "@/components/steps/EpitopeStep";
import DesignStep from "@/components/steps/DesignStep";
import ResultsStep from "@/components/steps/ResultsStep";

export type StepId = (typeof STEPS)[number]["key"];

export default function WorkflowView({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [runs, setRuns] = useState<DesignRun[]>([]);
  const [activeTargetId, setActiveTargetId] = useState<string | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [step, setStep] = useState<StepId>("target");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    // Yield before touching state, so nothing is set synchronously inside the
    // effect that calls this.
    await Promise.resolve();
    try {
      const [p, t, r] = await Promise.all([
        endpoints.project(projectId),
        endpoints.targets(projectId),
        endpoints.runs(projectId),
      ]);
      setProject(p);
      setTargets(t.items);
      setRuns(r.items);
      setActiveTargetId((current) => current ?? t.items[0]?.id ?? null);
      setActiveRunId((current) => current ?? r.items[0]?.id ?? null);
      return { targets: t.items, runs: r.items };
    } catch (e) {
      setError(e instanceof ApiError ? e.message_for_user : "Could not load the project");
      return null;
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    // The rule cannot see across the function boundary: refresh() yields
    // before it touches state, and this callback runs after the fetch
    // resolves, so nothing here is set synchronously during the effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh().then((data) => {
      // Land on the step the project is actually at, rather than making
      // someone click through what they have already done.
      if (!data) return;
      if (data.runs.length > 0) setStep("results");
      else if (data.targets.some((t) => t.epitope)) setStep("design");
      else if (data.targets.length > 0) setStep("epitope");
    });
  }, [refresh]);

  const activeTarget = targets.find((t) => t.id === activeTargetId) ?? null;
  const activeRun = runs.find((r) => r.id === activeRunId) ?? null;

  const done: Record<StepId, boolean> = {
    target: targets.length > 0,
    epitope: Boolean(activeTarget?.epitope),
    design: runs.length > 0,
    results: runs.some((r) => r.status === "complete" || r.status === "harvest_partial"),
  };

  const reachable: Record<StepId, boolean> = {
    target: true,
    epitope: done.target,
    design: done.epitope,
    results: done.design,
  };

  const remaining = project
    ? Number(project.budget.limit_usd) -
      Number(project.budget.spent_usd) -
      Number(project.budget.reserved_usd)
    : 0;
  const budgetLow =
    project !== null &&
    Number(project.budget.limit_usd) > 0 &&
    remaining / Number(project.budget.limit_usd) < 0.2;

  if (loading) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <Spinner label="Opening this project…" />
      </div>
    );
  }

  const stepIndex = STEPS.findIndex((s) => s.key === step);

  return (
    <div className="mx-auto max-w-[1280px] px-4 pb-16 sm:px-6">
      {/* --- project heading -------------------------------------------- */}
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3 border-b border-line py-5">
        <div className="min-w-0">
          <h1 className="display truncate text-xl">{project?.name}</h1>
          <p className="mt-1 text-sm text-muted">{project?.infection.name}</p>
        </div>
        {project && (
          <div className="text-right">
            <p className={`tnum text-lg ${budgetLow ? "text-caution" : "text-ink"}`}>
              {formatMoney(String(remaining))}
            </p>
            <p className="text-[13px] text-muted">
              left to spend of {formatMoney(project.budget.limit_usd)}
              {Number(project.budget.reserved_usd) > 0 && (
                <span className="text-caution">
                  {" "}
                  · {formatMoney(project.budget.reserved_usd)} held by a running job
                </span>
              )}
            </p>
          </div>
        )}
      </div>

      {/* --- the four steps, as a line ---------------------------------- */}
      <nav aria-label="Steps" className="py-5">
        {/* All four must fit on a phone. If the line scrolls, the step you are
            actually on ends up off-screen, which is worse than small type. */}
        <ol className="flex items-stretch gap-1.5 sm:gap-0">
          {STEPS.map((s, i) => {
            const current = step === s.key;
            const complete = done[s.key];
            const open = reachable[s.key];
            return (
              <li key={s.key} className="min-w-0 flex-1">
                <button
                  type="button"
                  disabled={!open}
                  onClick={() => setStep(s.key)}
                  aria-current={current ? "step" : undefined}
                  className="group relative block w-full pb-3 pt-1 text-left disabled:cursor-not-allowed"
                >
                  {/* The rail. Filled behind every step already reached. */}
                  <span
                    aria-hidden="true"
                    className={`absolute inset-x-0 top-0 h-[3px] ${
                      i <= stepIndex ? "bg-signal" : "bg-line"
                    } ${i === 0 ? "rounded-l-full" : ""} ${
                      i === STEPS.length - 1 ? "rounded-r-full" : ""
                    } transition-colors duration-[var(--dur-panel)]`}
                  />
                  <span className="mt-3 flex items-center gap-1.5 sm:gap-2">
                    <span
                      className={`grid size-[20px] shrink-0 place-items-center rounded-full border text-[11px] sm:size-[22px] sm:text-[12px] transition-colors duration-[var(--dur-ui)] ${
                        complete
                          ? "border-signal bg-signal text-white"
                          : current
                            ? "border-signal text-signal"
                            : open
                              ? "border-line text-muted"
                              : "border-line text-faint"
                      }`}
                    >
                      {complete ? "✓" : i + 1}
                    </span>
                    <span
                      className={`truncate text-[13px] sm:text-sm ${
                        current
                          ? "font-medium text-ink"
                          : open
                            ? "text-muted group-hover:text-ink"
                            : "text-faint"
                      }`}
                    >
                      {s.label}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      {error && (
        <div className="mb-5">
          <Notice tone="danger" title={error} />
        </div>
      )}

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={step}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: DUR.panel, ease: EASE_OUT }}
        >
          {step === "target" && (
            <TargetStep
              projectId={projectId}
              targets={targets}
              activeTargetId={activeTargetId}
              onSelect={setActiveTargetId}
              onChanged={async () => {
                await refresh();
              }}
              onNext={() => setStep("epitope")}
            />
          )}

          {step === "epitope" && activeTarget && (
            <EpitopeStep
              projectId={projectId}
              target={activeTarget}
              onSaved={async () => {
                await refresh();
                setStep("design");
              }}
            />
          )}

          {step === "design" && activeTarget && (
            <DesignStep
              projectId={projectId}
              target={activeTarget}
              remainingUsd={remaining}
              onSubmitted={async (run) => {
                setActiveRunId(run.id);
                await refresh();
                setStep("results");
              }}
            />
          )}

          {step === "results" && (
            <ResultsStep
              projectId={projectId}
              runs={runs}
              activeRunId={activeRunId}
              onSelectRun={setActiveRunId}
              onRunUpdated={async () => {
                await refresh();
              }}
              isRunActive={
                activeRun !== null && ACTIVE_STATUSES.includes(activeRun.status)
              }
            />
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

"use client";

/**
 * The workflow. A left rail of the seven steps, like the function selector on a
 * bench instrument: it shows where you are in a process, not where you may
 * navigate. Budget sits at its foot because spending is a first-class concern.
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
import TargetStep from "@/components/steps/TargetStep";
import EpitopeStep from "@/components/steps/EpitopeStep";
import DesignStep from "@/components/steps/DesignStep";
import ResultsStep from "@/components/steps/ResultsStep";

export type StepId = "target" | "epitope" | "design" | "results";

const STEPS: { id: StepId; n: string; label: string }[] = [
  { id: "target", n: "02", label: "Target" },
  { id: "epitope", n: "03", label: "Epitope" },
  { id: "design", n: "04", label: "Design" },
  { id: "results", n: "05", label: "Results" },
];

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

  const remaining = project
    ? Number(project.budget.limit_usd) -
      Number(project.budget.spent_usd) -
      Number(project.budget.reserved_usd)
    : 0;
  const budgetLow =
    project !== null && Number(project.budget.limit_usd) > 0 && remaining / Number(project.budget.limit_usd) < 0.2;

  if (loading) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <Spinner label="loading project" />
      </div>
    );
  }

  return (
    <div className="flex min-h-[calc(100vh-49px)] flex-col lg:flex-row">
      {/* --- rail ------------------------------------------------------- */}
      <aside className="shrink-0 border-b border-line bg-panel lg:w-[232px] lg:border-b-0 lg:border-r">
        <div className="border-b border-line px-4 py-3">
          <p className="text-[10px] uppercase tracking-widest text-faint">project</p>
          <h1 className="mt-1 text-sm leading-snug text-ink">{project?.name}</h1>
          <p className="mt-1 text-xs leading-snug text-muted">
            {project?.infection.name}
          </p>
        </div>

        <nav aria-label="Workflow steps">
          <ol className="flex overflow-x-auto lg:block">
            {STEPS.map((s) => {
              const current = step === s.id;
              const complete = done[s.id];
              const reachable =
                s.id === "target" ||
                (s.id === "epitope" && done.target) ||
                (s.id === "design" && done.epitope) ||
                (s.id === "results" && done.design);
              return (
                <li key={s.id} className="shrink-0 lg:border-b lg:border-line">
                  <button
                    type="button"
                    disabled={!reachable}
                    onClick={() => setStep(s.id)}
                    aria-current={current ? "step" : undefined}
                    className={`relative flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-[var(--dur-ui)] disabled:cursor-not-allowed disabled:opacity-35 ${
                      current ? "bg-panel-hi text-ink" : "text-muted hover:text-ink"
                    }`}
                  >
                    {current && (
                      <motion.span
                        layoutId="step-marker"
                        className="absolute inset-y-0 left-0 w-[2px] bg-signal"
                        transition={{ duration: DUR.ui, ease: EASE_OUT }}
                      />
                    )}
                    <span className="tnum text-[11px] text-faint">{s.n}</span>
                    <span className="flex-1 text-sm">{s.label}</span>
                    {complete && (
                      <span
                        className="block size-1.5 rounded-full bg-signal"
                        aria-label="complete"
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        {project && (
          <div className="border-t border-line px-4 py-3 lg:mt-auto">
            <p className="text-[10px] uppercase tracking-widest text-faint">budget</p>
            <p className={`tnum mt-1 text-lg ${budgetLow ? "text-caution" : "text-ink"}`}>
              {formatMoney(String(remaining))}
            </p>
            <p className="text-[11px] text-muted">
              of {formatMoney(project.budget.limit_usd)} remaining
            </p>
            {Number(project.budget.reserved_usd) > 0 && (
              <p className="tnum mt-1 text-[11px] text-caution">
                {formatMoney(project.budget.reserved_usd)} held by a running job
              </p>
            )}
          </div>
        )}
      </aside>

      {/* --- canvas ----------------------------------------------------- */}
      <div className="min-w-0 flex-1">
        {error && (
          <div className="p-4">
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
    </div>
  );
}

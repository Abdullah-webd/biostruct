"use client";

/**
 * Configuring and submitting a design run. The only place in the product that
 * spends money, so the flow is deliberately two-stage: estimate, then confirm
 * the exact figure. The API refuses a submission whose confirmed price does not
 * match a fresh estimate, so this is not merely a UI nicety.
 */

import { useState } from "react";

import {
  ApiError,
  endpoints,
  formatMoney,
  type DesignRun,
  type Estimate,
  type Target,
} from "@/lib/api";
import { Button, Field, Notice, Panel, Readout } from "@/components/ui";

const PRESETS = [10, 50, 100, 250, 500];
const UI_MAX = 2000;

export default function DesignStep({
  projectId,
  target,
  remainingUsd,
  onSubmitted,
}: {
  projectId: string;
  target: Target;
  remainingUsd: number;
  onSubmitted: (run: DesignRun) => Promise<void>;
}) {
  const [count, setCount] = useState(10);
  const [modality, setModality] = useState<"nanobody" | "antibody">("nanobody");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState("");

  const epitope = target.epitope;

  async function getEstimate() {
    setBusy(true);
    setError("");
    setDetail("");
    setEstimate(null);
    try {
      setEstimate(
        await endpoints.estimate(projectId, {
          target_id: target.id,
          num_proteins: count,
          modality,
        }),
      );
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        setDetail(e.detail ?? "");
      } else {
        setError("Could not get an estimate");
      }
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!estimate) return;
    setBusy(true);
    setError("");
    setDetail("");
    try {
      const run = await endpoints.createRun(projectId, {
        request: { target_id: target.id, num_proteins: count, modality },
        // Exactly what was shown. A mismatch is refused server-side.
        confirmed_cost_usd: estimate.total_usd,
      });
      await onSubmitted(run);
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        setDetail(e.detail ?? "");
        // The price moved, so the shown figure is stale and must be refreshed
        // before it can be confirmed again.
        if (e.code === "cost_estimate_stale") setEstimate(null);
      } else {
        setError("Could not submit the run");
      }
      setBusy(false);
    }
  }

  if (!epitope) {
    return (
      <div className="p-4 sm:p-6">
        <Notice tone="caution" title="This target has no epitope yet">
          Go back to step 03 and choose which residues to design against.
        </Notice>
      </div>
    );
  }

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <header>
        <p className="text-[10px] uppercase tracking-widest text-signal">step 04</p>
        <h2 className="display mt-1 text-2xl">Generate candidates</h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
          BoltzProt-1 designs binders against the epitope you selected. You see
          the price before anything is spent, and the run is submitted at that
          exact figure.
        </p>
      </header>

      {error && (
        <Notice tone="danger" title={error}>
          {detail}
        </Notice>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Run configuration">
          <div className="space-y-5 p-4">
            <div>
              <span className="mb-1.5 block text-[11px] uppercase tracking-widest text-muted">
                Designs to generate
              </span>
              <div className="flex flex-wrap gap-1">
                {PRESETS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => {
                      setCount(n);
                      setEstimate(null);
                    }}
                    aria-pressed={count === n}
                    className={`tnum border px-3 py-1.5 text-sm transition-colors duration-[var(--dur-ui)] active:scale-[0.97] ${
                      count === n
                        ? "border-signal bg-signal text-ground"
                        : "border-line text-muted hover:border-faint hover:text-ink"
                    }`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <Field
                label=""
                className="mt-2"
                type="number"
                min={10}
                max={UI_MAX}
                value={count}
                onChange={(e) => {
                  setCount(Number(e.target.value));
                  setEstimate(null);
                }}
                hint={`Between 10 and ${UI_MAX}. The engine itself allows a million, which at this price would be a $50,000 request, so we cap it here.`}
              />
            </div>

            <div>
              <span className="mb-1.5 block text-[11px] uppercase tracking-widest text-muted">
                Modality
              </span>
              <div className="flex gap-1">
                {(["nanobody", "antibody"] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      setModality(m);
                      setEstimate(null);
                    }}
                    aria-pressed={modality === m}
                    className={`border px-3 py-1.5 text-sm capitalize transition-colors duration-[var(--dur-ui)] active:scale-[0.97] ${
                      modality === m
                        ? "border-signal bg-signal text-ground"
                        : "border-line text-muted hover:border-faint hover:text-ink"
                    }`}
                  >
                    {m}
                  </button>
                ))}
              </div>
            </div>

            <div className="divide-y divide-line border-t border-line pt-1">
              <Readout label="Target" value={target.accession} />
              <Readout label="Structure" value={`${epitope.pdb_id} · ${epitope.chain_id}`} />
              <Readout
                label="Epitope residues"
                value={epitope.author_residues.join(", ") || "—"}
              />
              <Readout
                label="Residues selected"
                value={epitope.residue_letters || "—"}
                tone="text-epitope"
              />
              <Readout
                label="Mapping verified"
                value={epitope.verified ? "yes" : "not verified"}
                tone={epitope.verified ? "text-signal" : "text-caution"}
              />
            </div>
          </div>
        </Panel>

        <Panel title="Cost">
          <div className="p-4">
            {!estimate ? (
              <>
                <p className="text-sm leading-relaxed text-muted">
                  Getting an estimate is free and does not start anything.
                </p>
                <Button
                  variant="primary"
                  onClick={() => void getEstimate()}
                  disabled={busy || count < 10}
                  className="mt-4"
                >
                  {busy ? "Estimating…" : "Get an estimate"}
                </Button>
              </>
            ) : (
              <>
                <div className="border border-line bg-ground p-4">
                  <p className="text-[10px] uppercase tracking-widest text-faint">
                    this run will cost
                  </p>
                  <p className="tnum mt-1 text-3xl text-ink">
                    {formatMoney(estimate.total_usd)}
                  </p>
                  <p className="tnum mt-1 text-xs text-muted">
                    {estimate.units} designs × {formatMoney(estimate.unit_usd)}
                  </p>
                </div>

                <div className="mt-4 divide-y divide-line">
                  <Readout
                    label="Budget remaining"
                    value={formatMoney(estimate.budget_remaining_usd)}
                  />
                  <Readout
                    label="After this run"
                    value={formatMoney(
                      String(
                        Number(estimate.budget_remaining_usd) - Number(estimate.total_usd),
                      ),
                    )}
                    tone={estimate.within_budget ? "text-ink" : "text-danger"}
                  />
                </div>

                {!estimate.livemode && (
                  <div className="mt-4">
                    <Notice tone="caution" title="This is a test-mode key">
                      The run is free and returns synthetic results: made-up
                      metrics and a placeholder structure. Useful for checking
                      the workflow, not for science. Results will be labelled.
                    </Notice>
                  </div>
                )}

                {!estimate.within_budget && (
                  <div className="mt-4">
                    <Notice tone="danger" title="Over the project budget">
                      Reduce the number of designs or raise the budget.
                    </Notice>
                  </div>
                )}

                {estimate.disclaimer && (
                  <p className="mt-3 text-[11px] leading-relaxed text-faint">
                    {estimate.disclaimer}
                  </p>
                )}

                <div className="mt-4 flex flex-wrap gap-2">
                  <Button
                    variant="primary"
                    onClick={() => void submit()}
                    disabled={busy || !estimate.within_budget}
                  >
                    {busy
                      ? "Submitting…"
                      : `Confirm ${formatMoney(estimate.total_usd)} and run`}
                  </Button>
                  <Button variant="ghost" onClick={() => setEstimate(null)} disabled={busy}>
                    Change
                  </Button>
                </div>
              </>
            )}
            <p className="mt-4 text-[11px] leading-relaxed text-faint">
              Remaining in this project: {formatMoney(String(remainingUsd))}
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

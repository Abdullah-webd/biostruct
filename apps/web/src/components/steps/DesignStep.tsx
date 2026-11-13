"use client";

/**
 * Step 3 — design the proteins.
 *
 * The only place in the product that spends money, so the flow is deliberately
 * two-stage: estimate, then confirm the exact figure. The API refuses a
 * submission whose confirmed price does not match a fresh estimate, so this is
 * not merely a UI nicety.
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
import {
  Button,
  Field,
  Notice,
  Panel,
  Readout,
  StepHeader,
  Term,
} from "@/components/ui";
import { STEP_INTRO } from "@/lib/words";

const PRESETS = [10, 50, 100, 250, 500];
const UI_MAX = 2000;

/** The two shapes the model can design, said in terms of what they are. */
const MODALITIES = [
  {
    id: "nanobody" as const,
    label: "Nanobody",
    because:
      "A single small domain, originally from camels. Cheap to make, stable, and it reaches into pockets a full antibody cannot.",
  },
  {
    id: "antibody" as const,
    label: "Antibody",
    because:
      "The full two-chain shape your immune system makes. Larger, and the format most existing drugs use.",
  },
];

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
        setError("Could not work out what this would cost");
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
        setError("Could not start the run");
      }
      setBusy(false);
    }
  }

  if (!epitope) {
    return (
      <Notice tone="caution" title="You have not chosen where to attack yet">
        Go back to step 2 and pick the spot on the protein to design against.
      </Notice>
    );
  }

  const chipClass = (on: boolean) =>
    `rounded-md border px-3.5 py-2 text-sm transition-colors duration-[var(--dur-ui)] active:scale-[0.97] ${
      on
        ? "border-signal bg-signal text-white"
        : "border-line text-muted hover:border-faint hover:text-ink"
    }`;

  return (
    <div className="space-y-6">
      <StepHeader title="Design the proteins">{STEP_INTRO.design}</StepHeader>

      {error && (
        <Notice tone="danger" title={error}>
          {detail}
        </Notice>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="What to make">
          <div className="space-y-6 p-4">
            <div>
              <p className="mb-1 text-sm font-medium text-ink">
                How many designs to try
              </p>
              <p className="mb-2.5 text-[13px] leading-relaxed text-muted">
                Each one is an independent attempt. Most will be no good, which is
                why the number matters: more attempts, better odds that one is
                worth taking to a lab.
              </p>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => {
                      setCount(n);
                      setEstimate(null);
                    }}
                    aria-pressed={count === n}
                    className={`tnum ${chipClass(count === n)}`}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <Field
                label="Or a specific number"
                className="mt-3"
                type="number"
                min={10}
                max={UI_MAX}
                value={count}
                onChange={(e) => {
                  setCount(Number(e.target.value));
                  setEstimate(null);
                }}
                hint={`Between 10 and ${UI_MAX}. The engine itself would accept a million, which at this price is a $50,000 request, so we cap it here.`}
              />
            </div>

            <div>
              <p className="mb-2.5 text-sm font-medium text-ink">
                What shape should each <Term k="binder" /> be?
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {MODALITIES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setModality(m.id);
                      setEstimate(null);
                    }}
                    aria-pressed={modality === m.id}
                    className={`rounded-lg border px-3.5 py-3 text-left transition-colors duration-[var(--dur-ui)] ${
                      modality === m.id
                        ? "border-signal bg-signal-dim"
                        : "border-line hover:border-faint"
                    }`}
                  >
                    <span
                      className={`block text-sm font-medium ${
                        modality === m.id ? "text-signal" : "text-ink"
                      }`}
                    >
                      {m.label}
                    </span>
                    <span className="mt-1 block text-[13px] leading-relaxed text-muted">
                      {m.because}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <div className="divide-y divide-line border-t border-line pt-1">
              <Readout label="Protein being attacked" value={target.protein_name} />
              <Readout
                label="3D shape used"
                value={`${epitope.pdb_id.toUpperCase()}, chain ${epitope.chain_id}`}
              />
              <Readout
                label="Positions targeted"
                value={epitope.author_residues.join(", ") || "—"}
              />
              <Readout
                label="Building blocks there"
                value={epitope.residue_letters || "—"}
              />
              <Readout
                label="Positions double-checked"
                value={epitope.verified ? "yes" : "not checked"}
                tone={epitope.verified ? "text-good" : "text-caution"}
              />
            </div>
          </div>
        </Panel>

        <Panel title="What it costs">
          <div className="p-4">
            {!estimate ? (
              <>
                <p className="text-sm leading-relaxed text-muted">
                  Checking the price is free and starts nothing. You will see the
                  exact figure before anything is spent.
                </p>
                <Button
                  variant="primary"
                  onClick={() => void getEstimate()}
                  disabled={busy || count < 10}
                  className="mt-4"
                >
                  {busy ? "Checking…" : "Check the price"}
                </Button>
              </>
            ) : (
              <>
                <div className="rounded-lg border border-line bg-wash p-4">
                  <p className="text-[13px] text-muted">This run will cost</p>
                  <p className="tnum mt-1 text-[32px] leading-none text-ink">
                    {formatMoney(estimate.total_usd)}
                  </p>
                  <p className="tnum mt-2 text-[13px] text-muted">
                    {estimate.units} designs × {formatMoney(estimate.unit_usd)} each
                  </p>
                </div>

                <div className="mt-4 divide-y divide-line">
                  <Readout
                    label="You have"
                    value={formatMoney(estimate.budget_remaining_usd)}
                  />
                  <Readout
                    label="Left afterwards"
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
                    <Notice tone="caution" title="This is a practice run, and it is free">
                      It returns made-up numbers and a stand-in 3D shape, so you can
                      see how the whole thing works without spending anything. The
                      results will say so on every screen. Not science.
                    </Notice>
                  </div>
                )}

                {!estimate.within_budget && (
                  <div className="mt-4">
                    <Notice tone="danger" title="That costs more than this project has left">
                      Try fewer designs, or raise the project&rsquo;s budget.
                    </Notice>
                  </div>
                )}

                {estimate.disclaimer && (
                  <p className="mt-3 text-[12px] leading-relaxed text-faint">
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
                      ? "Starting…"
                      : `Spend ${formatMoney(estimate.total_usd)} and start`}
                  </Button>
                  <Button variant="ghost" onClick={() => setEstimate(null)} disabled={busy}>
                    Change something
                  </Button>
                </div>
              </>
            )}
            <p className="mt-4 text-[12px] leading-relaxed text-faint">
              This project has {formatMoney(String(remainingUsd))} left to spend.
            </p>
          </div>
        </Panel>
      </div>
    </div>
  );
}

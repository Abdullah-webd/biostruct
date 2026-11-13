"use client";

/**
 * Step 1 — pick the protein to attack.
 *
 * v1 opened with an empty box expecting UniProt query syntax, which is a wall
 * for anyone who has not used UniProt. v2 leads with the common resistance
 * proteins by name, and keeps the search box underneath for people who know
 * exactly what they want.
 */

import { motion } from "motion/react";
import { type ReactNode, useState } from "react";

import {
  ApiError,
  endpoints,
  type Target,
  type TargetCandidate,
  type TargetSearchResponse,
} from "@/lib/api";
import {
  Button,
  Empty,
  Field,
  Notice,
  Panel,
  SkeletonRows,
  StepHeader,
  Term,
} from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import { STEP_INTRO } from "@/lib/words";

/**
 * Starting points, by what the protein does rather than what it is called.
 * Each one runs a real search — the user still chooses from the results, so a
 * preset is a shortcut past the query syntax, not a claim about what exists.
 * The first is the case this product has been run against end to end.
 */
const PRESETS: {
  label: string;
  because: ReactNode;
  query: string;
  worked?: boolean;
}[] = [
  {
    label: "KPC carbapenemase",
    because: (
      <>
        Lets <em>Klebsiella pneumoniae</em> destroy carbapenems, the antibiotics
        kept in reserve for when nothing else works.
      </>
    ),
    query: 'protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"',
    worked: true,
  },
  {
    label: "NDM metallo-β-lactamase",
    because:
      "Spreads between species on a plasmid and defeats almost every β-lactam antibiotic there is.",
    query: 'protein_name:"metallo-beta-lactamase NDM"',
  },
  {
    label: "CTX-M β-lactamase",
    because: (
      <>
        The most common cause of resistance to cephalosporins in <em>E. coli</em>{" "}
        worldwide.
      </>
    ),
    query: 'protein_name:"beta-lactamase CTX-M"',
  },
  {
    label: "OXA-48 β-lactamase",
    because:
      "A carbapenemase that is easy to miss in the lab, so it spreads quietly through hospitals.",
    query: 'protein_name:"beta-lactamase OXA-48"',
  },
];

export default function TargetStep({
  projectId,
  targets,
  activeTargetId,
  onSelect,
  onChanged,
  onNext,
}: {
  projectId: string;
  targets: Target[];
  activeTargetId: string | null;
  onSelect: (id: string) => void;
  onChanged: () => Promise<void>;
  onNext: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TargetSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function run(q: string) {
    setQuery(q);
    setSearching(true);
    setError("");
    setResults(null);
    try {
      setResults(
        await endpoints.searchTargets(projectId, { query: q, reviewed_only: true }),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message_for_user : "The search did not work");
    } finally {
      setSearching(false);
    }
  }

  async function add(candidate: TargetCandidate) {
    setAdding(candidate.accession);
    setError("");
    try {
      await endpoints.addTarget(projectId, candidate.accession);
      await onChanged();
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message_for_user : "Could not add that protein",
      );
    } finally {
      setAdding(null);
    }
  }

  return (
    <div className="space-y-6">
      <StepHeader title="Which protein is making this infection resistant?">
        {STEP_INTRO.target}
      </StepHeader>

      {error && <Notice tone="danger" title={error} />}

      {/* --- the plain path: pick a known resistance protein ------------- */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-ink">Common starting points</h3>
        <ul className="grid gap-3 sm:grid-cols-2">
          {PRESETS.map((p) => (
            <li key={p.query}>
              <button
                type="button"
                onClick={() => void run(p.query)}
                disabled={searching}
                className="h-full w-full rounded-lg border border-line bg-panel px-4 py-3.5 text-left transition-colors duration-[var(--dur-ui)] hover:border-signal hover:bg-signal-dim disabled:opacity-50"
              >
                <span className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-ink">{p.label}</span>
                  {p.worked && (
                    <span className="text-[12px] text-good">worked example</span>
                  )}
                </span>
                <span className="mt-1 block text-[13px] leading-relaxed text-muted">
                  {p.because}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* --- the expert path -------------------------------------------- */}
      <details className="group rounded-lg border border-line bg-wash px-4 py-3">
        <summary className="cursor-pointer text-sm text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink">
          Search for something else
        </summary>
        <form
          method="post"
          action=""
          onSubmit={(e) => {
            e.preventDefault();
            void run(query);
          }}
          className="mt-4 space-y-3"
        >
          <Field
            label="Protein name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="beta-lactamase"
            hint="Plain words work. UniProt query syntax works too, if you use it. Only reviewed entries are searched — unreviewed ones look identical in a list but carry far weaker evidence."
          />
          <Button type="submit" variant="primary" disabled={searching || !query.trim()}>
            {searching ? "Searching…" : "Search"}
          </Button>
        </form>
      </details>

      {searching && <SkeletonRows rows={3} />}

      {results && (
        <Panel
          title={
            results.items.length === 1
              ? "1 protein found"
              : `${results.items.length} proteins found`
          }
        >
          {results.degraded_sources.length > 0 && (
            <div className="border-b border-line p-3">
              <Notice
                tone="caution"
                title={`We could not reach: ${results.degraded_sources.join(", ")}`}
              >
                So this list is incomplete. Something missing here does not mean it
                does not exist — it means we could not check.
              </Notice>
            </div>
          )}
          {results.items.length === 0 ? (
            <Empty title="Nothing matched that">
              Try fewer words, or one of the starting points above.
            </Empty>
          ) : (
            <ul className="divide-y divide-line">
              {results.items.map((candidate, index) => {
                const already = targets.some((t) => t.accession === candidate.accession);
                return (
                  <motion.li
                    key={candidate.accession}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: DUR.ui, delay: stagger(index), ease: EASE_OUT }}
                    className="flex flex-wrap items-start justify-between gap-4 px-4 py-3.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-ink">{candidate.protein_name}</p>
                      <p className="mt-0.5 text-[13px] italic text-muted">
                        {candidate.organism}
                      </p>
                      <p className="mt-1.5 text-[13px] text-muted">
                        <span className="tnum">{candidate.pdb_count}</span>{" "}
                        {candidate.pdb_count === 1
                          ? "measured 3D shape"
                          : "measured 3D shapes"}{" "}
                        available
                        {candidate.active_sites.length > 0 && (
                          <>
                            {" · "}
                            <span className="tnum">
                              {candidate.active_sites.length}
                            </span>{" "}
                            known attack site
                            {candidate.active_sites.length === 1 ? "" : "s"}
                          </>
                        )}
                      </p>
                      <p className="mt-1 text-[12px] text-faint">
                        <Term k="accession" /> <span className="tnum">{candidate.accession}</span>
                        {" · "}
                        <span className="tnum">{candidate.length}</span> building blocks long
                      </p>
                    </div>
                    <Button
                      variant={already ? "ghost" : "primary"}
                      disabled={already || adding === candidate.accession}
                      onClick={() => void add(candidate)}
                    >
                      {already
                        ? "Added"
                        : adding === candidate.accession
                          ? "Adding…"
                          : "Use this one"}
                    </Button>
                  </motion.li>
                );
              })}
            </ul>
          )}
        </Panel>
      )}

      {targets.length > 0 && (
        <Panel
          title={
            targets.length === 1
              ? "1 protein in this project"
              : `${targets.length} proteins in this project`
          }
          aside={
            <Button variant="primary" onClick={onNext} className="px-3 py-1.5 text-[13px]">
              Next: choose where to attack
            </Button>
          }
        >
          <ul className="divide-y divide-line">
            {targets.map((target) => (
              <li key={target.id}>
                <button
                  type="button"
                  onClick={() => onSelect(target.id)}
                  aria-pressed={activeTargetId === target.id}
                  className={`flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left transition-colors duration-[var(--dur-ui)] ${
                    activeTargetId === target.id ? "bg-signal-dim" : "hover:bg-panel-hi"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm text-ink">
                      {target.protein_name}
                    </span>
                    <span className="mt-0.5 block text-[13px] text-muted">
                      <span className="tnum">{target.structures.length}</span> 3D shapes ·{" "}
                      <span className="tnum">{target.references.length}</span> papers
                      {target.degraded_sources.length > 0 && (
                        <span className="text-caution">
                          {" "}
                          · incomplete: {target.degraded_sources.join(", ")}
                        </span>
                      )}
                    </span>
                  </span>
                  <span className="shrink-0 text-[13px]">
                    {target.epitope ? (
                      <span className="text-good">attack site chosen</span>
                    ) : (
                      <span className="text-caution">needs an attack site</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}

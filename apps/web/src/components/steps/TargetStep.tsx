"use client";

import { motion } from "motion/react";
import { useState } from "react";

import {
  ApiError,
  endpoints,
  type Target,
  type TargetCandidate,
  type TargetSearchResponse,
} from "@/lib/api";
import { Button, Empty, Field, Notice, Panel, SkeletonRows } from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

const EXAMPLE = 'protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"';

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
  const [query, setQuery] = useState(EXAMPLE);
  const [results, setResults] = useState<TargetSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function search(event: React.FormEvent) {
    event.preventDefault();
    setSearching(true);
    setError("");
    setResults(null);
    try {
      setResults(await endpoints.searchTargets(projectId, { query, reviewed_only: true }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message_for_user : "Search failed");
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
      setError(e instanceof ApiError ? e.message_for_user : "Could not add the target");
    } finally {
      setAdding(null);
    }
  }

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <header>
        <p className="text-[10px] uppercase tracking-widest text-signal">step 02</p>
        <h2 className="display mt-1 text-2xl">Find a biological target</h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
          Searches reviewed UniProt entries, then gathers the available PDB
          structures and recent literature. Unreviewed entries are excluded by
          default: they look identical in a list but carry far weaker evidence.
        </p>
      </header>

      {error && <Notice tone="danger" title={error} />}

      <Panel title="Search UniProt">
        <form onSubmit={search} className="space-y-4 p-4">
          <Field
            label="Query"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            hint="UniProt query syntax. Plain text works too."
          />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" disabled={searching}>
              {searching ? "Searching…" : "Search"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setQuery(EXAMPLE)}>
              Use the KPC-2 example
            </Button>
          </div>
        </form>
      </Panel>

      {searching && <SkeletonRows rows={3} />}

      {results && (
        <Panel title={`${results.items.length} candidate${results.items.length === 1 ? "" : "s"}`}>
          {results.degraded_sources.length > 0 && (
            <div className="border-b border-line">
              <Notice
                tone="caution"
                title={`Some sources were unavailable: ${results.degraded_sources.join(", ")}`}
              >
                The panel below is incomplete. An absence here does not mean an
                absence of evidence — it means we could not reach that source.
              </Notice>
            </div>
          )}
          {results.items.length === 0 ? (
            <Empty title="Nothing matched">Try a broader query.</Empty>
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
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="tnum text-sm text-signal">
                          {candidate.accession}
                        </span>
                        <span className="text-sm text-ink">{candidate.protein_name}</span>
                      </div>
                      <p className="mt-1 text-xs italic text-muted">{candidate.organism}</p>
                      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-faint">
                        <li className="tnum">{candidate.length} aa</li>
                        <li className="tnum">{candidate.pdb_count} structures</li>
                        <li>{candidate.evidence_level}</li>
                        {candidate.active_sites.length > 0 && (
                          <li className="tnum">
                            {candidate.active_sites.length} annotated active sites
                          </li>
                        )}
                      </ul>
                    </div>
                    <Button
                      variant={already ? "ghost" : "default"}
                      disabled={already || adding === candidate.accession}
                      onClick={() => void add(candidate)}
                    >
                      {already
                        ? "Added"
                        : adding === candidate.accession
                          ? "Adding…"
                          : "Add to project"}
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
          title={`${targets.length} target${targets.length === 1 ? "" : "s"} in this project`}
          aside={
            <Button variant="primary" onClick={onNext} className="px-2 py-1 text-xs">
              Continue
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
                    activeTargetId === target.id ? "bg-panel-hi" : "hover:bg-panel-hi"
                  }`}
                >
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-2">
                      <span className="tnum text-sm text-signal">{target.accession}</span>
                      <span className="truncate text-sm text-ink">
                        {target.protein_name}
                      </span>
                    </span>
                    <span className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-faint">
                      <span className="tnum">{target.structures.length} structures</span>
                      <span className="tnum">{target.references.length} references</span>
                      {target.degraded_sources.length > 0 && (
                        <span className="text-caution">
                          incomplete: {target.degraded_sources.join(", ")}
                        </span>
                      )}
                    </span>
                  </span>
                  <span className="shrink-0 text-[11px]">
                    {target.epitope ? (
                      <span className="text-signal">epitope set</span>
                    ) : (
                      <span className="text-caution">needs an epitope</span>
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

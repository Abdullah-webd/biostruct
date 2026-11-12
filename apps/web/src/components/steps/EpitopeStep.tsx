"use client";

/**
 * Choosing the epitope. The most dangerous step in the product, so the UI is
 * built to make the conversion visible rather than hidden.
 *
 * A researcher names residues the way the literature does — Ser70. The design
 * model wants a 0-based index into the modelled chain, which for KPC-2 is 39.
 * There is also a third system: UniProt calls the same residue 69. Nothing
 * errors if you get it wrong; the run completes and designs against the wrong
 * surface. So the panel shows the numbering, the gaps, the resolved indices and
 * the resulting amino acids, and offers to verify the mapping against what the
 * caller expects.
 */

import { useMemo, useState } from "react";

import { ApiError, endpoints, type Target } from "@/lib/api";
import { Button, Field, Notice, Panel, Readout } from "@/components/ui";
import { StructureViewer } from "@/components/StructureViewer";

const THREE_LETTER: Record<string, string> = {
  A: "Ala", R: "Arg", N: "Asn", D: "Asp", C: "Cys", Q: "Gln", E: "Glu",
  G: "Gly", H: "His", I: "Ile", L: "Leu", K: "Lys", M: "Met", F: "Phe",
  P: "Pro", S: "Ser", T: "Thr", W: "Trp", Y: "Tyr", V: "Val",
};

export default function EpitopeStep({
  projectId,
  target,
  onSaved,
}: {
  projectId: string;
  target: Target;
  onSaved: () => Promise<void>;
}) {
  const best = target.structures[0];
  const [pdbId, setPdbId] = useState(target.epitope?.pdb_id ?? best?.pdb_id ?? "");
  const [chainId, setChainId] = useState(target.epitope?.chain_id ?? "A");
  const [residues, setResidues] = useState(
    (target.epitope?.author_residues ?? []).join(", "),
  );
  const [verify, setVerify] = useState(true);
  const [preview, setPreview] = useState<{
    indices: number[];
    letters: string;
    numbering: string;
    gaps: string[];
    modelled: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState("");

  const parsed = useMemo(
    () =>
      residues
        .split(/[,\s]+/)
        .map((r) => r.trim())
        .filter(Boolean)
        .map(Number)
        .filter((n) => Number.isInteger(n) && n > 0),
    [residues],
  );

  /** A dry run: convert without saving, so a mistake is visible first. */
  async function check() {
    setBusy(true);
    setError("");
    setDetail("");
    setPreview(null);
    try {
      const result = await endpoints.residueMap(pdbId, chainId, parsed.join(","));
      setPreview({
        indices: result.design_indices ?? [],
        letters: result.residue_letters ?? "",
        numbering: result.mapping.author_numbering,
        gaps: result.mapping.gaps,
        modelled: result.mapping.modelled_residues,
      });
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        setDetail(e.detail ?? "");
      } else {
        setError("Could not read the residue mapping");
      }
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError("");
    setDetail("");
    try {
      // When the letters are known, send them back as expectations so the
      // server verifies the mapping rather than trusting it.
      const expected: Record<string, string> = {};
      if (verify && preview && preview.letters.length === preview.indices.length) {
        parsed.forEach((author, i) => {
          const letter = preview.letters[i];
          if (letter) expected[String(author)] = THREE_LETTER[letter] ?? letter;
        });
      }
      await endpoints.setEpitope(projectId, target.id, {
        pdb_id: pdbId,
        chain_id: chainId,
        author_residues: parsed,
        expected_residues: Object.keys(expected).length ? expected : undefined,
      });
      await onSaved();
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.message);
        setDetail(e.detail ?? "");
      } else {
        setError("Could not save the epitope");
      }
      setBusy(false);
    }
  }

  const suggestion = target.active_sites;

  return (
    <div className="space-y-5 p-4 sm:p-6">
      <header>
        <p className="text-[10px] uppercase tracking-widest text-signal">step 03</p>
        <h2 className="display mt-1 text-2xl">Choose the epitope</h2>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
          Name residues in the numbering the literature uses — 70 for KPC-2&rsquo;s
          catalytic serine. We convert to the index the model needs and refuse if
          a residue is not in the structure, rather than snapping to a neighbour.
        </p>
      </header>

      {error && (
        <Notice tone="danger" title={error}>
          {detail}
        </Notice>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
        <div className="space-y-5">
          <Panel title="Structure and residues">
            <div className="space-y-4 p-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
                <label className="block">
                  <span className="mb-1.5 block text-[11px] uppercase tracking-widest text-muted">
                    Structure
                  </span>
                  <select
                    value={pdbId}
                    onChange={(e) => setPdbId(e.target.value)}
                    className="w-full border border-line bg-ground px-3 py-2 text-sm text-ink focus:border-signal"
                  >
                    {target.structures.map((s) => (
                      <option key={s.pdb_id} value={s.pdb_id}>
                        {s.pdb_id} — {s.quality_note}
                      </option>
                    ))}
                  </select>
                </label>
                <Field
                  label="Chain"
                  value={chainId}
                  onChange={(e) => setChainId(e.target.value.toUpperCase())}
                />
              </div>

              <Field
                label="Residues (author numbering)"
                value={residues}
                onChange={(e) => setResidues(e.target.value)}
                placeholder="70, 73, 130, 166"
                hint="Comma separated. These are the numbers in the PDB file and in the papers."
              />

              {suggestion.length > 0 && (
                <div className="border border-line bg-ground p-3">
                  <p className="text-[11px] uppercase tracking-widest text-faint">
                    UniProt annotates these active sites
                  </p>
                  <p className="mt-1 text-xs leading-relaxed text-muted">
                    In <span className="text-ink">UniProt numbering</span>, which is
                    a different system — position{" "}
                    <span className="tnum text-ink">{suggestion[0].position}</span>{" "}
                    here is residue{" "}
                    <span className="tnum text-ink">{suggestion[0].position + 1}</span>{" "}
                    in the PDB file for this protein.
                  </p>
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {suggestion.map((site) => (
                      <li key={site.position}>
                        <button
                          type="button"
                          onClick={() =>
                            setResidues((current) => {
                              const next = String(site.position + 1);
                              const parts = current.split(/[,\s]+/).filter(Boolean);
                              return parts.includes(next)
                                ? current
                                : [...parts, next].join(", ");
                            })
                          }
                          title={site.description}
                          className="tnum border border-line px-2 py-1 text-[11px] text-muted transition-colors duration-[var(--dur-ui)] hover:border-signal hover:text-signal active:scale-[0.97]"
                        >
                          +{site.position + 1}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <label className="flex items-start gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={verify}
                  onChange={(e) => setVerify(e.target.checked)}
                  className="mt-0.5 accent-[var(--color-signal)]"
                />
                <span>
                  Verify the mapping against the expected amino acids. Turns
                  &ldquo;this looks right&rdquo; into &ldquo;this is right&rdquo;.
                </span>
              </label>

              <div className="flex flex-wrap gap-2">
                <Button onClick={() => void check()} disabled={busy || parsed.length === 0}>
                  {busy ? "Checking…" : "Check the mapping"}
                </Button>
                <Button
                  variant="primary"
                  onClick={() => void save()}
                  disabled={busy || parsed.length === 0 || !preview}
                >
                  Save epitope
                </Button>
              </div>
            </div>
          </Panel>

          {preview && (
            <Panel title="Conversion">
              <div className="p-4">
                <div className="grid gap-px bg-line sm:grid-cols-2">
                  <div className="bg-panel p-3">
                    <p className="text-[10px] uppercase tracking-widest text-faint">
                      you entered · author numbering
                    </p>
                    <p className="tnum mt-1 text-sm text-ink">{parsed.join("  ")}</p>
                  </div>
                  <div className="bg-panel p-3">
                    <p className="text-[10px] uppercase tracking-widest text-faint">
                      sent to the model · 0-based index
                    </p>
                    <p className="tnum mt-1 text-sm text-signal">
                      {preview.indices.join("  ")}
                    </p>
                  </div>
                </div>

                <div className="mt-4 border border-line bg-ground p-3">
                  <p className="text-[10px] uppercase tracking-widest text-faint">
                    residues selected
                  </p>
                  <p className="tnum mt-1 text-xl tracking-[0.2em] text-epitope">
                    {preview.letters || "—"}
                  </p>
                  <p className="mt-1 text-xs text-muted">
                    If these are not the residues you meant, the numbering is wrong.
                  </p>
                </div>

                <div className="mt-4 space-y-0 divide-y divide-line">
                  <Readout label="Chain covers" value={preview.numbering} />
                  <Readout label="Residues modelled" value={preview.modelled} />
                  <Readout
                    label="Gaps in the numbering"
                    value={preview.gaps.length ? preview.gaps.join(", ") : "none"}
                    tone={preview.gaps.length ? "text-caution" : "text-muted"}
                    title="Missing residues, usually a disordered loop the crystallographer could not place."
                  />
                </div>

                {preview.gaps.length > 0 && (
                  <p className="mt-3 text-xs leading-relaxed text-muted">
                    This numbering is not contiguous, which is why an offset
                    calculation would be wrong past the first gap.
                  </p>
                )}
              </div>
            </Panel>
          )}
        </div>

        <div className="lg:sticky lg:top-[65px] lg:self-start">
          <Panel title={`${pdbId || "structure"} · chain ${chainId}`}>
            <div className="relative aspect-square">
              {pdbId ? (
                <StructureViewer
                  url={`https://files.rcsb.org/download/${pdbId.toUpperCase()}.cif`}
                  targetChains={[chainId]}
                  epitopeIndices={preview?.indices ?? []}
                />
              ) : (
                <div className="absolute inset-0 grid place-items-center text-xs text-faint">
                  choose a structure
                </div>
              )}
            </div>
          </Panel>
          {best && (
            <p className="mt-2 text-[11px] leading-relaxed text-faint">
              {best.title}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

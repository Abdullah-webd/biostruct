"use client";

/**
 * Step 2 — choose where on the protein to attack.
 *
 * This is still the most dangerous step in the product, so the conversion stays
 * visible rather than hidden. A researcher names building blocks the way the
 * literature does — Ser70. The design model wants a 0-based index into the
 * modelled chain, which for KPC-2 is 39. There is a third system too: UniProt
 * calls the same one 69. Nothing errors if you get it wrong; the run completes
 * and designs against the wrong surface.
 *
 * What changed in v2 is who has to do the work. v1 opened with an empty box and
 * expected you to know the numbers. v2 fills in the known attack site, checks
 * it, and shows you the result to confirm — so the common case is reading
 * rather than typing, and the box is still there when you want it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, endpoints, type Target } from "@/lib/api";
import { Button, Field, Notice, Panel, Readout, StepHeader, Term } from "@/components/ui";
import { StructureViewer } from "@/components/StructureViewer";
import { STEP_INTRO } from "@/lib/words";

const THREE_LETTER: Record<string, string> = {
  A: "Ala", R: "Arg", N: "Asn", D: "Asp", C: "Cys", Q: "Gln", E: "Glu",
  G: "Gly", H: "His", I: "Ile", L: "Leu", K: "Lys", M: "Met", F: "Phe",
  P: "Pro", S: "Ser", T: "Thr", W: "Trp", Y: "Tyr", V: "Val",
};

const FULL_NAME: Record<string, string> = {
  A: "alanine", R: "arginine", N: "asparagine", D: "aspartate", C: "cysteine",
  Q: "glutamine", E: "glutamate", G: "glycine", H: "histidine", I: "isoleucine",
  L: "leucine", K: "lysine", M: "methionine", F: "phenylalanine", P: "proline",
  S: "serine", T: "threonine", W: "tryptophan", Y: "tyrosine", V: "valine",
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
  const suggestion = target.active_sites;

  // UniProt counts from 0 here; the PDB file and the papers count from 1.
  const suggested = useMemo(
    () => suggestion.map((s) => s.position + 1),
    [suggestion],
  );

  const [pdbId, setPdbId] = useState(target.epitope?.pdb_id ?? best?.pdb_id ?? "");
  const [chainId, setChainId] = useState(target.epitope?.chain_id ?? "A");
  const [residues, setResidues] = useState(
    (target.epitope?.author_residues ?? suggested).join(", "),
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
  const check = useCallback(async () => {
    // Yield first, so nothing is set synchronously inside a calling effect.
    await Promise.resolve();
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
        setError("Could not check those positions against the structure");
      }
    } finally {
      setBusy(false);
    }
  }, [pdbId, chainId, parsed]);

  /* Check the pre-filled suggestion once, on arrival, so the common case is
     confirming a result rather than performing a lookup. Only ever runs when
     the page filled the box itself. */
  const autoChecked = useRef(false);
  useEffect(() => {
    if (autoChecked.current) return;
    if (!pdbId || parsed.length === 0) return;
    if (target.epitope) return;
    autoChecked.current = true;
    // check() yields before touching state, so this is not a synchronous
    // setState during the effect; the rule cannot follow it across the call.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void check();
  }, [pdbId, parsed.length, target.epitope, check]);

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
        setError("Could not save that attack site");
      }
      setBusy(false);
    }
  }

  const prefilled =
    !target.epitope &&
    suggested.length > 0 &&
    parsed.length === suggested.length &&
    parsed.every((n, i) => n === suggested[i]);

  return (
    <div className="space-y-6">
      <StepHeader title="Where on that protein should we attack?">
        {STEP_INTRO.epitope}
      </StepHeader>

      {error && (
        <Notice tone="danger" title={error}>
          {detail}
        </Notice>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
        <div className="space-y-5">
          {prefilled && (
            <Notice tone="good" title="We filled this in for you">
              These are the positions science has already identified as this
              protein&rsquo;s working parts — the ones that actually destroy the
              antibiotic. If you have no reason to change them, go straight on.
            </Notice>
          )}

          {/* The confirmation, before the controls: the common case is reading. */}
          {preview && (
            <Panel title="What you have selected">
              <div className="p-4">
                <p className="text-sm leading-relaxed text-ink">
                  {preview.letters ? (
                    <>
                      The designed protein will be aimed at{" "}
                      <span className="font-medium">
                        {preview.letters
                          .split("")
                          .map((l, i) => `${FULL_NAME[l] ?? l} ${parsed[i]}`)
                          .join(", ")}
                      </span>{" "}
                      on chain {chainId} of structure{" "}
                      <span className="tnum">{pdbId.toUpperCase()}</span>. They are
                      highlighted in pink in the model.
                    </>
                  ) : (
                    "Nothing selected yet."
                  )}
                </p>
                <p className="mt-2 text-[13px] leading-relaxed text-muted">
                  If those are not the building blocks you meant, the numbers below
                  are in the wrong system — see the conversion.
                </p>

                <div className="mt-4">
                  <Button
                    variant="primary"
                    onClick={() => void save()}
                    disabled={busy || parsed.length === 0}
                  >
                    {busy ? "Saving…" : "Use this site, and design against it"}
                  </Button>
                </div>
              </div>
            </Panel>
          )}

          <details className="group rounded-lg border border-line bg-wash px-4 py-3" open={!preview}>
            <summary className="cursor-pointer text-sm text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink">
              Choose the spot myself
            </summary>

            <div className="mt-4 space-y-4">
              <div className="grid gap-4 sm:grid-cols-[1fr_110px]">
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-ink">
                    Which measured 3D shape to work from
                  </span>
                  <select
                    value={pdbId}
                    onChange={(e) => setPdbId(e.target.value)}
                    className="w-full rounded-md border border-line bg-ground px-3 py-2 text-sm text-ink focus:border-signal"
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
                label="Positions to attack"
                value={residues}
                onChange={(e) => setResidues(e.target.value)}
                placeholder="70, 73, 130, 166"
                hint="Comma separated, using the numbers printed in papers and in the structure file — not UniProt's."
              />

              {suggestion.length > 0 && (
                <div className="rounded-md border border-line bg-ground p-3">
                  <p className="text-[13px] font-medium text-ink">
                    Known working parts of this protein
                  </p>
                  <p className="mt-1 text-[13px] leading-relaxed text-muted">
                    Click to add. These have been converted from UniProt&rsquo;s
                    numbering, which is offset by one from the papers&rsquo;.
                  </p>
                  <ul className="mt-2.5 flex flex-wrap gap-2">
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
                          className="tnum rounded border border-line px-2 py-1 text-[12px] text-muted transition-colors duration-[var(--dur-ui)] hover:border-signal hover:text-signal active:scale-[0.97]"
                        >
                          + {site.position + 1}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <label className="flex items-start gap-2 text-[13px] text-muted">
                <input
                  type="checkbox"
                  checked={verify}
                  onChange={(e) => setVerify(e.target.checked)}
                  className="mt-0.5 accent-[var(--color-signal)]"
                />
                <span>
                  Make the server prove it landed on the building blocks shown,
                  instead of taking it on trust. Leave this on.
                </span>
              </label>

              <Button onClick={() => void check()} disabled={busy || parsed.length === 0}>
                {busy ? "Checking…" : "Check these positions"}
              </Button>
            </div>
          </details>

          {preview && (
            <Panel title="How your numbers were converted">
              <div className="p-4">
                <p className="text-[13px] leading-relaxed text-muted">
                  Three different numbering systems describe the same{" "}
                  <Term k="residue" />, and mixing them up is the one mistake here
                  that produces a confident, wrong answer. So the conversion is
                  shown rather than assumed.
                </p>
                <div className="mt-3 grid gap-px overflow-hidden rounded-md bg-line sm:grid-cols-2">
                  <div className="bg-panel p-3">
                    <p className="text-[13px] text-muted">
                      What you entered, as papers number it
                    </p>
                    <p className="tnum mt-1 text-sm text-ink">{parsed.join("  ")}</p>
                  </div>
                  <div className="bg-panel p-3">
                    <p className="text-[13px] text-muted">What the model receives</p>
                    <p className="tnum mt-1 text-sm text-signal">
                      {preview.indices.join("  ")}
                    </p>
                  </div>
                </div>

                <div className="mt-4 divide-y divide-line">
                  <Readout label="This chain covers" value={preview.numbering} />
                  <Readout
                    label="Building blocks actually present"
                    value={preview.modelled}
                  />
                  <Readout
                    label="Missing stretches"
                    value={preview.gaps.length ? preview.gaps.join(", ") : "none"}
                    tone={preview.gaps.length ? "text-caution" : "text-muted"}
                    title="Usually a floppy loop the crystallographer could not pin down."
                  />
                </div>

                {preview.gaps.length > 0 && (
                  <p className="mt-3 text-[13px] leading-relaxed text-muted">
                    The numbering skips, which is exactly why subtracting a fixed
                    offset would go wrong after the first gap. Each position is
                    looked up individually instead.
                  </p>
                )}
              </div>
            </Panel>
          )}
        </div>

        <div className="lg:sticky lg:top-[64px] lg:self-start">
          <Panel
            title={pdbId ? `${pdbId.toUpperCase()}, chain ${chainId}` : "3D shape"}
          >
            <div className="specimen relative aspect-square overflow-hidden rounded-b-lg">
              {pdbId ? (
                <StructureViewer
                  url={`https://files.rcsb.org/download/${pdbId.toUpperCase()}.cif`}
                  targetChains={[chainId]}
                  epitopeIndices={preview?.indices ?? []}
                />
              ) : (
                <div className="absolute inset-0 grid place-items-center text-[13px] text-faint">
                  Choose a structure
                </div>
              )}
            </div>
          </Panel>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            This shape was measured in a lab, not predicted. Drag to turn it.
          </p>
          {best && <p className="mt-1 text-[12px] text-faint">{best.title}</p>}
        </div>
      </div>
    </div>
  );
}

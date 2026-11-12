"use client";

/**
 * Landing page. Motion dial 7: it has one job, which is to make a stranger
 * understand what this does in about eight seconds.
 *
 * The signature is that the hero specimen is the real thing — PDB 2OV5, the
 * KPC-2 carbapenemase this project targets — rotating as it would in the
 * software a structural biologist already uses. Not an abstract blob.
 */

import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText } from "gsap/SplitText";
import Link from "next/link";
import { useRef, useState } from "react";

import { StructureViewer } from "@/components/StructureViewer";
import { Button } from "@/components/ui";

gsap.registerPlugin(useGSAP, ScrollTrigger, SplitText);

// The target itself, straight from the PDB. Handing an RCSB URL to the viewer
// is the same move the design engine makes.
const KPC2_CIF = "https://files.rcsb.org/download/2OV5.cif";

const STEPS = [
  { n: "01", t: "Infection", d: "Start from the clinical problem, not a protein id." },
  { n: "02", t: "Target", d: "UniProt, the PDB and PubMed, gathered in one panel with its gaps named." },
  { n: "03", t: "Epitope", d: "Name residues the way the literature does. We convert, and refuse to guess." },
  { n: "04", t: "Design", d: "BoltzProt-1 generates binders. You confirm the price before anything is spent." },
  { n: "05", t: "Structures", d: "Every predicted complex, mirrored into your own storage and rendered." },
  { n: "06", t: "Ranking", d: "Transparent weights, auditable contributions, and the absolute numbers stated plainly." },
  { n: "07", t: "Export", d: "Sequences, structures and a provenance report for the bench." },
];

export default function Landing() {
  const root = useRef<HTMLDivElement>(null);
  const [showViewer, setShowViewer] = useState(false);

  useGSAP(
    () => {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

      // The viewer is a heavy chunk, so it mounts after the intro has had its
      // moment rather than competing with it for the main thread.
      const viewerTimer = window.setTimeout(() => setShowViewer(true), reduced ? 0 : 450);

      if (reduced) {
        gsap.set("[data-reveal]", { opacity: 1, y: 0, clearProps: "all" });
        gsap.set("[data-reveal='pending']", { opacity: 1 });
        return () => window.clearTimeout(viewerTimer);
      }

      const headline = new SplitText("[data-split]", { type: "lines", linesClass: "line" });

      // Lift the reveal gate *before* building the timeline. `from` tweens
      // render immediately on creation and capture the current computed value
      // as their destination, so with the gate still applied every element
      // would animate from opacity 0 to opacity 0 and never appear.
      gsap.set("[data-reveal='pending']", { opacity: 1 });

      // Focal sequence: ~1.3s, and the nav and CTA are usable throughout.
      const intro = gsap.timeline({ defaults: { ease: "expo.out" } });
      intro
        .from("[data-nav]", { y: -16, opacity: 0, duration: 0.6 }, 0)
        .from(
          headline.lines,
          { yPercent: 115, opacity: 0, duration: 1.1, stagger: 0.09 },
          0.05,
        )
        .from("[data-sub]", { y: 14, opacity: 0, duration: 0.7 }, 0.45)
        .from("[data-cta] > *", { y: 12, opacity: 0, duration: 0.6, stagger: 0.08 }, 0.6)
        .from("[data-specimen]", { opacity: 0, duration: 1.1 }, 0.2)
        .from(
          "[data-stat]",
          { y: 10, opacity: 0, duration: 0.5, stagger: 0.07 },
          0.75,
        );

      // Set-piece: the numbering problem. Our actual differentiator, and a
      // genuinely surprising fact, so it earns a pinned scrub.
      gsap.timeline({
        scrollTrigger: {
          trigger: "[data-numbering]",
          start: "top 72%",
          end: "bottom 60%",
          scrub: 0.6,
        },
      })
        .from("[data-num-row]", { xPercent: -4, opacity: 0, stagger: 0.25, duration: 1 })
        .to("[data-num-converge]", { opacity: 1, duration: 0.8 }, 0.6);

      // Workflow steps: a horizontal reading rhythm rather than a card grid.
      gsap.from("[data-step]", {
        scrollTrigger: { trigger: "[data-steps]", start: "top 78%" },
        y: 20,
        opacity: 0,
        duration: 0.7,
        stagger: 0.07,
        ease: "expo.out",
      });

      gsap.utils.toArray<HTMLElement>("[data-section-rule]").forEach((rule) => {
        gsap.from(rule, {
          scrollTrigger: { trigger: rule, start: "top 90%" },
          scaleX: 0,
          transformOrigin: "left center",
          duration: 1,
          ease: "expo.out",
        });
      });

      return () => window.clearTimeout(viewerTimer);
    },
    { scope: root },
  );

  return (
    <div ref={root} className="relative">
      <nav
        data-nav
        className="sticky top-0 z-30 border-b border-line bg-ground/85 backdrop-blur-md"
      >
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3.5">
          <span className="flex items-baseline gap-2">
            <span className="display text-lg tracking-tight">BIOSTRUCT</span>
            <span className="hidden text-[10px] uppercase tracking-[0.2em] text-faint sm:inline">
              protein design
            </span>
          </span>
          <div className="flex items-center gap-2">
            <Link href="/login">
              <Button variant="ghost">Sign in</Button>
            </Link>
            <Link href="/register">
              <Button variant="primary">Start a project</Button>
            </Link>
          </div>
        </div>
      </nav>

      <main id="main">
        {/* --- hero ------------------------------------------------------ */}
        <section className="relative mx-auto grid max-w-[1400px] gap-10 px-6 pb-20 pt-14 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:gap-6 lg:pt-20">
          <div className="relative z-10">
            <p
              data-reveal="pending"
              data-sub
              className="mb-5 flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-signal"
            >
              <span className="block h-px w-8 bg-signal" />
              Drug-resistant infection
            </p>

            <h1 className="display text-[clamp(2.1rem,4.6vw,3.9rem)]">
              <span data-split className="block">
                Design proteins against the targets antibiotics can no longer reach.
              </span>
            </h1>

            <p
              data-reveal="pending"
              data-sub
              className="mt-6 max-w-xl text-base leading-relaxed text-muted"
            >
              Biostruct takes you from a resistant infection to a ranked set of
              candidate protein binders — gathering the target evidence, running
              the design models, and showing you the structures. It states what
              the computation does and does not know, every time.
            </p>

            <div data-reveal="pending" data-cta className="mt-8 flex flex-wrap gap-3">
              <Link href="/register">
                <Button variant="primary" className="px-5 py-2.5">
                  Start a project
                </Button>
              </Link>
              <Link href="/login">
                <Button className="px-5 py-2.5">Sign in</Button>
              </Link>
            </div>

            <dl
              data-reveal="pending"
              className="mt-10 grid max-w-xl grid-cols-3 gap-px border border-line bg-line"
            >
              {[
                ["Target", "KPC-2", "carbapenemase"],
                ["Structures", "86", "in the PDB"],
                ["Per design", "$0.05", "shown before you spend"],
              ].map(([label, value, note]) => (
                <div key={label} data-stat className="bg-panel px-4 py-3">
                  <dt className="text-[10px] uppercase tracking-widest text-faint">
                    {label}
                  </dt>
                  <dd className="tnum mt-1 text-lg text-ink">{value}</dd>
                  <dd className="text-[11px] text-muted">{note}</dd>
                </div>
              ))}
            </dl>
          </div>

          {/* The specimen. Real structure, rotating, as the field expects. */}
          <div
            data-specimen
            className="relative aspect-square w-full overflow-hidden border border-line bg-ground lg:aspect-[4/5]"
          >
            {showViewer ? (
              <StructureViewer
                url={KPC2_CIF}
                targetChains={["A"]}
              />
            ) : (
              <div className="absolute inset-0 grid place-items-center">
                <span className="text-[11px] uppercase tracking-widest text-faint">
                  loading specimen
                </span>
              </div>
            )}
            <div className="pointer-events-none absolute left-3 top-3 border border-line bg-panel/85 px-2 py-1 backdrop-blur-sm">
              <p className="tnum text-[10px] uppercase tracking-widest text-muted">
                PDB 2OV5 · KPC-2 · 1.85 Å
              </p>
            </div>
          </div>
        </section>

        <div data-section-rule className="hairline mx-auto max-w-[1400px]" />

        {/* --- the numbering problem ------------------------------------- */}
        <section
          data-numbering
          className="mx-auto max-w-[1400px] px-6 py-20 lg:py-28"
        >
          <div className="grid gap-10 lg:grid-cols-[0.9fr_1.1fr] lg:gap-16">
            <div>
              <p className="mb-4 text-[11px] uppercase tracking-[0.2em] text-signal">
                Why this is hard
              </p>
              <h2 className="display text-[clamp(1.8rem,3.4vw,2.9rem)]">
                Three numbering systems describe the same residue.
              </h2>
              <p className="mt-5 max-w-lg text-sm leading-relaxed text-muted">
                KPC-2&rsquo;s catalytic serine is position 69 in UniProt, residue
                70 in the literature and in the PDB file, and index 39 in the
                array the design model wants. The structure models 261 of 293
                residues, numbered from 30, and the numbering skips 58 and 253.
              </p>
              <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted">
                Subtract an offset and you are reading glycine instead of lysine.
                Pass the number straight through and you aim 31 residues away, at
                a different surface. Nothing errors. The run completes, bills you,
                and designs against the wrong site.
              </p>
              <p className="mt-6 max-w-lg border-l-2 border-signal/40 pl-4 text-sm leading-relaxed text-ink">
                Biostruct converts between all three and refuses when it cannot.
                Tell it the residue you expect and it verifies the mapping instead
                of trusting it.
              </p>
            </div>

            <div className="self-center">
              <div className="border border-line bg-panel">
                <div className="grid grid-cols-4 gap-px border-b border-line bg-line text-[10px] uppercase tracking-widest">
                  {["Literature", "UniProt", "PDB author", "Model index"].map((h) => (
                    <div key={h} className="bg-panel px-3 py-2 text-faint">
                      {h}
                    </div>
                  ))}
                </div>
                {[
                  ["Ser70", "69", "70", "39", "S"],
                  ["Lys73", "72", "73", "42", "K"],
                  ["Ser130", "129", "130", "99", "S"],
                  ["Glu166", "165", "166", "135", "E"],
                ].map(([lit, uni, auth, idx, letter]) => (
                  <div
                    key={lit}
                    data-num-row
                    className="grid grid-cols-4 items-center gap-px border-b border-line bg-line last:border-0"
                  >
                    <div className="bg-panel px-3 py-2.5">
                      <span className="tnum text-sm text-ink">{lit}</span>
                    </div>
                    <div className="bg-panel px-3 py-2.5">
                      <span className="tnum text-sm text-muted">{uni}</span>
                    </div>
                    <div className="bg-panel px-3 py-2.5">
                      <span className="tnum text-sm text-muted">{auth}</span>
                    </div>
                    <div className="flex items-center justify-between bg-panel px-3 py-2.5">
                      <span className="tnum text-sm text-signal">{idx}</span>
                      <span className="tnum text-xs text-epitope">{letter}</span>
                    </div>
                  </div>
                ))}
              </div>
              <p
                data-num-converge
                className="mt-3 text-center text-xs text-faint opacity-0"
              >
                Four different numbers. One catalytic site, spelling S·K·S·E.
              </p>
            </div>
          </div>
        </section>

        <div data-section-rule className="hairline mx-auto max-w-[1400px]" />

        {/* --- workflow -------------------------------------------------- */}
        <section data-steps className="mx-auto max-w-[1400px] px-6 py-20 lg:py-28">
          <p className="mb-4 text-[11px] uppercase tracking-[0.2em] text-signal">
            The workflow
          </p>
          <h2 className="display mb-12 max-w-2xl text-[clamp(1.8rem,3.4vw,2.9rem)]">
            Seven steps, one environment.
          </h2>
          <ol className="grid gap-px bg-line sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step) => (
              <li key={step.n} data-step className="bg-panel p-5">
                <span className="tnum text-[11px] text-signal">{step.n}</span>
                <h3 className="mt-2 text-base font-medium text-ink">{step.t}</h3>
                <p className="mt-2 text-xs leading-relaxed text-muted">{step.d}</p>
              </li>
            ))}
          </ol>
        </section>

        <div data-section-rule className="hairline mx-auto max-w-[1400px]" />

        {/* --- the honest bit ------------------------------------------- */}
        <section className="mx-auto max-w-[1400px] px-6 py-20 lg:py-28">
          <div className="grid gap-10 lg:grid-cols-2 lg:gap-16">
            <div>
              <p className="mb-4 text-[11px] uppercase tracking-[0.2em] text-caution">
                What this is not
              </p>
              <h2 className="display text-[clamp(1.8rem,3.4vw,2.9rem)]">
                None of this is a medicine.
              </h2>
            </div>
            <div className="space-y-4 text-sm leading-relaxed text-muted">
              <p>
                Biostruct produces computational prioritisation: an ordering of
                which candidates are worth testing first. It does not show that
                any of them binds its target, and it certainly does not show a
                therapeutic effect. Experimental validation is always required.
              </p>
              <p>
                That is not boilerplate. A real run against KPC-2 returned
                predicted binding confidences around{" "}
                <span className="tnum text-ink">2×10⁻⁶</span>. Because scores are
                normalised within a run, the best of those ten designs still
                ranked first — so the platform reports the absolute number in the
                same breath as the rank, and says plainly when nothing in a run
                is expected to bind.
              </p>
              <p className="text-ink">
                A real campaign generates thousands of designs and expects almost
                all of them to fail. An interface that hides that is not helping
                anyone.
              </p>
            </div>
          </div>
        </section>

        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1400px] flex-wrap items-center justify-between gap-4 px-6 py-8">
            <p className="text-xs text-faint">
              Biostruct · computational prioritisation, not evidence of
              therapeutic effect
            </p>
            <Link href="/register">
              <Button variant="primary">Start a project</Button>
            </Link>
          </div>
        </footer>
      </main>
    </div>
  );
}

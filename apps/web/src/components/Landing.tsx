"use client";

/**
 * Landing page. One job: make a stranger understand what this does, and why it
 * matters, in about eight seconds.
 *
 * The signature is that the specimen is the real thing — the KPC-2 enzyme this
 * project targets, straight from the PDB, turning the way it would in the
 * software a structural biologist already uses. A dark viewport inset into a
 * white page: the one bold surface, with everything else quiet around it.
 *
 * Motion is deliberately thinner than v1. The current design guidance is
 * explicit that a fade-up on every section reads as generated work, so there is
 * one orchestrated entrance, one scroll set-piece that earns its scrub because
 * the fact is genuinely surprising, and nothing else.
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
  {
    t: "Name the infection",
    d: "Start from the bug on the ward, not from a protein ID you already know.",
  },
  {
    t: "Find what makes it resistant",
    d: "Usually an enzyme the bacterium makes to destroy the drug. We gather what is known about it and say what we could not reach.",
  },
  {
    t: "Choose where to attack it",
    d: "The working part of that enzyme, pre-selected and checked against the measured structure.",
  },
  {
    t: "Design proteins against it",
    d: "A model invents small proteins shaped to jam that spot. You see the price before anything is spent.",
  },
  {
    t: "Look at what came back",
    d: "Every predicted shape, rendered, with the designed protein and the target in different colours.",
  },
  {
    t: "See what is worth a lab",
    d: "Ranked, with the reasoning shown and the real odds stated — including when the answer is none of them.",
  },
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
        .from("[data-specimen]", { opacity: 0, duration: 1.1 }, 0.2);

      // The one set-piece: the numbering problem. It is the product's actual
      // differentiator and a genuinely surprising fact, so it earns a scrub.
      gsap
        .timeline({
          scrollTrigger: {
            trigger: "[data-numbering]",
            start: "top 72%",
            end: "bottom 60%",
            scrub: 0.6,
          },
        })
        .from("[data-num-row]", { xPercent: -4, opacity: 0, stagger: 0.25, duration: 1 })
        .to("[data-num-converge]", { opacity: 1, duration: 0.8 }, 0.6);

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
        <div className="mx-auto flex max-w-[1240px] items-center justify-between px-6 py-3.5">
          <span className="display text-[19px] text-ink">Biostruct</span>
          <div className="flex items-center gap-2">
            <Link href="/login">
              <Button variant="ghost">Sign in</Button>
            </Link>
            <Link href="/register">
              <Button variant="primary">Get started</Button>
            </Link>
          </div>
        </div>
      </nav>

      <main id="main">
        {/* --- hero ------------------------------------------------------ */}
        <section className="relative mx-auto grid max-w-[1240px] gap-12 px-6 pb-20 pt-14 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:gap-10 lg:pt-20">
          <div className="relative z-10">
            <h1 className="display text-[clamp(2.1rem,4.4vw,3.6rem)]">
              <span data-split className="block">
                Some infections have run out of antibiotics. Let&rsquo;s design
                something new.
              </span>
            </h1>

            <p
              data-reveal="pending"
              data-sub
              className="mt-6 max-w-xl text-[17px] leading-relaxed text-muted"
            >
              Bacteria survive our drugs by making enzymes that chop them up.
              Biostruct designs small proteins shaped to jam those enzymes, shows
              you each one in 3D, and tells you honestly which are worth a lab —
              including when the answer is none of them.
            </p>

            <div data-reveal="pending" data-cta className="mt-8 flex flex-wrap gap-3">
              <Link href="/register">
                <Button variant="primary" className="px-5 py-2.5">
                  Get started
                </Button>
              </Link>
              <Link href="/login">
                <Button className="px-5 py-2.5">Sign in</Button>
              </Link>
            </div>

            <p
              data-reveal="pending"
              data-sub
              className="mt-8 max-w-xl border-l-[3px] border-line pl-4 text-[15px] leading-relaxed text-muted"
            >
              Built for the places where resistant infections are hardest to
              treat, by people who have to work with what the published evidence
              actually says.
            </p>
          </div>

          {/* The specimen. Real structure, turning, as the field expects. */}
          <div
            data-specimen
            className="specimen relative aspect-square w-full overflow-hidden rounded-lg lg:aspect-[4/5]"
          >
            {showViewer ? (
              <StructureViewer url={KPC2_CIF} targetChains={["A"]} />
            ) : (
              <div className="absolute inset-0 grid place-items-center">
                <span className="text-[13px] text-faint">Loading the model…</span>
              </div>
            )}
            {/* Top, not bottom: the viewer puts its own legend and controls
                along the bottom edge, and the two would sit on top of each
                other. */}
            <div className="pointer-events-none absolute inset-x-3 top-3 rounded-md bg-black/45 px-3 py-2 backdrop-blur-sm">
              <p className="text-[13px] leading-snug text-white">
                This is the real enzyme that lets <em>Klebsiella</em> survive our
                last-resort antibiotics. Measured in a lab, not drawn.
              </p>
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-[1240px] border-t border-line" />

        {/* --- the numbering problem ------------------------------------- */}
        <section data-numbering className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
          <div className="grid gap-12 lg:grid-cols-[0.95fr_1.05fr] lg:gap-16">
            <div>
              <h2 className="display text-[clamp(1.7rem,3.2vw,2.6rem)]">
                The mistake that produces a confident, wrong answer
              </h2>
              <p className="mt-6 max-w-lg text-[15px] leading-relaxed text-muted">
                A protein is a chain, and every link in it has a number. The
                trouble is that three different databases number the same link
                differently. The spot you want on this enzyme is called 69 in one,
                70 in the papers, and 39 in the file the design model reads.
              </p>
              <p className="mt-4 max-w-lg text-[15px] leading-relaxed text-muted">
                Pick the wrong one and nothing breaks. No error appears. The run
                finishes, charges you, and quietly designs proteins against a part
                of the enzyme that does not matter.
              </p>
              <p className="mt-6 max-w-lg border-l-[3px] border-signal pl-4 text-[15px] leading-relaxed text-ink">
                Biostruct converts between all three, shows you the conversion,
                and refuses rather than guessing. Tell it which building block you
                expect to find there and it proves it landed on that one.
              </p>
            </div>

            <div className="self-center">
              <div className="overflow-hidden rounded-lg border border-line">
                <div className="grid grid-cols-4 gap-px border-b border-line bg-line text-[13px]">
                  {["In papers", "In UniProt", "In the file", "To the model"].map((h) => (
                    <div key={h} className="bg-wash px-3 py-2 text-muted">
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
                      <span className="tnum text-[13px] text-danger">{letter}</span>
                    </div>
                  </div>
                ))}
              </div>
              <p
                data-num-converge
                className="mt-3 text-center text-[13px] text-muted opacity-0"
              >
                Four different numbers, one spot on the enzyme.
              </p>
            </div>
          </div>
        </section>

        <div className="mx-auto max-w-[1240px] border-t border-line" />

        {/* --- workflow -------------------------------------------------- */}
        <section className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
          <h2 className="display mb-3 max-w-2xl text-[clamp(1.7rem,3.2vw,2.6rem)]">
            What actually happens
          </h2>
          <p className="mb-12 max-w-xl text-[15px] leading-relaxed text-muted">
            Six steps, in one place. You do the first three; the computer does the
            rest and shows its working.
          </p>
          <ol className="grid gap-x-10 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {STEPS.map((step, i) => (
              <li key={step.t} className="border-t border-line pt-4">
                <span className="tnum text-[13px] text-faint">{i + 1}</span>
                <h3 className="mt-1 text-base font-medium text-ink">{step.t}</h3>
                <p className="mt-2 text-[14px] leading-relaxed text-muted">{step.d}</p>
              </li>
            ))}
          </ol>
        </section>

        <div className="mx-auto max-w-[1240px] border-t border-line" />

        {/* --- the honest bit ------------------------------------------- */}
        <section className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
          <div className="grid gap-10 lg:grid-cols-2 lg:gap-16">
            <div>
              <h2 className="display text-[clamp(1.7rem,3.2vw,2.6rem)]">
                None of this is a medicine
              </h2>
            </div>
            <div className="space-y-4 text-[15px] leading-relaxed text-muted">
              <p>
                What comes out is a shortlist: which designs are worth testing
                first. It is not proof that any of them sticks to its target, and
                it is certainly not proof that any of them would treat anyone.
                Somebody has to make them and test them.
              </p>
              <p>
                That is not small print. A real run against this enzyme came back
                with every design scoring about{" "}
                <span className="tnum text-ink">two in a million</span>. Because
                rankings are worked out within a batch, the best of those ten
                still came first — so Biostruct prints the real odds next to the
                rank, and says plainly when nothing in a batch is expected to
                work.
              </p>
              <p className="text-ink">
                A serious campaign designs thousands and expects nearly all of
                them to fail. An interface that hides that is not helping anyone.
              </p>
            </div>
          </div>
        </section>

        <footer className="border-t border-line">
          <div className="mx-auto flex max-w-[1240px] flex-wrap items-center justify-between gap-4 px-6 py-8">
            <p className="text-[13px] text-muted">
              Biostruct — a shortlist for the bench, not evidence of a cure.
            </p>
            <Link href="/register">
              <Button variant="primary">Get started</Button>
            </Link>
          </div>
        </footer>
      </main>
    </div>
  );
}

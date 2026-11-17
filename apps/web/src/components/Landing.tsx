"use client";

/**
 * Landing page. One job: make a stranger understand, in about eight seconds,
 * what this is for and why it matters — then tell the story properly for the
 * person who keeps reading.
 *
 * The story is the product's actual argument, in order:
 *   1. Antibiotics are failing, and they are failing worst here.
 *   2. The reason is a specific protein the bacterium makes.
 *   3. Finding something that blocks that protein is a computer's job, and
 *      until now it needed a supercomputer and a structural biologist.
 *   4. Antidote is that, as something you can talk to — in your own language.
 *
 * Every number on this page has a citation, which is the same promise the
 * product makes inside. A landing page that claims what the product refuses to
 * claim would be the loudest possible lie about it.
 *
 * The signature is that the specimen is real: KPC-2, the enzyme behind much of
 * Africa's carbapenem resistance, straight from the PDB, turning in the dark
 * viewport that is the page's one bold surface.
 *
 * Motion is deliberately restrained. Current design guidance is explicit that a
 * fade-up on every section reads as generated work, so there is one
 * orchestrated entrance, one scroll set-piece on the fact that genuinely
 * surprises people, and nothing else.
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
// is exactly what the agent does with a real target.
const KPC2_CIF = "https://files.rcsb.org/download/2OV5.cif";

const LANCET =
  "https://www.thelancet.com/journals/langlo/article/PIIS2214-109X(23)00539-9/fulltext";

/** The three things a researcher actually does here. Steps 2 and 3 of drug
 *  discovery — the only two a computer can do — and nothing it cannot. */
const CAPABILITIES = [
  {
    n: "01",
    t: "Paste the sequence",
    d: "The amino acids of the enzyme your isolate actually makes — straight out of your sequencing output, FASTA header and line breaks and all. No protein identifiers to look up, no file formats to convert.",
  },
  {
    n: "02",
    t: "See the structure",
    d: "A model predicts how that chain folds in three dimensions, and you can turn it on screen. This is the mutant you have, not a textbook reference — which is the point, because a resistant isolate's enzyme is rarely the one in the database.",
  },
  {
    n: "03",
    t: "Get ten molecules to test",
    d: "A second model docks candidate molecules into that structure and ranks them. A compound already known to inhibit this class of enzyme goes in with them, so you can tell at a glance whether the run worked. Download the shortlist and take it to the bench.",
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

      // The one set-piece: the scale of the problem, counted out. It earns a
      // scrub because the last line genuinely surprises people — this is the
      // worst-affected region in the world, not a footnote to someone else's
      // crisis.
      //
      // Hidden here rather than with a CSS class, so the line is readable when
      // motion is reduced and when JS never runs. Hiding it in markup left it
      // permanently invisible in both cases.
      gsap.set("[data-num-converge]", { opacity: 0 });
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

      // The remaining sections each get their own treatment. Repeating one
      // fade-up down the page is the single clearest tell of generated work,
      // so the gesture changes with what the section is doing: a mechanism
      // explained line by line, a list counted off, a caveat uncovered, an
      // invitation that arrives.

      // "A bacterium does not resist a drug" — the heading lands as a
      // statement, then the explanation follows it in.
      const mechanismHeading = new SplitText("[data-mechanism] h2", { type: "lines" });
      gsap
        .timeline({
          scrollTrigger: { trigger: "[data-mechanism]", start: "top 72%" },
          defaults: { ease: "expo.out" },
        })
        .from(mechanismHeading.lines, {
          yPercent: 110,
          opacity: 0,
          duration: 0.9,
          stagger: 0.07,
        })
        .from("[data-mechanism] p", { x: 18, opacity: 0, duration: 0.7, stagger: 0.1 }, 0.25);

      // The three capabilities are counted off: each rule draws across before
      // its item appears, so the list reads as enumerated rather than revealed.
      gsap
        .timeline({
          scrollTrigger: { trigger: "[data-capabilities]", start: "top 70%" },
          defaults: { ease: "expo.out" },
        })
        .from("[data-capabilities] li", {
          scaleX: 0,
          transformOrigin: "left center",
          duration: 0.55,
          stagger: 0.12,
        })
        .from(
          "[data-capabilities] li > *",
          { y: 14, opacity: 0, duration: 0.6, stagger: 0.05 },
          0.2,
        );

      // The caveats are uncovered rather than floated in — a wipe, because the
      // point of the section is that nothing is being hidden.
      gsap
        .timeline({
          scrollTrigger: { trigger: "[data-caveats]", start: "top 72%" },
          defaults: { ease: "expo.out" },
        })
        .from("[data-caveats] h2", {
          clipPath: "inset(0 100% 0 0)",
          duration: 0.9,
        })
        .from("[data-caveats] p", { opacity: 0, y: 10, duration: 0.7, stagger: 0.12 }, 0.2);

      // The close simply arrives.
      gsap
        .timeline({
          scrollTrigger: { trigger: "[data-close]", start: "top 78%" },
          defaults: { ease: "expo.out" },
        })
        .from("[data-close] h2", { y: 20, opacity: 0, duration: 0.8 })
        .from("[data-close] p", { y: 14, opacity: 0, duration: 0.6 }, 0.12)
        .from("[data-close] a", { scale: 0.94, opacity: 0, duration: 0.6 }, 0.22);

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
          <span className="display text-[19px] text-ink">Antidote</span>
          <div className="flex items-center gap-2">
            <Link href="/login">
              <Button variant="ghost">Sign in</Button>
            </Link>
            <Link href="/register">
              <Button variant="primary">Fold a protein</Button>
            </Link>
          </div>
        </div>
      </nav>

      <main id="main">
        {/* --- hero ------------------------------------------------------ */}
        <section className="relative mx-auto grid max-w-[1240px] gap-12 px-6 pb-20 pt-14 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:gap-10 lg:pt-20">
          <div>
            <h1
              data-split
              className="display text-[clamp(2.1rem,4.4vw,3.6rem)] leading-[1.04]"
            >
Paste a sequence. Get the structure. Get ten molecules to test.
            </h1>
            <p
              data-sub
              className="mt-6 max-w-xl text-[17px] leading-relaxed text-muted"
            >
              Antidote does the two steps of drug discovery a computer can
              actually do. Paste the amino-acid sequence of the enzyme making an
              infection drug-resistant; it predicts the 3D structure, then ranks
              molecules that could jam it — a shortlist for your laboratory to
              test.
            </p>
            <div data-cta className="mt-8 flex flex-wrap items-center gap-3">
              <Link href="/register">
                <Button variant="primary">Fold a protein</Button>
              </Link>
              <Link href="/login">
                <Button>Sign in</Button>
              </Link>
            </div>
            <p className="mt-5 max-w-xl text-[13px] leading-relaxed text-faint">
              Two steps, two buttons. Everything after the shortlist is a bench,
              and that part is still yours.
            </p>
          </div>

          <figure data-specimen className="relative">
            <div className="overflow-hidden rounded-lg border border-line bg-specimen">
              {showViewer ? (
                <StructureViewer
                  url={KPC2_CIF}
                  targetChains={["A"]}
                  className="aspect-square w-full"
                />
              ) : (
                <div className="aspect-square w-full bg-specimen" />
              )}
            </div>
            <figcaption className="mt-3 text-[13px] leading-relaxed text-muted">
              <span className="text-ink">KPC-2 carbapenemase</span> — the enzyme
              that lets <em>Klebsiella pneumoniae</em> destroy carbapenems, our
              antibiotic of last resort. Measured structure, PDB 2OV5. This is
              the shape a drug has to fit into.
            </figcaption>
          </figure>
        </section>

        {/* --- the problem, counted out ---------------------------------- */}
        <section
          data-numbering
          className="border-y border-line bg-wash"
        >
          <div className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
            <h2 className="display max-w-2xl text-[clamp(1.7rem,3.2vw,2.6rem)] leading-tight">
              Resistance is not a future problem here.
            </h2>

            <dl className="mt-10 flex flex-col gap-0">
              {[
                {
                  v: "1,050,000",
                  d: "deaths associated with bacterial antimicrobial resistance in the WHO African region, in a single year.",
                },
                {
                  v: "250,000",
                  d: "of those deaths caused directly by the resistance itself — not merely alongside it.",
                },
                {
                  v: "23.5",
                  d: "deaths per 100,000 people in sub-Saharan Africa.",
                },
              ].map((row) => (
                <div
                  key={row.v}
                  data-num-row
                  className="grid gap-2 border-t border-line py-6 sm:grid-cols-[minmax(0,260px)_1fr] sm:gap-8"
                >
                  <dt className="font-mono text-[clamp(1.6rem,3vw,2.4rem)] leading-none text-ink">
                    {row.v}
                  </dt>
                  <dd className="max-w-xl text-[15px] leading-relaxed text-muted">
                    {row.d}
                  </dd>
                </div>
              ))}
            </dl>

            <p
              data-num-converge
              className="mt-8 max-w-2xl border-t border-line pt-8 text-[17px] leading-relaxed text-ink"
            >
              That last figure is the highest rate of any region on earth. The
              places with the most to lose have the least access to the tools
              that find new drugs.
            </p>

            <p className="mt-5 text-[13px] text-faint">
              Figures from the Lancet Global Health cross-country analysis of the
              WHO African region.{" "}
              <a
                href={LANCET}
                target="_blank"
                rel="noreferrer"
                className="text-signal underline decoration-line underline-offset-2 transition-colors duration-[var(--dur-ui)] hover:decoration-signal"
              >
                Read the study
              </a>
              .
            </p>
          </div>
        </section>

        {/* --- why it happens -------------------------------------------- */}
        <section data-mechanism className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
          <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
            <h2 className="display text-[clamp(1.7rem,3.2vw,2.6rem)] leading-tight">
              A bacterium does not resist a drug. It makes a protein that
              destroys it.
            </h2>
            <div className="flex flex-col gap-4 text-[15px] leading-relaxed text-muted">
              <p>
                When meropenem stops working on a ward, there is usually a
                specific enzyme behind it — a carbapenemase like KPC, NDM or
                OXA-48 — cutting the antibiotic apart before it can act.
              </p>
              <p>
                Which enzyme it is matters enormously, because the drugs that
                block one do not block another. Avibactam shuts down KPC and is
                very nearly useless against NDM. Getting that wrong wastes
                months.
              </p>
              <p className="text-ink">
                So the question is never &ldquo;what antibiotic next&rdquo;. It
                is: which protein is doing this, what shape is it, and what fits
                into it.
              </p>
            </div>
          </div>
        </section>

        {/* --- what you do here ------------------------------------------ */}
        <section className="border-t border-line">
          <div className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
            <h2 className="display mb-12 max-w-2xl text-[clamp(1.7rem,3.2vw,2.6rem)] leading-tight">
              The whole thing, in three steps.
            </h2>
            <ol data-capabilities className="grid gap-10 md:grid-cols-3 md:gap-8">
              {CAPABILITIES.map((step) => (
                <li key={step.n} className="border-t border-line pt-5">
                  <span className="font-mono text-[11px] tracking-[0.1em] text-faint">
                    {step.n}
                  </span>
                  <h3 className="mt-2 text-[17px] text-ink">{step.t}</h3>
                  <p className="mt-2.5 text-[14px] leading-relaxed text-muted">
                    {step.d}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* --- the honesty section --------------------------------------- */}
        <section data-caveats className="border-t border-line bg-wash">
          <div className="mx-auto max-w-[1240px] px-6 py-20 lg:py-28">
            <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr] lg:gap-16">
              <h2 className="display text-[clamp(1.7rem,3.2vw,2.6rem)] leading-tight">
                What this is not.
              </h2>
              <div className="flex flex-col gap-4 text-[15px] leading-relaxed text-muted">
                <p>
                  Antidote does not discover drugs. It does two steps — sequence
                  to structure, structure to candidate molecules — and hands you
                  a ranked shortlist. Everything after that is a bench, an animal
                  model and a trial.
                </p>
                <p>
                  Nothing here predicts toxicity or safety. No free tool does, and
                  claiming otherwise about a medicine would be the most dangerous
                  thing this product could say.
                </p>
                <p className="text-ink">
                  Every molecule it ranks is a hypothesis. That is why a known
                  inhibitor rides along in every screen: if it does not come out
                  near the top, the ranking is telling you nothing, and Antidote
                  says so instead of handing you ten numbers that look like
                  findings.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* --- close ------------------------------------------------------ */}
        <section className="border-t border-line">
          <div data-close className="mx-auto max-w-[1240px] px-6 py-20 text-center lg:py-28">
            <h2 className="display mx-auto max-w-2xl text-[clamp(1.7rem,3.2vw,2.6rem)] leading-tight">
              Start with the infection in front of you.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-muted">
              Paste a sequence and watch it fold. No identifiers to look up, no
              file formats, no queue.
            </p>
            <div className="mt-8 flex justify-center">
              <Link href="/register">
                <Button variant="primary">Fold a protein</Button>
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1240px] flex-wrap items-center justify-between gap-3 px-6 py-6 text-[13px] text-faint">
          <span>Antidote — research tooling for drug-resistant infection.</span>
          <span>
            Computational prioritisation, not evidence of therapeutic effect.
          </span>
        </div>
      </footer>
    </div>
  );
}

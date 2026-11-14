"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, endpoints, formatMoney, type Project } from "@/lib/api";
import { Button, Empty, Field, Notice, Panel, SkeletonRows } from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import { INFECTIONS, type InfectionCase, rememberInfection } from "@/lib/infections";

/**
 * What a project costs by default. Designing one protein is about five cents,
 * so this is roughly five hundred designs — far more than anyone needs for a
 * first run, and nothing is spent without the exact figure being confirmed.
 * It is only on the form for people who deliberately want a tighter ceiling.
 */
const DEFAULT_BUDGET = "25";

export default function ProjectsView() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState("");
  const [custom, setCustom] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [infection, setInfection] = useState("");
  const [pathogen, setPathogen] = useState("");
  const [budget, setBudget] = useState(DEFAULT_BUDGET);

  useEffect(() => {
    endpoints
      .projects()
      .then((r) => setProjects(r.items))
      .catch((e) =>
        setError(e instanceof ApiError ? e.message_for_user : "Could not load your work"),
      );
  }, []);

  async function make(
    body: { name: string; infection: string; pathogen: string; budget_usd: string },
    key: string,
    /** Set when the user picked from the catalogue, so step 1 can skip ahead. */
    infectionId?: string,
  ) {
    setBusy(key);
    setError("");
    try {
      const project = await endpoints.createProject({
        name: body.name,
        infection: { name: body.infection, pathogen: body.pathogen || null },
        // Sent as a string on purpose: the API rejects numbers for money so an
        // amount cannot drift through binary floating point.
        budget_usd: body.budget_usd,
      });
      if (infectionId) rememberInfection(project.id, infectionId);
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message_for_user : "Could not start that project");
      setBusy(null);
    }
  }

  function pick(c: InfectionCase) {
    void make(
      {
        name: c.projectName,
        infection: c.clinicalName,
        pathogen: c.organism,
        budget_usd: DEFAULT_BUDGET,
      },
      c.id,
      c.id,
    );
  }

  return (
    <div className="mx-auto max-w-[1000px] px-4 py-10 sm:px-6">
      <div className="mb-8 max-w-2xl">
        <h1 className="display text-[30px]">Your work</h1>
        <p className="mt-2 text-[15px] leading-relaxed text-muted">
          One project per infection. Each one starts from a bug that beats our
          antibiotics and ends with a short list of proteins worth taking into a lab.
        </p>
      </div>

      {error && (
        <div className="mb-6">
          <Notice tone="danger" title={error} />
        </div>
      )}

      {/* --- what this actually is ---------------------------------------
          Nobody should have to infer the point of the product from a form.
          Three beats: the problem, the move, what you walk away with. */}
      <section className="mb-8 rounded-lg border border-line bg-wash p-5 sm:p-6">
        <h2 className="display text-[19px] text-ink">What this is for</h2>
        <dl className="mt-4 grid gap-5 sm:grid-cols-3">
          <div>
            <dt className="text-sm font-medium text-ink">The problem</dt>
            <dd className="mt-1.5 text-[13.5px] leading-relaxed text-muted">
              An antibiotic works by jamming something a bacterium needs. Some
              bacteria now make an enzyme that cuts the antibiotic up before it can.
              For a few infections, every drug we have is being cut up.
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-ink">What we do about it</dt>
            <dd className="mt-1.5 text-[13.5px] leading-relaxed text-muted">
              Rather than hunt for another drug, we design a new protein — a small
              molecule built to clamp onto that enzyme and block it, so the
              antibiotic survives long enough to work.
            </dd>
          </div>
          <div>
            <dt className="text-sm font-medium text-ink">What you end up with</dt>
            <dd className="mt-1.5 text-[13.5px] leading-relaxed text-muted">
              A ranked shortlist of designed proteins, each with a predicted 3D
              shape and a file a lab can build from. These are predictions —
              somebody still has to make them and test them.
            </dd>
          </div>
        </dl>
      </section>

      {/* --- choose, don't type ------------------------------------------- */}
      {!custom && (
        <section className="mb-8">
          <h2 className="text-base font-medium text-ink">
            Which infection do you want to work on?
          </h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted">
            Pick one and everything is set up for you. All four are bacteria that
            survive by cutting antibiotics apart, which is the kind of problem this
            tool is built for.
          </p>

          <ul className="mt-4 grid gap-3 sm:grid-cols-2">
            {INFECTIONS.map((c, index) => (
              <motion.li
                key={c.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: DUR.ui, delay: stagger(index), ease: EASE_OUT }}
              >
                <button
                  type="button"
                  onClick={() => pick(c)}
                  disabled={busy !== null}
                  className={`flex h-full w-full flex-col rounded-lg border p-4 text-left transition-colors duration-[var(--dur-ui)] disabled:opacity-60 ${
                    c.worked
                      ? "border-signal bg-signal-dim hover:bg-signal-dim/70"
                      : "border-line bg-panel hover:border-signal hover:bg-signal-dim"
                  }`}
                >
                  {c.worked && (
                    <span className="mb-1.5 text-[12px] font-medium text-signal">
                      Start here — this is the one we have run end to end
                    </span>
                  )}
                  <span className="text-sm font-medium leading-snug text-ink">
                    {c.headline}
                  </span>
                  <span className="mt-1 text-[13px] italic text-muted">{c.organism}</span>
                  <span className="mt-2.5 text-[13px] leading-relaxed text-muted">
                    <span className="text-ink/70">Where you meet it. </span>
                    {c.where}
                  </span>
                  <span className="mt-2 text-[13px] leading-relaxed text-muted">
                    <span className="text-ink/70">Why it is hard to treat. </span>
                    {c.how}
                  </span>
                  <span className="mt-3 flex-1" />
                  <span className="mt-1 text-[12.5px] text-faint">
                    We will attack its {c.enzyme}
                  </span>
                  <span className="mt-3 text-[13px] font-medium text-signal">
                    {busy === c.id ? "Setting it up…" : "Work on this →"}
                  </span>
                </button>
              </motion.li>
            ))}
          </ul>

          <button
            type="button"
            onClick={() => setCustom(true)}
            disabled={busy !== null}
            className="mt-4 text-sm text-muted underline-offset-4 transition-colors duration-[var(--dur-ui)] hover:text-ink hover:underline"
          >
            None of these — I know the infection I want to work on
          </button>
        </section>
      )}

      {/* --- the expert path, unchanged in capability --------------------- */}
      {custom && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: DUR.panel, ease: EASE_OUT }}
          className="mb-8"
        >
          <Panel title="Describe your own infection">
            <form
              method="post"
              action=""
              onSubmit={(e) => {
                e.preventDefault();
                void make({ name, infection, pathogen, budget_usd: budget }, "custom");
              }}
              className="space-y-5 p-4"
            >
              <Field
                label="What should we call this project?"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Resistant Klebsiella on the ICU ward"
                hint="Any name that will mean something to you in a week."
              />
              <Field
                label="What infection are you working on?"
                required
                value={infection}
                onChange={(e) => setInfection(e.target.value)}
                placeholder="Carbapenem-resistant Klebsiella pneumoniae"
                hint="Describe it the way you would to a colleague. If you are not sure, go back and pick one of the four instead."
              />
              <Field
                label="Which organism, if you know it?"
                value={pathogen}
                onChange={(e) => setPathogen(e.target.value)}
                placeholder="Klebsiella pneumoniae"
                hint="Optional. Leave it blank if you do not know."
              />
              <Field
                label="Most you are willing to spend on this project"
                required
                inputMode="decimal"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                hint="A hard ceiling, in US dollars. Designing one protein costs about five cents, and nothing is spent without you confirming the exact figure first."
              />
              <div className="flex gap-2">
                <Button type="submit" variant="primary" disabled={busy !== null}>
                  {busy === "custom" ? "Starting…" : "Start this project"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setCustom(false)}
                  disabled={busy !== null}
                >
                  Back to the four
                </Button>
              </div>
            </form>
          </Panel>
        </motion.div>
      )}

      <Panel
        title={
          projects === null
            ? "Loading"
            : projects.length === 1
              ? "1 project"
              : `${projects.length} projects`
        }
      >
        {projects === null ? (
          <SkeletonRows rows={3} />
        ) : projects.length === 0 ? (
          <Empty title="Nothing here yet">
            Pick one of the infections above — the first one takes about a minute.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {projects.map((project, index) => {
              const remaining =
                Number(project.budget.limit_usd) -
                Number(project.budget.spent_usd) -
                Number(project.budget.reserved_usd);
              return (
                <motion.li
                  key={project.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: DUR.ui,
                    delay: stagger(index),
                    ease: EASE_OUT,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => router.push(`/projects/${project.id}`)}
                    className="flex w-full items-center justify-between gap-4 px-4 py-3.5 text-left transition-colors duration-[var(--dur-ui)] hover:bg-panel-hi"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-ink">
                        {project.name}
                      </span>
                      <span className="mt-0.5 block truncate text-[13px] text-muted">
                        {project.infection.name}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="tnum block text-sm text-ink">
                        {formatMoney(String(remaining))}
                      </span>
                      <span className="block text-[12px] text-muted">left to spend</span>
                    </span>
                  </button>
                </motion.li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}

"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, endpoints, formatMoney, type Project } from "@/lib/api";
import { Button, Empty, Field, Notice, Panel, SkeletonRows } from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

/** The case this product has been run against end to end. */
const EXAMPLE = {
  name: "Klebsiella that survives our last-resort antibiotics",
  infection: "Carbapenem-resistant Klebsiella pneumoniae",
  pathogen: "Klebsiella pneumoniae",
  budget_usd: "25",
};

export default function ProjectsView() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [infection, setInfection] = useState("");
  const [pathogen, setPathogen] = useState("");
  const [budget, setBudget] = useState("25");

  useEffect(() => {
    endpoints
      .projects()
      .then((r) => setProjects(r.items))
      .catch((e) =>
        setError(e instanceof ApiError ? e.message_for_user : "Could not load your work"),
      );
  }, []);

  async function make(body: {
    name: string;
    infection: string;
    pathogen: string;
    budget_usd: string;
  }) {
    setBusy(true);
    setError("");
    try {
      const project = await endpoints.createProject({
        name: body.name,
        infection: { name: body.infection, pathogen: body.pathogen || null },
        // Sent as a string on purpose: the API rejects numbers for money so an
        // amount cannot drift through binary floating point.
        budget_usd: body.budget_usd,
      });
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message_for_user : "Could not start that project",
      );
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1000px] px-4 py-10 sm:px-6">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="display text-[30px]">Your work</h1>
          <p className="mt-2 text-[15px] leading-relaxed text-muted">
            One project per infection. Each one starts from a resistant bug and
            ends with a short list of proteins worth taking into a lab.
          </p>
        </div>
      </div>

      {error && (
        <div className="mb-6">
          <Notice tone="danger" title={error} />
        </div>
      )}

      {/* The five-minute path. Nobody evaluating this should have to know what
          to type into an empty form first. */}
      {!creating && (
        <div className="mb-8 rounded-lg border border-signal bg-signal-dim p-5">
          <h2 className="text-base font-medium text-ink">
            New here? Start from a real case
          </h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink/80">
            <em>Klebsiella pneumoniae</em> that shrugs off carbapenems — the
            antibiotics kept back for when everything else has failed. It survives
            by making an enzyme that chops the drug up. We will design proteins to
            jam that enzyme. Everything is filled in for you, and the first run is
            free.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={busy}
              onClick={() => void make(EXAMPLE)}
            >
              {busy ? "Setting it up…" : "Walk me through this example"}
            </Button>
            <Button variant="ghost" onClick={() => setCreating(true)} disabled={busy}>
              I have my own infection in mind
            </Button>
          </div>
        </div>
      )}

      {creating && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: DUR.panel, ease: EASE_OUT }}
          className="mb-8"
        >
          <Panel title="Start a project">
            <form
              method="post"
              action=""
              onSubmit={(e) => {
                e.preventDefault();
                void make({ name, infection, pathogen, budget_usd: budget });
              }}
              className="space-y-5 p-4"
            >
              <Field
                label="What should we call this?"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Resistant Klebsiella on the ICU ward"
              />
              <Field
                label="What infection are you working on?"
                required
                value={infection}
                onChange={(e) => setInfection(e.target.value)}
                placeholder="Carbapenem-resistant Klebsiella pneumoniae"
                hint="Describe it the way you would to a colleague."
              />
              <Field
                label="Which organism, if you know it?"
                value={pathogen}
                onChange={(e) => setPathogen(e.target.value)}
                placeholder="Klebsiella pneumoniae"
              />
              <Field
                label="How much are you willing to spend on this?"
                required
                inputMode="decimal"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                hint="A hard ceiling, in US dollars. Designing one protein costs about five cents, and nothing is spent without you confirming the exact figure first."
              />
              <div className="flex gap-2">
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? "Starting…" : "Start"}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setCreating(false)}>
                  Cancel
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
            Start with the example above — it takes about a minute.
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

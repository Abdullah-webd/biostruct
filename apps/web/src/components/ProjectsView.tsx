"use client";

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, endpoints, formatMoney, type Project } from "@/lib/api";
import { Button, Empty, Field, Notice, Panel, SkeletonRows } from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

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
        setError(e instanceof ApiError ? e.message_for_user : "Could not load projects"),
      );
  }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const project = await endpoints.createProject({
        name,
        infection: { name: infection, pathogen: pathogen || null },
        // Sent as a string on purpose: the API rejects numbers for money so an
        // amount cannot drift through binary floating point.
        budget_usd: budget,
      });
      router.push(`/projects/${project.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message_for_user : "Could not create the project");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[1100px] px-4 py-10 sm:px-6">
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="display text-3xl">Projects</h1>
          <p className="mt-2 text-sm text-muted">
            One project per infection. A project can hold several targets, because
            resistant isolates usually carry more than one resistance gene.
          </p>
        </div>
        {!creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            New project
          </Button>
        )}
      </div>

      {error && (
        <div className="mb-6">
          <Notice tone="danger" title={error} />
        </div>
      )}

      {creating && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: DUR.panel, ease: EASE_OUT }}
          className="mb-8"
        >
          <Panel title="New project">
            <form method="post" action="" onSubmit={create} className="space-y-5 p-4">
              <Field
                label="Project name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="KPC-2 nanobody binders"
              />
              <Field
                label="Infection"
                required
                value={infection}
                onChange={(e) => setInfection(e.target.value)}
                placeholder="Carbapenem-resistant Klebsiella pneumoniae"
                hint="Describe it the way you would to a colleague."
              />
              <Field
                label="Pathogen"
                value={pathogen}
                onChange={(e) => setPathogen(e.target.value)}
                placeholder="Klebsiella pneumoniae"
              />
              <Field
                label="Budget (USD)"
                required
                inputMode="decimal"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                hint="A hard ceiling on design spend for this project. Roughly $0.05 per design."
              />
              <div className="flex gap-2">
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? "Creating…" : "Create project"}
                </Button>
                <Button type="button" variant="ghost" onClick={() => setCreating(false)}>
                  Cancel
                </Button>
              </div>
            </form>
          </Panel>
        </motion.div>
      )}

      <Panel title={projects ? `${projects.length} project${projects.length === 1 ? "" : "s"}` : "Loading"}>
        {projects === null ? (
          <SkeletonRows rows={3} />
        ) : projects.length === 0 ? (
          <Empty title="No projects yet">
            Create one to start from an infection and work towards candidates.
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
                      <span className="mt-0.5 block truncate text-xs text-muted">
                        {project.infection.name}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="tnum block text-sm text-ink">
                        {formatMoney(String(remaining))}
                      </span>
                      <span className="block text-[10px] uppercase tracking-widest text-faint">
                        remaining
                      </span>
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

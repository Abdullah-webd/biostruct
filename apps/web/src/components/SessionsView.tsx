"use client";

/**
 * The session list: where a researcher resumes work or starts something new.
 *
 * Ordered by last activity rather than creation, which is what the API returns
 * and what a person coming back after a week actually wants — the conversation
 * they were in, not the newest one they happened to open.
 */

import { AnimatePresence, motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { type SessionSummary, agentApi } from "@/lib/agent";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";
import { Button, Empty, Notice, Panel, SkeletonRows } from "@/components/ui";

export default function SessionsView() {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    agentApi
      .sessions()
      .then((items) => alive && setSessions(items))
      .catch((e: unknown) => alive && setError(String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const start = useCallback(async () => {
    setCreating(true);
    setError("");
    try {
      const session = await agentApi.create({});
      router.push(`/sessions/${session.id}`);
    } catch (e: unknown) {
      setError(String(e));
      setCreating(false);
    }
  }, [router]);

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl text-ink">Research sessions</h1>
          <p className="mt-1 max-w-prose text-sm text-muted">
            Each session is one line of enquiry — an outbreak, an enzyme, a
            question about what the literature says. Antidote keeps the thread,
            the structures and the evidence together.
          </p>
        </div>
        <Button variant="primary" onClick={() => void start()} disabled={creating}>
          {creating ? "Starting…" : "New session"}
        </Button>
      </header>

      {error ? (
        <Notice tone="danger" title="Could not load your sessions">
          {error}
        </Notice>
      ) : null}

      {loading ? (
        <Panel className="p-4">
          <SkeletonRows rows={3} />
        </Panel>
      ) : sessions.length === 0 ? (
        <Empty title="No sessions yet">
          Start one and describe the infection you are working on, in your own
          words. Finding the protein and reading the literature costs nothing.
        </Empty>
      ) : (
        <ul className="flex flex-col gap-2">
          <AnimatePresence initial={false}>
            {sessions.map((session, i) => (
              <motion.li
                key={session.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{
                  duration: DUR.panel,
                  delay: stagger(i, 0.05),
                  ease: EASE_OUT,
                }}
              >
                <button
                  type="button"
                  onClick={() => router.push(`/sessions/${session.id}`)}
                  className="flex w-full items-center justify-between gap-4 rounded-lg border border-line bg-panel px-4 py-3 text-left transition-[border-color,background-color,transform] duration-[var(--dur-ui)] ease-[var(--ease-out)] hover:border-signal hover:bg-signal-dim active:scale-[0.995] active:duration-[var(--dur-press)]"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-[15px] text-ink">
                      {session.title}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted">
                      {session.message_count === 0
                        ? "Nothing asked yet"
                        : `${session.message_count} message${session.message_count === 1 ? "" : "s"}`}
                      {session.last_message_at
                        ? ` · ${relative(session.last_message_at)}`
                        : ""}
                    </span>
                  </span>
                  <span className="shrink-0 font-mono text-xs text-faint">
                    ${session.budget_remaining_usd} left
                  </span>
                </button>
              </motion.li>
            ))}
          </AnimatePresence>
        </ul>
      )}
    </div>
  );
}

/** Plain words, because a raw timestamp in a list is noise. */
function relative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

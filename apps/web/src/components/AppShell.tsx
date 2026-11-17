"use client";

/**
 * The application chrome: a persistent session rail on the left, the work on
 * the right. One surface, one flow — there is no second way through this
 * product and no navigation asking a researcher to choose a mental model
 * before they have done anything.
 *
 * The rail is the pattern every agent interface has converged on (ChatGPT,
 * Claude): conversations are the unit of work, they persist, and you return to
 * one rather than starting again. It collapses on narrow screens because a
 * 240px rail on a phone leaves nothing for the thread.
 *
 * Motion dial 4 — app-screen calm. Route transitions, rows arriving in
 * sequence, tactile controls. Nothing scroll-triggered: this is an instrument
 * someone uses for an hour.
 */

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useCallback, useEffect, useState } from "react";

import { ApiError, endpoints, type User } from "@/lib/api";
import { SESSIONS_CHANGED, type SessionSummary, agentApi } from "@/lib/agent";
import { Button, Spinner } from "@/components/ui";
import { DUR, EASE_OUT, stagger } from "@/lib/motion";

export default function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [state, setState] = useState<"checking" | "ready" | "anonymous">("checking");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [creating, setCreating] = useState(false);
  const [railOpen, setRailOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    endpoints
      .me()
      .then((u) => {
        if (!cancelled) {
          setUser(u);
          setState("ready");
        }
      })
      .catch((e) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.isAuth) {
          setState("anonymous");
          router.replace("/login");
        } else {
          setState("anonymous");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const refresh = useCallback(async () => {
    await Promise.resolve();
    try {
      setSessions(await agentApi.sessions());
    } catch {
      /* the rail is navigation; a failure here must not block the thread */
    }
  }, []);

  useEffect(() => {
    if (state !== "ready") return;
    const load = async () => {
      await Promise.resolve();
      await refresh();
    };
    void load();
    // A thread that has just named itself, or a run that finished, should be
    // reflected here without waiting for a navigation.
    window.addEventListener(SESSIONS_CHANGED, load);
    return () => window.removeEventListener(SESSIONS_CHANGED, load);
  }, [state, pathname, refresh]);

  const startSession = useCallback(async () => {
    setCreating(true);
    try {
      const session = await agentApi.create({});
      await refresh();
      router.push(`/sessions/${session.id}`);
    } finally {
      setCreating(false);
    }
  }, [refresh, router]);

  async function signOut() {
    await endpoints.logout().catch(() => {});
    router.push("/login");
  }

  if (state === "checking") {
    return (
      <div className="grid min-h-screen place-items-center">
        <Spinner label="Signing you in…" />
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden">
      {/* The rail. Fixed and overlaid below lg, where the thread needs the width. */}
      <AnimatePresence>
        {railOpen ? (
          <motion.button
            type="button"
            aria-label="Close the session list"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: DUR.ui }}
            onClick={() => setRailOpen(false)}
            className="fixed inset-0 z-40 bg-ink/20 lg:hidden"
          />
        ) : null}
      </AnimatePresence>

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col border-r border-line bg-wash transition-transform duration-[var(--dur-panel)] ease-[var(--ease-out)] lg:static lg:translate-x-0 ${
          railOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-3.5">
          <Link
            href="/sessions"
            className="display text-[17px] text-ink transition-opacity duration-[var(--dur-ui)] hover:opacity-70"
          >
            Antidote
          </Link>
          <button
            type="button"
            onClick={() => setRailOpen(false)}
            aria-label="Close the session list"
            className="text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink lg:hidden"
          >
            ✕
          </button>
        </div>

        <div className="px-3">
          <Button
            variant="primary"
            onClick={() => void startSession()}
            disabled={creating}
            className="w-full"
          >
            {creating ? "Starting…" : "New research"}
          </Button>
        </div>

        <nav className="mt-4 min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          <p className="px-2 pb-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-faint">
            Your research
          </p>
          {sessions.length === 0 ? (
            <p className="px-2 text-xs leading-relaxed text-muted">
              Nothing yet. Start one and describe the infection you are working
              on, in your own words.
            </p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              <AnimatePresence initial={false}>
                {sessions.map((session, i) => {
                  const active = pathname === `/sessions/${session.id}`;
                  return (
                    <motion.li
                      key={session.id}
                      initial={{ opacity: 0, x: -6 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{
                        duration: DUR.ui,
                        delay: stagger(i, 0.03, 0.25),
                        ease: EASE_OUT,
                      }}
                    >
                      <Link
                        href={`/sessions/${session.id}`}
                        // Closed here rather than in an effect on the path: on
                        // a phone the rail covers the thread you just opened.
                        onClick={() => setRailOpen(false)}
                        aria-current={active ? "page" : undefined}
                        className={`block truncate rounded-md px-2 py-1.5 text-[13px] transition-colors duration-[var(--dur-ui)] ${
                          active
                            ? "bg-signal-dim text-ink"
                            : "text-muted hover:bg-panel-hi hover:text-ink"
                        }`}
                      >
                        {session.title}
                      </Link>
                    </motion.li>
                  );
                })}
              </AnimatePresence>
            </ul>
          )}
        </nav>

        <div className="border-t border-line px-3 py-2.5">
          {user ? (
            <p className="truncate pb-1 text-[11px] text-faint">{user.email}</p>
          ) : null}
          <Button
            variant="ghost"
            onClick={signOut}
            className="w-full justify-start px-2 py-1 text-[13px]"
          >
            Sign out
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-line px-4 py-2.5 lg:hidden">
          <button
            type="button"
            onClick={() => setRailOpen(true)}
            aria-label="Open the session list"
            className="text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink"
          >
            ☰
          </button>
          <span className="display text-[15px] text-ink">Antidote</span>
        </header>

        <AnimatePresence mode="wait" initial={false}>
          <motion.main
            id="main"
            key={pathname}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: DUR.panel, ease: EASE_OUT }}
            className="min-h-0 flex-1 overflow-hidden"
          >
            {children}
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  );
}

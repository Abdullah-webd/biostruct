"use client";

/**
 * The app chrome. Motion dial 5: route transitions, data arriving in sequence,
 * tactile controls. No scroll theatrics in a tool someone uses for an hour.
 */

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { ApiError, endpoints, type User } from "@/lib/api";
import { Button, Spinner } from "@/components/ui";
import { DUR, EASE_OUT } from "@/lib/motion";

export default function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<User | null>(null);
  const [state, setState] = useState<"checking" | "ready" | "anonymous">("checking");

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
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-ground/85 backdrop-blur-md">
        <div className="flex items-center justify-between gap-4 px-4 py-2.5 sm:px-6">
          <Link
            href="/projects"
            className="display text-[17px] text-ink transition-opacity duration-[var(--dur-ui)] hover:opacity-70"
          >
            Biostruct
          </Link>
          <div className="flex items-center gap-3">
            {user && (
              <span className="hidden text-[13px] text-muted sm:inline">{user.email}</span>
            )}
            <Button variant="ghost" onClick={signOut} className="px-2.5 py-1 text-[13px]">
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <AnimatePresence mode="wait" initial={false}>
        <motion.main
          id="main"
          key={pathname}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: DUR.panel, ease: EASE_OUT }}
          className="flex-1"
        >
          {children}
        </motion.main>
      </AnimatePresence>
    </div>
  );
}

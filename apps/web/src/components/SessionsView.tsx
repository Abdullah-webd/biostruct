"use client";

/**
 * The empty state you land on before you have picked a session.
 *
 * Not a list — the rail on the left is the list. This is one job: get someone
 * who has just arrived into a conversation, with a sentence explaining what
 * they are about to talk to.
 */

import { motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { agentApi } from "@/lib/agent";
import { DUR, EASE_OUT } from "@/lib/motion";
import { Button, Notice } from "@/components/ui";

export default function SessionsView() {
  const router = useRouter();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const start = useCallback(async () => {
    setCreating(true);
    setError("");
    try {
      const session = await agentApi.create({});
      router.push(`/sessions/${session.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setCreating(false);
    }
  }, [router]);

  return (
    <div className="grid h-full place-items-center px-6">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: DUR.reveal, ease: EASE_OUT }}
        className="max-w-lg text-center"
      >
        <h1 className="font-serif text-3xl text-ink">
          Find the enzyme. Fold it. Find what blocks it.
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Describe a resistant infection in your own words — typed, or spoken in
          Yorùbá, Hausa, Igbo, Amharic or Pidgin. Antidote identifies the
          protein behind the resistance, reads the literature with citations you
          can open, predicts the structure, and ranks molecules for your lab to
          test.
        </p>
        {error ? (
          <div className="mt-4 text-left">
            <Notice tone="danger" title="Could not start">
              {error}
            </Notice>
          </div>
        ) : null}
        <div className="mt-6">
          <Button variant="primary" onClick={() => void start()} disabled={creating}>
            {creating ? "Starting…" : "Start research"}
          </Button>
        </div>
      </motion.div>
    </div>
  );
}

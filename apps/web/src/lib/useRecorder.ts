"use client";

/**
 * Recording a spoken question.
 *
 * The whole point of this feature is that a researcher who thinks in Yorùbá
 * should not have to compose a precise technical question in English before
 * they can ask it. So the bar for this has to be low: press, talk, read what
 * we heard, correct it, send.
 *
 * It never sends anything. The transcript comes back as a draft in the
 * composer, because speech models mis-hear scientific nouns — we measured
 * "Klebsiella" transcribed as "cleps ella" — and a mis-heard organism must not
 * be the thing that starts a paid run.
 *
 * Microphone access can be refused, unavailable (every browser requires a
 * secure context) or simply absent on the machine. All three end with the
 * button disabled and a sentence saying so, never a silent no-op.
 */

import { useCallback, useRef, useState } from "react";

export type RecorderState = "idle" | "recording" | "working" | "unsupported";

/** The longest we will record. Spitch bills per second; a stuck recorder is a
 *  real way to waste money, and a spoken question is seconds long. */
const MAX_MS = 60_000;

export function useRecorder(onAudio: (blob: Blob) => Promise<void>) {
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stop = useCallback(() => {
    if (stopTimer.current) {
      clearTimeout(stopTimer.current);
      stopTimer.current = null;
    }
    const active = recorder.current;
    if (active && active.state !== "inactive") active.stop();
  }, []);

  const start = useCallback(async () => {
    setError("");
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setState("unsupported");
      setError("This browser cannot record audio. You can still type.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setState("idle");
      setError("Microphone access was refused. You can still type your question.");
      return;
    }

    chunks.current = [];
    const mr = new MediaRecorder(stream);
    recorder.current = mr;
    mr.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.current.push(e.data);
    };
    mr.onstop = () => {
      // Release the microphone immediately — a browser tab holding one open
      // shows a recording indicator and people reasonably find that alarming.
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks.current, { type: mr.mimeType || "audio/webm" });
      chunks.current = [];
      if (blob.size === 0) {
        setState("idle");
        setError("Nothing was recorded.");
        return;
      }
      setState("working");
      onAudio(blob)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setState("idle"));
    };
    mr.start();
    setState("recording");
    stopTimer.current = setTimeout(stop, MAX_MS);
  }, [onAudio, stop]);

  return { state, error, start, stop, clearError: () => setError("") };
}

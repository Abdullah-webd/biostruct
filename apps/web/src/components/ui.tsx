"use client";

/**
 * Shared primitives.
 *
 * v1 set every label in tracked-out uppercase and small monospace. That is one
 * of the documented generated-design tells, and it also made short labels hard
 * to read at a glance. v2 is sentence case in the body face, and monospace is
 * reserved for things that are counted or compared.
 *
 * Every control has press feedback at --dur-press, because a control that does
 * not acknowledge a click feels broken regardless of what happens next.
 */

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

import { TERMS, type Term as TermDef, type TermKey } from "@/lib/words";

export function Button({
  children,
  variant = "default",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger";
}) {
  const tones = {
    default: "border-line bg-ground text-ink hover:border-faint hover:bg-panel-hi",
    primary:
      "border-signal bg-signal text-white font-medium hover:bg-[#194d8e] hover:border-[#194d8e]",
    ghost: "border-transparent text-muted hover:text-ink hover:bg-panel-hi",
    danger: "border-line bg-ground text-danger hover:border-danger hover:bg-[#fdf3f2]",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 rounded-md border px-3.5 py-2 text-sm transition-[background-color,border-color,color,transform] duration-[var(--dur-ui)] ease-[var(--ease-out)] active:scale-[0.98] active:duration-[var(--dur-press)] disabled:cursor-not-allowed disabled:opacity-40 ${tones} ${className}`}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  error,
  className = "",
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  error?: string;
}) {
  const id = props.id ?? props.name ?? label.toLowerCase().replace(/\s+/g, "-");
  return (
    <label htmlFor={id} className={`block ${className}`}>
      <span className="mb-1.5 block text-sm font-medium text-ink">{label}</span>
      {hint && !error && (
        <span id={`${id}-hint`} className="mb-1.5 block text-[13px] text-muted">
          {hint}
        </span>
      )}
      <input
        {...props}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={`w-full rounded-md border bg-ground px-3 py-2 text-sm text-ink placeholder:text-faint transition-colors duration-[var(--dur-ui)] ${
          error ? "border-danger" : "border-line focus:border-signal"
        }`}
      />
      {error && (
        <span id={`${id}-error`} className="mt-1 block text-[13px] text-danger">
          {error}
        </span>
      )}
    </label>
  );
}

export function Panel({
  children,
  className = "",
  title,
  aside,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  aside?: ReactNode;
}) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line bg-wash px-4 py-2.5">
          <h2 className="text-sm font-medium text-ink">{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
  );
}

/**
 * The plain name for a technical term, with the real name and a one-line
 * explanation on hover and focus. Nothing in this product should be a word the
 * reader cannot look up without leaving the page.
 */
export function Term({ k, className = "" }: { k: TermKey; className?: string }) {
  const t: TermDef = TERMS[k];
  return (
    <abbr
      title={t.technical ? `${t.technical} — ${t.meaning}` : t.meaning}
      className={`cursor-help border-b border-dotted border-faint no-underline ${className}`}
    >
      {t.plain}
    </abbr>
  );
}

/** The opening of every step: what is about to happen, in one sentence. */
export function StepHeader({
  title,
  children,
  aside,
}: {
  title: string;
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-2xl">
        <h2 className="display text-[26px]">{title}</h2>
        <p className="mt-2 text-[15px] leading-relaxed text-muted">{children}</p>
      </div>
      {aside}
    </header>
  );
}

/** A labelled readout. The whole product is readouts. */
export function Readout({
  label,
  value,
  unit,
  tone = "text-ink",
  title,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  tone?: string;
  title?: string;
}) {
  // Monospace is for figures that line up and get compared. A word like
  // "nanobody" set in mono reads as a code dump, which was a v1 tell.
  const numeric =
    typeof value === "number" ||
    (typeof value === "string" && /^[-+]?[\d.,\s×x^eE+-]+$/.test(value.trim()));
  return (
    <div className="flex items-baseline justify-between gap-3 py-2" title={title}>
      <span className="text-[13px] text-muted">{label}</span>
      <span className={`${numeric ? "tnum" : ""} text-sm ${tone}`}>
        {value}
        {unit && <span className="ml-1 text-xs text-faint">{unit}</span>}
      </span>
    </div>
  );
}

/**
 * A statement the user must not miss. Used for test-mode results and for runs
 * where nothing is predicted to bind — the two places where a cheerful UI
 * would actively mislead.
 */
export function Notice({
  tone = "caution",
  title,
  children,
}: {
  tone?: "caution" | "signal" | "danger" | "good";
  title: string;
  children?: ReactNode;
}) {
  const tones = {
    caution: "border-caution bg-[#fdf8ef] text-caution",
    signal: "border-signal bg-signal-dim text-signal",
    danger: "border-danger bg-[#fdf3f2] text-danger",
    good: "border-good bg-[#f0f8f4] text-good",
  }[tone];
  return (
    <div className={`rounded-md border-l-[3px] px-4 py-3 ${tones}`} role="note">
      <p className="text-sm font-medium">{title}</p>
      {children && (
        <div className="mt-1 text-[13px] leading-relaxed text-ink/80">{children}</div>
      )}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 text-[13px] text-muted">
      <span className="block size-2 animate-pulse rounded-full bg-signal" />
      <span>{label}</span>
    </div>
  );
}

/** Skeleton rows, so data arrives into a shape rather than onto a spinner. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-px overflow-hidden rounded-lg" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-11 animate-pulse bg-panel-hi"
          style={{ animationDelay: `${i * 90}ms`, opacity: 1 - i * 0.12 }}
        />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm text-ink">{title}</p>
      {children && <div className="mt-2 text-[13px] text-muted">{children}</div>}
    </div>
  );
}

"use client";

/**
 * Shared primitives. Deliberately small: this is instrument chrome, not a
 * design system.
 *
 * Every control has press feedback at --dur-press, because a control that does
 * not acknowledge a click feels broken regardless of what happens next.
 */

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

export function Button({
  children,
  variant = "default",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger";
}) {
  const tones = {
    default:
      "border-line bg-panel text-ink hover:border-faint hover:bg-panel-hi",
    primary:
      "border-signal bg-signal text-ground font-medium hover:brightness-110",
    ghost: "border-transparent text-muted hover:text-ink hover:bg-panel",
    danger: "border-line bg-panel text-danger hover:border-danger",
  }[variant];
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-2 border px-3 py-2 text-sm transition-[background-color,border-color,color,transform] duration-[var(--dur-ui)] ease-[var(--ease-out)] active:scale-[0.98] active:duration-[var(--dur-press)] disabled:cursor-not-allowed disabled:opacity-40 ${tones} ${className}`}
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
      <span className="mb-1.5 block text-[11px] uppercase tracking-widest text-muted">
        {label}
      </span>
      <input
        {...props}
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={`w-full border bg-ground px-3 py-2 text-sm text-ink placeholder:text-faint transition-colors duration-[var(--dur-ui)] ${
          error ? "border-danger" : "border-line focus:border-signal"
        }`}
      />
      {hint && !error && (
        <span id={`${id}-hint`} className="mt-1 block text-xs text-faint">
          {hint}
        </span>
      )}
      {error && (
        <span id={`${id}-error`} className="mt-1 block text-xs text-danger">
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
    <section className={`border border-line bg-panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[11px] uppercase tracking-widest text-muted">{title}</h2>
          {aside}
        </header>
      )}
      {children}
    </section>
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
  label: string;
  value: ReactNode;
  unit?: string;
  tone?: string;
  title?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5" title={title}>
      <span className="text-xs text-muted">{label}</span>
      <span className={`tnum text-sm ${tone}`}>
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
  tone?: "caution" | "signal" | "danger";
  title: string;
  children?: ReactNode;
}) {
  const tones = {
    caution: "border-caution/40 bg-caution/5 text-caution",
    signal: "border-signal/40 bg-signal/5 text-signal",
    danger: "border-danger/40 bg-danger/5 text-danger",
  }[tone];
  return (
    <div className={`border-l-2 px-4 py-3 ${tones}`} role="note">
      <p className="text-sm font-medium">{title}</p>
      {children && (
        <div className="mt-1 text-xs leading-relaxed text-muted">{children}</div>
      )}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 text-xs text-muted">
      <span className="block size-2 animate-pulse rounded-full bg-signal" />
      <span className="uppercase tracking-widest">{label}</span>
    </div>
  );
}

/** Skeleton rows, so data arrives into a shape rather than onto a spinner. */
export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-px" aria-hidden="true">
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
      <p className="text-sm text-muted">{title}</p>
      {children && <div className="mt-2 text-xs text-faint">{children}</div>}
    </div>
  );
}

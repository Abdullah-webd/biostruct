/**
 * Motion helpers.
 *
 * Two scales, never mixed: UI feedback is fast and precise, reveals are
 * cinematic. Entrances never use ease-in, nothing bounces, and nothing starts
 * from scale(0).
 */

export const EASE_OUT = [0.23, 1, 0.32, 1] as const;
export const EASE_EXPO = [0.16, 1, 0.3, 1] as const;

export const DUR = {
  press: 0.12,
  ui: 0.2,
  panel: 0.3,
  reveal: 0.9,
  hero: 1.2,
} as const;

/** Rows and cards arrive in sequence, not all at once. */
export function stagger(index: number, step = 0.045, max = 0.4): number {
  return Math.min(index * step, max);
}

export const fadeUp = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
} as const;

export const panelIn = {
  initial: { opacity: 0, y: 8, scale: 0.995 },
  animate: { opacity: 1, y: 0, scale: 1 },
} as const;

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

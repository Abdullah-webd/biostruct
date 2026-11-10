# AGENTS.md

Instructions for any coding agent working in this repository (Claude Code, Cursor, Codex, Copilot, etc.).

## 1. Every website is a motion website

- Pages must feel designed and alive, never static or template-like. Before writing or restyling UI, read and follow `~/.claude/skills/motion-web/SKILL.md` and its `reference/` files (setup, patterns, anti-slop, qa).
- Write a short design plan first: palette (4–6 hex), typefaces, layout sketch, signature element, and a **motion thesis** (intro sequence, scroll set-pieces per page, route transitions, interaction feedback, intensity 1–10).
- Stack: Next.js App Router + TypeScript + Tailwind + `gsap` / `@gsap/react` (ScrollTrigger, SplitText) + `lenis` + `motion` (`motion/react`), unless the project already uses something else. For GSAP API details see `~/.claude/skills/gsap-*/SKILL.md`.
- Every page gets an authored entrance, scroll-driven or layout motion, a route transition, and feedback on every control. Vary patterns per section. Never one uniform fade-up everywhere.
- Avoid AI-look defaults: purple/blue gradients, Inter/Roboto-only type, centered hero + three identical cards, emoji icons, glassmorphism everywhere, count-up stats, placeholder copy.
- Respect `prefers-reduced-motion`; content must be visible without JS; animate only transform/opacity/clip-path.

## 2. Imagery

- Generate real imagery (hero, sections, products, textures, OG image, hero video loops) with Higgsfield if the agent has access to it (see `~/.claude/skills/higgsfield-assets/SKILL.md`). Never ship stock hotlinks, grey boxes or emoji stand-ins. Download and optimize assets into the repo.

## 3. Nothing ships until it is proven working

Follow `~/.claude/skills/ship-verify/SKILL.md` before every push, merge or deploy:

1. Typecheck, lint, unit/integration tests, production build: all green.
2. Run the production build locally and test the **whole app**, not just the changed feature:
   `node ~/.claude/skills/ship-verify/scripts/smoke.mjs http://localhost:3000` plus the Playwright E2E suite for critical flows.
3. Deploy, then repeat the smoke test and critical flows **on the live URL**, and check logs.
4. Fix, then re-run everything, until it all passes. Report the evidence. Never claim "done" or "working" without it, and state anything that could not be verified.

Motion QA: `node ~/.claude/skills/motion-web/scripts/motion-check.mjs <url>` must show every section animating, CLS ≤ 0.1, and reduced-motion content visible.

## Project notes

<!-- Live URL, deploy command, test accounts, critical flows. Keep VERIFY.md in sync. -->

# Verification map

Live URL: **not deployed yet**. Railway and Vercel are chosen (ARCHITECTURE §14)
but nothing is deployed, so live verification is not yet possible. Everything
below runs against the production build locally.

## Running it for tests

```bash
make dev    # API on :8000, natively (Colima keeps container ports in its VM)
make web    # web app on :3000
```

For the production build rather than dev:

```bash
cd apps/web && npx next build && npx next start --port 3100
```

## Checks

| What | Command |
|---|---|
| Backend tests | `make test` (382, needs Docker for a MongoDB replica set) |
| Backend lint and types | `cd apps/api && .venv/bin/ruff check . && .venv/bin/mypy` |
| Web types, lint, build | `cd apps/web && npx tsc --noEmit && npx eslint . && npx next build` |
| Whole-site smoke | `node ~/.claude/skills/ship-verify/scripts/smoke.mjs http://localhost:3100 --out .verify` |
| Critical flows | `cd apps/web && npm run walkthrough` |
| Motion | `cd apps/web && npm run motion-check` |
| API over HTTP | `cd apps/api && .venv/bin/python scripts/smoke.py http://127.0.0.1:8000` |
| Boltz adapter, live | `cd apps/api && .venv/bin/python scripts/verify_boltz.py` |
| Science sources, live | `cd apps/api && .venv/bin/python scripts/verify_sources.py` |
| Whole workflow, live engine | `make verify-e2e` |

## Critical flows

1. Register, land on projects, sign out, sign back in
2. Start the worked example in one click ("Walk me through this example"), or
   fill the form in by hand
3. Pick a resistance protein from the starting points, or search for another;
   add it and see its structures and literature
4. Step 2 pre-fills the attack site from the annotated active sites and checks
   it on arrival: the conversion and the selected building blocks (`SKSE` for
   KPC-2) are shown before anything is saved
5. Check the price, confirm the exact figure, start the run
6. Watch progress over SSE through to completion
7. Read the ranking, with the real odds stated next to the rank
8. Open a design's structure in the viewer

## Known non-issues

- The smoke crawl only reaches the three public pages (landing, login,
  register). Everything behind auth is covered by `npm run walkthrough`, which
  drives the real UI from registration to a ranked result.
- A test-mode Boltz key returns a 522-byte placeholder structure, so the viewer
  shows a stub rather than a fold. The UI labels this; it is not a defect.
- `ui-walkthrough.mjs` must wait on the ranking panel heading, not on prose:
  an earlier version matched the words "best first" in the step's own intro
  copy and so reported success before the run had finished.

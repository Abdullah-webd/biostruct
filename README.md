# Antidote

**Paste a protein sequence. Get its 3D structure. Get ten molecules that might block it.**

Antidote does the two steps of drug discovery a computer can actually do, for the enzymes that make
infections drug-resistant:

| | Step | What happens |
|---|---|---|
| **2** | Sequence → structure | A model folds the amino-acid chain into a predicted 3D structure, which you can turn on screen. |
| **3** | Structure → molecules | A second model docks candidate molecules into it and ranks them. You download the shortlist. |

Steps 1, 4 and 5 — sequencing, lab testing, clinical trials — happen in a laboratory. This tool does
not do them and does not pretend to.

Built with African research institutions as the primary audience: antimicrobial resistance kills more
people per head in sub-Saharan Africa than anywhere else on earth, and the places with the most to
lose have the least access to the tooling that finds new drugs.

---

## The one thing that makes a result trustworthy

**Every screen contains a positive control** — a compound already known to inhibit that class of
enzyme. If it does not rank near the top, the run is not telling you anything, and the interface says
so in red *before* it shows you the ranking.

The control is chosen per Ambler class, because this matters: avibactam is the reference inhibitor
for class A and is very nearly useless against the class B metallo-enzymes. A single global control
would make a working screen look broken against NDM.

## What it deliberately does not do

- **No toxicity or safety prediction.** Nothing in this stack assesses it, no free tool does, and
  claiming otherwise about a medicine would be the most dangerous thing this product could say.
- **No therapeutic claims.** Every molecule it ranks is a computational hypothesis for a bench to
  test.

---

## Running it

```bash
docker compose up -d --wait mongo       # MongoDB (replica set, for change streams)
cd apps/api && uv sync                  # Python 3.12
cd apps/web && npm install              # Next.js
```

Copy `.env.example` to `.env` and fill in the keys. Then, in two terminals:

```bash
cd apps/api && .venv/bin/uvicorn biostruct.main:app --port 8000
cd apps/web && PORT=3002 npm run dev
```

Open <http://localhost:3002>.

### Test mode vs live mode

A Boltz key announces its own mode in its prefix, and the app refuses to start on a mismatch.

- **`sk_bc_ws_test_…`** — the default. Runs the real flow and returns **synthetic** results: a
  stand-in structure, and molecules the engine substitutes for the ones you sent. Nothing is charged.
  Every screen that shows synthetic data is labelled as such.
- **`sk_bc_ws_live_…`** — real predictions, real money (~$0.025 per fold, ~$0.025 per molecule
  screened). In development this is refused unless you set **`BOLTZ_ALLOW_LIVE=1`**, so spending is
  always a deliberate choice. Use that flag rather than `ENV=prod` for a local demo: `ENV=prod` also
  forces secure cookies, and sign-in then fails over plain http.

```bash
BOLTZ_API_KEY=sk_bc_ws_live_… BOLTZ_ALLOW_LIVE=1 .venv/bin/uvicorn biostruct.main:app --port 8000
```

> **`.env` is not shell-sourceable** — it contains an unquoted `&`, so `source .env` stops partway
> through. The app reads the file directly via pydantic-settings, so this only affects scripts.

---

## The deployment

| | |
|---|---|
| Web | Vercel — <https://antidote-silk.vercel.app> |
| API | Railway, project `antidote-api` |
| Database | MongoDB Atlas, database **`antidote`** |
| Structures | Stored in Mongo (`STORAGE_BACKEND=mongo`), because a container's disk is ephemeral |
| Sign-ups | **Closed** (`ALLOW_REGISTRATION=false`) — a live engine key behind a public URL means a new account could spend real credits |

The browser only ever talks to the Vercel origin; `API_ORIGIN` points Next's
rewrite at Railway, which keeps the session cookie first-party.

### Seeding the demo account

```bash
cd apps/api
ENV=prod MONGODB_DB=antidote STORAGE_BACKEND=mongo \
  BOLTZ_API_KEY=sk_bc_ws_live_… .venv/bin/python scripts/seed_demo.py
```

> **`MONGODB_DB` must match the deployment.** It defaults to `biostruct`
> locally and the deployment uses `antidote`. Seeding without it writes a
> perfectly good demo account into a database nothing reads, and sign-in on
> the live site then fails with no obvious cause.

---

## Checks

```bash
make test     # 552 tests, inside the compose network
make lint     # ruff, ruff-format, mypy --strict
cd apps/web && npm run lint && npm run build
```

`make test` runs **inside** the compose network on purpose: on Docker Desktop/Colima the host cannot
reach the published MongoDB port, and a host-run `pytest` silently skips the integration tests.

---

## Where things are

| Path | |
|---|---|
| `apps/api/src/biostruct/lab/` | The two steps: models, and the service that runs them |
| `apps/api/src/biostruct/engines/` | Boltz clients — folding, screening, molecule design |
| `apps/api/src/biostruct/library.py` | The curated inhibitor library and its per-class controls |
| `apps/api/src/biostruct/sources/` | ChEMBL, UniProt, RCSB, Europe PMC, PubMed |
| `apps/web/src/components/Lab.tsx` | The product, on one page |
| `docs/` | What each API actually does, measured — not what its docs claim |

The `docs/*-observed.md` files are worth reading before touching an integration. They record
behaviour found by probing the live APIs, including several things the official documentation does
not say and at least one place where it is wrong.

# Biostruct — AI-Powered Therapeutic Protein Design Platform

An integrated research environment that takes a researcher from a **drug-resistant infection** to a
**ranked, explained, exportable list of candidate therapeutic protein binders** — without needing a
GPU cluster, an MLOps team, or a computational-biology background.

Built for early-stage therapeutic protein discovery, with African research institutions as the
primary audience.

> **Status: approved, build starting.** Start with **[ARCHITECTURE.md](ARCHITECTURE.md)**, backed by
> **[docs/research.md](docs/research.md)**.

---

## What it does

| Step | |
|---|---|
| 1 | Select a drug-resistant infection |
| 2 | Discover a biological target across UniProt, RCSB PDB and PubMed, with the evidence attached |
| 3 | Generate candidate protein binders with **BoltzProt-1** |
| 4 | Predict 3D structures and view them interactively in **Mol\*** |
| 5 | Evaluate predicted protein–target interaction |
| 6 | Rank and compare candidates with transparent, configurable, reproducible criteria |
| 7 | Export sequences, structures and a provenance report for the wet lab |

An agentic assistant (orchestrator + five specialists, OpenAI Agents SDK) runs alongside the whole
workflow, over text or voice.

---

## What it is not

This platform produces **computational prioritisation, not evidence of therapeutic effect.**

No candidate it generates is a drug. Predicted interface confidence is not potency, and a high
ranking is not a claim that a protein works. Experimental validation is always required. The
platform enforces this: a non-bypassable Scientific Review guardrail blocks any generated text that
asserts efficacy or clinical readiness, and every export leads with its limitations.

---

## Stack

**Web** — Next.js (App Router) · TypeScript · Tailwind · Mol\* · GSAP/Motion/Lenis
**API** — Python 3.12 · FastAPI · reconciler worker (no queue, no broker)
**Science** — Boltz API (`BoltzProt-1` design, `Boltz-2` structure + affinity) · UniProt · RCSB PDB · PubMed
**AI** — OpenAI Agents SDK · Realtime API (voice)
**Data** — MongoDB (data, cache, change-stream progress) · Railway Bucket (S3-compatible)

One correction worth knowing up front: **Boltz-2 cannot design binders** — it scores complexes.
De novo design comes from **BoltzProt-1** (hosted API) or BoltzGen (open source, needs an A100).
See [docs/research.md §1](docs/research.md).

---

## Try it

The backend works today. There is no web UI yet, but the API ships with an
interactive page you can click the whole workflow through.

```bash
cp .env.example .env     # add BOLTZ_API_KEY and OPENAI_API_KEY
make dev                 # terminal 1 — the API on :8000
make web                 # terminal 2 — the interface
```

`make web` prints the URL it chose. It uses port 3000 when free and steps to
the next one when not, so it will not collide with another project you have
running.

It serves the production build rather than the dev server, because that is the
build that gets verified and it does not depend on the hot-reload websocket.
Use `make web-dev` when you are editing the frontend and want hot reload.

Then create an account and work down the left rail: find a target, choose an
epitope, estimate, confirm, watch the run, read the ranked candidates.

The API also serves an interactive page at http://localhost:8000/docs if you
would rather drive it directly.

`make dev` runs the API directly on your machine. If your Docker runs in a VM
(Colima, Rancher Desktop, Podman machine) then `make up` will start a perfectly
healthy container whose port never reaches your browser, which looks exactly
like a broken app. Running it natively avoids that. It uses `MONGODB_URI` from
`.env`, so with an Atlas connection string no local database is needed.

Driving the API directly rather than through the UI, at
http://localhost:8000/docs:

1. `POST /auth/register` — any email, password of 12+ characters
2. `POST /projects` — name it, with `infection.name`
3. `POST /projects/{id}/targets/search` — try `protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"`
4. `POST /projects/{id}/targets` — `{"accession": "Q9F663"}` (KPC-2)
5. `PUT  /projects/{id}/targets/{id}/epitope` — `{"pdb_id":"2OV5","chain_id":"A","author_residues":[70,73,130,166]}`
6. `POST /projects/{id}/runs/estimate` then `POST /projects/{id}/runs` with the price it quoted
7. `GET  /projects/{id}/runs/{id}/ranking` — scored, explained candidates

With a test-mode Boltz key this is free and finishes in about ten seconds, and
the results are synthetic. Every response says so.

Other commands: `make test`, `make lint`, `make verify-e2e`, `make logs`, `make down`.
Credentials are explained in **[docs/credentials.md](docs/credentials.md)**.

## Repository

```
ARCHITECTURE.md     the design, and the approval gate
AGENTS.md           rules for any coding agent working here
docs/research.md    verified findings behind every decision
docs/credentials.md what keys are needed, when, and how to get them
apps/web            Next.js frontend            (M0)
apps/api            FastAPI backend + worker    (M0)
```

## Cost note

**Building this costs nothing.** Boltz's free tier is 200 predictions/month and the whole build
needs ~150: 30 to prove the live API behaves as documented, ~20 to record test fixtures, and 100 for
one good demo run. All other development and CI replays those fixtures and never calls the API.

Cost control is still a built-in feature, for two reasons that have nothing to do with our build:
a real research campaign is 10,000+ designs at ~$0.05 each, and `num_proteins` accepts up to
1,000,000 — so one misplaced digit would be a $50,000 call. Every run shows an estimate and requires
confirmation, every project has a hard budget, and the account carries an independent spending limit.

## Licence

[MIT](LICENSE) for the code. See [NOTICE](NOTICE) for what the software does not claim,
and for who owns the sequences and structures it generates (the researcher, not us).

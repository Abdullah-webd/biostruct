# Baobab — AI-Powered Therapeutic Protein Design Platform

An integrated research environment that takes a researcher from a **drug-resistant infection** to a
**ranked, explained, exportable list of candidate therapeutic protein binders** — without needing a
GPU cluster, an MLOps team, or a computational-biology background.

Built for early-stage therapeutic protein discovery, with African research institutions as the
primary audience.

> **Status: pre-build.** The architecture is written and awaiting approval. No feature code yet.
> Start with **[ARCHITECTURE.md](ARCHITECTURE.md)**, backed by **[docs/research.md](docs/research.md)**.

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
**Data** — MongoDB (data, cache, change-stream progress) · Cloudflare R2

One correction worth knowing up front: **Boltz-2 cannot design binders** — it scores complexes.
De novo design comes from **BoltzProt-1** (hosted API) or BoltzGen (open source, needs an A100).
See [docs/research.md §1](docs/research.md).

---

## Getting started

Nothing to run yet. Once M0 lands:

```bash
cp .env.example .env     # only BOLTZ_API_KEY and OPENAI_API_KEY are required
docker compose up -d     # MongoDB (single-node replica set)
uv sync                  # Python 3.12 deps
pnpm install             # web deps
```

Two API keys and Docker is the whole setup — no cloud accounts needed to develop.
See **[docs/credentials.md](docs/credentials.md)** for what each credential is and how to get it.

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

TBD before the repo goes public.

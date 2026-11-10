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
**API** — Python 3.12 · FastAPI · ARQ worker
**Science** — Boltz API (`BoltzProt-1` design, `Boltz-2` structure + affinity) · UniProt · RCSB PDB · PubMed
**AI** — OpenAI Agents SDK · Realtime API (voice)
**Data** — MongoDB · Redis · Cloudflare R2

One correction worth knowing up front: **Boltz-2 cannot design binders** — it scores complexes.
De novo design comes from **BoltzProt-1** (hosted API) or BoltzGen (open source, needs an A100).
See [docs/research.md §1](docs/research.md).

---

## Getting started

Nothing to run yet. Once M0 lands:

```bash
cp .env.example .env     # fill in BOLTZ_API_KEY and OPENAI_API_KEY
docker compose up -d     # MongoDB + Redis
uv sync                  # Python 3.12 deps
pnpm install             # web deps
```

## Repository

```
ARCHITECTURE.md     the design, and the approval gate
AGENTS.md           rules for any coding agent working here
docs/research.md    verified findings behind every decision
apps/web            Next.js frontend            (M0)
apps/api            FastAPI backend + worker    (M0)
```

## Cost note

Boltz is usage-based: ~**$0.05 per design** at the KPC-2 crop size, so a 100-design run is ~$5. The
free tier is 200 predictions/month. The platform therefore treats cost control as a feature —
every run shows an estimate and requires confirmation, and every project has a hard budget.

## Licence

TBD before the repo goes public.

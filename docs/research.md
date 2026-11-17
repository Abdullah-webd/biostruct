# Research — Biostruct v2

Date: 2026-11-16. Everything below was checked against primary sources in this
session. Where a source disagreed with another, both are recorded.

The brief: rebuild Biostruct as an agentic platform that helps **African drug
researchers** work on infections that no longer respond to treatment. The
reference pipeline is the five-step process the user researched (patient →
sequencing → protein → 3D shape → candidate molecules → lab → trials). Our
product occupies **steps 2 and 3 only** and must say so.

---

## 1. The single most important finding: we are using one eighth of the Boltz API

Our current integration calls exactly one endpoint family, `/protein/design`
([`apps/api/src/biostruct/engines/boltz.py`](../apps/api/src/biostruct/engines/boltz.py)).
The API actually exposes all of this
([REST reference](https://api.boltz.bio/docs/api/api)):

| User's researched step | Boltz endpoint | We use it? |
|---|---|---|
| **Step 2** — amino acids → 3D shape | `POST /compute/v1/predictions/structure-and-binding` (`boltz-2.1`) | **No** |
| **Step 3** — screen a library of existing molecules | `POST /compute/v1/small-molecule/library-screen` | **No** |
| **Step 3** — invent new molecules against the pocket | `POST /compute/v1/small-molecule/design` | **No** |
| **Step 3** — explore a large library on a budget | `POST /compute/v1/small-molecule/explore` | **No** |
| **Step 3** — drug-likeness triage | `POST /compute/v1/predictions/adme` (`adme-v1`) | **No** |
| Protein binder design (biologics route) | `POST /compute/v1/protein/design` | **Yes — only this** |
| Redesign an existing protein sequence | `POST /compute/v1/protein/sequence-redesign` | No |
| Screen a protein library | `POST /compute/v1/protein/library-screen` | No |

Every family has a matching `estimate-cost`, `/{id}`, `/{id}/results`,
`/{id}/stop`, `/{id}/resume` and `/{id}/delete-data`. Our existing
estimate→confirm→poll→harvest machinery therefore generalises to all of them
with no new concepts — this is a large, already-paid-for asset.

**Consequence:** the pipeline the user researched is not something we need to
invent. It is sitting behind the API key we already have.

### 1.1 `structure-and-binding` — the "AlphaFold step"

[Method reference](https://api.boltz.bio/docs/api/api/resources/predictions/subresources/structure_and_binding/methods/start)

- Entity types: `protein`, `rna`, `dna`, `ligand_ccd`, `ligand_smiles`, `glycan`.
- `binding` object requests binding metrics, with two forms:
  `ligand_protein_binding` (needs `binder_chain_id`; ligand limited to one copy
  and ≤2048 heavy atoms) and **`protein_protein_binding`** (`binder_chain_ids`).
- `model: "boltz-2.1"`. Options: `recycling_steps` (3), `sampling_steps` (200,
  min 50), `seed`, `step_scale` (1.638, range 1.3–2), `num_samples` 1–10, up to
  4 CIF/PDB `templates`.
- Billing unit is **samples**.

> **Discrepancy to verify empirically.** The open-source Boltz docs state the
> affinity module supports *small molecules only* and "does not support
> computation of protein–protein binding affinity", with a ≤128-atom ligand cap
> and ≤56 heavy atoms recommended
> ([boltz prediction docs](https://github.com/jwohlwend/boltz/blob/main/docs/prediction.md)).
> The hosted API documents a `protein_protein_binding` type and a 2048-heavy-atom
> ligand cap. The hosted service is newer (`boltz-2.1`). **Do not claim
> protein–protein affinity until we have run it once and seen the output.**

### 1.2 `small-molecule/library-screen` — the "Step 3" step

[Guide](https://api.boltz.com/docs/api/guides/small-molecule-library-screen/)

- **We supply the molecules.** `molecules: [{smiles, id?}]`. There is **no
  hosted billion-compound library**. The `id` comes back as `external_id` so
  results can be joined to our own records.
- `target`: one or more protein entities, plus optional pocket residues,
  reference ligands, constraints, covalent bonds.
- `molecule_filters`: a built-in structural-alert level (`recommended` default,
  or `extra` / `aggressive` / `disabled`) plus custom filters — Lipinski, RDKit
  descriptor ranges, SMARTS patterns, SMARTS catalogues (**PAINS**, **BRENK**),
  SMILES regex. A molecule must pass all filters; failures are skipped silently
  and never appear in results (we must surface that count).
- Results **stream in as molecules are scored** and the run can be stopped
  early. Per molecule: `metrics` — `binding_confidence`, `optimization_score`,
  `structure_confidence`, `iptm`, `ptm`, `complex_plddt`, `complex_iplddt` (all
  0–1) — plus `adme` when available, `artifacts` (presigned `.cif` and
  `.tar.gz`), and `warnings`.
- Billing unit is **molecules**. Data retained 7 days by default.

### 1.3 `predictions/adme` — narrower than the brief assumes

[Method reference](https://api.boltz.bio/docs/api/api/resources/predictions/subresources/adme/methods/start)

- 1–128 molecules per request, results in input order. `model: "adme-v1"`.
- Salts and solvent fragments are stripped and charges neutralised first.
- Returns **exactly three** "Tier 1" values: `lipophilicity` (internal LogD),
  `permeability`, and `solubility` (`high-confidence` / `medium-confidence` /
  `high-risk`).

> **This contradicts the brief.** The researched Step 3 says the AI filters out
> "any chemical that looks toxic to the human heart or liver." **Boltz ADME does
> no toxicity prediction at all** — no hERG, no hepatotoxicity, no metabolic
> stability. What we can honestly offer is *developability triage*
> (will it dissolve, will it cross a membrane, is the chemistry a known
> troublemaker). Claiming toxicity screening would be false and a judge with a
> pharmacology background would catch it.

### 1.4 Pricing

[Costs guide](https://api.boltz.com/docs/api/guides/costs/) documents the
*mechanism* but publishes **no rates**. Billing units: samples
(structure-and-binding), molecules (library screen, molecule design), proteins
(protein design). The `$0.0050` figure on that page is explicitly illustrative.
Final billing is measured compute at run time.

**Therefore: `estimate-cost` before every run is not optional, it is the only
way to know a price.** Our existing estimate→confirm flow is exactly right and
must be kept. Test-mode keys (`sk_bc_*_test_`) run the full flow against real
endpoints, return synthetic results, use no GPU and are never billed.

### 1.5 Agent integration

[Agent integrations guide](https://api.boltz.com/docs/api/guides/agent-integrations/).
Boltz ships a `boltz-api` **CLI**, a Claude Code plugin
(`boltz-bio/boltz-api-skills`), a Codex plugin and a Gemini/Antigravity
extension. **There is no MCP server and no published tool schema.** Our backend
should keep calling REST directly — we already have a typed, tested client, and
shelling out to a CLI from a server process would be a downgrade.

---

## 2. Grounded citations

The brief is explicit: no hallucinated references. Every claim the agent makes
must carry a resolvable identifier.

| Source | Key needed | Coverage | Verdict |
|---|---|---|---|
| **Europe PMC** | No | ~40M biomedical records, ~8M full text; returns structured annotations, not summaries | **Primary.** Biomedical-native, free, no key, full-text search |
| **OpenAlex** | No | ~250M works, ~90% DOI coverage, citation graph | **Secondary** — citation counts, open-access links, cross-checking |
| Semantic Scholar | Optional | Excellent citation graph, CS-biased | Not needed |
| PubMed E-utilities | No | Already integrated (`sources/pubmed.py`) | Keep for evidence attached to a target |

Sources: [Europe PMC API](https://dev.to/0012303/europe-pmc-has-a-free-api-search-40m-biomedical-papers-with-full-text-1b9g),
[OpenAlex](https://dev.to/0012303/openalex-has-a-free-api-search-250m-academic-works-without-any-key-4915),
[API comparison matrix](https://scilex.readthedocs.io/en/latest/reference/api-comparison.html).
These are secondary write-ups; endpoint shapes must be confirmed against the
official docs during implementation.

**Design rule that follows:** the agent must not be allowed to *write* a
citation. It may only *attach* one that came back from a tool call, and a
deterministic verifier re-resolves every identifier before the answer reaches
the user. A citation that does not resolve is removed and the sentence it
supported is flagged. This is a code gate, not a prompt instruction.

---

## 3. Structure from sequence — two different problems

| Situation | Route | Cost | Latency |
|---|---|---|---|
| A known, catalogued protein | **AlphaFold DB** `GET https://alphafold.ebi.ac.uk/api/prediction/{uniprot_accession}` — free, no auth, returns `cifUrl` / `pdbUrl` / PAE data | Free | Instant |
| An experimentally measured shape | **RCSB PDB** — already integrated (`sources/rcsb.py`) | Free | Instant |
| **A mutant from a patient isolate, or any novel sequence** | **Boltz `structure-and-binding`** | Paid | Minutes |
| A complex (protein + candidate molecule) | **Boltz `structure-and-binding`** | Paid | Minutes |

Sources: [AlphaFold DB API reference](https://cdn.jsdelivr.net/npm/@saibolla/ada@0.1.3/skills/alphafold-database/references/api_reference.md)
(third-party mirror — version suffixes differ between sources, `v4` vs `v6`;
confirm the current one against `alphafold.ebi.ac.uk` at implementation time).

This distinction is a genuine product feature and matches the brief's Step 1→2
transition. A resistant isolate's protein is usually a **mutant**, and a mutant
is exactly what is *not* in AlphaFold DB. "Paste the amino acids from your
sequencing run and we will fold the thing you actually have" is the honest
version of the brief's Step 2, and it is the step where free lookup fails and
real compute is required.

---

## 4. Where the molecules come from

Library screening needs SMILES we supply. Options, cheapest first:

1. **A curated starter library we ship.** Known β-lactamase inhibitors
   (avibactam `CHEMBL1689063`, clavulanic acid, tazobactam, vaborbactam,
   relebactam) plus close analogues. Small, explicable, and includes known
   positives — which doubles as a **sanity control**: if a screen cannot rank
   avibactam highly against KPC, the screen is not working.
2. **ChEMBL** — `https://www.ebi.ac.uk/chembl/api/data/`, read-only REST, free,
   no key, supports SMILES, pagination and filtering; resources include
   Molecule, Target, Activity, Assay. Python client `chembl_webresource_client`.
   This is how we assemble a few hundred to a few thousand relevant molecules.
   ([ChEMBL web services](https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services))
3. **Boltz `small-molecule/design`** — generate novel molecules against the
   pocket rather than screening existing ones.

Caution found in the sources: avibactam is a poor inhibitor of **class B
metallo-β-lactamases** (NDM). So the known-positive control must be chosen per
target class, not globally.
([CARD](https://card.mcmaster.ca/ontology/36727))

**Honest scaling statement:** the brief says the AI "opens a massive digital
library containing billions of tiny chemical molecules." We will screen
**hundreds to thousands**, because every molecule is billed compute. The
`explore` endpoint exists precisely to sample a large library under a budget —
that is the honest version of "billions", and we should describe it that way.

---

## 5. Why small molecules fix the problem I raised earlier

Earlier in this project I flagged that protein binders cannot reach
β-lactamases, which sit in the **periplasm** of Gram-negative bacteria, behind
an outer membrane. That objection is fatal to the therapeutic story for
designed protein binders against these targets.

Small molecules do not have that problem — and that is not a theory. **Every
β-lactamase inhibitor in clinical use is a small molecule**: clavulanic acid,
tazobactam, avibactam, vaborbactam, relebactam. They are co-formulated with an
antibiotic, they cross the outer membrane, and they work in patients today.

Moving the product's centre of gravity from protein binder design to
small-molecule screening therefore:

- matches what real drugs for this target class actually are,
- removes the delivery objection entirely,
- matches the user's researched pipeline (Step 3 is explicitly about "tiny
  chemical molecules that can be turned into pills"),
- and keeps protein design available as a second track for surface targets,
  where binders genuinely do work.

This is the strongest scientific argument in the whole rebuild.

---

## 6. Interpreting the numbers honestly

From [Boltz-2 paper](https://jeremywohlwend.com/assets/boltz2.pdf) and the
[Rowan FAQ](https://www.rowansci.com/blog/boltz2-faq):

- Affinity values are **log(IC50 µM)**, convertible to kcal/mol via
  `(6 − affinity) × 1.364`. It is a **non-standard pIC50**, so numbers are not
  comparable with other tools.
- The **binary binding probability** is for separating binders from decoys —
  i.e. **hit discovery**, which is exactly our use case.
- The **affinity value** is for ranking *close analogues* during optimisation
  and "should only be used when comparing different active molecules, not
  inactives."

**Design rule:** at the hit-finding stage we rank primarily on binding
probability / `binding_confidence`, and we must not present the affinity number
as a predicted potency for a molecule that probably is not a binder at all.
This is the v2 equivalent of the batch-relative ranking caveat we already
enforce with a test, and it should be enforced the same way.

---

## 7. The African framing, with real figures

From the Lancet Global Health cross-country analysis of the WHO African region
(2019 data, published 2023):

- **~1.05 million deaths associated** with bacterial AMR in the WHO African
  region; **250,000 directly attributable**.
- Of 3.83M deaths from infection and 1.86M from 33 priority bacterial pathogens
  in the region.
- **Seven pathogens** accounted for 821,000 AMR-associated deaths; **four
  exceeded 100,000 each**: *Streptococcus pneumoniae*, *Klebsiella pneumoniae*,
  *Escherichia coli*, *Staphylococcus aureus*.
- **Sub-Saharan Africa had the highest AMR death rate in the world**: 23.5 per
  100,000.
- Driver: high malaria, HIV and TB burden means repeated and prolonged
  antimicrobial use, which accelerates resistance.

Sources: [Lancet Global Health](https://www.thelancet.com/journals/langlo/article/PIIS2214-109X(23)00539-9/fulltext),
[IHME](https://www.healthdata.org/research-analysis/library/burden-bacterial-antimicrobial-resistance-who-africa-region-2019-cross),
[Sub-Saharan AMR review 2015–2025](https://link.springer.com/article/10.1186/s12982-026-02248-8),
[Accelerating drug discovery in Africa through data science](https://pmc.ncbi.nlm.nih.gov/articles/PMC12647589/).

Three of the four top killers — *K. pneumoniae*, *E. coli*, *S. aureus* — are in
scope for this pipeline today. That is the landing page, and every number on it
has a citation.

---

## 8. Competitive landscape

| Product | What it is | Gap for us |
|---|---|---|
| **[Tamarind Bio](https://www.tamarind.bio/)** | Closest competitor. No-code + API, 250+ comp-bio tools (RFdiffusion, ProteinMPNN, BindCraft, **Boltz Prot-1**), $13.6M Series A May 2025 | A tool *menu*. You must already know which of 250 tools to run and why. No research layer, no citations, no guidance |
| Neurosnap | Hosted model catalogue | Same: a catalogue, not a workflow |
| Cradle, Biomatter, Arzeda | Protein engineering for industry | Not infection, not Africa, enterprise sales |
| Latch Bio | Bioinformatics data platform | Different layer entirely |

Sources: [Tamarind tools](https://www.tamarind.bio/tools/boltz-prot-1),
[CB Insights](https://www.cbinsights.com/company/tamarind-bio/alternatives-competitors),
[YC](https://www.ycombinator.com/companies/tamarind-bio).

**The honest differentiation is not the models — everyone has the same models.**
It is: (a) the question "which infection?" instead of "which tool?",
(b) a research layer with real citations attached to the pipeline that uses
them, (c) a scope deliberately set at African AMR priorities, and (d) refusing
to overclaim. None of the above does any of that.

---

## 9. Agent design decisions (per `ai-agent-builder`)

- **Pattern: orchestrator + typed tools with code gates.** Not an autonomous
  loop. Every scientific step costs real money and produces results people may
  act on, so each one passes through an `estimate-cost` → human confirm gate
  that the *code* enforces, not the prompt.
- **Model:** `claude-opus-5-5` for the orchestrator (adaptive thinking,
  `effort: high`); `claude-haiku-5-5` for cheap structured extraction
  (parsing a sequence out of pasted text, summarising an abstract). Tool-call
  JSON must be parsed with a real JSON parser; forced `tool_choice` is a 400 on
  Opus 5.5, so steer with `strict: true` plus prompt instruction.
- **Tools** (few, high-level, namespaced): `literature.search`,
  `literature.fetch`, `target.resolve`, `target.fold`, `pocket.define`,
  `molecules.library`, `molecules.screen`, `molecules.design`, `molecules.adme`,
  `results.rank`.
- **Guardrails:** citations verified by re-resolution; spend ceiling per project
  enforced by compare-and-set (already built); every tool result treated as
  data, never as instructions; no claim of clinical efficacy anywhere.
- **Evals before prompt tuning:** 20–50 cases covering target resolution from a
  plain-English infection, sequence→fold, a screen whose known positive must
  rank highly, and citation resolution. Needed because "the agent sounds right"
  is not a measurement.

---

## 10. What this means for the rebuild

1. The scientific spine changes from *design protein binders* to
   **fold the target, then find molecules that jam it** — with protein design
   kept as a second track.
2. Steps 2 and 3 of the user's pipeline are already available behind our
   existing API key, with the same job lifecycle we have already built, tested
   and shipped.
3. The honest limits to state on screen: no toxicity prediction, hundreds not
   billions of molecules, predictions not measurements, and steps 4 and 5 of
   the pipeline (lab, trials) are explicitly not ours.
4. Keep: auth, MongoDB, budget ledger with compare-and-set, reconciler, SSE,
   Mol*, the residue-numbering work, ship-verify. These are the parts that took
   longest and none of them are wasted.

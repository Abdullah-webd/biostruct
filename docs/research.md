# Research log

All findings below were gathered on **2026-11-10** before any architecture decision was made.
Where a fact was verified by direct inspection of a shipped artifact (rather than a web page),
that is stated explicitly, because it is the stronger form of evidence.

---

## 1. The central correction: Boltz-2 cannot design binders

The original brief said "integrate Boltz, through its API" for protein design. That conflates
three different models. Getting this wrong would have cost the project its core feature.

| Model | What it actually does | Available how |
|---|---|---|
| **Boltz-1 / Boltz-2** | Structure co-folding + **binding affinity prediction**. It *scores* a complex you give it. It does **not generate** new binders. | Open source (MIT); also hosted |
| **BoltzGen** | Generative **binder design** — proteins/peptides against protein, nucleic-acid or small-molecule targets. Released 2025-10-26, MIT licence. | Open source only; **not** on the hosted API |
| **BoltzProt-1** | Protein design pipeline. Boltz describes it as "a major advance over BoltzGen", doing "de novo nanobody design across novelty-controlled targets", scored/ranked by **Boltz-PPI**. | **Hosted API only** |
| **BoltzMol-1** | Small-molecule hit discovery (catalog ranking + generation over a >74-billion-compound make-on-demand space). | Hosted API only |

Reported benchmark (Boltz's own claim, not independently verified): *"In de novo nanobody design
across ten hard benchmark targets, BoltzProt-1 nearly tripled the hit rate over BoltzGen"*, with
*"58% of them passing a stringent multi-criteria developability panel in full"*.

BoltzGen's own paper reports nanomolar nanobody binders against 6 of 9 novel targets, testing
≤15 designs per target pair.

### Decision
Use the **hosted Boltz API** (`BoltzProt-1` for design, `Boltz-2` for structure + affinity).

Rejected: self-hosting BoltzGen. It needs an A100-class GPU, ~6 GB of Boltz weights plus ~33 GB of
ESMFold2 weights, a CUDA 13 driver, and minutes per design; its README recommends 10,000–60,000
designs for a real campaign. That is an infrastructure project, not a feature.

Kept as a hedge: a single `DesignEngine` interface so a `BoltzGenLocalEngine` can be added later
for labs that must keep data on-premises. This matters for the African research-institution
audience, some of whom cannot send target data to a US API.

> ⚠️ **Caveat to carry into the product copy.** A ChemRxiv preprint found that the *open-source*
> Boltz-2 (v2.2.1) affinity path does not score the protein–ligand coordinates actually written to
> the output structure. The authors state this does not apply to the closed-source hosted API. We
> cannot verify hosted affinity against pose quality, so affinity-derived numbers are presented as
> model scores, never as predicted potency.

**Sources:** [boltz.com](https://boltz.com/) · [BoltzMol-1/BoltzProt-1 API launch, 2026-06-16](https://boltz.com/boltzmol-boltzprot-api) · [BoltzGen announcement](https://boltz.com/boltzgen) · [BoltzGen preprint](https://www.biorxiv.org/content/10.1101/2025.11.20.689494v1.full) · [MIT Jameel Clinic](https://jclinic.mit.edu/boltzgen/) · [MIT News](https://news.mit.edu/2025/mit-scientists-debut-generative-ai-model-that-could-create-molecules-addressing-hard-to-treat-diseases-1125) · [github.com/HannesStark/boltzgen](https://github.com/HannesStark/boltzgen) · [NVIDIA Boltz-2 NIM](https://docs.nvidia.com/nim/bionemo/boltz2/latest/inference.html) · [Rowan: how to run Boltz-2](https://www.rowansci.com/blog/how-to-run-boltz-2)

---

## 2. The Boltz API surface — verified by SDK inspection

**Correction to an earlier draft of this document.** It claimed Boltz publishes no API reference.
That was wrong, and the mistake was looking at the wrong site. There are two:

| Site | What it is |
|---|---|
| `docs.boltz.com` | **Boltz Lab only** — the web product. No API, auth or key pages. This is the trap. |
| **`api.boltz.com/docs`** | **The actual API documentation** (Stainless-generated): quickstart, authentication, costs, API reference. |

Verified from `api.boltz.com/docs`:
- Sign-up and key creation: **`https://api.boltz.com/console`**. The quickstart says *"After signing
  up for an account in the API Console, you'll be granted credits so you can test the API for
  free."* No amount stated.
- Base URL **`https://api.boltz.bio`**; auth header **`x-api-key: <key>`**; env var `BOLTZ_API_KEY`.
- Key prefixes: `sk_bc_ws_test_`, `sk_bc_ws_live_`, `sk_bc_admin_test_`, `sk_bc_admin_live_`.
  Full key shown **once**, at creation.
- **Test-mode keys (`sk_bc_*_test_`) are free**: *"no GPU usage, and no billing"*, returning
  synthetic results. Significant for us — see §2a below.
- Admin keys can mint further keys via `POST /compute/v1/admin/api-keys`; workspace keys cannot.
- A keyless CLI OAuth path exists:
  `boltz-api --auth-issuer-url "https://lab.boltz.bio" auth login [--device-code]`.
- Packages: `pip install boltz-api`, `npm install boltz-api`.

Still true, and still the reason for our approach: **there is no published OpenAPI spec.**
`api.boltz.com/openapi.json` returns the console shell, not a spec, and the SDK's GitHub repo
(`boltz-bio/boltz-api-python`, linked from their own reference) returns 404. PyPI and npm block
automated fetches.

So we installed the official SDK and read its Stainless-generated types. **Everything in the rest of
this section is verified from shipped code**, not prose — which is stronger than prose anyway, since
it gives exact field names and bounds that no narrative page documents.

- Package: **`boltz-api`**, version **0.50.0**, licence Apache-2.0, contact `contact@boltz.bio`.
- Import: `from boltz_api import Boltz`; `Boltz(api_key=os.environ["BOLTZ_API_KEY"])`.
- Deps: `httpx`, `pydantic`, `anyio`, `distro`, `sniffio`, `typing-extensions`. Async client included.

### 2b. Verified live against the API (2026-11-11)

Confirmed with a real **test-mode workspace key** (`sk_bc_ws_test_`), free and unbilled:

- `GET https://api.boltz.bio/compute/v1/auth/me` → **200**. Returns `principal_type`,
  `api_key_id`, `key_type: "workspace"`, **`mode: "test"`**, `organization_id`,
  `selected_organization_id`, `workspace_id`, `organization_name`. So the API reports its own
  mode — we can assert at startup that production is not accidentally running on a test key, and
  that development is not accidentally running on a live one.
- `POST /compute/v1/protein/design/estimate-cost` → **200**, with a `no_template` protein target
  plus a `boltz_curated` / `boltz_nanobody` binder spec and `num_proteins: 10`:

```json
{
  "estimated_cost_usd": "0.5000",
  "breakdown": { "application": "protein_design", "num_units": 10, "cost_per_unit_usd": "0.0500" },
  "disclaimer": "This is an estimate only and may differ from your actual charges. Final billing is based on exact token counts computed at run time. ..."
}
```

Three things this settles:

1. **The request schema in this document is correct.** A `no_template` target and a
   `boltz_curated` nanobody binder spec are accepted as designed, so §8's `DesignSpec` maps
   straight onto the wire format.
2. **$0.05/design is confirmed**, matching the published 257–512 crop tier for a KPC-2-sized
   target. The research figures were right.
3. ⚠️ **Money comes back as a *string*, not a number** (`"0.5000"`, `"0.0500"`). All cost and
   budget arithmetic must use `Decimal`, never `float`. Parsing these as floats would introduce
   rounding error into spend tracking. Enforced by a unit test.

Also noted: billing is *"based on exact token counts computed at run time"*, so the estimate is
genuinely an estimate. The budget must therefore **reserve** the estimate and **settle** the actual
afterwards — which is why `budgets` carries both `reserved_usd` and `spent_usd`.

### 2a. Test-mode keys change the development plan

Because test-mode keys are unbilled and return synthetic results, development does **not** need
recorded fixtures as its only defence against cost. The plan becomes:

| Purpose | Key |
|---|---|
| Day-to-day development, CI, E2E | **test key** — free, fast, no GPU wait |
| Contract tests | recorded fixtures (deterministic, offline) |
| M1 proof that the live API matches its types | **live key**, ~30 predictions |
| The demo run | **live key**, ~100 predictions |

Fixtures are still worth keeping: they make tests deterministic and runnable offline, and they
catch SDK field renames. But test keys mean a contributor can exercise the real request/response
path end to end without spending anything, which fixtures alone cannot prove.

One caution: synthetic results are **not** scientifically meaningful. Ranking logic must be
validated against the real M1 run, never against test-mode output.

### Resource tree

```
client.protein.design              # BoltzProt-1 — de novo binder design   ← our core
client.protein.library_screen      # screen an existing sequence library
client.protein.sequence_redesign   # redesign an existing sequence
client.predictions.structure_and_binding   # Boltz-2 structure + affinity
client.predictions.adme
client.small_molecule.{design,explore,library_screen}
client.share_links
client.auth.me
client.admin.{api_keys,usage,workspaces}
```

### REST paths (all under `/compute/v1/`)

```
POST   /compute/v1/protein/design                     start a run
GET    /compute/v1/protein/design/{id}                 status + progress
GET    /compute/v1/protein/design/{id}/results         paginated results
POST   /compute/v1/protein/design/{id}/stop
POST   /compute/v1/protein/design/{id}/resume
POST   /compute/v1/protein/design/estimate-cost        ← cost guardrail
GET    /compute/v1/protein/design/curated-specifications
POST   /compute/v1/protein/design/{id}/delete-data     ← data-retention control
POST   /compute/v1/predictions/structure-and-binding
GET    /compute/v1/admin/usage
GET    /compute/v1/admin/workspaces/{id}/spending-limit
POST   /compute/v1/admin/workspaces/{id}/spending-limit  ← hard budget cap
```

Per-resource methods: `start`, `retrieve`, `list`, `list_results`, `estimate_cost`, `stop`,
`resume`, `delete_data`. There is also a convenience `run()` that submits, polls every 5 s and
downloads to a directory — **we do not use `run()`**; it blocks. We drive `start` + `retrieve`
ourselves so progress can be streamed to the browser.

### `protein.design.start()` parameters

| Param | Notes |
|---|---|
| `target` | `structure_template` or `no_template` |
| `binder_specification` | `no_template` / `structure_template` / `boltz_curated` / `uniformly_sampled` |
| `num_proteins` | **min 10, max 1,000,000** |
| `idempotency_key` | prevents duplicate submission on retry — we always send one |
| `workspace_id` | admin keys only |

`target.structure_template` takes a `structure` (a **URL is auto-detected**, or base64 CIF with
media type `chemical/x-cif`) plus a `chain_selection` map. Each chain entry declares which residues
to keep, which are **epitope residues** (the hotspot/binding site), which are non-binding, and
which are flexible.

**This is the single most useful finding for our workflow:** because `structure` accepts a URL, an
RCSB mmCIF URL can be handed straight to Boltz. Step 2 → Step 3 of the workflow needs no file
upload, no download, no staging bucket.

`binder_specification.boltz_curated` takes `binder: "boltz_nanobody" | "boltz_antibody"` and lets
Boltz manage the template list. This is our default — it is the shortest path from a PDB ID to
real designs. Its `rules` support real medicinal-chemistry filters applied *before* scoring:
`excluded_amino_acids` (e.g. exclude C, P), `excluded_sequence_motifs` (with `X` wildcard, e.g.
`"NXS"` to drop N-glycosylation sequons) and `max_hydrophobic_fraction`.

`binder_specification.no_template` takes `modality: "peptide" | "antibody" | "nanobody" |
"custom_protein"` and sequence entities with fixed/designed segments.

### Job status

```python
status: Literal["pending", "running", "succeeded", "failed", "stopped"]
progress: Optional[Progress]          # includes num_proteins_generated
```

### Result metrics — these ARE our ranking inputs

Binder runs return `metrics` with, verbatim from the SDK types:

| Metric | Direction | SDK's own description |
|---|---|---|
| `binding_confidence` | higher better | "Primary metric for hit discovery." |
| `iptm` | higher better | "Confidence in the protein-protein interface." |
| `ipsae_min` | higher better | lower of target→binder and binder→target ipSAE at a 10 Å PAE cutoff |
| `min_interaction_pae` | **lower better** | "Lower values indicate higher confidence." |
| `structure_confidence` | higher better | overall fold confidence |
| `helix_fraction`, `sheet_fraction`, `loop_fraction` | descriptive | secondary-structure composition |

Each result also carries `id`, `created_at`, `entities[]` (the designed sequence as
`{type: "protein", value: "<AA sequence>", chain_ids: [...]}`), `warnings[]` (`{code, message}`),
and `artifacts`:

```
artifacts.structure.url  + url_expires_at    # the predicted complex, mmCIF
artifacts.archive.url    + url_expires_at    # full run archive
```

Generic (non-binder) runs return only `structure_confidence` and the secondary-structure
fractions — **no binding metrics**. Our ranking code must branch on
`result.type: "binder" | "generic"`.

> ⚠️ `url_expires_at` means **presigned, expiring URLs**. We cannot store these and show them in the
> UI next week. Every artifact must be copied into our own object storage on completion. This is a
> hard architectural requirement, not an optimisation.

### Pricing (from [boltz.com/pricing](https://boltz.com/pricing) and [/api](https://boltz.com/api))

Priced per molecule, and for proteins by crop size:

| Crop size | Protein design & screen | Structure & binding |
|---|---|---|
| 0–256 | $0.025 | $0.025 |
| 257–512 | $0.05 | $0.05 |
| 513–1024 | $0.10 | $0.10 |
| 1025–2048 | $0.20 | $0.20 |
| 2049+ | $0.40 | $0.40 |

ADME: $0.01/molecule. Boltz Lab "Professional": $0 subscription, **up to 200 free predictions per
month**, then usage-based. No published rate limits.

> ⚠️ **Open question.** The "200 free predictions" line appears on the *Boltz Lab* plan table
> (the web product), not on the API page. Whether it covers **API** usage is **unverified**.
> Resolve before relying on it: `client.auth.me()` and `GET /compute/v1/admin/usage` will say,
> and both are free to call. Until then, treat the free tier as probable but unconfirmed.

### What this actually costs us, by phase

`num_proteins` has a floor of **10** and a ceiling of **1,000,000**. KPC-2 is ~293 aa, so a
nanobody–KPC-2 complex lands in the 257–512 crop tier at ~**$0.05/design**.

| Phase | Predictions needed | Cost |
|---|---|---|
| M1 — prove the live API matches its types | 3 runs × 10 designs = **30** | free tier |
| Capture fixtures for every response shape (once) | ~**20** | free tier |
| All other dev + CI, forever | **0** — replayed from fixtures | free |
| One good demo run | **100** | free tier |
| **Total to build the whole platform** | **~150** | **$0** |

**So the build is free.** 200/month covers it with room to spare, provided development replays
recorded fixtures instead of hitting the API — which the contract-test design already requires for
determinism, independent of cost.

Money appears in exactly two places, and neither is the build:

1. **Real research use.** BoltzGen's own README recommends 10,000–60,000 designs for a genuine
   campaign. At 200/month free that is decades. Any researcher doing real work pays, so the
   platform needs budget and estimate features *as product features for them* — not for us.
2. **Accidents.** `num_proteins` max is 1,000,000. One misplaced digit is **1,000,000 × $0.05 =
   $50,000**. This, not our dev spend, is the real reason for a hard spending limit, a mandatory
   confirmed estimate, and a per-project budget ceiling. A UI cap on `num_proteins` is cheap
   insurance against a mistake that cannot be undone.

Therefore: `estimate_cost` before every run, explicit human confirmation, a Boltz **workspace
spending limit** set out of band as a second independent backstop, a UI ceiling on `num_proteins`,
and small defaults during development.

---

## 3. OpenAI Agents SDK + Realtime

- Package **`openai-agents`**, latest **0.24.0** (2026-10-15), requires **Python ≥ 3.10**.
  [PyPI](https://pypi.org/project/openai-agents/)
- `Agent(name, instructions, tools, handoffs, model, input_guardrails, output_guardrails, output_type)`.
- Runner: `await Runner.run(...)`, `Runner.run_sync(...)`, `Runner.run_streamed(...)` (not awaited;
  returns an object with `.stream_events()`).
- **Handoffs vs agents-as-tools.** A handoff *transfers control* — the specialist takes over the
  conversation. `agent.as_tool(tool_name=..., tool_description=...)` keeps the orchestrator in
  control and returns the specialist's output as a tool result.
  **We need agents-as-tools**, because our orchestrator must compose several specialists'
  output into one answer (e.g. "compare these three candidates") rather than hand the user off.
  [docs](https://openai.github.io/openai-agents-python/tools/#agents-as-tools)
- **Sessions:** built-ins include `SQLiteSession`, `RedisSession`, `SQLAlchemySession`,
  **`MongoDBSession`**, `OpenAIConversationsSession`. Custom stores implement the `Session`
  protocol (`session_id`, `session_settings`, `get_items`, `add_items`, `pop_item`,
  `clear_session`) — no base class needed. `MongoDBSession` matches our chosen database, so chat
  history needs no bespoke persistence layer.
- **Context:** pass `context=obj` to `Runner.run`; tools take `wrapper: RunContextWrapper[T]` as
  their first argument and read `wrapper.context`. Context is **not** sent to the model — correct
  place for the project id, the Mongo handle and the Boltz client.
- **Guardrails:** `@input_guardrail` / `@output_guardrail` returning
  `GuardrailFunctionOutput(output_info, tripwire_triggered)`. Input guardrails run on the first
  agent only; output guardrails on the final agent. Tripwires raise
  `InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered`.
  This is exactly the right mechanism for the brief's **Scientific Review Agent**.
- **Tracing:** on by default, sent to OpenAI's Traces dashboard. Disable with
  `OPENAI_AGENTS_DISABLE_TRACING=1`, `set_tracing_disabled(True)` or
  `RunConfig(tracing_disabled=True)`. Group with `with trace("name")`.
  Default-on egress of research content is a **compliance decision**, not a default to accept
  silently — see ARCHITECTURE §Security.
- **Streaming events:** `raw_response_event`, `run_item_stream_event`, `agent_updated_stream_event`;
  run items include `message_output_created`, `tool_called`, `tool_output`,
  `reasoning_item_created`, `handoff_requested`.
- **Long-running tools:** the docs document `@tool(timeout=...)` with
  `timeout_behavior` of `error_as_result` / `raise_exception`, and durable integrations (Temporal,
  Dapr, Restate, DBOS). There is **no documented pattern for a tool that runs for minutes**, which
  confirms our approach: design tools must *start a job and return immediately*.

### Realtime voice
- Models: **`gpt-realtime-2.1`** recommended (also `-2`, `-2.1-mini`, `-1.5`). Max session 60 min.
- **Browser architecture is settled:** the browser connects **directly to OpenAI over WebRTC**
  using an ephemeral key minted by our backend.
  - Mint: `POST https://api.openai.com/v1/realtime/client_secrets` with our standard key as Bearer.
    Response has `value` (`ek_...`) and `expires_at`. TTL `expires_after.seconds` defaults to **600**,
    range 10–7200.
  - Browser: `POST https://api.openai.com/v1/realtime/calls` with the ephemeral key as Bearer and
    `Content-Type: application/sdp`; response body is the SDP answer. Data channel label `oai-events`.
  - [WebRTC guide](https://developers.openai.com/api/docs/guides/voice-webrtc)
- **Tool execution is the open question.** With browser WebRTC, function calls surface *in the
  browser* via `response.done` (output type `function_call`); you reply with
  `conversation.item.create` + `response.create`. Our tools (Boltz, Mongo, UniProt) must run
  server-side and must never see a browser-held key. See ARCHITECTURE §Voice for the bridge design.
  The Python SDK's `RealtimeAgent` runs tools server-side but over a **server WebSocket only** —
  WebRTC is not available in the Python SDK.
- Pricing per 1M tokens: `gpt-realtime-2.1` audio in **$32.00** / out **$64.00**, cached in $0.40;
  `-mini` $10.00 / $20.00. `gpt-live-1` is $0.05/min. Transcription $0.017/min.
  [pricing](https://developers.openai.com/api/docs/pricing)
- The `openai/openai-realtime-agents` demo repo still calls realtime "beta" and is likely stale.
- The npm page for `@openai/agents` returned HTTP 403 — **TypeScript SDK version unverified**; pin
  it at install time and record the version.

**Sources:** [Agents SDK](https://openai.github.io/openai-agents-python/) · [realtime guide](https://openai.github.io/openai-agents-python/realtime/guide) · [voice agents](https://developers.openai.com/docs/guides/voice-agents)

---

## 4. Scientific data sources

| Source | Access | Notes |
|---|---|---|
| **UniProt** | `https://rest.uniprot.org/uniprotkb/search?query=...&format=json` | No key. Supports `organism_id`, `reviewed:true`, cross-refs to PDB. Use `reviewed:true` (Swiss-Prot) to avoid drowning the user in TrEMBL predictions. |
| **RCSB PDB** | Search API `https://search.rcsb.org/rcsbsearch/v2/query`; data API `https://data.rcsb.org/rest/v1/core/entry/{id}`; files `https://files.rcsb.org/download/{id}.cif` | No key. The `files.rcsb.org` CIF URL is what we hand to Boltz as a target `structure`. |
| **PubMed** | NCBI E-utilities `esearch`/`esummary`/`efetch` | **3 req/s without a key, 10 with one.** Needs `tool=` and `email=` params, and a server-side rate limiter. |

All three are public, unauthenticated and well-suited to agent tools. None needs scraping.

---

## 5. Target choice: *Klebsiella pneumoniae* carbapenemase (KPC-2)

Selected for the validated end-to-end workflow.

- **Burden and African relevance.** *K. pneumoniae* is among the leading causes of
  healthcare-associated infection in sub-Saharan Africa, and resistance there is poorly
  characterised relative to better-resourced settings. A Gauteng, South Africa study found
  `bla_NDM-1` (n=11) and `bla_OXA-181` (n=11) as the commonest carbapenemase genes, with 10 of 22
  sequenced isolates carrying **two or more** carbapenemase genes.
- **Design implication.** Multiple co-carried carbapenemases mean a single-target design is
  scientifically insufficient. The platform's data model must therefore treat a project as
  **one disease → many targets**, not one target. This changed our schema.
- **Tractability.** KPC is the most clinically significant class A carbapenemase, it has solved
  PDB structures, and there is protein-inhibitor prior art (β-lactamase inhibitory protein BLIP-II
  against KPC-2) so a reviewer can judge whether our output is sane.
- **Honest framing.** Prior art here is early-stage *in vitro* / *in silico* work, not a validated
  African therapeutic programme. Nothing in the product should imply otherwise.

Gap acknowledged: we did not find a WHO or African Union priority-pathogen protein-target list in
this round. Worth a follow-up before expanding beyond KPC.

**Sources:** [Gauteng CPKP molecular epidemiology](https://www.nature.com/articles/s41598-024-70910-9) · [SA prevalence meta-analysis](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12290021/) · [12-year SSA AMR analysis](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10408162/) · [Ghana carbapenemase detection](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9803230/) · [BLIP-II vs KPC-2](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10817188/) · [Ethiopia phage study](https://pmc.ncbi.nlm.nih.gov/articles/PMC13670530)

---

## 6. Comparable products, and where we differ

| Product | What it is | Gap we exploit |
|---|---|---|
| **Boltz Lab** | Boltz's own web UI over the same models | Model-centric. Starts from a target you already have. No disease→target discovery, no literature, no agents. |
| **Tamarind Bio** | Hosted catalogue of ~100 bio models, job-per-model | A tool launcher. The researcher still stitches the workflow together by hand — the exact problem in our brief. |
| **Rowan** | Computational chemistry platform, strong Boltz-2 support | Small-molecule/chemistry oriented, not binder design campaigns. |
| **OpenProtein.ai** | Protein-engineering API + workflows | Developer API, not a research environment. Assumes computational expertise. |
| **NVIDIA BioNeMo** | Model microservices (incl. Boltz-2 NIM) | Infrastructure. Needs GPUs and an MLOps team — precisely our audience's constraint. |

**Our differentiator is the connective tissue, not the model.** Everyone above gives you a model
endpoint. Nobody takes you from *"carbapenem-resistant Klebsiella in my ward"* to a ranked,
explained, exportable candidate list with the literature attached and the uncertainty stated.

This also sets the quality bar: since the science is Boltz's, **our** work is judged on target
discovery, ranking transparency, interpretation and honesty. Those must be excellent.

---

## 7. Pitfalls identified, and the decision each one forced

| # | Pitfall | Decision |
|---|---|---|
| 1 | Boltz-2 can't design binders | Use BoltzProt-1 via hosted API (§1) |
| 2 | Artifact URLs expire (`url_expires_at`) | Mirror every artifact to our own object storage on job completion |
| 3 | A design run takes minutes–hours; `num_proteins` max is 1,000,000 | Agent tools start jobs and return a job id immediately; a reconciler polls |
| 4 | `num_proteins` max is 1,000,000 — one typo is a $50,000 call. (The *build* itself fits in the free tier; see §2.) | `estimate_cost` + confirmed estimate + workspace spending limit + per-project budget + UI cap on `num_proteins` |
| 5 | No published OpenAPI spec; SDK is Stainless-generated and will churn | Pin `boltz-api==0.50.0`; isolate behind one adapter module; contract-test against recorded fixtures |
| 6 | Agents SDK tracing is on by default and egresses content to OpenAI | Explicit opt-in per deployment; default off for self-hosted installs |
| 7 | Browser WebRTC surfaces tool calls client-side | Server-side tool bridge; the browser never holds a Boltz or OpenAI standard key |
| 8 | LLMs will happily overstate what a computational score means | Scientific Review Agent as an **output guardrail**, not a suggestion; claim-linting on every generated narrative |
| 9 | `generic` design results carry no binding metrics | Rank only `type: "binder"` results; branch explicitly |
| 10 | Hosted affinity may not correspond to the written pose (§1 caveat) | Never present affinity as predicted potency; label as model score |
| 11 | PubMed is 3 req/s unkeyed | Server-side token-bucket limiter + cache; send `tool=`/`email=` |
| 12 | System Python here is 3.9; `openai-agents` needs ≥3.10 | Pin Python 3.12 via `uv` |
| 13 | Isolates carry multiple carbapenemases | Schema is one project → many targets |
| 14 | Sending target data to a US API may be a blocker for some institutions | `DesignEngine` interface keeps a future on-prem BoltzGen path open |

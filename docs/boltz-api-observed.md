# The Boltz API as it actually behaves

Captured from the live API on **2026-11-11** with a test-mode workspace key
(`sk_bc_ws_test_`), by submitting a real 10-design BoltzProt-1 run against KPC-2
(PDB 2OV5) and harvesting the results.

This document exists because **Boltz publishes no OpenAPI spec**, and in several
places the generated SDK's declared types disagree with what the API returns.
Where they disagree, this file wins — the adapter is written against observed
behaviour, and `tests/fixtures/boltz/` holds the captured payloads so a drift
fails the build.

Reference docs: `https://api.boltz.com/docs` (not `docs.boltz.com`, which covers
only the Boltz Lab web product). Base URL `https://api.boltz.bio`, auth header
`x-api-key`.

---

## Verified: a raw RCSB URL can be the target

```json
"structure": { "type": "url", "url": "https://files.rcsb.org/download/2OV5.cif" }
```

Accepted, and Boltz **fetches and re-hosts it at submission time** — the echoed
`input.target.structure.url` in the job payload points at Boltz's own S3 copy,
not at RCSB.

This is load-bearing for the product: there is no upload, download or staging
step between choosing a PDB structure and starting a design run.

---

## Corrections to the SDK's declared types

### 1. Results carry no `type` discriminator

The SDK declares `BinderProteinDesignResult` and `GenericProteinDesignResult`
with `type: Literal["binder"] | Literal["generic"]`. **The live response has no
`type` field at all.**

So binder-vs-generic is inferred from whether binding metrics are present. The
adapter uses `DesignMetrics.is_binder`, and ranking only scores binder results.

### 2. `ipsae_min` was absent from every result

It is `Optional` in the SDK, and in practice it was missing from all 10 results.
Ranking therefore **redistributes its weight** across the remaining metrics and
flags the candidate, rather than defaulting it to 0 — which would silently bury
an otherwise strong candidate.

Unknown whether it appears in live mode. Treat its presence as optional either way.

### 3. Pagination is cursor-based, not offset-based

```json
{ "data": [...], "has_more": true, "first_id": "...", "last_id": "..." }
```

Paging uses `after_id` (also `before_id`, `ids`, `limit`; default limit 100).
Verified by paging a 10-result run 4 at a time: 3 pages, 10 unique results.

There is no total count. The run's `progress.num_proteins_generated` is the only
count available.

### 4. Job payloads carry more fields than the SDK lists

Observed on `POST /protein/design` and `GET /protein/design/{id}`:

| Field | Note |
|---|---|
| `pipeline`, `pipeline_version` | `"boltzprot"`, `"1.0"` |
| `engine`, `engine_version` | `"boltzprot"`, `"1.0"` |
| **`livemode`** | `false` for a test-mode run. See below. |
| `started_at`, `completed_at`, `stopped_at` | nullable timestamps |
| `data_deleted_at` | nullable; set once `delete-data` has run |
| `input` | the whole request echoed back, with the re-hosted structure URL |
| `progress.total_proteins_to_generate` | the denominator for a progress bar |
| `progress.latest_result_id` | newest result so far |

### 5. `livemode` is the real test/live signal

Each run says whether it is live. We store it and carry it to the UI, because
**test-mode results are synthetic and must never be presented as science.**
Absent `livemode` is parsed as `false`: assuming results are real is the
dangerous default.

---

## Residue indexing — a correctness trap

From the `chain_selection` schema:

- Indices are **0-indexed**.
- `epitope_residues`, `non_binding_residues` and `flexible_residues` must all be
  within `crop_residues`.
- `epitope_residues` and `non_binding_residues` must not overlap.

PDB files number residues by *author* numbering, which does not start at 1 and
can skip. Passing an author residue number straight through would silently mark
the wrong epitope — designs aimed at the wrong part of the protein, with no error
anywhere.

Measured on 2OV5 chain A (KPC-2): 261 residues modelled of 293, author numbering
30–292, **non-contiguous** (gaps at 58 and 253). So `index = author - 30` is
wrong after the first gap, and `index = author` puts Ser70 on Ala101. There is
also a **third** numbering system: UniProt annotates the catalytic serine at
position 69 while the literature and the PDB call it 70.

The adapter validates the subset and overlap constraints locally; the conversions
live in `sources/residues.py` and are covered by 48 tests.

---

## Artifacts expire in about 30 minutes

```json
"artifacts": {
  "structure": { "url": "...", "url_expires_at": "2026-11-11T15:06:05.074Z" },
  "archive":   { "url": "...", "url_expires_at": "2026-11-11T15:06:05.074Z" }
}
```

Created 14:36, expiring 15:06. **Thirty minutes**, which is tighter than ADR-005
assumed. Mirroring artifacts into our own storage is the harvester's
highest-priority work, and a harvest that stalls for half an hour loses data that
was paid for.

The curated-specification endpoint returns presigned URLs with the same ~30
minute window, so those must not be cached beyond it or handed to a browser to
fetch later.

---

## Test mode: what it does and does not prove

Test-mode keys are free and unbilled. They exercise the real request and
response path, which is what the adapter needs. They do **not** produce science,
and a live run on 2026-11-11 showed the differences are larger than expected.

| | Test mode | Live mode (verified) |
|---|---|---|
| Structure artifact | **522-byte stub**, `data_test_fixture`, 3 atoms | **261 KB ModelCIF**, 2,871 atoms, the full complex |
| Protein entities per result | **two**, random lengths | **one** — only the designed binder |
| Chain ids | `A`, `B` | binder `NANO1`, target keeps `A` |
| `binding_confidence` | ~0.7 | **~1e-6** |
| `ipsae_min` | absent | **also absent** (0 of 10) |
| Duration | ~10 seconds | ~minutes |

### Which chain is the designed binder (settled)

A live result's `entities` list contains **only the designed binder** — the
target is not repeated there. The structure file contains both:

```
chain 'A'      : 261 residues -> AEPFAKLEQDFGGSIGVYAMDTGSGATVSYRAEERFPLCSS...  (KPC-2 target)
chain 'NANO1'  : 121 residues -> QVKLEESGGGLVQAGRSLRLSCAASEHTFSKYTVGWFRQAP...  (designed nanobody)
```

That binder sequence begins `QVKLEESGGGLVQAGRSLRLSCAAS`, which is a genuine VHH
framework, and 121 residues is nanobody-sized.

The rule we implement does not depend on the `NANO1` naming: **the target keeps
the chain id that was submitted, so any other chain in the result is designed.**
Candidates store `binder_chain_ids` and `target_chain_ids` explicitly.

### The structure artifact is ModelCIF

`data_model`, with `_struct.pdbx_structure_determination_methodology
computational` and a ModelCIF dictionary reference. A standard `_atom_site` loop
with 19 columns including `auth_asym_id`, `label_comp_id` and `label_seq_id`, so
Mol\* can read it directly. Saved as
`apps/api/tests/fixtures/boltz/structure_live_mode.cif` and kept permanently, so
the viewer never needs another paid run to be tested.

### `binding_confidence` is on a completely different scale in live mode

This is the most important finding, and the one most likely to have caused real
harm.

| | |
|---|---|
| Test mode | 0.7286, 0.7889, 0.7177 |
| Live mode | 2.35e-06, 1.82e-06, 8.88e-07, down to 2.75e-07 |

Roughly **300,000 times smaller**. For ten nanobody designs against KPC-2 that
is scientifically unsurprising — a real campaign generates thousands and expects
almost all of them to fail — but it broke our ranking's honesty.

Ranking normalises **within a run**, so the best of ten hopeless designs still
scores near 1.0. Before this run, the platform described a design with a
0.0000024 predicted binding probability as *"promising"*. See
[docs/residue-numbering.md](residue-numbering.md) for the other member of this
family of silent errors.

The fix (ADR-030): a relative score and an absolute band are reported
separately. Display bands over the model's raw output — high ≥ 0.5, moderate
≥ 0.1, low ≥ 0.01, otherwise negligible — are presentation thresholds, not
validated cutoffs, and the raw value is always shown. A negligible band yields
the verdict `no_predicted_binding` regardless of rank, the run is flagged
`nothing_predicted_to_bind`, and every explanation now **leads with the absolute
value** rather than the flattering rank.

## Permissions: `/admin/usage` needs an admin key

```
GET /compute/v1/admin/usage -> 403 {"code":"forbidden","message":"Admin API key required"}
```

A **workspace** key cannot read usage. So the open question "how much of the free
allowance is left?" cannot be answered with a workspace key — it needs an admin
key, or the console UI.

`/auth/me` does work with a workspace key and returns `key_type`, `mode`,
`workspace_id`, `organization_id` and `organization_name`.

The usage endpoint also requires `starting_at`, `ending_at` and
`window_size` (`HOUR`, max 31 days; `DAY`, max 365) — it is not a balance
endpoint but a time-bucketed usage report.

---

## Costs, confirmed

A nanobody–KPC-2 complex (293-residue target) bills at the **257–512 crop tier**:

| | |
|---|---|
| per design | **$0.0500** |
| 10 designs | $0.5000 |
| 100 designs | $5.00 |

Money arrives as **strings** (`"0.0500"`), never numbers. Parsing as float would
drift spend tracking, so all money is `Decimal` (ADR-017).

The response's own disclaimer states billing is "based on exact token counts
computed at run time", so an estimate is genuinely an estimate — hence budgets
reserve the estimate and settle the actual.

---

## Reproducing this

```bash
cd apps/api
python scripts/verify_boltz.py              # 10 designs, refuses a live key
python scripts/verify_boltz.py --allow-live # only when billing is intended
```

The script checks identity, cost arithmetic, submission, polling to a terminal
state, full harvest with pagination, and artifact download. Re-run it after any
`boltz-api` bump or if contract tests start failing.

---

# Part 2 — the v2 endpoints, observed 2026-11-17

Captured with a test-mode workspace key against the live API, by running a real
`structure-and-binding` prediction and a real 3-molecule `library-screen`.
Base `https://api.boltz.bio`, prefix `/compute/v1`, header `x-api-key`.

## Prices, which the docs do not publish

`estimate-cost` is the only published price source. Observed:

| Application | `cost_per_unit_usd` | Unit |
|---|---|---|
| `structure_and_binding` | **$0.0250** | sample |
| `small_molecule_library_screen` | **$0.0250** | molecule |

So a 250-molecule screen is **$6.25** and a single fold is **2.5 cents**. Every
estimate carries a disclaimer that billing is actual token counts at run time,
and that large library-screen estimates are *extrapolated from a sample*.

## `POST /predictions/structure-and-binding`

Request is wrapped: `{"input": {"entities": [...], "binding": {...}}, "model": "boltz-2.1"}`.
Job ids are prefixed `sab_pred_`. Response carries `model: "boltz-2.1"`,
`version: "v2026-03-01"`, and `expires_at` seven days out.

Output shape:

```
output.best_sample.metrics   structure_confidence, ptm, iptm, ligand_iptm,
                             protein_iptm, complex_plddt, complex_iplddt,
                             complex_pde, complex_ipde
output.best_sample.structure {url, url_expires_at}   # ~30 min presigned
output.all_sample_results[]  same shape, one per num_samples
output.archive               {url, url_expires_at}
```

### No affinity field is returned

Submitted with `binding: {"type": "ligand_protein_binding", "binder_chain_id": "B"}`
and the response contained **zero occurrences of the string `affinity`**. There
is no `affinity_pred_value` and no binary binding probability — only the
confidence metrics above.

Unknown whether live mode differs. **Until a live run shows otherwise, the
product must not promise a predicted affinity from this endpoint.** For hit
discovery use `library-screen`, whose `binding_confidence` the docs call the
"primary metric for hit discovery".

### `protein_protein_binding` is accepted

`estimate-cost` returned 200 for
`{"type": "protein_protein_binding", "binder_chain_ids": ["B"]}`, contradicting
the open-source docs' statement that protein–protein affinity is unsupported.
Accepted at submission is not the same as *returns a useful number*; unresolved
until a live run.

## `POST /small-molecule/library-screen`

**The body is flat. There is no `input` wrapper and no `model` field.**

```json
{
  "target": {"entities": [{"type": "protein", "chain_ids": ["A"], "value": "<sequence>"}]},
  "molecules": [{"smiles": "<SMILES>", "id": "<your id>"}],
  "molecule_filters": {"boltz_smarts_catalog_filter_level": "recommended"},
  "idempotency_key": "..."
}
```

Optional on `target`: `pocket_residues` (map chain → residue numbers; **omitted
means the model auto-detects the pocket**), `constraints`, `reference_ligands`,
`bonds`.

### The validator lies when you send an unsupported property

Sending `"model": "boltz-2.1"` produced:

> `request body contains an unsupported property 'model'. 1 other field needs
> attention.` … `target_profile: is required`

**There is no `target_profile` field.** The request validator is union-based and
reports failures from a different branch, so a bogus required-field name appears
whenever any property is unsupported. Cost us several probe rounds. **Trust the
"unsupported property" half of the message and ignore the invented requirement.**

### Job and results

Job ids are prefixed `sm_scr_`; `pipeline: "boltzmol"`, `pipeline_version: "1.0"`
(`engine` / `engine_version` are deprecated). Progress:

```json
{"total_molecules_to_screen": 3, "num_molecules_screened": 3,
 "num_molecules_failed": 0, "latest_result_id": "..."}
```

`GET /{id}/results` pages with `after_id` / `before_id` / `ids` / `limit`
(1–1000, default 100) and returns `data`, `first_id`, `last_id`, `has_more`.
Each result: `smiles`, `external_id`, `metrics` (**`binding_confidence`** — the
primary hit metric — plus `optimization_score`, `structure_confidence`, `iptm`,
`ptm`, `complex_plddt`, `complex_iplddt`), **`adme` inline**
(`lipophilicity`, `permeability`, `solubility`), `artifacts.structure`,
`artifacts.ligand_structure`, `artifacts.archive`, and `warnings[]`.

ADME comes back **with the screen**, so a separate `/predictions/adme` call is
only needed for molecules we are not screening.

### Test mode discards the ids you send

Molecules submitted as `avibactam`, `penicillin-g`, `ethanol-decoy` came back
with `external_id` of `mol_0`, `mol_1`, `mol_2`, and metrics that are plainly
synthetic — the ethanol decoy outscored avibactam.

Two consequences:

1. **Results cannot be joined back to our library by `external_id` in test
   mode.** Join by array position for test runs, by `external_id` in live mode,
   and assert which mode is in play rather than assuming.
2. **The positive-control eval (avibactam must rank highly against KPC) cannot
   run in test mode.** It must be a live-mode test, run deliberately and
   cheaply — three molecules is 7.5 cents.

# ChEMBL API — observed behaviour

Measured against the live API on **2026-11-17**. Base URL
`https://www.ebi.ac.uk/chembl/api/data`. No key, no auth, free.
Official docs: <https://chembl.gitbook.io/chembl-interface-documentation/web-services/chembl-data-web-services>

This file records what the API *does*, which in several places is not what a
reader of the documentation would assume. Each item cost a probe to find, and
each one is pinned by a test in `apps/api/tests/test_chembl.py` against a
captured response, so a change at ChEMBL fails our build instead of quietly
corrupting a library.

## 1. Every number arrives as a string

The single most dangerous property of this API.

```json
{ "max_phase": "4.0",
  "molecule_properties": { "alogp": "-1.10", "full_mwt": "199.16" } }
```

```json
{ "standard_value": "21.0", "pchembl_value": "7.68" }
```

Sorted as text, `"9.0"` comes after `"150.0"`. A potency ranking built on the
raw field would be **inverted at the top**, which is precisely the part a
researcher reads, and nothing would raise. `_num()` in `sources/chembl.py` is
the only reason that cannot happen here.

## 2. `action_type` is an object, not a string

```json
"action_type": { "action_type": "INHIBITOR",
                 "description": "Negatively effects (inhibits) ...",
                 "parent_type": "NEGATIVE MODULATOR" }
```

Rendered naively, a results table shows a dict. Flattened on parse.

## 3. A molecule can exist with no chemical structure

`CHEMBL3545239` (PF-05019702) returns `"molecule_structures": null`. There is
nothing to submit to a screen, so it must be detected **before** a paid call,
not after. Antibodies and other biotherapeutics land here too, correctly.

## 4. A requested id can vanish without being named

Asking for five ids via `molecule_chembl_id__in` returned four rows and
`"total_count": 4`. Nothing in the response says which id was absent. A curated
library would therefore be quietly smaller than intended; `molecules()` returns
`(found, missing)` so the caller is told.

## 5. Similarity search returns salts and stereoisomers at 100 %

A 70 % search around avibactam:

| ChEMBL ID | similarity | what it actually is |
|---|---|---|
| CHEMBL4578584 | 100 | the sodium salt (`...O.[Na+]`) |
| CHEMBL3990425 | 100 | the enantiomer |
| CHEMBL3895524 | 100 | the salt of the enantiomer |
| CHEMBL3704994 | 100 | a diastereomer |
| CHEMBL2107817 | 100 | "AVIBACTAM SODIUM" |

Five slots, **$0.125 of screening, one answer.** De-duplication by
`parent_chembl_id` plus a salt-stripping key is not a nicety here, it is the
difference between a library and a bill.

## 6. Free-text *molecule* search is weak; *target* search is good but unranked

`molecule/search?q=beta-lactamase inhibitor` returned mostly unnamed compounds.
Molecules are therefore reached **through a target's measured activities**,
which is both more accurate and free.

Target search does find the right proteins — `q=KPC-2` → `CHEMBL5437`,
`CHEMBL6132` — but relevance is not trustworthy:

```
CHEMBL6132      Carbapenem-hydrolyzing beta-lactamase KPC   Klebsiella pneumoniae
CHEMBL3562180   Carbapenem-hydrolyzing beta-lactamase KPC   Klebsiella oxytoca
CHEMBL1835      Thromboxane-A synthase                      Homo sapiens      <-- unrelated
CHEMBL4028      Thromboxane-A synthase                      Rattus norvegicus <-- unrelated
```

**No code path may take `targets[0]` and screen against it.** `find_targets()`
takes `organism` and `proteins_only`, and the agent must state which target it
picked so the researcher can correct it before money is spent.

## 7. `limit` is clamped silently, and search totals are capped

`limit=1001` is served as `limit=1000`. A free-text search reports
`"total_count": 10000` regardless of the real number (a search-engine cap), so
that figure is never shown to a user as a count.

## 8. Not every measurement means "binds"

`activity?target_chembl_id=CHEMBL5437` returns 127 rows whose `standard_type`
is:

| type | n | usable for ranking? |
|---|---|---|
| Ki | 43 | **yes** |
| Inhibition | 31 | no — percentages, not comparable across assays |
| MIC | 30 | no — whole-organism, not binding |
| Activity | 8 | no — percentages |
| Kcat | 5 | **no — the enzyme destroys this molecule** |
| Km | 5 | **no — same** |
| Kcat/Km | 5 | **no — same** |

The last three are the scientifically important exclusion: a library built from
"everything measured against KPC" would include the enzyme's own **substrates**,
i.e. paying to ask whether an enzyme is inhibited by its food. `POTENCY_TYPES`
admits only `IC50 / Ki / Kd / EC50`.

`standard_relation` matters just as much. `>` means the assay **failed** to show
binding at that concentration, so including it seeds the library with known
non-binders. Only `=`, `<` and `<=` count.

Units and relation can also both be `null` on a row that otherwise looks fine.

## 9. A missing molecule is a 404 with an empty body

`molecule/CHEMBL99999999999.json` → `404`, no JSON. Our shared client turns any
404 into `SourceUnavailable`, which is right for a source being down and wrong
for a compound not existing, so `ChemblClient.molecule()` translates it back
into a plain `None`.

## 10. Useful facts

- No rate-limit headers are published. Responses carry `x-chembl-in-cache` and
  `x-chembl-retrieval-time`, and `cache-control: max-age=30000000`. We rate-limit
  conservatively anyway.
- **Avibactam has a measured Ki of 11 nM against KPC-2** (`CHEMBL1689063` vs
  `CHEMBL5437`). That is where the positive control's expected rank comes from:
  a real assay, not an assumption.
- All 127 KPC-2 activities are `assay_type: "B"` (binding).

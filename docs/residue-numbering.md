# Residue numbering: three systems, and why it matters

The single most dangerous conversion in Biostruct. Getting it wrong produces a
design run that completes, costs money, and targets the wrong part of the
protein — with no error raised anywhere.

Everything below was measured against **PDB 2OV5 chain A** (KPC-2 carbapenemase,
UniProt Q9F663) on 2026-11-11, and is pinned by tests in
`apps/api/tests/test_residues.py`.

## The three systems

| System | Basis | Where it appears |
|---|---|---|
| **UniProt position** | 1-based, contiguous, over the *full* sequence | UniProt's curated active-site annotations |
| **PDB author number** | Ambler numbering for this family: gaps, does not start at 1 | The literature, PDB files, what researchers say |
| **Design index** | 0-based into the *modelled* chain | What the Boltz design API requires |

## The specific numbers

- UniProt Q9F663 is **293** residues.
- 2OV5 chain A models **261** of them — the crystal omits a signal peptide and
  disordered termini.
- Author numbering runs **30 to 292** and is **not contiguous**: it skips 58 and 253.

So for KPC-2's catalytic residues:

| Literature | UniProt position | Author number | Design index | Residue |
|---|---|---|---|---|
| Ser70 | 69 | 70 | **39** | S |
| Lys73 | 72 | 73 | **42** | K |
| Ser130 | 129 | 130 | **99** | S |
| Glu166 | 165 | 166 | **135** | E |
| Asn170 | — | 170 | **139** | N |

## Why shortcuts fail

**Subtracting the first author number** (`index = author - 30`) gets Ser70 right
by coincidence and then fails:

| Intended | Shortcut lands on |
|---|---|
| Lys73 | Gly |
| Ser130 | Asp |
| Glu166 | Leu |
| Asn170 | Ser |

**Passing the author number as the index** is worse: Ser70 becomes **Ala101**,
31 residues away on a different surface.

**Treating a UniProt position as an author number** also fails: UniProt's
position 69 read as author 69 selects **Cys**, not the catalytic Ser. The `+1`
only appears after the gap at 58, so it is not a fixed offset either.

## How the code handles it

`apps/api/src/biostruct/sources/residues.py`

- `ResidueMap` is built from RCSB's `auth_to_entity_poly_seq_mapping`, an
  explicit array — element *i* holds the author number of design index *i* —
  precisely because the relationship is not arithmetic.
- `to_index(author)` converts, and **raises rather than guessing**. An unmodelled
  residue gets a specific message naming the covered range, and a residue inside
  a gap is reported as probably disordered.
- `to_indices([...])` reports *every* failure at once, so fixing a ten-residue
  epitope does not take ten round trips.
- `index_from_uniprot_position(position, sequence)` handles the third system by
  aligning the modelled chain inside the full sequence, and checks the residue
  identity as it goes.
- `verify_identities({70: "Ser", ...})` turns "the mapping looks right" into
  "the mapping is right". For KPC-2 the catalytic residues are known from the
  literature, so this is a check that can actually be run.
- `ResidueMap` refuses to construct at all if the mapping array and the sequence
  disagree in length, since a partial mapping mis-places everything after the gap.

## In the API

`PUT /projects/{id}/targets/{id}/epitope` takes **either** numbering, in separate
fields, because they are not interchangeable:

```json
{
  "pdb_id": "2OV5",
  "chain_id": "A",
  "author_residues": [70, 73, 130, 166],
  "expected_residues": {"70": "Ser", "166": "Glu"}
}
```

```json
{ "pdb_id": "2OV5", "chain_id": "A", "uniprot_positions": [69, 72, 129, 165] }
```

Both produce design indices `[39, 42, 99, 135]`. The response includes
`residue_letters` (`"SKSE"` for KPC-2's catalytic site) so a wrong selection is
visible at a glance, and a `mapping` summary so the conversion is not a black box.

`GET /structures/{pdb_id}/chains/{chain_id}/residue-map` inspects a chain's
numbering without committing to anything, which lets the UI explain *why*
residue 5 is unavailable before a researcher tries it.

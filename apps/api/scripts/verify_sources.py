"""Exercise the scientific data sources against the live APIs.

Covers the full step-2 and step-3 path: find a target, gather its evidence, then
convert author residue numbers into the 0-based indices the design API needs —
and *prove* that conversion by checking the mapped residues are the amino acids
the literature says they should be.

    python scripts/verify_sources.py

No keys required; all three sources are public. Exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio

from biostruct.sources import ScienceSources
from biostruct.sources.residues import ResidueMappingError

# KPC-2, the project's reference target.
KPC2_ACCESSION = "Q9F663"
KPC2_PDB = "2OV5"
KPC2_CHAIN = "A"

# Catalytic and substrate-binding residues of class A carbapenemases, in the
# Ambler (author) numbering used throughout the literature. These are the
# ground truth the mapping is checked against.
KPC2_CATALYTIC = {70: "Ser", 73: "Lys", 130: "Ser", 166: "Glu", 170: "Asn", 237: "Thr"}

_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{f' - {detail}' if detail else ''}")
    if not ok:
        _failures.append(label)


def heading(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


async def main() -> int:
    sources = ScienceSources(ncbi_email="")
    try:
        heading("1. UniProt search (reviewed entries only)")
        found = await sources.search_targets(
            'protein_name:"carbapenem-hydrolyzing beta-lactamase KPC"'
        )
        check("search returned results", bool(found.targets), f"{len(found.targets)}")
        check("no sources degraded", not found.degradation.any, str(found.degradation.sources))
        for t in found.targets[:3]:
            print(f"    {t.accession}  {t.protein_name[:46]}")
            print(
                f"        {t.organism} | {t.length} aa | {t.evidence_level} | {len(t.pdb_ids)} PDB"
            )
        kpc2 = next((t for t in found.targets if t.accession == KPC2_ACCESSION), None)
        check(f"{KPC2_ACCESSION} is among the hits", kpc2 is not None)
        if kpc2 is not None:
            check("entry is reviewed", kpc2.reviewed)
            check("sequence length is 293", kpc2.length == 293, str(kpc2.length))

        heading("2. Evidence fan-out (UniProt + RCSB + PubMed, concurrently)")
        evidence = await sources.gather_evidence(KPC2_ACCESSION, structure_limit=5)
        summary = evidence.summary()
        for key, value in summary.items():
            print(f"    {key:18s}: {value}")
        check("structures found", bool(evidence.structures))
        check("references found", bool(evidence.references))
        check("target is designable", evidence.is_designable)
        check(
            "no sources degraded", not evidence.degradation.any, str(evidence.degradation.failures)
        )

        best = evidence.best_structure
        if best is not None:
            print(f"\n    best structure: {best.pdb_id} - {best.quality_note}")
            print(f"    cif url       : {best.cif_url}")
            check("CIF url points at RCSB", best.cif_url.startswith("https://files.rcsb.org/"))

        print("\n    literature:")
        for ref in evidence.references[:3]:
            print(f"      PMID {ref.pmid}: {ref.title[:58]}")
            print(f"        {ref.journal[:40]} ({ref.pubdate})")

        heading("3. Residue mapping — the correctness trap")
        rmap = await sources.residue_map(KPC2_PDB, KPC2_CHAIN)
        described = rmap.describe()
        for key, value in described.items():
            print(f"    {key:20s}: {value}")
        check("mapping length matches sequence", rmap.length == len(rmap.sequence))
        check(
            "numbering is non-contiguous, as expected for Ambler numbering",
            not rmap.is_contiguous,
            f"gaps={described['gaps']}",
        )
        check("author numbering starts at 30, not 1", rmap.author_range[0] == "30")

        print("\n    author number -> design index, with identity check:")
        for author, three_letter in KPC2_CATALYTIC.items():
            try:
                index = rmap.to_index(author)
                residue = rmap.sequence[index]
            except ResidueMappingError as exc:
                check(f"map residue {author}", False, str(exc))
                continue
            naive = author - int(rmap.author_range[0])
            naive_residue = rmap.sequence[naive] if naive < rmap.length else "?"
            ok = residue == three_letter[0].upper() or residue == {
                "Ser": "S",
                "Lys": "K",
                "Glu": "E",
                "Asn": "N",
                "Thr": "T",
            }.get(three_letter, "")
            print(
                f"      {three_letter}{author:<4} -> index {index:<4} residue {residue}"
                f"   (a naive offset would give {naive} -> {naive_residue})"
            )
            check(f"{three_letter}{author} maps to the right residue", ok, residue)

        heading("4. The mapping proves itself")
        try:
            rmap.verify_identities(KPC2_CATALYTIC)
            check("verify_identities passes for all catalytic residues", True)
        except ResidueMappingError as exc:
            check("verify_identities passes for all catalytic residues", False, str(exc))

        # And it must *fail* when the expectation is wrong, or it proves nothing.
        try:
            rmap.verify_identities({70: "Trp"})
            check("verify_identities rejects a wrong expectation", False, "it accepted Trp70")
        except ResidueMappingError:
            check("verify_identities rejects a wrong expectation", True)

        heading("5. Unmodelled residues are refused, not guessed")
        for absent in (1, 5, 999):
            try:
                rmap.to_index(absent)
                check(f"residue {absent} is refused", False, "it was mapped")
            except ResidueMappingError as exc:
                detail = str(exc).split(".")[0]
                check(f"residue {absent} is refused", True, detail[:60])

        heading("6. Caching and rate limiting hold up")
        import time

        start = time.monotonic()
        for _ in range(5):
            await sources.uniprot.get(KPC2_ACCESSION)
        elapsed = time.monotonic() - start
        print(f"    5 repeat lookups took {elapsed:.2f}s")
        check("repeat lookups are not rate-limited to a crawl", elapsed < 10.0, f"{elapsed:.2f}s")

        heading("RESULT")
        if _failures:
            print(f"{len(_failures)} check(s) failed:")
            for f in _failures:
                print(f"  - {f}")
            return 1
        print("All checks passed. Target discovery and residue mapping work end to end.")
        return 0
    finally:
        await sources.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

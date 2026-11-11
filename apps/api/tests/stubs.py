"""Controllable stand-ins for the external services.

Endpoint tests need to drive failure paths (a degraded source, an unmappable
residue) on demand, which recorded HTTP fixtures cannot express. The data these
return is still taken from real captures, so the shapes stay honest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from biostruct.sources.http import Degradation, SourceUnavailable
from biostruct.sources.pubmed import Reference
from biostruct.sources.rcsb import StructureRef
from biostruct.sources.residues import ResidueMap
from biostruct.sources.science import TargetEvidence, TargetSearchResult
from biostruct.sources.uniprot import ActiveSite, ProteinTarget

FIXTURES = Path(__file__).parent / "fixtures"


def _kpc2_chain_a() -> dict[str, Any]:
    return json.loads((FIXTURES / "rcsb" / "2ov5_chain_a.json").read_text())  # type: ignore[no-any-return]


def _uniprot_sequence() -> str:
    data = json.loads((FIXTURES / "uniprot" / "q9f663.json").read_text())
    return str(data["sequence"]["value"])


def kpc2_target() -> ProteinTarget:
    """KPC-2 as UniProt actually returns it."""
    return ProteinTarget(
        accession="Q9F663",
        entry_name="BLAK2_KLEPN",
        protein_name="Carbapenem-hydrolyzing beta-lactamase KPC-2",
        organism="Klebsiella pneumoniae",
        organism_id=573,
        length=293,
        sequence=_uniprot_sequence(),
        reviewed=True,
        function="Carbapenem-hydrolyzing beta-lactamase.",
        gene_names=("bla",),
        pdb_ids=("2OV5", "9F0T"),
        # UniProt numbering, which is NOT the author numbering.
        active_sites=(
            ActiveSite(position=69, description="Nucleophile; acyl-ester intermediate"),
            ActiveSite(position=72, description="Proton acceptor"),
            ActiveSite(position=129, description="Proton donor"),
            ActiveSite(position=165, description="Proton acceptor"),
        ),
    )


def kpc2_structures() -> list[StructureRef]:
    return [
        StructureRef(
            pdb_id="9F0T",
            title="KPC-2 at sub-angstrom resolution",
            method="X-RAY DIFFRACTION",
            resolution_a=0.98,
            released="2024-01-01",
        ),
        StructureRef(
            pdb_id="2OV5",
            title="Crystal structure of the KPC-2 carbapenemase",
            method="X-RAY DIFFRACTION",
            resolution_a=1.85,
            released="2007-05-01",
        ),
    ]


def kpc2_references() -> list[Reference]:
    return [
        Reference(
            pmid="18056283",
            title="Plasmid-mediated carbapenem-hydrolyzing beta-lactamase KPC-2",
            journal="Antimicrobial agents and chemotherapy",
            pubdate="2008 Feb",
            doi="10.1128/AAC.01123-07",
        )
    ]


def kpc2_residue_map() -> ResidueMap:
    data = _kpc2_chain_a()
    return ResidueMap.from_rcsb(
        pdb_id="2OV5",
        chain_id="A",
        sequence=data["sequence"],
        auth_to_entity_poly_seq_mapping=data["auth_to_entity_poly_seq_mapping"],
    )


class StubSources:
    """Quacks like :class:`ScienceSources`, with switches for failure modes."""

    def __init__(self) -> None:
        self.targets: list[ProteinTarget] = [kpc2_target()]
        self.structures: list[StructureRef] = kpc2_structures()
        self.references: list[Reference] = kpc2_references()
        self.degraded: dict[str, str] = {}
        self.residue_map_error: str | None = None
        self.search_calls: list[dict[str, Any]] = []
        self.evidence_calls: list[str] = []
        self.residue_map_calls: list[tuple[str, str]] = []

    async def aclose(self) -> None:
        return None

    async def search_targets(
        self,
        query: str,
        *,
        organism_id: int | None = None,
        reviewed_only: bool = True,
        limit: int = 25,
    ) -> TargetSearchResult:
        self.search_calls.append(
            {
                "query": query,
                "organism_id": organism_id,
                "reviewed_only": reviewed_only,
                "limit": limit,
            }
        )
        degradation = Degradation()
        for source, detail in self.degraded.items():
            degradation.record(source, detail)
        targets = [] if "uniprot" in self.degraded else list(self.targets)
        return TargetSearchResult(targets=targets, degradation=degradation)

    async def gather_evidence(
        self, accession: str, *, structure_limit: int = 10, reference_limit: int = 10
    ) -> TargetEvidence:
        self.evidence_calls.append(accession)
        if "uniprot" in self.degraded:
            raise SourceUnavailable("uniprot", self.degraded["uniprot"])
        target = next((t for t in self.targets if t.accession.upper() == accession.upper()), None)
        if target is None:
            raise SourceUnavailable("uniprot", f"{accession} not found (404)")
        degradation = Degradation()
        for source, detail in self.degraded.items():
            degradation.record(source, detail)
        return TargetEvidence(
            target=target,
            structures=[] if "rcsb" in self.degraded else list(self.structures),
            references=[] if "pubmed" in self.degraded else list(self.references),
            degradation=degradation,
        )

    async def residue_map(self, pdb_id: str, chain_id: str) -> ResidueMap:
        self.residue_map_calls.append((pdb_id, chain_id))
        if self.residue_map_error:
            raise SourceUnavailable("rcsb", self.residue_map_error)
        return kpc2_residue_map()

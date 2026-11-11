"""Controllable stand-ins for the external services.

Endpoint tests need to drive failure paths (a degraded source, an unmappable
residue) on demand, which recorded HTTP fixtures cannot express. The data these
return is still taken from real captures, so the shapes stay honest.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from biostruct.engines.base import (
    Artifact,
    CostEstimate,
    DesignEntity,
    DesignMetrics,
    DesignResult,
    DesignSpec,
    EngineIdentity,
    EngineJob,
    EngineUnavailable,
    JobStatus,
)
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


# --- design engine ---------------------------------------------------------


class FakeEngine:
    """A controllable DesignEngine.

    Exists so reconciler tests can drive the paths that matter and cannot be
    expressed with recorded HTTP: a run that fails mid-harvest, an artifact URL
    that has expired, a transport error that must not fail the run.
    """

    name = "fake"

    def __init__(self) -> None:
        self.status: JobStatus = JobStatus.succeeded
        self.livemode = False
        self.generated = 3
        self.total = 3
        self.error: str | None = None
        self.results: list[DesignResult] = [fake_result(i) for i in range(3)]
        # Raise after yielding this many results, to simulate a crash or outage.
        self.fail_after: int | None = None
        self.raise_on_get_job: Exception | None = None
        self.raise_on_results: Exception | None = None
        self.unit_cost = Decimal("0.05")
        self.get_job_calls = 0
        self.results_calls: list[str | None] = []
        self.started: list[tuple[DesignSpec, str]] = []
        self.stopped: list[str] = []

    async def estimate_cost(self, spec: DesignSpec) -> CostEstimate:
        return CostEstimate(
            total_usd=self.unit_cost * spec.num_proteins,
            unit_usd=self.unit_cost,
            units=spec.num_proteins,
            application="protein_design",
            disclaimer="estimate only",
        )

    async def start_design(self, spec: DesignSpec, *, idempotency_key: str) -> EngineJob:
        self.started.append((spec, idempotency_key))
        return EngineJob(
            job_id=f"job_{len(self.started)}",
            status=JobStatus.pending,
            livemode=self.livemode,
            total_to_generate=spec.num_proteins,
            generated=0,
            engine="fake",
        )

    async def get_job(self, job_id: str) -> EngineJob:
        self.get_job_calls += 1
        if self.raise_on_get_job is not None:
            raise self.raise_on_get_job
        return EngineJob(
            job_id=job_id,
            status=self.status,
            livemode=self.livemode,
            total_to_generate=self.total,
            generated=self.generated,
            error=self.error,
            engine="fake",
        )

    async def iter_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[DesignResult]:
        self.results_calls.append(after_id)
        if self.raise_on_results is not None:
            raise self.raise_on_results
        start = 0
        if after_id is not None:
            ids = [r.result_id for r in self.results]
            start = ids.index(after_id) + 1 if after_id in ids else 0
        for emitted, result in enumerate(self.results[start:]):
            if self.fail_after is not None and emitted >= self.fail_after:
                raise EngineUnavailable("connection lost mid-harvest")
            yield result

    async def stop_design(self, job_id: str) -> EngineJob:
        self.stopped.append(job_id)
        return EngineJob(job_id=job_id, status=JobStatus.stopped, livemode=self.livemode)

    async def resume_design(self, job_id: str) -> EngineJob:
        return EngineJob(job_id=job_id, status=JobStatus.running, livemode=self.livemode)

    async def delete_data(self, job_id: str) -> None:
        return None

    async def whoami(self) -> EngineIdentity:
        return EngineIdentity(
            key_type="workspace",
            mode="live" if self.livemode else "test",
            organization_name="Test Lab",
        )

    async def aclose(self) -> None:
        return None


def fake_result(index: int, *, with_structure: bool = True) -> DesignResult:
    """A design result shaped like the real ones (docs/boltz-api-observed.md)."""
    expires = datetime.now(UTC) + timedelta(minutes=30)
    return DesignResult(
        result_id=f"res_{index:03d}",
        entities=(
            DesignEntity(chain_ids=("A",), kind="protein", value="QVQLVESGGGLVQ" * 3),
            DesignEntity(chain_ids=("B",), kind="protein", value="AEPFAKLEQDFGG" * 3),
        ),
        metrics=DesignMetrics(
            structure_confidence=0.80 + index / 100,
            binding_confidence=0.70 + index / 100,
            iptm=0.60 + index / 100,
            # Deliberately *rising* with the index, so binding confidence and
            # interface error disagree. If they agreed, re-weighting could never
            # change the order and the re-ranking tests would prove nothing.
            min_interaction_pae=3.0 + index * 4,
            # Absent in practice, as observed on every live result.
            ipsae_min=None,
            helix_fraction=0.3,
            sheet_fraction=0.3,
            loop_fraction=0.4,
        ),
        structure=Artifact(url=f"https://example.org/s/{index}", expires_at=expires)
        if with_structure
        else None,
        archive=Artifact(url=f"https://example.org/a/{index}", expires_at=expires),
        warnings=(),
        created_at=datetime.now(UTC),
        raw={"id": f"res_{index:03d}", "captured": True},
    )

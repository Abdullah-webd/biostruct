"""The scientific-sources facade.

Fans out across UniProt, RCSB and PubMed concurrently, and — the important part
— reports which sources failed instead of quietly returning less.

An incomplete evidence panel shown as complete is the worst failure mode in this
layer: a researcher would conclude there is no literature on a target when in
fact PubMed was briefly unreachable. So every result carries a
:class:`Degradation` and the UI is expected to show it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.sources.http import CachedHTTPClient, Degradation, SourceUnavailable
from biostruct.sources.pubmed import PubMedClient, Reference
from biostruct.sources.rcsb import RCSBClient, StructureRef
from biostruct.sources.residues import ResidueMap
from biostruct.sources.uniprot import ProteinTarget, UniProtClient

log = logging.getLogger(__name__)


@dataclass
class TargetSearchResult:
    targets: list[ProteinTarget] = field(default_factory=list)
    degradation: Degradation = field(default_factory=Degradation)


@dataclass
class TargetEvidence:
    """Everything known about one candidate target, with gaps made explicit."""

    target: ProteinTarget
    structures: list[StructureRef] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    degradation: Degradation = field(default_factory=Degradation)

    @property
    def is_designable(self) -> bool:
        """Whether a structure-templated design run is possible at all."""
        return bool(self.structures)

    @property
    def best_structure(self) -> StructureRef | None:
        """Highest-resolution structure, which is the sensible default choice."""
        with_resolution = [s for s in self.structures if s.resolution_a is not None]
        if with_resolution:
            return min(with_resolution, key=lambda s: s.resolution_a or 0.0)
        return self.structures[0] if self.structures else None

    def summary(self) -> dict[str, Any]:
        return {
            "accession": self.target.accession,
            "protein_name": self.target.protein_name,
            "organism": self.target.organism,
            "evidence_level": self.target.evidence_level,
            "length": self.target.length,
            "structures": len(self.structures),
            "references": len(self.references),
            "designable": self.is_designable,
            "degraded_sources": self.degradation.sources,
        }


class ScienceSources:
    """One object the rest of the app (and the agent tools) talk to."""

    def __init__(
        self,
        *,
        db: AsyncIOMotorDatabase[dict[str, Any]] | None = None,
        ncbi_email: str = "",
        ncbi_api_key: str = "",
        uniprot: UniProtClient | None = None,
        rcsb: RCSBClient | None = None,
        pubmed: PubMedClient | None = None,
    ) -> None:
        self._clients: list[CachedHTTPClient] = []

        def make(source: str, rate: float, ttl: timedelta) -> CachedHTTPClient:
            client = CachedHTTPClient(source=source, db=db, rate_per_second=rate, ttl=ttl)
            self._clients.append(client)
            return client

        self.uniprot = uniprot or UniProtClient(make("uniprot", 5.0, timedelta(days=7)))
        self.rcsb = rcsb or RCSBClient(make("rcsb", 5.0, timedelta(days=7)))
        # 2.5/s against NCBI's published 3/s, leaving headroom for a second
        # instance before we would be refused.
        self.pubmed = pubmed or PubMedClient(
            make("pubmed", 2.5 if not ncbi_api_key else 8.0, timedelta(hours=24)),
            email=ncbi_email,
            api_key=ncbi_api_key,
        )

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()

    # --- step 2 of the workflow -------------------------------------------

    async def search_targets(
        self,
        query: str,
        *,
        organism_id: int | None = None,
        reviewed_only: bool = True,
        limit: int = 25,
    ) -> TargetSearchResult:
        degradation = Degradation()
        try:
            targets = await self.uniprot.search(
                query, organism_id=organism_id, reviewed_only=reviewed_only, limit=limit
            )
        except SourceUnavailable as exc:
            # UniProt is the spine of this step: without it there is nothing to
            # show, so this one does fail rather than degrade.
            degradation.record(exc.source, exc.detail)
            return TargetSearchResult(targets=[], degradation=degradation)
        return TargetSearchResult(targets=targets, degradation=degradation)

    async def gather_evidence(
        self,
        accession: str,
        *,
        structure_limit: int = 10,
        reference_limit: int = 10,
    ) -> TargetEvidence:
        """Everything about one target, fetched concurrently.

        PubMed and RCSB failures degrade; a UniProt failure is fatal for this
        call because the other two need the accession's details to query at all.
        """
        degradation = Degradation()
        target = await self.uniprot.get(accession)

        structures_task = asyncio.create_task(self._structures_for(target, limit=structure_limit))
        references_task = asyncio.create_task(self._references_for(target, limit=reference_limit))
        structures_outcome, references_outcome = await asyncio.gather(
            structures_task, references_task, return_exceptions=True
        )

        structures: list[StructureRef] = []
        references: list[Reference] = []

        if isinstance(structures_outcome, BaseException):
            detail = (
                structures_outcome.detail
                if isinstance(structures_outcome, SourceUnavailable)
                else str(structures_outcome)
            )
            degradation.record("rcsb", detail)
            log.warning("RCSB unavailable for %s: %s", accession, detail)
        else:
            structures = structures_outcome

        if isinstance(references_outcome, BaseException):
            detail = (
                references_outcome.detail
                if isinstance(references_outcome, SourceUnavailable)
                else str(references_outcome)
            )
            degradation.record("pubmed", detail)
            log.warning("PubMed unavailable for %s: %s", accession, detail)
        else:
            references = references_outcome

        return TargetEvidence(
            target=target,
            structures=structures,
            references=references,
            degradation=degradation,
        )

    async def _structures_for(self, target: ProteinTarget, *, limit: int) -> list[StructureRef]:
        pdb_ids = await self.rcsb.structures_for_uniprot(target.accession, limit=limit)
        if not pdb_ids:
            # Fall back to UniProt's own cross-references, which sometimes list
            # entries the search index has not caught up with.
            pdb_ids = list(target.pdb_ids[:limit])
        if not pdb_ids:
            return []
        fetched = await asyncio.gather(
            *(self.rcsb.get_structure(p) for p in pdb_ids), return_exceptions=True
        )
        structures = [s for s in fetched if isinstance(s, StructureRef)]
        failed = len(fetched) - len(structures)
        if failed:
            log.warning("%d of %d structure lookups failed", failed, len(fetched))
        return structures

    async def _references_for(self, target: ProteinTarget, *, limit: int) -> list[Reference]:
        return await self.pubmed.search_for_target(
            target.protein_name, target.organism, limit=limit
        )

    # --- step 3 of the workflow -------------------------------------------

    async def residue_map(self, pdb_id: str, chain_id: str) -> ResidueMap:
        """The author-number to design-index mapping for a chain.

        Not wrapped in degradation handling on purpose: if this is unavailable we
        must refuse to build a design spec, because guessing the mapping would
        aim the design at the wrong surface (see ``residues.py``).
        """
        return await self.rcsb.get_residue_map(pdb_id, chain_id)

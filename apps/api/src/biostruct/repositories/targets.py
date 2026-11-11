from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.models.target import (
    ActiveSiteOut,
    EpitopeOut,
    ReferenceOut,
    StructureRefOut,
    Target,
)
from biostruct.sources.science import TargetEvidence


class TargetRepository:
    """Targets belong to a project, and a project belongs to a user.

    Every query therefore carries the project id, and callers resolve the
    project against the current user first, so a target can never be read
    across tenants.
    """

    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    @staticmethod
    def _to_target(doc: dict[str, Any]) -> Target:
        return Target(
            id=doc["_id"],
            project_id=doc["project_id"],
            accession=doc["accession"],
            protein_name=doc["protein_name"],
            organism=doc.get("organism", ""),
            organism_id=doc.get("organism_id"),
            length=doc.get("length", 0),
            sequence=doc.get("sequence", ""),
            reviewed=doc.get("reviewed", False),
            function=doc.get("function", ""),
            gene_names=list(doc.get("gene_names") or []),
            active_sites=[ActiveSiteOut(**s) for s in doc.get("active_sites") or []],
            structures=[StructureRefOut(**s) for s in doc.get("structures") or []],
            references=[ReferenceOut(**r) for r in doc.get("references") or []],
            epitope=EpitopeOut(**doc["epitope"]) if doc.get("epitope") else None,
            degraded_sources=list(doc.get("degraded_sources") or []),
            evidence_fetched_at=doc["evidence_fetched_at"],
            created_at=doc["created_at"],
            schema_version=doc.get("schema_version", 1),
        )

    @staticmethod
    def _evidence_doc(evidence: TargetEvidence) -> dict[str, Any]:
        t = evidence.target
        return {
            "accession": t.accession,
            "protein_name": t.protein_name,
            "organism": t.organism,
            "organism_id": t.organism_id,
            "length": t.length,
            "sequence": t.sequence,
            "reviewed": t.reviewed,
            "function": t.function,
            "gene_names": list(t.gene_names),
            "active_sites": [
                {"position": s.position, "description": s.description} for s in t.active_sites
            ],
            "structures": [
                {
                    "pdb_id": s.pdb_id,
                    "title": s.title,
                    "method": s.method,
                    "resolution_a": s.resolution_a,
                    "released": s.released,
                    "cif_url": s.cif_url,
                    "quality_note": s.quality_note,
                }
                for s in evidence.structures
            ],
            "references": [
                {
                    "pmid": r.pmid,
                    "title": r.title,
                    "journal": r.journal,
                    "pubdate": r.pubdate,
                    "doi": r.doi,
                    "url": r.url,
                }
                for r in evidence.references
            ],
            # Recorded so a researcher can see the evidence panel was incomplete
            # when it was captured, rather than assuming absence of literature.
            "degraded_sources": evidence.degradation.sources,
            "evidence_fetched_at": datetime.now(UTC),
        }

    async def create(self, *, project_id: str, evidence: TargetEvidence) -> Target:
        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "project_id": project_id,
            "epitope": None,
            "created_at": datetime.now(UTC),
            "schema_version": 1,
            **self._evidence_doc(evidence),
        }
        await self.db.targets.insert_one(doc)
        return self._to_target(doc)

    async def list_for_project(self, project_id: str) -> list[Target]:
        cursor = self.db.targets.find({"project_id": project_id}).sort("created_at", -1)
        return [self._to_target(d) async for d in cursor]

    async def get(self, target_id: str, *, project_id: str) -> Target | None:
        doc = await self.db.targets.find_one({"_id": target_id, "project_id": project_id})
        return self._to_target(doc) if doc else None

    async def get_by_accession(self, accession: str, *, project_id: str) -> Target | None:
        doc = await self.db.targets.find_one(
            {"accession": accession.upper(), "project_id": project_id}
        )
        return self._to_target(doc) if doc else None

    async def set_epitope(
        self, target_id: str, *, project_id: str, epitope: EpitopeOut
    ) -> Target | None:
        doc = await self.db.targets.find_one_and_update(
            {"_id": target_id, "project_id": project_id},
            {"$set": {"epitope": epitope.model_dump(), "epitope_set_at": datetime.now(UTC)}},
            return_document=True,
        )
        return self._to_target(doc) if doc else None

    async def refresh_evidence(
        self, target_id: str, *, project_id: str, evidence: TargetEvidence
    ) -> Target | None:
        doc = await self.db.targets.find_one_and_update(
            {"_id": target_id, "project_id": project_id},
            {"$set": self._evidence_doc(evidence)},
            return_document=True,
        )
        return self._to_target(doc) if doc else None

    async def delete(self, target_id: str, *, project_id: str) -> bool:
        result = await self.db.targets.delete_one({"_id": target_id, "project_id": project_id})
        return result.deleted_count == 1

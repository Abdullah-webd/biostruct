from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from biostruct.db import money_from_db, money_to_db
from biostruct.models.run import (
    Candidate,
    CandidateMetrics,
    DesignRun,
    RunProgress,
    RunStatus,
)

# How long a worker may hold a run before another may take it over. Long enough
# that a slow harvest is not stolen mid-flight, short enough that a crashed
# worker's runs are picked up promptly.
LEASE_SECONDS = 300


class RunRepository:
    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    # --- mapping -----------------------------------------------------------

    @staticmethod
    def _to_run(doc: dict[str, Any]) -> DesignRun:
        progress = doc.get("progress") or {}
        actual = doc.get("cost_actual_usd")
        return DesignRun(
            id=doc["_id"],
            project_id=doc["project_id"],
            target_id=doc["target_id"],
            engine=doc.get("engine", ""),
            engine_job_id=doc.get("engine_job_id"),
            status=RunStatus(doc["status"]),
            livemode=doc.get("livemode", False),
            num_proteins=doc.get("num_proteins", 0),
            modality=doc.get("modality", "nanobody"),
            name=doc.get("name"),
            epitope_pdb_id=doc.get("epitope_pdb_id", ""),
            epitope_chain_id=doc.get("epitope_chain_id", ""),
            epitope_design_indices=list(doc.get("epitope_design_indices") or []),
            progress=RunProgress(
                total_to_generate=progress.get("total_to_generate"),
                generated=progress.get("generated", 0),
                harvested=progress.get("harvested", 0),
            ),
            cost_estimate_usd=money_from_db(doc.get("cost_estimate_usd", "0")),
            cost_actual_usd=money_from_db(actual) if actual is not None else None,
            error=doc.get("error"),
            harvest_cursor=doc.get("harvest_cursor"),
            created_at=doc["created_at"],
            updated_at=doc.get("updated_at", doc["created_at"]),
            started_at=doc.get("started_at"),
            completed_at=doc.get("completed_at"),
            schema_version=doc.get("schema_version", 1),
        )

    # --- creation ----------------------------------------------------------

    async def create(
        self,
        *,
        project_id: str,
        target_id: str,
        engine: str,
        idempotency_key: str,
        num_proteins: int,
        modality: str,
        name: str | None,
        epitope_pdb_id: str,
        epitope_chain_id: str,
        epitope_design_indices: list[int],
        cost_estimate_usd: Decimal,
    ) -> DesignRun | None:
        """Create a run in ``pending``. Returns None if the key is already used.

        The unique index on ``idempotency_key`` is what makes a retried
        submission safe: the second attempt collides rather than starting — and
        billing — a second run.
        """
        now = datetime.now(UTC)
        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "project_id": project_id,
            "target_id": target_id,
            "engine": engine,
            "engine_job_id": None,
            "idempotency_key": idempotency_key,
            "status": RunStatus.pending.value,
            "livemode": False,
            "num_proteins": num_proteins,
            "modality": modality,
            "name": name,
            "epitope_pdb_id": epitope_pdb_id,
            "epitope_chain_id": epitope_chain_id,
            "epitope_design_indices": epitope_design_indices,
            "progress": {"total_to_generate": num_proteins, "generated": 0, "harvested": 0},
            "cost_estimate_usd": money_to_db(cost_estimate_usd),
            "cost_actual_usd": None,
            "error": None,
            "harvest_cursor": None,
            "claimed_until": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "schema_version": 1,
        }
        try:
            await self.db.design_runs.insert_one(doc)
        except DuplicateKeyError:
            return None
        return self._to_run(doc)

    async def get_by_idempotency_key(self, key: str) -> DesignRun | None:
        doc = await self.db.design_runs.find_one({"idempotency_key": key})
        return self._to_run(doc) if doc else None

    # --- reads -------------------------------------------------------------

    async def get(self, run_id: str, *, project_id: str) -> DesignRun | None:
        doc = await self.db.design_runs.find_one({"_id": run_id, "project_id": project_id})
        return self._to_run(doc) if doc else None

    async def get_unscoped(self, run_id: str) -> DesignRun | None:
        """For the reconciler, which is not acting on behalf of a user."""
        doc = await self.db.design_runs.find_one({"_id": run_id})
        return self._to_run(doc) if doc else None

    async def list_for_project(self, project_id: str, *, limit: int = 50) -> list[DesignRun]:
        cursor = (
            self.db.design_runs.find({"project_id": project_id}).sort("created_at", -1).limit(limit)
        )
        return [self._to_run(d) async for d in cursor]

    async def count_recent_for_project(self, project_id: str, *, within_hours: int) -> int:
        since = datetime.now(UTC) - timedelta(hours=within_hours)
        return int(
            await self.db.design_runs.count_documents(
                {"project_id": project_id, "created_at": {"$gte": since}}
            )
        )

    # --- the reconciler's work list ---------------------------------------

    async def claim_due(self, *, limit: int = 20) -> list[DesignRun]:
        """Claim active runs that are due a poll.

        The run documents *are* the work list (ADR-003/004), so there is no
        queue to lose a message. ``claimed_until`` is a lease: it lets several
        workers run without two of them polling the same job, and a crashed
        worker's claim simply expires.
        """
        now = datetime.now(UTC)
        lease_until = now + timedelta(seconds=LEASE_SECONDS)
        claimed: list[DesignRun] = []
        for _ in range(limit):
            doc = await self.db.design_runs.find_one_and_update(
                {
                    "status": {
                        "$in": [
                            RunStatus.pending.value,
                            RunStatus.running.value,
                            RunStatus.harvesting.value,
                        ]
                    },
                    "$or": [
                        {"claimed_until": None},
                        {"claimed_until": {"$lte": now}},
                    ],
                    "$and": [{"$or": [{"next_poll_at": None}, {"next_poll_at": {"$lte": now}}]}],
                },
                {"$set": {"claimed_until": lease_until}},
                sort=[("updated_at", 1)],
                return_document=True,
            )
            if doc is None:
                break
            claimed.append(self._to_run(doc))
        return claimed

    async def release_claim(self, run_id: str, *, next_poll_in_seconds: float) -> None:
        await self.db.design_runs.update_one(
            {"_id": run_id},
            {
                "$set": {
                    "claimed_until": None,
                    "next_poll_at": datetime.now(UTC) + timedelta(seconds=next_poll_in_seconds),
                }
            },
        )

    async def oldest_active_age_seconds(self) -> float | None:
        """For the scan-lag metric that alerts at 30 minutes."""
        doc = await self.db.design_runs.find_one(
            {
                "status": {
                    "$in": [
                        RunStatus.pending.value,
                        RunStatus.running.value,
                        RunStatus.harvesting.value,
                    ]
                }
            },
            sort=[("updated_at", 1)],
            projection={"updated_at": 1},
        )
        if not doc:
            return None
        updated = doc["updated_at"]
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        return float((datetime.now(UTC) - updated).total_seconds())

    # --- writes ------------------------------------------------------------

    async def _set(self, run_id: str, fields: dict[str, Any]) -> DesignRun | None:
        fields["updated_at"] = datetime.now(UTC)
        doc = await self.db.design_runs.find_one_and_update(
            {"_id": run_id}, {"$set": fields}, return_document=True
        )
        return self._to_run(doc) if doc else None

    async def mark_submitted(
        self, run_id: str, *, engine_job_id: str, status: RunStatus, livemode: bool
    ) -> DesignRun | None:
        return await self._set(
            run_id,
            {
                "engine_job_id": engine_job_id,
                "status": status.value,
                "livemode": livemode,
                "started_at": datetime.now(UTC),
            },
        )

    async def mark_submit_failed(self, run_id: str, *, error: str) -> DesignRun | None:
        return await self._set(
            run_id,
            {
                "status": RunStatus.submit_failed.value,
                "error": error[:2000],
                "completed_at": datetime.now(UTC),
            },
        )

    async def update_progress(
        self, run_id: str, *, status: RunStatus, generated: int, total: int | None
    ) -> DesignRun | None:
        fields: dict[str, Any] = {
            "status": status.value,
            "progress.generated": generated,
        }
        if total is not None:
            fields["progress.total_to_generate"] = total
        return await self._set(run_id, fields)

    async def mark_terminal(
        self,
        run_id: str,
        *,
        status: RunStatus,
        error: str | None = None,
        cost_actual_usd: Decimal | None = None,
    ) -> DesignRun | None:
        fields: dict[str, Any] = {
            "status": status.value,
            "completed_at": datetime.now(UTC),
            "claimed_until": None,
        }
        if error is not None:
            fields["error"] = error[:2000]
        if cost_actual_usd is not None:
            fields["cost_actual_usd"] = money_to_db(cost_actual_usd)
        return await self._set(run_id, fields)

    async def advance_harvest(
        self, run_id: str, *, cursor: str, harvested: int
    ) -> DesignRun | None:
        """Record harvest progress so a crash resumes instead of restarting.

        Written after each page, which is what makes the harvest idempotent and
        resumable (ARCHITECTURE.md §6.3).
        """
        return await self._set(run_id, {"harvest_cursor": cursor, "progress.harvested": harvested})

    async def set_status(self, run_id: str, status: RunStatus) -> DesignRun | None:
        return await self._set(run_id, {"status": status.value})


class CandidateRepository:
    def __init__(self, db: AsyncIOMotorDatabase[dict[str, Any]]) -> None:
        self.db = db

    @staticmethod
    def _to_candidate(doc: dict[str, Any]) -> Candidate:
        metrics = doc.get("metrics") or {}
        return Candidate(
            id=doc["_id"],
            run_id=doc["run_id"],
            project_id=doc["project_id"],
            engine_result_id=doc["engine_result_id"],
            sequences=list(doc.get("sequences") or []),
            chain_ids=list(doc.get("chain_ids") or []),
            metrics=CandidateMetrics(**metrics),
            warnings=list(doc.get("warnings") or []),
            structure_key=doc.get("structure_key"),
            structure_bytes=doc.get("structure_bytes"),
            archive_key=doc.get("archive_key"),
            livemode=doc.get("livemode", False),
            created_at=doc["created_at"],
        )

    async def upsert(
        self,
        *,
        run_id: str,
        project_id: str,
        engine_result_id: str,
        sequences: list[str],
        chain_ids: list[str],
        metrics: dict[str, float | None],
        warnings: list[dict[str, str]],
        structure_key: str | None,
        structure_bytes: int | None,
        archive_key: str | None,
        livemode: bool,
        raw: dict[str, Any],
    ) -> None:
        """Idempotent by (run, engine result id).

        A resumed harvest may re-see a result it already wrote; upserting means
        that is harmless rather than a duplicate candidate.
        """
        await self.db.candidates.update_one(
            {"run_id": run_id, "engine_result_id": engine_result_id},
            {
                "$set": {
                    "project_id": project_id,
                    "sequences": sequences,
                    "chain_ids": chain_ids,
                    "metrics": {k: v for k, v in metrics.items() if v is not None},
                    "warnings": warnings,
                    "structure_key": structure_key,
                    "structure_bytes": structure_bytes,
                    "archive_key": archive_key,
                    "livemode": livemode,
                    # Verbatim payload: storage is cheap, re-running a paid job
                    # after a schema change is not (ADR-012).
                    "raw": raw,
                },
                "$setOnInsert": {
                    "_id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "engine_result_id": engine_result_id,
                    "created_at": datetime.now(UTC),
                },
            },
            upsert=True,
        )

    async def count_for_run(self, run_id: str) -> int:
        return int(await self.db.candidates.count_documents({"run_id": run_id}))

    async def list_for_run(
        self, run_id: str, *, limit: int = 100, skip: int = 0
    ) -> list[Candidate]:
        cursor = (
            self.db.candidates.find({"run_id": run_id})
            .sort("metrics.binding_confidence", -1)
            .skip(skip)
            .limit(limit)
        )
        return [self._to_candidate(d) async for d in cursor]

    async def get(self, candidate_id: str, *, project_id: str) -> Candidate | None:
        doc = await self.db.candidates.find_one({"_id": candidate_id, "project_id": project_id})
        return self._to_candidate(doc) if doc else None

    async def list_for_project(self, project_id: str) -> list[Candidate]:
        cursor = self.db.candidates.find({"project_id": project_id})
        return [self._to_candidate(d) async for d in cursor]

    async def delete_for_run(self, run_id: str) -> int:
        result = await self.db.candidates.delete_many({"run_id": run_id})
        return int(result.deleted_count)

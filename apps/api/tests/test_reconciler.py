"""Reconciler behaviour, including the failure paths that matter most.

Three properties are load-bearing and each has tests here:

* a transport failure **never** fails a run — only the engine may declare that
* a crash mid-harvest **resumes** from its cursor, without duplicating
* a run whose structures could not be mirrored is marked **partial**, not
  complete, because the presigned URLs may already have expired
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.budget import BudgetService
from biostruct.engines.base import EngineRejected, EngineUnavailable, JobStatus
from biostruct.jobs.reconciler import Reconciler
from biostruct.models.run import RunStatus
from biostruct.repositories.runs import CandidateRepository, RunRepository
from biostruct.storage import LocalDiskStorage

from .stubs import FakeEngine, fake_result

PROJECT = "proj-1"
TARGET = "target-1"


def artifact_handler(*, fail_urls: set[str] | None = None) -> Any:
    """Serves artifact downloads, optionally failing specific URLs."""
    failures = fail_urls or set()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in failures:
            # What an expired presigned URL actually looks like.
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
        return httpx.Response(200, text="data_biostruct\n_atom_site.id\n1\n")

    return handler


class Harness:
    def __init__(
        self,
        db: AsyncIOMotorDatabase[dict[str, Any]],
        tmp_path: Any,
        *,
        fail_urls: set[str] | None = None,
    ) -> None:
        self.db = db
        self.engine = FakeEngine()
        self.storage = LocalDiskStorage(tmp_path)
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(artifact_handler(fail_urls=fail_urls))
        )
        self.reconciler = Reconciler(
            db=db, engine=self.engine, storage=self.storage, http_client=self.http
        )
        self.runs = RunRepository(db)
        self.candidates = CandidateRepository(db)
        self.budget = BudgetService(db)

    async def seed_project(self, limit: str = "25", reserved: str = "5") -> None:
        await self.db.projects.insert_one(
            {
                "_id": PROJECT,
                "budget": {
                    "limit_usd": limit,
                    "spent_usd": "0",
                    "reserved_usd": reserved,
                },
            }
        )

    async def seed_run(
        self,
        *,
        status: RunStatus = RunStatus.running,
        engine_job_id: str | None = "job_1",
        cost: str = "5",
        livemode: bool = False,
        harvest_cursor: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        # Unique per call: several runs are seeded in a single test.
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        await self.db.design_runs.insert_one(
            {
                "_id": run_id,
                "project_id": PROJECT,
                "target_id": TARGET,
                "engine": "fake",
                "engine_job_id": engine_job_id,
                "idempotency_key": f"idem-{run_id}",
                "status": status.value,
                "livemode": livemode,
                "num_proteins": 3,
                "modality": "nanobody",
                "name": None,
                "epitope_pdb_id": "2OV5",
                "epitope_chain_id": "A",
                "epitope_design_indices": [39, 42],
                "progress": {"total_to_generate": 3, "generated": 0, "harvested": 0},
                "cost_estimate_usd": cost,
                "cost_actual_usd": None,
                "error": None,
                "harvest_cursor": harvest_cursor,
                "claimed_until": None,
                "next_poll_at": None,
                "created_at": now,
                "updated_at": now,
                "started_at": now,
                "completed_at": None,
                "schema_version": 1,
            }
        )
        return run_id

    async def close(self) -> None:
        await self.http.aclose()


@pytest.fixture
async def harness(client, settings, tmp_path):
    from biostruct.db import database

    db = database.db
    assert db is not None
    h = Harness(db, tmp_path)
    await h.seed_project()
    try:
        yield h
    finally:
        await h.close()


class TestSuccessfulHarvest:
    async def test_succeeded_run_is_harvested_and_completed(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        harness.engine.status = JobStatus.succeeded

        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.complete
        assert final.progress.harvested == 3
        assert await harness.candidates.count_for_run(run_id) == 3

    async def test_every_structure_is_mirrored_into_our_storage(self, harness: Harness) -> None:
        """Not a Boltz URL: those expire in about 30 minutes."""
        run_id = await harness.seed_run()
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        candidates = await harness.candidates.list_for_run(run_id)
        assert len(candidates) == 3
        for candidate in candidates:
            assert candidate.structure_key is not None
            assert candidate.structure_key.startswith(f"runs/{run_id}/structure/")
            assert await harness.storage.exists(candidate.structure_key)
            body = await harness.storage.get_bytes(candidate.structure_key)
            assert body.startswith(b"data_")

    async def test_sequences_and_metrics_are_stored(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        candidate = (await harness.candidates.list_for_run(run_id))[0]
        assert len(candidate.sequences) == 2
        assert candidate.metrics.binding_confidence is not None
        assert candidate.metrics.is_binder is True
        # Absent in practice, and must stay absent rather than becoming 0.
        assert candidate.metrics.ipsae_min is None

    async def test_raw_payload_is_kept(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        doc = await harness.db.candidates.find_one({"run_id": run_id})
        assert doc is not None
        assert doc["raw"]["captured"] is True

    async def test_test_mode_runs_are_flagged_on_candidates(self, harness: Harness) -> None:
        """So synthetic results can never be exported as findings (ADR-024)."""
        run_id = await harness.seed_run(livemode=False)
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        assert all(not c.livemode for c in await harness.candidates.list_for_run(run_id))

        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.is_exportable is False


class TestCrashResume:
    async def test_harvest_resumes_from_its_cursor(self, harness: Harness) -> None:
        """The property that makes a worker restart cost nothing."""
        run_id = await harness.seed_run()
        harness.engine.fail_after = 2  # dies after two results

        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        mid = await harness.runs.get_unscoped(run_id)
        assert mid is not None
        assert mid.status is RunStatus.harvesting, "must not be marked complete"
        assert mid.harvest_cursor == "res_001"
        assert await harness.candidates.count_for_run(run_id) == 2

        # Recover and run again: it picks up after the cursor.
        harness.engine.fail_after = None
        await harness.reconciler.process(mid)

        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.complete
        assert await harness.candidates.count_for_run(run_id) == 3
        assert harness.engine.results_calls[-1] == "res_001"

    async def test_resume_does_not_duplicate_candidates(self, harness: Harness) -> None:
        """Candidates are upserted by engine result id, so re-seeing one is safe."""
        run_id = await harness.seed_run()
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        assert await harness.candidates.count_for_run(run_id) == 3

        # Replay the whole harvest from the beginning.
        harvested_again = await harness.runs.get_unscoped(run_id)
        assert harvested_again is not None
        await harness.reconciler._harvest(
            harvested_again.model_copy(update={"harvest_cursor": None})
        )
        assert await harness.candidates.count_for_run(run_id) == 3

    async def test_transport_failure_never_fails_the_run(self, harness: Harness) -> None:
        """Only the engine may declare a run failed."""
        run_id = await harness.seed_run()
        harness.engine.raise_on_get_job = EngineUnavailable("connection reset")

        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        after = await harness.runs.get_unscoped(run_id)
        assert after is not None
        assert after.status is RunStatus.running, "a 5xx must not fail the run"
        assert after.error is None

        # And the budget reservation is untouched.
        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.reserved_usd == Decimal("5")

    async def test_claim_is_released_so_the_run_is_retried(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        harness.engine.raise_on_get_job = EngineUnavailable("down")
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        doc = await harness.db.design_runs.find_one({"_id": run_id})
        assert doc is not None
        assert doc["claimed_until"] is None
        assert doc["next_poll_at"] is not None


class TestMirrorFailures:
    async def test_unmirrored_structure_marks_the_run_partial(
        self, client, settings, tmp_path
    ) -> None:
        """Not complete: the presigned URLs may already have expired."""
        from biostruct.db import database

        db = database.db
        assert db is not None
        h = Harness(db, tmp_path, fail_urls={"https://example.org/s/1"})
        await h.seed_project()
        run_id = await h.seed_run()
        try:
            run = await h.runs.get_unscoped(run_id)
            assert run is not None
            await h.reconciler.process(run)

            final = await h.runs.get_unscoped(run_id)
            assert final is not None
            assert final.status is RunStatus.harvest_partial
            assert final.error is not None
            assert "30 minutes" in final.error
        finally:
            await h.close()

    async def test_candidate_is_still_written_without_its_structure(
        self, client, settings, tmp_path
    ) -> None:
        """The sequence and metrics are worth keeping even if the file is lost."""
        from biostruct.db import database

        db = database.db
        assert db is not None
        h = Harness(db, tmp_path, fail_urls={"https://example.org/s/1"})
        await h.seed_project()
        run_id = await h.seed_run()
        try:
            run = await h.runs.get_unscoped(run_id)
            assert run is not None
            await h.reconciler.process(run)
            candidates = await h.candidates.list_for_run(run_id)
            assert len(candidates) == 3
            missing = [c for c in candidates if c.structure_key is None]
            assert len(missing) == 1
            assert missing[0].sequences, "sequence must survive a mirror failure"
        finally:
            await h.close()

    async def test_result_without_a_structure_is_not_a_failure(self, harness: Harness) -> None:
        harness.engine.results = [fake_result(0, with_structure=False)]
        harness.engine.generated = 1
        harness.engine.total = 1
        run_id = await harness.seed_run()
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.complete


class TestTerminalStates:
    async def test_engine_failure_releases_the_budget(self, harness: Harness) -> None:
        """Charging for a run that produced nothing would be wrong and invisible."""
        run_id = await harness.seed_run()
        harness.engine.status = JobStatus.failed
        harness.engine.error = "target chain not found"

        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.failed
        assert final.error is not None and "target chain" in final.error
        assert final.cost_actual_usd == Decimal("0")

        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.reserved_usd == Decimal("0")
        assert budget.spent_usd == Decimal("0")
        assert budget.remaining_usd == Decimal("25")

    async def test_stopped_run_releases_the_budget(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        harness.engine.status = JobStatus.stopped
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.stopped
        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.reserved_usd == Decimal("0")

    async def test_engine_rejection_fails_the_run(self, harness: Harness) -> None:
        """A 4xx will not get better by retrying."""
        run_id = await harness.seed_run()
        harness.engine.raise_on_get_job = EngineRejected("no such job", status_code=404)
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.failed

    async def test_test_mode_run_settles_at_zero_cost(self, harness: Harness) -> None:
        """Test-mode runs are unbilled, so they must not consume budget."""
        run_id = await harness.seed_run(livemode=False)
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.spent_usd == Decimal("0")
        assert budget.reserved_usd == Decimal("0")

    async def test_live_run_records_the_spend(self, harness: Harness) -> None:
        run_id = await harness.seed_run(livemode=True)
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.spent_usd == Decimal("5")
        assert budget.reserved_usd == Decimal("0")

    async def test_unsubmitted_run_is_failed_and_refunded(self, harness: Harness) -> None:
        run_id = await harness.seed_run(status=RunStatus.pending, engine_job_id=None)
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)
        final = await harness.runs.get_unscoped(run_id)
        assert final is not None
        assert final.status is RunStatus.submit_failed
        assert "no credits were used" in (final.error or "")
        budget = await harness.budget.get(PROJECT)
        assert budget is not None
        assert budget.reserved_usd == Decimal("0")


class TestProgressTracking:
    async def test_running_job_records_progress_without_completing(self, harness: Harness) -> None:
        run_id = await harness.seed_run()
        harness.engine.status = JobStatus.running
        harness.engine.generated = 2
        run = await harness.runs.get_unscoped(run_id)
        assert run is not None
        await harness.reconciler.process(run)

        after = await harness.runs.get_unscoped(run_id)
        assert after is not None
        assert after.status is RunStatus.running
        assert after.progress.generated == 2
        assert after.progress.percent == 67
        assert await harness.candidates.count_for_run(run_id) == 0


class TestWorkList:
    """The run documents are the work list; there is no queue (ADR-003)."""

    async def test_claim_due_picks_up_active_runs(self, harness: Harness) -> None:
        await harness.seed_run(status=RunStatus.running)
        await harness.seed_run(status=RunStatus.pending, engine_job_id="job_2")
        claimed = await harness.runs.claim_due()
        assert len(claimed) == 2

    async def test_terminal_runs_are_not_claimed(self, harness: Harness) -> None:
        await harness.seed_run(status=RunStatus.complete)
        await harness.seed_run(status=RunStatus.failed, engine_job_id="job_2")
        assert await harness.runs.claim_due() == []

    async def test_a_claimed_run_is_not_claimed_again(self, harness: Harness) -> None:
        """The lease is what lets several workers run without colliding."""
        await harness.seed_run(status=RunStatus.running)
        first = await harness.runs.claim_due()
        assert len(first) == 1
        assert await harness.runs.claim_due() == []

    async def test_an_expired_claim_is_picked_up(self, harness: Harness) -> None:
        """A crashed worker's runs must not be stranded."""
        run_id = await harness.seed_run(status=RunStatus.running)
        await harness.db.design_runs.update_one(
            {"_id": run_id},
            {"$set": {"claimed_until": datetime.now(UTC) - timedelta(minutes=10)}},
        )
        claimed = await harness.runs.claim_due()
        assert [r.id for r in claimed] == [run_id]

    async def test_a_run_waiting_for_its_next_poll_is_skipped(self, harness: Harness) -> None:
        run_id = await harness.seed_run(status=RunStatus.running)
        await harness.db.design_runs.update_one(
            {"_id": run_id},
            {"$set": {"next_poll_at": datetime.now(UTC) + timedelta(minutes=5)}},
        )
        assert await harness.runs.claim_due() == []

    async def test_tick_processes_everything_due(self, harness: Harness) -> None:
        await harness.seed_run(status=RunStatus.running)
        handled = await harness.reconciler.tick()
        assert handled == 1

    async def test_tick_survives_a_failing_run(self, harness: Harness) -> None:
        """One bad run must not stop the loop."""
        await harness.seed_run(status=RunStatus.running)
        harness.engine.raise_on_get_job = RuntimeError("unexpected")
        assert await harness.reconciler.tick() == 1

    async def test_scan_lag_is_measurable(self, harness: Harness) -> None:
        """Backs the alert that fires at 30 minutes."""
        assert await harness.runs.oldest_active_age_seconds() is None
        await harness.seed_run(status=RunStatus.running)
        age = await harness.runs.oldest_active_age_seconds()
        assert age is not None and age >= 0

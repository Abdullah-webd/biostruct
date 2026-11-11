"""The reconciler: the only component that polls a running design job.

Shape of it (ADR-003, ADR-004): the long-running work lives inside Boltz, not
inside us. So there is no queue and no broker — a scan over the ``design_runs``
collection *is* the work list, which is strictly more robust than a queue
because a dropped message would leave a run un-polled forever while a scan
retries by construction.

The harvest step is the dangerous one. Boltz artifact URLs expire in about
**30 minutes**, so mirroring them is urgent work, not background work: a
harvest that stalls loses structures that have already been paid for. Harvest
therefore records a cursor after every page, so a crash resumes instead of
restarting, and writes candidates idempotently so a resumed page cannot
duplicate them.

Transport failures never fail a run. Only Boltz may declare a run failed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.budget import BudgetService
from biostruct.engines.base import (
    DesignEngine,
    DesignResult,
    EngineRejected,
    EngineUnavailable,
    JobStatus,
)
from biostruct.models.run import DesignRun, RunStatus
from biostruct.repositories.runs import CandidateRepository, RunRepository
from biostruct.storage import StorageBackend, StorageError, artifact_key, mirror_url

log = logging.getLogger(__name__)

# Backoff ladder for polling one run: attentive at first, then relaxed.
POLL_FIRST_MINUTE = 10.0
POLL_EARLY = 30.0
POLL_LATE = 60.0
SCAN_INTERVAL = 10.0

# A run that has not changed status in this long is flagged for a human.
STALE_AFTER_SECONDS = 24 * 60 * 60


@dataclass
class HarvestOutcome:
    harvested: int = 0
    mirrored: int = 0
    mirror_failures: int = 0
    complete: bool = False


class Reconciler:
    """Polls active runs, harvests finished ones, mirrors their artifacts."""

    def __init__(
        self,
        *,
        db: AsyncIOMotorDatabase[dict[str, Any]],
        engine: DesignEngine,
        storage: StorageBackend,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.runs = RunRepository(db)
        self.candidates = CandidateRepository(db)
        self.budget = BudgetService(db)
        self.engine = engine
        self.storage = storage
        self._http = http_client
        self._owns_http = http_client is None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self._loop(), name="reconciler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    async def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=120.0, follow_redirects=True)
        return self._http

    async def _loop(self) -> None:
        log.info("reconciler started")
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The loop must survive anything; the next tick re-reads state.
                log.exception("reconciler tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=SCAN_INTERVAL)
        log.info("reconciler stopped")

    # --- one pass ----------------------------------------------------------

    async def tick(self) -> int:
        """Process every run currently due. Returns how many were handled."""
        claimed = await self.runs.claim_due()
        for run in claimed:
            try:
                await self.process(run)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("failed to process run %s", run.id)
                await self.runs.release_claim(run.id, next_poll_in_seconds=POLL_LATE)
        return len(claimed)

    def _next_poll_delay(self, run: DesignRun) -> float:
        started = run.started_at or run.created_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - started).total_seconds()
        if age < 60:
            return POLL_FIRST_MINUTE
        if age < 600:
            return POLL_EARLY
        return POLL_LATE

    async def process(self, run: DesignRun) -> None:
        """Advance one run by one step."""
        if run.status is RunStatus.pending and not run.engine_job_id:
            # Submission is the API's job, not ours. A pending run with no
            # engine job means submission never completed, so there is nothing
            # to poll and nothing to harvest.
            await self._fail_unsubmitted(run)
            return

        if run.status is RunStatus.harvesting:
            await self._harvest(run)
            return

        if not run.engine_job_id:
            await self.runs.release_claim(run.id, next_poll_in_seconds=POLL_LATE)
            return

        try:
            job = await self.engine.get_job(run.engine_job_id)
        except EngineUnavailable as exc:
            # Transport or 5xx: back off, and crucially do NOT fail the run.
            log.warning("engine unavailable for run %s: %s", run.id, exc)
            await self.runs.release_claim(run.id, next_poll_in_seconds=POLL_LATE)
            return
        except EngineRejected as exc:
            # The engine refuses to talk about this job at all, which does not
            # get better by retrying.
            log.error("engine rejected run %s: %s", run.id, exc)
            await self._finish_without_results(
                run, status=RunStatus.failed, error=f"engine rejected the job: {exc}"
            )
            return

        if job.status is JobStatus.succeeded:
            await self.runs.update_progress(
                run.id,
                status=RunStatus.harvesting,
                generated=job.generated,
                total=job.total_to_generate,
            )
            refreshed = await self.runs.get_unscoped(run.id)
            await self._harvest(refreshed or run)
            return

        if job.status is JobStatus.failed:
            await self._finish_without_results(
                run, status=RunStatus.failed, error=job.error or "the engine reported a failure"
            )
            return

        if job.status is JobStatus.stopped:
            await self._finish_without_results(run, status=RunStatus.stopped)
            return

        # Still pending or running: record progress and come back later.
        await self.runs.update_progress(
            run.id,
            status=RunStatus.running if job.status is JobStatus.running else RunStatus.pending,
            generated=job.generated,
            total=job.total_to_generate,
        )
        await self.runs.release_claim(run.id, next_poll_in_seconds=self._next_poll_delay(run))

    # --- harvest -----------------------------------------------------------

    async def _harvest(self, run: DesignRun) -> None:
        """Page results, mirror artifacts, write candidates.

        Mirroring happens *before* the candidate is considered done, and the
        cursor advances only after a page is fully written, so a crash resumes
        at the last complete page.
        """
        if not run.engine_job_id:
            return
        outcome = HarvestOutcome(harvested=run.progress.harvested)
        cursor = run.harvest_cursor
        http = await self._http_client()

        try:
            async for result in self.engine.iter_results(run.engine_job_id, after_id=cursor):
                mirrored_ok = await self._store_candidate(run, result, http)
                outcome.harvested += 1
                if mirrored_ok:
                    outcome.mirrored += 1
                else:
                    outcome.mirror_failures += 1
                cursor = result.result_id
                await self.runs.advance_harvest(run.id, cursor=cursor, harvested=outcome.harvested)
        except EngineUnavailable as exc:
            # Resume from the cursor on the next pass. Nothing is lost, though
            # the clock is running on the remaining presigned URLs.
            log.warning("harvest interrupted for run %s: %s", run.id, exc)
            await self.runs.release_claim(run.id, next_poll_in_seconds=POLL_EARLY)
            return
        except EngineRejected as exc:
            log.error("harvest rejected for run %s: %s", run.id, exc)
            await self._finish_without_results(
                run, status=RunStatus.failed, error=f"could not read results: {exc}"
            )
            return

        outcome.complete = True
        total = await self.candidates.count_for_run(run.id)

        if outcome.mirror_failures:
            # Deliberately not "complete": some structures are missing and the
            # presigned URLs may already have expired, so the UI must say so
            # rather than present a partial run as finished.
            log.error(
                "run %s harvested %d results but %d artifacts failed to mirror",
                run.id,
                outcome.harvested,
                outcome.mirror_failures,
            )
            await self._settle_cost(run)
            await self.runs.mark_terminal(
                run.id,
                status=RunStatus.harvest_partial,
                error=(
                    f"{outcome.mirror_failures} of {outcome.harvested} structures could "
                    "not be copied to storage. Boltz artifact links expire about 30 "
                    "minutes after a run finishes, so some may no longer be retrievable."
                ),
            )
            return

        await self._settle_cost(run)
        await self.runs.mark_terminal(run.id, status=RunStatus.complete)
        log.info(
            "run %s complete: %d candidates, %d artifacts mirrored",
            run.id,
            total,
            outcome.mirrored,
        )

    async def _store_candidate(
        self, run: DesignRun, result: DesignResult, http: httpx.AsyncClient
    ) -> bool:
        """Mirror a result's artifacts and write its candidate document.

        Returns False when the structure could not be mirrored. The candidate is
        still written, so its sequence and metrics are not lost, but the run
        will be marked partial.
        """
        structure_key: str | None = None
        structure_bytes: int | None = None
        archive_key: str | None = None
        mirrored = True

        if result.structure is not None:
            key = artifact_key(run.id, result.result_id, "structure", "cif")
            try:
                stored = await mirror_url(
                    storage=self.storage,
                    url=result.structure.url,
                    key=key,
                    content_type="chemical/x-cif",
                    client=http,
                )
                structure_key = stored.key
                structure_bytes = stored.size_bytes
            except StorageError as exc:
                log.error(
                    "could not mirror structure for %s/%s: %s",
                    run.id,
                    result.result_id,
                    exc,
                )
                mirrored = False

        if result.archive is not None:
            key = artifact_key(run.id, result.result_id, "archive", "zip")
            try:
                stored = await mirror_url(
                    storage=self.storage,
                    url=result.archive.url,
                    key=key,
                    content_type="application/zip",
                    client=http,
                )
                archive_key = stored.key
            except StorageError as exc:
                # An archive is a convenience; its loss does not make the run
                # partial the way a missing structure does.
                log.warning("could not mirror archive for %s/%s: %s", run.id, result.result_id, exc)

        metrics = result.metrics
        await self.candidates.upsert(
            run_id=run.id,
            project_id=run.project_id,
            engine_result_id=result.result_id,
            sequences=[e.value for e in result.protein_entities],
            chain_ids=[c for e in result.protein_entities for c in e.chain_ids],
            # Chain roles, determined rather than guessed. A live result's
            # entities contain only the designed binder, and the target keeps
            # the chain id we submitted, so anything that is not that chain is
            # designed. Verified on a live KPC-2 run: binder on NANO1, target
            # on A (docs/boltz-api-observed.md).
            binder_chain_ids=[
                c for e in result.protein_entities for c in e.chain_ids if c != run.epitope_chain_id
            ],
            target_chain_ids=[run.epitope_chain_id] if run.epitope_chain_id else [],
            metrics={
                "structure_confidence": metrics.structure_confidence,
                "binding_confidence": metrics.binding_confidence,
                "iptm": metrics.iptm,
                "min_interaction_pae": metrics.min_interaction_pae,
                "ipsae_min": metrics.ipsae_min,
                "helix_fraction": metrics.helix_fraction,
                "sheet_fraction": metrics.sheet_fraction,
                "loop_fraction": metrics.loop_fraction,
            },
            warnings=[{"code": w.code, "message": w.message} for w in result.warnings],
            structure_key=structure_key,
            structure_bytes=structure_bytes,
            archive_key=archive_key,
            livemode=run.livemode,
            raw=result.raw,
        )
        return mirrored

    # --- cost and failure --------------------------------------------------

    async def _settle_cost(self, run: DesignRun) -> None:
        """Release the reservation and record the actual spend.

        We do not have a per-run invoice from the engine, so the estimate is
        recorded as the actual. Documented rather than silently assumed: the
        engine bills on exact token counts, so this can differ.
        """
        await self.budget.settle(
            run.project_id,
            reserved_usd=run.cost_estimate_usd,
            actual_usd=run.cost_estimate_usd if run.livemode else Decimal("0"),
        )

    async def _finish_without_results(
        self, run: DesignRun, *, status: RunStatus, error: str | None = None
    ) -> None:
        """End a run that produced nothing, releasing its budget reservation.

        Charging for a failed run would be both wrong and invisible.
        """
        await self.budget.release(run.project_id, run.cost_estimate_usd)
        await self.runs.mark_terminal(
            run.id, status=status, error=error, cost_actual_usd=Decimal("0")
        )

    async def _fail_unsubmitted(self, run: DesignRun) -> None:
        await self.budget.release(run.project_id, run.cost_estimate_usd)
        await self.runs.mark_submit_failed(
            run.id,
            error=(
                "the run was created but never submitted to the engine, so nothing "
                "was started and no credits were used"
            ),
        )

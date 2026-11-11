"""Design runs: estimate, confirm, submit, watch, harvest results.

The spending path is deliberately awkward. A run can only be submitted with the
cost the researcher actually saw, that cost is re-checked against a fresh
estimate, the project budget is reserved atomically before submission, and the
number of designs is capped well below what the engine would accept. No AI agent
can reach any of this: submission is a human action (ARCHITECTURE.md §7).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from biostruct import errors
from biostruct.budget import BudgetExceeded, BudgetService
from biostruct.deps import (
    CsrfDep,
    CurrentUser,
    DbDep,
    EngineDep,
    ProjectsDep,
    RunsDep,
    SettingsDep,
    TargetsDep,
)
from biostruct.engines.base import (
    CuratedBinder,
    DesignSpec,
    EngineRejected,
    EngineUnavailable,
    StructureTarget,
    TargetChain,
)
from biostruct.engines.base import (
    DesignRules as EngineRules,
)
from biostruct.models.run import (
    CandidateList,
    DesignRun,
    EstimateResponse,
    MetricContributionOut,
    RankedCandidateList,
    RankedCandidateOut,
    RunCreate,
    RunRequest,
    RunStatus,
)
from biostruct.models.target import Target
from biostruct.repositories import ProjectRepository, TargetRepository

log = logging.getLogger(__name__)
router = APIRouter(tags=["runs"], dependencies=[CsrfDep])

# A human cannot meaningfully start more than this, and it bounds the blast
# radius of a loop bug (ARCHITECTURE.md §9).
MAX_RUNS_PER_HOUR = 5

# How long a cost estimate stays valid for submission.
ESTIMATE_TOLERANCE = Decimal("0.0001")


class RunList(BaseModel):
    items: list[DesignRun]


async def _load_target_and_project(
    projects: ProjectRepository,
    targets: TargetRepository,
    project_id: str,
    target_id: str,
    user_id: str,
) -> Target:
    if await projects.get(project_id, user_id=user_id) is None:
        raise errors.not_found("Project")
    target = await targets.get(target_id, project_id=project_id)
    if target is None:
        raise errors.not_found("Target")
    if target.epitope is None:
        raise errors.ProblemError(
            code="epitope_required",
            title="Choose an epitope first",
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This target has no epitope selected. Set one with "
                "PUT /projects/{project_id}/targets/{target_id}/epitope so the design "
                "run knows which surface to aim at."
            ),
        )
    return target


def _build_spec(target: Target, request: RunRequest) -> DesignSpec:
    epitope = target.epitope
    assert epitope is not None  # guaranteed by _load_target_and_project
    rules = EngineRules(
        excluded_amino_acids=tuple(request.rules.excluded_amino_acids),
        excluded_sequence_motifs=tuple(request.rules.excluded_sequence_motifs),
        max_hydrophobic_fraction=request.rules.max_hydrophobic_fraction,
    )
    return DesignSpec(
        target=StructureTarget(
            # The RCSB URL goes straight to the engine: verified that it fetches
            # it itself, so no file passes through us.
            structure_url=epitope.cif_url,
            chains=(
                TargetChain(
                    chain_id=epitope.chain_id,
                    crop_residues="all",
                    # Already converted from author numbering and verified.
                    epitope_residues=tuple(epitope.design_indices),
                ),
            ),
        ),
        binder=CuratedBinder(kind=request.modality, rules=rules),  # type: ignore[arg-type]
        num_proteins=request.num_proteins,
        name=request.name,
    )


# --- estimate --------------------------------------------------------------


@router.post("/projects/{project_id}/runs/estimate")
async def estimate_run(
    project_id: str,
    body: RunRequest,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
    engine: EngineDep,
    db: DbDep,
    settings: SettingsDep,
) -> EstimateResponse:
    """What this run would cost. Free to call, and required before submitting."""
    target = await _load_target_and_project(projects, targets, project_id, body.target_id, user.id)
    try:
        estimate = await engine.estimate_cost(_build_spec(target, body))
    except EngineUnavailable as exc:
        raise errors.engine_unavailable(str(exc)) from exc
    except EngineRejected as exc:
        raise errors.ProblemError(
            code="spec_rejected",
            title="The design engine rejected this request",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    budget = await BudgetService(db).get(project_id)
    if budget is None:
        raise errors.not_found("Project")
    return EstimateResponse(
        total_usd=estimate.total_usd,
        unit_usd=estimate.unit_usd,
        units=estimate.units,
        disclaimer=estimate.disclaimer,
        budget_limit_usd=budget.limit_usd,
        budget_remaining_usd=budget.remaining_usd,
        within_budget=estimate.total_usd <= budget.remaining_usd,
        livemode=settings.boltz_key_mode() == "live",
    )


# --- submit ----------------------------------------------------------------


@router.post("/projects/{project_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    project_id: str,
    body: RunCreate,
    request: Request,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
    runs: RunsDep,
    engine: EngineDep,
    db: DbDep,
) -> DesignRun:
    """Submit a design run.

    Order matters and is not negotiable: re-estimate, compare against what the
    researcher confirmed, reserve the budget, create the run row, then submit.
    The reservation precedes submission so a crash between the two costs us a
    held reservation rather than an unbilled run we have no record of.
    """
    target = await _load_target_and_project(
        projects, targets, project_id, body.request.target_id, user.id
    )

    recent = await runs.count_recent_for_project(project_id, within_hours=1)
    if recent >= MAX_RUNS_PER_HOUR:
        raise errors.rate_limited(3600)

    epitope = target.epitope
    assert epitope is not None  # guaranteed by _load_target_and_project
    spec = _build_spec(target, body.request)
    try:
        fresh = await engine.estimate_cost(spec)
    except EngineUnavailable as exc:
        raise errors.engine_unavailable(str(exc)) from exc
    except EngineRejected as exc:
        raise errors.ProblemError(
            code="spec_rejected",
            title="The design engine rejected this request",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # The researcher must have seen this price. A mismatch means the page was
    # stale or the request was altered after confirmation.
    if abs(fresh.total_usd - body.confirmed_cost_usd) > ESTIMATE_TOLERANCE:
        raise errors.cost_estimate_stale(
            f"you confirmed ${body.confirmed_cost_usd} but this run now costs "
            f"${fresh.total_usd}. Re-estimate and confirm the new price."
        )

    budget = BudgetService(db)
    try:
        await budget.reserve(project_id, fresh.total_usd)
    except BudgetExceeded as exc:
        raise errors.budget_exceeded(
            str(exc),
            requested_usd=str(exc.requested),
            remaining_usd=str(exc.remaining),
            limit_usd=str(exc.limit),
        ) from exc

    idempotency_key = request.headers.get("idempotency-key") or str(uuid.uuid4())
    run = await runs.create(
        project_id=project_id,
        target_id=target.id,
        engine=engine.name,
        idempotency_key=idempotency_key,
        num_proteins=body.request.num_proteins,
        modality=body.request.modality,
        name=body.request.name,
        epitope_pdb_id=epitope.pdb_id,
        epitope_chain_id=epitope.chain_id,
        epitope_design_indices=list(epitope.design_indices),
        cost_estimate_usd=fresh.total_usd,
    )
    if run is None:
        # The key was already used, so this is a retry of a submission that
        # already happened. Release the duplicate reservation and return the
        # original run rather than starting a second one.
        await budget.release(project_id, fresh.total_usd)
        existing = await runs.get_by_idempotency_key(idempotency_key)
        if existing is None:  # pragma: no cover - only under a race we then lose
            raise errors.ProblemError(
                code="submission_conflict",
                title="Duplicate submission",
                status_code=status.HTTP_409_CONFLICT,
            )
        return existing

    try:
        job = await engine.start_design(spec, idempotency_key=idempotency_key)
    except (EngineUnavailable, EngineRejected) as exc:
        await budget.release(project_id, fresh.total_usd)
        failed = await runs.mark_submit_failed(run.id, error=str(exc))
        log.error("submission failed for run %s: %s", run.id, exc)
        if failed is not None:
            return failed
        raise errors.engine_unavailable(str(exc)) from exc

    submitted = await runs.mark_submitted(
        run.id,
        engine_job_id=job.job_id,
        status=RunStatus.running if job.status.value == "running" else RunStatus.pending,
        livemode=job.livemode,
    )
    return submitted or run


# --- reads -----------------------------------------------------------------


@router.get("/projects/{project_id}/runs")
async def list_runs(
    project_id: str, user: CurrentUser, projects: ProjectsDep, runs: RunsDep
) -> RunList:
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    return RunList(items=await runs.list_for_project(project_id))


@router.get("/projects/{project_id}/runs/{run_id}")
async def get_run(
    project_id: str, run_id: str, user: CurrentUser, projects: ProjectsDep, runs: RunsDep
) -> DesignRun:
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")
    return run


@router.get("/projects/{project_id}/runs/{run_id}/candidates")
async def list_candidates(
    project_id: str,
    run_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    runs: RunsDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> CandidateList:
    from biostruct.repositories.runs import CandidateRepository

    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")
    candidates = CandidateRepository(db)
    return CandidateList(
        items=await candidates.list_for_run(run_id, limit=limit, skip=skip),
        total=await candidates.count_for_run(run_id),
        run_status=run.status,
        # Surfaced so a UI can never present synthetic results as findings.
        livemode=run.livemode,
    )


@router.get("/projects/{project_id}/runs/{run_id}/ranking")
async def rank_run_candidates(
    project_id: str,
    run_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    runs: RunsDep,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> RankedCandidateList:
    """Rank this run's candidates and explain each score.

    Computed on read from stored metrics, so changing the project's weights
    re-ranks instantly and spends nothing (ARCHITECTURE.md §6.4).
    """
    from biostruct.ranking import (
        MetricInput,
        Weights,
        explain,
        rank_candidates,
        verdict_for,
    )
    from biostruct.repositories.runs import CandidateRepository

    project = await projects.get(project_id, user_id=user.id)
    if project is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")

    repo = CandidateRepository(db)
    candidates = await repo.list_for_run(run_id, limit=limit)
    by_id = {c.id: c for c in candidates}

    weights = Weights.from_mapping({k: v for k, v in project.ranking_weights.as_mapping().items()})
    result = rank_candidates(
        [
            MetricInput(
                candidate_id=c.id,
                structure_confidence=c.metrics.structure_confidence,
                binding_confidence=c.metrics.binding_confidence,
                iptm=c.metrics.iptm,
                min_interaction_pae=c.metrics.min_interaction_pae,
                ipsae_min=c.metrics.ipsae_min,
            )
            for c in candidates
        ],
        weights,
    )

    return RankedCandidateList(
        items=[
            RankedCandidateOut(
                candidate=by_id[ranked.candidate_id],
                rank=ranked.rank,
                score=ranked.score,
                percentile=ranked.percentile,
                binding_confidence=ranked.binding_confidence,
                binding_band=ranked.binding_band,
                verdict=verdict_for(ranked),
                explanation=explain(ranked, result),
                contributions=[
                    MetricContributionOut(
                        metric=c.metric,
                        label=c.label,
                        raw=c.raw,
                        normalised=c.normalised,
                        weight=c.weight,
                        contribution=c.contribution,
                        higher_is_better=c.higher_is_better,
                        is_constant=c.is_constant,
                    )
                    for c in ranked.contributions
                ],
                missing_metrics=list(ranked.missing_metrics),
                weight_redistributed=ranked.weight_redistributed,
            )
            for ranked in result.ranked
            if ranked.candidate_id in by_id
        ],
        total=result.count,
        run_status=run.status,
        livemode=run.livemode,
        weights=result.weights.as_mapping(),
        excluded=[{"candidate_id": cid, "reason": why} for cid, why in result.excluded],
        constant_metrics=list(result.constant_metrics),
        metrics_absent_everywhere=list(result.metrics_absent_everywhere),
        best_binding_confidence=result.best_binding_confidence,
        nothing_predicted_to_bind=result.nothing_predicted_to_bind,
        assessment=result.absolute_assessment,
    )


@router.get("/projects/{project_id}/candidates/{candidate_id}/structure.cif")
async def get_candidate_structure(
    project_id: str,
    candidate_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    db: DbDep,
    request: Request,
) -> Response:
    """Serve the mirrored structure from our own storage.

    Never a Boltz URL: those expire in about 30 minutes, so handing one to a
    browser would produce a viewer that works for half an hour and then does not.
    """
    from biostruct.repositories.runs import CandidateRepository

    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    candidate = await CandidateRepository(db).get(candidate_id, project_id=project_id)
    if candidate is None:
        raise errors.not_found("Candidate")
    if not candidate.structure_key:
        raise errors.not_found("Structure for this candidate")
    storage = request.app.state.storage
    try:
        data = await storage.get_bytes(candidate.structure_key)
    except Exception as exc:
        raise errors.ProblemError(
            code="structure_unavailable",
            title="Structure could not be read",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return Response(
        content=data,
        media_type="chemical/x-cif",
        headers={
            "content-disposition": f'inline; filename="{candidate_id}.cif"',
            "cache-control": "private, max-age=3600",
        },
    )


# --- control ---------------------------------------------------------------


@router.post("/projects/{project_id}/runs/{run_id}/stop")
async def stop_run(
    project_id: str,
    run_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    runs: RunsDep,
    engine: EngineDep,
) -> DesignRun:
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")
    if not run.engine_job_id:
        raise errors.ProblemError(
            code="not_running",
            title="This run was never submitted",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        await engine.stop_design(run.engine_job_id)
    except EngineUnavailable as exc:
        raise errors.engine_unavailable(str(exc)) from exc
    # The reconciler will observe the stopped status and release the budget.
    updated = await runs.set_status(run_id, RunStatus.running)
    return updated or run


@router.post("/projects/{project_id}/runs/{run_id}/resume")
async def resume_run(
    project_id: str,
    run_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    runs: RunsDep,
    engine: EngineDep,
) -> DesignRun:
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")
    if not run.engine_job_id:
        raise errors.ProblemError(
            code="not_running",
            title="This run was never submitted",
            status_code=status.HTTP_409_CONFLICT,
        )
    try:
        job = await engine.resume_design(run.engine_job_id)
    except EngineUnavailable as exc:
        raise errors.engine_unavailable(str(exc)) from exc
    updated = await runs.update_progress(
        run_id,
        status=RunStatus.running,
        generated=job.generated,
        total=job.total_to_generate,
    )
    return updated or run


# --- live progress ---------------------------------------------------------


@router.get("/projects/{project_id}/runs/{run_id}/events")
async def run_events(
    project_id: str,
    run_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    runs: RunsDep,
    db: DbDep,
) -> StreamingResponse:
    """Server-sent events for one run's progress.

    Current state is always sent first, read straight from MongoDB, and the
    change stream only carries deltas afterwards. That makes the stream an
    optimisation for liveness rather than a source of truth: if it dies,
    progress gets slower, never wrong (ARCHITECTURE.md §12).
    """
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    run = await runs.get(run_id, project_id=project_id)
    if run is None:
        raise errors.not_found("Run")

    async def stream() -> AsyncIterator[bytes]:
        def frame(event: str, payload: dict[str, object]) -> bytes:
            return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode()

        current = run
        yield frame("state", current.model_dump(mode="json"))
        if current.status.is_terminal:
            yield frame("complete", {"status": current.status.value})
            return

        last_seen = current.model_dump(mode="json")
        watcher: Any = None
        try:
            try:
                watcher = db.design_runs.watch(
                    [{"$match": {"documentKey._id": run_id}}],
                    full_document="updateLookup",
                )
            except Exception as exc:
                log.info("change stream unavailable, falling back to polling: %s", exc)
                watcher = None

            deadline = asyncio.get_running_loop().time() + 3600
            while asyncio.get_running_loop().time() < deadline:
                refreshed = await runs.get(run_id, project_id=project_id)
                if refreshed is None:
                    yield frame("error", {"detail": "run disappeared"})
                    return
                payload = refreshed.model_dump(mode="json")
                if payload != last_seen:
                    last_seen = payload
                    yield frame("state", payload)
                if refreshed.status.is_terminal:
                    yield frame("complete", {"status": refreshed.status.value})
                    return
                # A heartbeat keeps proxies from closing an idle connection.
                yield b": keep-alive\n\n"
                if watcher is not None:
                    try:
                        await asyncio.wait_for(watcher.next(), timeout=15)
                        continue
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        log.info("change stream ended, polling instead: %s", exc)
                        watcher = None
                await asyncio.sleep(3)
        finally:
            if watcher is not None:
                with contextlib.suppress(Exception):
                    await watcher.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )


@router.get("/projects/{project_id}/budget")
async def get_budget(
    project_id: str, user: CurrentUser, projects: ProjectsDep, db: DbDep
) -> dict[str, object]:
    if await projects.get(project_id, user_id=user.id) is None:
        raise errors.not_found("Project")
    budget = await BudgetService(db).get(project_id)
    if budget is None:
        raise errors.not_found("Project")
    return {
        "limit_usd": str(budget.limit_usd),
        "spent_usd": str(budget.spent_usd),
        "reserved_usd": str(budget.reserved_usd),
        "remaining_usd": str(budget.remaining_usd),
        "should_warn": budget.should_warn,
        "as_of": datetime.now(UTC).isoformat(),
    }

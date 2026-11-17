"""Turning an agreed proposal into real scientific work.

This is the piece that makes Antidote a product rather than a conversation.
The agent can describe a fold or a screen and price it; this module is what
actually runs it against Boltz, waits, harvests the results, and turns them
into something a researcher can look at.

Three things it is careful about:

* **A run outlives the request that started it.** Folding takes minutes. The
  HTTP call returns as soon as the job is accepted, and the work continues in
  the background, writing progress to MongoDB. Closing the browser does not
  cancel science that is already being billed.
* **Results are downloaded, not linked.** Boltz hands back presigned URLs that
  expire in seven days. A structure a researcher may want next month is pulled
  into our own storage while the link is alive.
* **Partial success is still success.** A screen of 40 molecules where 3 fail
  the structural filters returns 37 results and says so. Throwing the run away
  because it was not perfect would discard work that was already paid for.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.engines.base import JobStatus
from biostruct.engines.prediction import Entity, PredictionSpec
from biostruct.engines.screening import (
    DESIGN_MOLECULES_MIN,
    Molecule,
    MoleculeDesignSpec,
    ScreenSpec,
    ScreenTarget,
)
from biostruct.library import LIBRARY_SIZE_MAX, LibraryBuilder
from biostruct.models.conversation import ArtifactCreate, ArtifactKind
from biostruct.repositories.conversations import ConversationRepository
from biostruct.sources.http import SourceUnavailable

log = logging.getLogger(__name__)

RUNS = "agent_runs"

#: How often a running job is re-checked. Boltz folds take minutes, so a tight
#: loop buys nothing and burns their rate limit.
POLL_SECONDS = 6.0

#: A job that has not finished by now is almost certainly stuck. The run is
#: marked failed and the engine job id is kept, so it can be inspected rather
#: than silently abandoned.
MAX_WAIT_SECONDS = 1800.0


class RunKind(StrEnum):
    fold = "fold"
    screen = "screen"
    design = "design"


class RunState(StrEnum):
    pending = "pending"
    running = "running"
    harvesting = "harvesting"
    complete = "complete"
    failed = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (RunState.complete, RunState.failed)


@dataclass(frozen=True, slots=True)
class AgentRun:
    id: str
    session_id: str
    user_id: str
    kind: RunKind
    state: RunState
    title: str = ""
    engine_job_id: str = ""
    livemode: bool = False
    done: int = 0
    total: int = 0
    error: str = ""
    artifact_id: str = ""
    cost_usd: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def percent(self) -> int:
        if self.state is RunState.complete:
            return 100
        if self.total <= 0:
            return 0
        return max(0, min(100, round(self.done / self.total * 100)))


def _run_from_doc(doc: dict[str, Any]) -> AgentRun:
    return AgentRun(
        id=doc["_id"],
        session_id=doc["session_id"],
        user_id=doc["user_id"],
        kind=RunKind(doc["kind"]),
        state=RunState(doc["state"]),
        title=doc.get("title", ""),
        engine_job_id=doc.get("engine_job_id", ""),
        livemode=bool(doc.get("livemode", False)),
        done=int(doc.get("done", 0)),
        total=int(doc.get("total", 0)),
        error=doc.get("error", ""),
        artifact_id=doc.get("artifact_id", ""),
        cost_usd=doc.get("cost_usd", ""),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


class RunRejected(ValueError):
    """The proposal cannot be run as described, with a reason for the person."""


class AgentRunner:
    """Starts and supervises the paid scientific work."""

    def __init__(
        self,
        *,
        db: AsyncIOMotorDatabase[dict[str, Any]],
        engine: Any,
        conversations: ConversationRepository,
        sources: Any = None,
        chembl: Any = None,
        storage: Any = None,
    ) -> None:
        self.db = db
        self._engine = engine
        self._repo = conversations
        self._sources = sources
        self._chembl = chembl
        self._storage = storage
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ reads

    async def list_runs(self, session_id: str, *, user_id: str) -> list[AgentRun]:
        cursor = (
            self.db[RUNS]
            .find({"session_id": session_id, "user_id": user_id})
            .sort("created_at", 1)
            .limit(100)
        )
        return [_run_from_doc(d) async for d in cursor]

    async def get_run(self, run_id: str, *, user_id: str) -> AgentRun | None:
        doc = await self.db[RUNS].find_one({"_id": run_id, "user_id": user_id})
        return _run_from_doc(doc) if doc else None

    # ----------------------------------------------------------------- writes

    async def _update(self, run_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.now(UTC)
        await self.db[RUNS].update_one({"_id": run_id}, {"$set": fields})

    async def start(
        self, *, session_id: str, user_id: str, tool: str, arguments: dict[str, Any]
    ) -> AgentRun:
        """Accept a confirmed proposal and begin the work.

        Returns as soon as the run is recorded. The science happens in the
        background, because a fold takes minutes and an HTTP request should not
        be held open for them.
        """
        kind = {
            "fold_structure": RunKind.fold,
            "screen_molecules": RunKind.screen,
            "design_molecules": RunKind.design,
        }.get(tool)
        if kind is None:
            raise RunRejected(f"{tool!r} is not something that can be run.")

        session = await self._repo.get_session(session_id, user_id=user_id)
        if session is None:
            raise RunRejected("That session does not exist.")

        title = {
            RunKind.fold: "Folding the protein",
            RunKind.screen: "Screening molecules",
            RunKind.design: "Inventing molecules",
        }[kind]

        now = datetime.now(UTC)
        run_id = str(uuid.uuid4())
        await self.db[RUNS].insert_one(
            {
                "_id": run_id,
                "session_id": session_id,
                "user_id": user_id,
                "kind": kind.value,
                "state": RunState.pending.value,
                "title": title,
                "arguments": arguments,
                "engine_job_id": "",
                "livemode": False,
                "done": 0,
                "total": 0,
                "error": "",
                "artifact_id": "",
                "cost_usd": "",
                "created_at": now,
                "updated_at": now,
            }
        )

        task = asyncio.create_task(self._supervise(run_id, kind, arguments))
        # Held so the event loop does not garbage-collect a running task.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        run = await self.get_run(run_id, user_id=user_id)
        assert run is not None
        return run

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # --------------------------------------------------------------- the work

    async def _supervise(self, run_id: str, kind: RunKind, arguments: dict[str, Any]) -> None:
        """Run one job to completion, recording whatever happens.

        Every failure path ends with the run marked ``failed`` and a sentence a
        researcher can read. A run stuck in ``running`` forever is the one
        outcome that leaves someone staring at a spinner.
        """
        try:
            await self._update(run_id, state=RunState.running.value)
            if kind is RunKind.fold:
                await self._run_fold(run_id, arguments)
            elif kind is RunKind.screen:
                await self._run_screen(run_id, arguments)
            else:
                await self._run_design(run_id, arguments)
        except asyncio.CancelledError:
            await self._update(
                run_id,
                state=RunState.failed.value,
                error="The server restarted while this was running.",
            )
            raise
        except RunRejected as exc:
            await self._update(run_id, state=RunState.failed.value, error=str(exc))
        except Exception as exc:
            log.exception("agent run %s failed", run_id)
            await self._update(run_id, state=RunState.failed.value, error=str(exc))

    async def _sequence_for(self, arguments: dict[str, Any]) -> tuple[str, str]:
        """``(sequence, label)`` from either a pasted sequence or an accession.

        A pasted sequence wins: for a resistant isolate it is the mutant the
        researcher actually has, which is the whole point of step 2 and is
        exactly what no structure database holds.
        """
        sequence = "".join(str(arguments.get("sequence") or "").split()).upper()
        accession = str(arguments.get("accession") or "").strip().upper()
        if sequence:
            return sequence, accession or f"{len(sequence)}-residue sequence"
        if not accession:
            raise RunRejected("No sequence and no accession, so there is nothing to fold.")
        if self._sources is None:
            raise RunRejected("The protein sources are not available.")
        try:
            evidence = await self._sources.gather_evidence(accession)
        except SourceUnavailable as exc:
            raise RunRejected(f"Could not load {accession}: {exc.detail}") from exc
        target = evidence.target
        if not target.sequence:
            raise RunRejected(f"{accession} has no sequence on record.")
        return target.sequence, f"{accession} {target.protein_name}"

    async def _wait_for(self, run_id: str, poll: Any, *, progress: bool = False) -> Any:
        """Poll an engine job until it settles."""
        waited = 0.0
        while waited < MAX_WAIT_SECONDS:
            job = await poll()
            if progress and getattr(job, "progress", None) is not None:
                await self._update(run_id, done=job.progress.done, total=job.progress.total)
            if job.status is JobStatus.succeeded:
                return job
            if job.is_terminal:
                raise RunRejected(
                    getattr(job, "error", "") or f"The engine reported {job.status.value}."
                )
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        raise RunRejected("The job did not finish in time and was given up on.")

    async def _store(self, key: str, artifact: Any) -> str:
        """Pull a presigned artifact into our own storage before it expires."""
        if artifact is None or not getattr(artifact, "url", "") or self._storage is None:
            return ""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(artifact.url)
                response.raise_for_status()
                await self._storage.put_bytes(key, response.content, content_type="chemical/x-cif")
        except Exception as exc:
            log.warning("could not store %s: %s", key, exc)
            return ""
        return key

    async def _run_fold(self, run_id: str, arguments: dict[str, Any]) -> None:
        sequence, label = await self._sequence_for(arguments)
        samples = max(1, min(int(arguments.get("num_samples") or 1), 10))
        spec = PredictionSpec(
            entities=(Entity(kind="protein", value=sequence, chain_ids=("A",)),),
            num_samples=samples,
        )
        estimate = await self._engine.estimate_prediction_cost(spec)
        job = await self._engine.start_prediction(spec, idempotency_key=run_id)
        await self._update(
            run_id,
            engine_job_id=job.job_id,
            livemode=job.livemode,
            total=samples,
            cost_usd=str(estimate.total_usd),
            title=f"Folding {label}",
        )
        job = await self._wait_for(run_id, lambda: self._engine.get_prediction(job.job_id))
        await self._update(run_id, state=RunState.harvesting.value, done=samples)

        samples_out: list[dict[str, Any]] = []
        for index, sample in enumerate(job.samples):
            key = await self._store(f"structures/{run_id}/sample-{index}.cif", sample.structure)
            samples_out.append(
                {
                    "index": index,
                    "structure_key": key,
                    "structure_url": getattr(sample.structure, "url", ""),
                    "structure_confidence": sample.metrics.structure_confidence,
                    "ptm": sample.metrics.ptm,
                    "iptm": sample.metrics.iptm,
                    "complex_plddt": sample.metrics.complex_plddt,
                }
            )

        run = await self.db[RUNS].find_one({"_id": run_id})
        assert run is not None
        artifact = await self._repo.add_artifact(
            session_id=run["session_id"],
            user_id=run["user_id"],
            data=ArtifactCreate(
                kind=ArtifactKind.structure,
                title=f"Predicted structure — {label}",
                run_id=run_id,
                storage_key=samples_out[0]["structure_key"] if samples_out else "",
                payload={
                    "label": label,
                    "sequence": sequence,
                    "length": len(sequence),
                    "livemode": job.livemode,
                    "samples": samples_out,
                },
            ),
        )
        await self._update(
            run_id,
            state=RunState.complete.value,
            artifact_id=artifact.id if artifact else "",
        )

    async def _molecules_for(
        self, arguments: dict[str, Any]
    ) -> tuple[list[Molecule], list[dict[str, Any]]]:
        """The library to screen, with its provenance kept for the result table."""
        wanted = max(1, min(int(arguments.get("num_molecules") or 24), LIBRARY_SIZE_MAX))
        library = await LibraryBuilder(self._chembl).assemble(
            target_class=str(arguments.get("ambler_class") or "A").upper(),
            chembl_target_id=str(arguments.get("chembl_target_id") or ""),
            max_size=wanted,
        )
        if not library.entries:
            raise RunRejected("No molecules could be assembled for this target.")
        molecules = [
            Molecule(smiles=e.smiles, external_id=e.external_id or e.name or f"mol-{i}")
            for i, e in enumerate(library.entries)
        ]
        provenance = [
            {
                "external_id": e.external_id,
                "name": e.name,
                "is_control": e.is_control,
                "known_potency_nm": e.known_potency_nm,
                "note": e.note,
            }
            for e in library.entries
        ]
        return molecules, provenance

    async def _run_screen(self, run_id: str, arguments: dict[str, Any]) -> None:
        sequence, label = await self._sequence_for(arguments)
        molecules, provenance = await self._molecules_for(arguments)
        spec = ScreenSpec(
            target=ScreenTarget(entities=(("A", sequence),)),
            molecules=tuple(molecules),
        )
        estimate = await self._engine.estimate_screen_cost(spec)
        job = await self._engine.start_screen(spec, idempotency_key=run_id)
        await self._update(
            run_id,
            engine_job_id=job.job_id,
            livemode=job.livemode,
            total=len(molecules),
            cost_usd=str(estimate.total_usd),
            title=f"Screening {len(molecules)} molecules against {label}",
        )
        job = await self._wait_for(
            run_id, lambda: self._engine.get_screen(job.job_id), progress=True
        )
        await self._update(run_id, state=RunState.harvesting.value)
        rows = [r async for r in self._engine.iter_screen_results(job.job_id)]
        await self._finish_molecules(
            run_id, rows, provenance, label=label, livemode=job.livemode, kind="screen"
        )

    async def _run_design(self, run_id: str, arguments: dict[str, Any]) -> None:
        sequence, label = await self._sequence_for(arguments)
        count = max(
            DESIGN_MOLECULES_MIN,
            min(int(arguments.get("num_molecules") or DESIGN_MOLECULES_MIN), LIBRARY_SIZE_MAX),
        )
        spec = MoleculeDesignSpec(
            target=ScreenTarget(entities=(("A", sequence),)), num_molecules=count
        )
        estimate = await self._engine.estimate_design_cost(spec)
        job = await self._engine.start_molecule_design(spec, idempotency_key=run_id)
        await self._update(
            run_id,
            engine_job_id=job.job_id,
            livemode=job.livemode,
            total=count,
            cost_usd=str(estimate.total_usd),
            title=f"Inventing {count} molecules for {label}",
        )
        job = await self._wait_for(
            run_id, lambda: self._engine.get_screen(job.job_id), progress=True
        )
        await self._update(run_id, state=RunState.harvesting.value)
        rows = [r async for r in self._engine.iter_design_results(job.job_id)]
        await self._finish_molecules(
            run_id, rows, [], label=label, livemode=job.livemode, kind="design"
        )

    async def _finish_molecules(
        self,
        run_id: str,
        rows: list[Any],
        provenance: list[dict[str, Any]],
        *,
        label: str,
        livemode: bool,
        kind: str,
    ) -> None:
        """Rank, annotate and store the molecules a run produced."""
        by_id = {p["external_id"]: p for p in provenance if p.get("external_id")}

        results: list[dict[str, Any]] = []
        for row in rows:
            # Test mode replaces our ids and SMILES with its own, so a result
            # must never be joined back to a real library outside live mode.
            known = by_id.get(row.external_id) if livemode else None
            results.append(
                {
                    "result_id": row.result_id,
                    "smiles": row.smiles,
                    "external_id": row.external_id,
                    "name": (known or {}).get("name", ""),
                    "is_control": bool((known or {}).get("is_control")),
                    "known_potency_nm": (known or {}).get("known_potency_nm"),
                    "binding_confidence": row.metrics.binding_confidence,
                    "structure_confidence": row.metrics.structure_confidence,
                    "iptm": row.metrics.iptm,
                    "lipophilicity": row.adme.lipophilicity,
                    "permeability": row.adme.permeability,
                    "solubility": row.adme.solubility,
                    "warnings": [w.message for w in row.warnings],
                }
            )

        # Rank on binding confidence, the metric the API documents as primary
        # for hit discovery. Anything unscored sorts last rather than as zero.
        results.sort(
            key=lambda r: (
                r["binding_confidence"] is None,
                -(r["binding_confidence"] or 0.0),
            )
        )
        for position, row in enumerate(results, start=1):
            row["rank"] = position

        control_rank = next((r["rank"] for r in results if r["is_control"]), None)

        run = await self.db[RUNS].find_one({"_id": run_id})
        assert run is not None
        artifact = await self._repo.add_artifact(
            session_id=run["session_id"],
            user_id=run["user_id"],
            data=ArtifactCreate(
                kind=ArtifactKind.molecule_set,
                title=(
                    f"{len(results)} molecules "
                    f"{'screened against' if kind == 'screen' else 'designed for'} {label}"
                ),
                run_id=run_id,
                payload={
                    "label": label,
                    "kind": kind,
                    "livemode": livemode,
                    "requested": int(run.get("total", 0)),
                    "returned": len(results),
                    "control_rank": control_rank,
                    "results": results,
                },
            ),
        )
        await self._update(
            run_id,
            state=RunState.complete.value,
            done=len(results),
            artifact_id=artifact.id if artifact else "",
        )


__all__ = ["RUNS", "AgentRun", "AgentRunner", "RunKind", "RunRejected", "RunState"]

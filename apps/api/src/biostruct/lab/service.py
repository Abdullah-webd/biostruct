"""The two steps, and nothing else.

Step 2: a pasted sequence becomes a predicted 3D structure.
Step 3: that structure gets a ranked shortlist of molecules to test.

Both run against Boltz, both take minutes, and both therefore run in the
background while the browser polls. The request that starts the work returns
as soon as the job is accepted — holding it open would time out in every proxy
between here and the researcher, and cancelling on disconnect would abandon
work that is already being billed.

Two things carried over from the engine work, because they are the difference
between a result and a misleading result:

* **Every screen contains a positive control** — a compound already known to
  inhibit this class of enzyme. If it does not rank near the top, the run is
  not working, and that has to be said louder than the hits.
* **Test-mode results are never joined back to our library.** In test mode the
  engine discards the ids and SMILES we submitted and substitutes its own, so
  matching them back would label a random molecule as the control and make a
  synthetic run look validated.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from biostruct.engines.base import JobStatus
from biostruct.engines.prediction import Entity, PredictionSpec
from biostruct.engines.screening import Molecule, ScreenSpec, ScreenTarget
from biostruct.lab.models import (
    TOP_N,
    Hit,
    JobState,
    Prediction,
    Screen,
    clean_sequence,
)
from biostruct.library import LibraryBuilder

log = logging.getLogger(__name__)

PREDICTIONS = "lab_predictions"
SCREENS = "lab_screens"

#: Boltz jobs take minutes, so a tighter loop buys nothing and burns their
#: rate limit.
POLL_SECONDS = 5.0

#: A job still unfinished by now is stuck. The run is failed with a readable
#: reason rather than left spinning forever in front of someone.
MAX_WAIT_SECONDS = 1800.0

#: How many molecules we actually submit. Enough that a top ten means
#: something, few enough to come back while someone waits.
LIBRARY_SIZE = 24


class LabError(ValueError):
    """Something the researcher needs to know, in words they can act on."""


def _prediction(doc: dict[str, Any]) -> Prediction:
    return Prediction(
        id=doc["_id"],
        user_id=doc["user_id"],
        name=doc.get("name", ""),
        sequence=doc["sequence"],
        state=JobState(doc["state"]),
        error=doc.get("error", ""),
        engine_job_id=doc.get("engine_job_id", ""),
        livemode=bool(doc.get("livemode", False)),
        structure_confidence=doc.get("structure_confidence"),
        ptm=doc.get("ptm"),
        has_structure=bool(doc.get("structure_key")),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


def _screen(doc: dict[str, Any]) -> Screen:
    return Screen(
        id=doc["_id"],
        user_id=doc["user_id"],
        prediction_id=doc["prediction_id"],
        state=JobState(doc["state"]),
        error=doc.get("error", ""),
        engine_job_id=doc.get("engine_job_id", ""),
        livemode=bool(doc.get("livemode", False)),
        ambler_class=doc.get("ambler_class", "A"),
        submitted=int(doc.get("submitted", 0)),
        returned=int(doc.get("returned", 0)),
        control_rank=doc.get("control_rank"),
        control_name=doc.get("control_name", ""),
        hits=[Hit(**h) for h in doc.get("hits") or []],
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


class Lab:
    def __init__(
        self,
        *,
        db: AsyncIOMotorDatabase[dict[str, Any]],
        engine: Any,
        storage: Any = None,
        chembl: Any = None,
    ) -> None:
        self.db = db
        self._engine = engine
        self._storage = storage
        self._chembl = chembl
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Any) -> None:
        """Run work in the background, holding a reference so the event loop
        does not collect a job that is being billed right now."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------ step 2

    async def start_prediction(self, *, user_id: str, sequence: str, name: str = "") -> Prediction:
        try:
            cleaned = clean_sequence(sequence)
        except ValueError as exc:
            raise LabError(str(exc)) from exc

        now = datetime.now(UTC)
        doc: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "name": name.strip(),
            "sequence": cleaned,
            "state": JobState.pending.value,
            "error": "",
            "engine_job_id": "",
            "livemode": False,
            "structure_key": "",
            "created_at": now,
            "updated_at": now,
        }
        await self.db[PREDICTIONS].insert_one(doc)
        self._spawn(self._run_prediction(doc["_id"], cleaned))
        return _prediction(doc)

    async def _set(self, collection: str, doc_id: str, **fields: Any) -> None:
        fields["updated_at"] = datetime.now(UTC)
        await self.db[collection].update_one({"_id": doc_id}, {"$set": fields})

    async def _run_prediction(self, prediction_id: str, sequence: str) -> None:
        try:
            await self._set(PREDICTIONS, prediction_id, state=JobState.running.value)
            spec = PredictionSpec(
                entities=(Entity(kind="protein", value=sequence, chain_ids=("A",)),),
                num_samples=1,
            )
            job = await self._engine.start_prediction(spec, idempotency_key=prediction_id)
            await self._set(
                PREDICTIONS,
                prediction_id,
                engine_job_id=job.job_id,
                livemode=job.livemode,
            )
            job = await self._wait(lambda: self._engine.get_prediction(job.job_id))

            sample = job.samples[0] if job.samples else None
            key = ""
            if sample is not None:
                key = await self._store(f"lab/{prediction_id}/structure.cif", sample.structure)
            await self._set(
                PREDICTIONS,
                prediction_id,
                state=JobState.complete.value,
                structure_key=key,
                livemode=job.livemode,
                structure_confidence=sample.metrics.structure_confidence if sample else None,
                ptm=sample.metrics.ptm if sample else None,
            )
        except asyncio.CancelledError:
            await self._set(
                PREDICTIONS,
                prediction_id,
                state=JobState.failed.value,
                error="The server restarted while this was running.",
            )
            raise
        except Exception as exc:
            log.exception("prediction %s failed", prediction_id)
            await self._set(PREDICTIONS, prediction_id, state=JobState.failed.value, error=str(exc))

    # ------------------------------------------------------------ step 3

    async def start_screen(
        self, *, user_id: str, prediction_id: str, ambler_class: str = "A"
    ) -> Screen:
        doc = await self.db[PREDICTIONS].find_one({"_id": prediction_id, "user_id": user_id})
        if doc is None:
            raise LabError("That structure does not exist.")
        if doc["state"] != JobState.complete.value:
            raise LabError("Wait for the structure to finish before screening against it.")

        now = datetime.now(UTC)
        screen: dict[str, Any] = {
            "_id": str(uuid.uuid4()),
            "user_id": user_id,
            "prediction_id": prediction_id,
            "state": JobState.pending.value,
            "error": "",
            "engine_job_id": "",
            "livemode": False,
            "ambler_class": (ambler_class or "A").upper(),
            "submitted": 0,
            "returned": 0,
            "control_rank": None,
            "control_name": "",
            "hits": [],
            "created_at": now,
            "updated_at": now,
        }
        await self.db[SCREENS].insert_one(screen)
        self._spawn(self._run_screen(screen["_id"], doc["sequence"], screen["ambler_class"]))
        return _screen(screen)

    async def _run_screen(self, screen_id: str, sequence: str, ambler_class: str) -> None:
        try:
            await self._set(SCREENS, screen_id, state=JobState.running.value)
            library = await LibraryBuilder(self._chembl).assemble(
                target_class=ambler_class, max_size=LIBRARY_SIZE
            )
            if not library.entries:
                raise LabError(f"No molecules could be assembled for enzyme class {ambler_class}.")
            control = library.controls[0] if library.controls else None
            known = {e.external_id: e for e in library.entries if e.external_id}

            spec = ScreenSpec(
                target=ScreenTarget(entities=(("A", sequence),)),
                molecules=tuple(
                    Molecule(smiles=e.smiles, external_id=e.external_id or e.name)
                    for e in library.entries
                ),
                # Filters off, and this is a scientific judgement, not a
                # shortcut. The structural-alert catalogues exist to strip junk
                # out of million-compound vendor libraries. Our library is nine
                # hand-picked beta-lactamase inhibitors, most of them licensed
                # medicines. Measured on the live API: with the recommended
                # filters on, 6 of 9 were silently discarded — including
                # avibactam, the positive control. A filter that deletes the
                # molecule you are using to check the experiment is not
                # protecting anyone.
                filter_level="disabled",
            )
            job = await self._engine.start_screen(spec, idempotency_key=screen_id)
            await self._set(
                SCREENS,
                screen_id,
                engine_job_id=job.job_id,
                livemode=job.livemode,
                submitted=len(library.entries),
                control_name=control.name if control else "",
            )
            job = await self._wait(lambda: self._engine.get_screen(job.job_id))

            rows = [r async for r in self._engine.iter_screen_results(job.job_id)]
            hits = self._rank(rows, known, livemode=job.livemode)
            control_rank = next((h.rank for h in hits if h.is_control), None)

            await self._set(
                SCREENS,
                screen_id,
                state=JobState.complete.value,
                livemode=job.livemode,
                returned=len(rows),
                control_rank=control_rank,
                hits=[h.model_dump() for h in hits[:TOP_N]],
            )
        except asyncio.CancelledError:
            await self._set(
                SCREENS,
                screen_id,
                state=JobState.failed.value,
                error="The server restarted while this was running.",
            )
            raise
        except Exception as exc:
            log.exception("screen %s failed", screen_id)
            await self._set(SCREENS, screen_id, state=JobState.failed.value, error=str(exc))

    @staticmethod
    def _rank(rows: list[Any], known: dict[str, Any], *, livemode: bool) -> list[Hit]:
        """Order on binding confidence, the metric the API documents as primary
        for hit discovery.

        An unscored molecule sorts last rather than as zero: zero is a real
        score, and defaulting to it would bury something the engine simply did
        not report on.
        """
        hits: list[Hit] = []
        for row in rows:
            entry = known.get(row.external_id) if livemode else None
            hits.append(
                Hit(
                    rank=0,
                    smiles=row.smiles,
                    name=getattr(entry, "name", "") or "",
                    external_id=row.external_id,
                    is_control=bool(getattr(entry, "is_control", False)),
                    binding_confidence=row.metrics.binding_confidence,
                    structure_confidence=row.metrics.structure_confidence,
                    known_potency_nm=getattr(entry, "known_potency_nm", None),
                    lipophilicity=row.adme.lipophilicity,
                    permeability=row.adme.permeability,
                    solubility=row.adme.solubility or "",
                )
            )
        hits.sort(
            key=lambda h: (
                h.binding_confidence is None,
                -(h.binding_confidence or 0.0),
            )
        )
        for position, hit in enumerate(hits, start=1):
            hit.rank = position
        return hits

    # ----------------------------------------------------------- plumbing

    async def _wait(self, poll: Any) -> Any:
        waited = 0.0
        while waited < MAX_WAIT_SECONDS:
            job = await poll()
            if job.status is JobStatus.succeeded:
                return job
            if job.is_terminal:
                raise LabError(
                    getattr(job, "error", "") or f"The engine reported {job.status.value}."
                )
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
        raise LabError("The job did not finish in time and was given up on.")

    async def _store(self, key: str, artifact: Any) -> str:
        """Pull the structure into our own storage while the link is alive.

        Boltz presigns its URLs and they expire, so a viewer pointed at theirs
        would work for a few days and then quietly stop.
        """
        url = getattr(artifact, "url", "")
        if not url or self._storage is None:
            return ""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                await self._storage.put_bytes(key, response.content, content_type="chemical/x-cif")
        except Exception as exc:
            log.warning("could not store %s: %s", key, exc)
            return ""
        return key

    # -------------------------------------------------------------- reads

    async def list_predictions(self, user_id: str, *, limit: int = 50) -> list[Prediction]:
        cursor = self.db[PREDICTIONS].find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        return [_prediction(d) async for d in cursor]

    async def get_prediction(self, prediction_id: str, *, user_id: str) -> Prediction | None:
        doc = await self.db[PREDICTIONS].find_one({"_id": prediction_id, "user_id": user_id})
        return _prediction(doc) if doc else None

    async def structure_bytes(self, prediction_id: str, *, user_id: str) -> bytes | None:
        doc = await self.db[PREDICTIONS].find_one({"_id": prediction_id, "user_id": user_id})
        if doc is None or not doc.get("structure_key") or self._storage is None:
            return None
        try:
            data: bytes = await self._storage.get_bytes(doc["structure_key"])
        except Exception as exc:
            log.warning("could not read structure for %s: %s", prediction_id, exc)
            return None
        return data

    async def screens_for(self, prediction_id: str, *, user_id: str) -> list[Screen]:
        cursor = (
            self.db[SCREENS]
            .find({"prediction_id": prediction_id, "user_id": user_id})
            .sort("created_at", -1)
        )
        return [_screen(d) async for d in cursor]

    async def get_screen(self, screen_id: str, *, user_id: str) -> Screen | None:
        doc = await self.db[SCREENS].find_one({"_id": screen_id, "user_id": user_id})
        return _screen(doc) if doc else None


__all__ = ["LIBRARY_SIZE", "PREDICTIONS", "SCREENS", "Lab", "LabError"]

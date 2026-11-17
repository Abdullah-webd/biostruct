"""The agent runner — the only thing in the product that spends money.

These are about what happens when the work goes wrong, because the happy path
is the easy half. A run that dies silently leaves a researcher watching a
spinner forever; a run that throws away a harvested result discards something
already paid for.

The engine is a stub. MongoDB is real, because a run's whole observable
behaviour is the document it updates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from biostruct.agent.runner import AgentRunner, RunRejected, RunState
from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.engines.base import Artifact as EngineArtifact
from biostruct.engines.base import CostEstimate, JobStatus
from biostruct.engines.prediction import PredictedSample, PredictionJob, StructureMetrics
from biostruct.engines.screening import (
    Adme,
    MoleculeJob,
    MoleculeMetrics,
    MoleculeProgress,
    MoleculeResult,
)
from biostruct.models.conversation import SessionCreate
from biostruct.repositories.conversations import ConversationRepository

USER = "runner-user"

KPC = "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMSTFKVLLCGAVLSR"


@dataclass
class StubTarget:
    accession: str = "Q9F663"
    protein_name: str = "KPC-2"
    sequence: str = KPC


@dataclass
class StubEvidence:
    target: StubTarget


class StubSources:
    def __init__(self, sequence: str = KPC) -> None:
        self._sequence = sequence

    async def gather_evidence(self, accession: str) -> StubEvidence:
        return StubEvidence(StubTarget(accession=accession, sequence=self._sequence))


def estimate(total: str = "0.0250") -> CostEstimate:
    return CostEstimate(
        total_usd=Decimal(total), unit_usd=Decimal("0.0250"), units=1, application="x"
    )


class StubEngine:
    """An engine that succeeds, with every call recorded."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.idempotency: list[str] = []

    async def estimate_prediction_cost(self, spec: Any) -> CostEstimate:
        return estimate()

    async def start_prediction(self, spec: Any, *, idempotency_key: str) -> PredictionJob:
        self.started.append("prediction")
        self.idempotency.append(idempotency_key)
        return PredictionJob(job_id="pred_1", status=JobStatus.pending, livemode=True)

    async def get_prediction(self, job_id: str) -> PredictionJob:
        return PredictionJob(
            job_id=job_id,
            status=JobStatus.succeeded,
            livemode=True,
            samples=(
                PredictedSample(
                    metrics=StructureMetrics(structure_confidence=0.91, ptm=0.88),
                    structure=EngineArtifact(url="https://example.test/s.cif", expires_at=None),
                ),
            ),
        )

    async def estimate_screen_cost(self, spec: Any) -> CostEstimate:
        return estimate("0.2750")

    async def start_screen(self, spec: Any, *, idempotency_key: str) -> MoleculeJob:
        self.started.append("screen")
        self.idempotency.append(idempotency_key)
        return MoleculeJob(job_id="scr_1", status=JobStatus.pending, livemode=True)

    async def get_screen(self, job_id: str) -> MoleculeJob:
        return MoleculeJob(
            job_id=job_id,
            status=JobStatus.succeeded,
            livemode=True,
            progress=MoleculeProgress(total=3, done=3),
        )

    async def iter_screen_results(self, job_id: str) -> Any:
        rows = [
            ("r1", "CCO", "CHEMBL777", 0.41),
            ("r2", "CCN", "CHEMBL1689063", 0.95),
            ("r3", "CCC", "CHEMBL404", None),
        ]
        for result_id, smiles, external_id, score in rows:
            yield MoleculeResult(
                result_id=result_id,
                smiles=smiles,
                external_id=external_id,
                metrics=MoleculeMetrics(binding_confidence=score),
                adme=Adme(lipophilicity=2.0),
            )


class StubStorage:
    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    async def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.saved[key] = data


@pytest_asyncio.fixture
async def db(settings: Settings, clean_db: None) -> Any:
    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        settings.mongodb_uri, tz_aware=True
    )
    database = client[settings.mongodb_db]
    await ensure_indexes(database)
    try:
        yield database
    finally:
        client.close()


async def a_session(db: AsyncIOMotorDatabase[dict[str, Any]]) -> tuple[ConversationRepository, str]:
    repo = ConversationRepository(db)
    session = await repo.create_session(
        user_id=USER, data=SessionCreate(title="t"), default_budget_usd=Decimal("25")
    )
    return repo, session.id


def runner_for(
    db: AsyncIOMotorDatabase[dict[str, Any]],
    repo: ConversationRepository,
    *,
    engine: Any = None,
    storage: Any = None,
    sources: Any = None,
) -> AgentRunner:
    return AgentRunner(
        db=db,
        engine=engine or StubEngine(),
        conversations=repo,
        sources=sources or StubSources(),
        storage=storage,
    )


async def settle(runner: AgentRunner, run_id: str, *, limit: float = 6.0) -> Any:
    """Wait for the background supervisor to reach a terminal state."""
    waited = 0.0
    while waited < limit:
        await asyncio.sleep(0.1)
        waited += 0.1
        run = await runner.get_run(run_id, user_id=USER)
        if run and run.state.is_terminal:
            return run
    raise AssertionError("the run never settled")


class TestWhatCanBeRun:
    async def test_an_unknown_tool_is_refused_with_a_sentence(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_session(db)
        with pytest.raises(RunRejected, match="not something that can be run"):
            await runner_for(db, repo).start(
                session_id=session_id, user_id=USER, tool="delete_everything", arguments={}
            )

    async def test_a_run_cannot_be_started_on_another_users_session(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_session(db)
        with pytest.raises(RunRejected, match="does not exist"):
            await runner_for(db, repo).start(
                session_id=session_id,
                user_id="someone-else",
                tool="fold_structure",
                arguments={"accession": "Q9F663"},
            )

    async def test_folding_with_nothing_to_fold_fails_with_a_reason(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Failing in the background still has to end somewhere a person can
        read, not in a log nobody opens."""
        repo, session_id = await a_session(db)
        runner = runner_for(db, repo)
        run = await runner.start(
            session_id=session_id, user_id=USER, tool="fold_structure", arguments={}
        )
        settled = await settle(runner, run.id)
        assert settled.state is RunState.failed
        assert "nothing to fold" in settled.error


class TestFolding:
    async def test_a_fold_completes_and_produces_a_structure(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_session(db)
        storage = StubStorage()
        runner = runner_for(db, repo, storage=storage)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663", "num_samples": 1},
        )
        settled = await settle(runner, run.id)
        assert settled.state is RunState.complete
        assert settled.artifact_id

        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        structure = next(a for a in artifacts if a.kind.value == "structure")
        assert structure.payload["samples"][0]["structure_confidence"] == 0.91
        assert structure.payload["length"] == len(KPC)

    async def test_the_structure_file_is_pulled_into_our_own_storage(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Boltz presigns its URLs and they expire. A viewer that works for
        seven days and then does not is worse than none."""
        import httpx

        class FakeResponse:
            content = b"data_fold\n#\n"

            def raise_for_status(self) -> None:
                return None

        class FakeClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> FakeClient:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

            async def get(self, url: str) -> FakeResponse:
                return FakeResponse()

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        repo, session_id = await a_session(db)
        storage = StubStorage()
        runner = runner_for(db, repo, storage=storage)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663"},
        )
        await settle(runner, run.id)
        assert any(k.endswith(".cif") for k in storage.saved)
        assert next(iter(storage.saved.values())) == b"data_fold\n#\n"

    async def test_a_pasted_sequence_is_used_in_preference_to_an_accession(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """For a resistant isolate the pasted sequence is the mutant the
        researcher actually has. That is the entire point of the step, and no
        database holds it."""
        repo, session_id = await a_session(db)
        mutant = KPC.replace("S", "A", 1)
        runner = runner_for(db, repo, sources=StubSources(sequence=KPC))
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663", "sequence": mutant},
        )
        await settle(runner, run.id)
        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        assert artifacts[0].payload["sequence"] == mutant

    async def test_the_run_id_is_the_idempotency_key(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """So a retry collides with the first submission instead of starting a
        second billed job."""
        repo, session_id = await a_session(db)
        engine = StubEngine()
        runner = runner_for(db, repo, engine=engine)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663"},
        )
        await settle(runner, run.id)
        assert engine.idempotency == [run.id]


class TestScreening:
    async def test_molecules_come_back_ranked_on_binding_confidence(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_session(db)
        runner = runner_for(db, repo)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="screen_molecules",
            arguments={"accession": "Q9F663", "num_molecules": 6, "ambler_class": "A"},
        )
        settled = await settle(runner, run.id, limit=10.0)
        assert settled.state is RunState.complete

        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        molecules = next(a for a in artifacts if a.kind.value == "molecule_set")
        rows = molecules.payload["results"]
        assert [r["rank"] for r in rows] == [1, 2, 3]
        assert rows[0]["binding_confidence"] == 0.95

    async def test_an_unscored_molecule_sorts_last_not_as_zero(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Zero is a real score. Defaulting to it would bury a molecule the
        engine simply did not report on."""
        repo, session_id = await a_session(db)
        runner = runner_for(db, repo)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="screen_molecules",
            arguments={"accession": "Q9F663", "num_molecules": 6},
        )
        await settle(runner, run.id, limit=10.0)
        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        rows = next(a for a in artifacts if a.kind.value == "molecule_set").payload["results"]
        assert rows[-1]["binding_confidence"] is None

    async def test_the_positive_control_is_identified_in_the_results(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """The whole judgement of a screen rests on where the control landed."""
        repo, session_id = await a_session(db)
        runner = runner_for(db, repo)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="screen_molecules",
            arguments={"accession": "Q9F663", "num_molecules": 6, "ambler_class": "A"},
        )
        await settle(runner, run.id, limit=10.0)
        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        payload = next(a for a in artifacts if a.kind.value == "molecule_set").payload
        # Avibactam is the class A control and the stub returns it as CHEMBL1689063.
        control = next(r for r in payload["results"] if r["external_id"] == "CHEMBL1689063")
        assert control["is_control"] is True
        assert payload["control_rank"] == control["rank"]


class TestTestModeIsolation:
    async def test_test_mode_results_are_never_joined_to_our_library(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Measured behaviour: in test mode the engine discards the ids and
        SMILES we submitted and substitutes its own. Matching those back to our
        library would label a random molecule as our positive control."""

        class TestModeEngine(StubEngine):
            async def start_screen(self, spec: Any, *, idempotency_key: str) -> MoleculeJob:
                return MoleculeJob(job_id="scr_t", status=JobStatus.pending, livemode=False)

            async def get_screen(self, job_id: str) -> MoleculeJob:
                return MoleculeJob(
                    job_id=job_id,
                    status=JobStatus.succeeded,
                    livemode=False,
                    progress=MoleculeProgress(total=3, done=3),
                )

        repo, session_id = await a_session(db)
        runner = runner_for(db, repo, engine=TestModeEngine())
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="screen_molecules",
            arguments={"accession": "Q9F663", "num_molecules": 6, "ambler_class": "A"},
        )
        await settle(runner, run.id, limit=10.0)
        artifacts = await repo.list_artifacts(session_id, user_id=USER)
        payload = next(a for a in artifacts if a.kind.value == "molecule_set").payload
        assert payload["livemode"] is False
        assert payload["control_rank"] is None
        assert not any(r["is_control"] for r in payload["results"])
        assert all(r["known_potency_nm"] is None for r in payload["results"])


class TestFailureIsVisible:
    async def test_an_engine_failure_ends_the_run_with_a_message(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        class BrokenEngine(StubEngine):
            async def start_prediction(self, spec: Any, *, idempotency_key: str) -> PredictionJob:
                raise RuntimeError("Boltz refused the submission")

        repo, session_id = await a_session(db)
        runner = runner_for(db, repo, engine=BrokenEngine())
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663"},
        )
        settled = await settle(runner, run.id)
        assert settled.state is RunState.failed
        assert "refused the submission" in settled.error

    async def test_a_terminal_engine_status_is_not_treated_as_success(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        class FailingJob(StubEngine):
            async def get_prediction(self, job_id: str) -> PredictionJob:
                return PredictionJob(
                    job_id=job_id, status=JobStatus.failed, livemode=True, error="out of memory"
                )

        repo, session_id = await a_session(db)
        runner = runner_for(db, repo, engine=FailingJob())
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663"},
        )
        settled = await settle(runner, run.id)
        assert settled.state is RunState.failed
        assert "out of memory" in settled.error

    async def test_runs_are_listed_only_to_their_owner(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        repo, session_id = await a_session(db)
        runner = runner_for(db, repo)
        run = await runner.start(
            session_id=session_id,
            user_id=USER,
            tool="fold_structure",
            arguments={"accession": "Q9F663"},
        )
        await settle(runner, run.id)
        assert len(await runner.list_runs(session_id, user_id=USER)) == 1
        assert await runner.list_runs(session_id, user_id="intruder") == []
        assert await runner.get_run(run.id, user_id="intruder") is None

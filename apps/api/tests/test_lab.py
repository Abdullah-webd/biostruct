"""The two steps.

Most of this file is about the sequence a researcher actually pastes, because
that is the one place a person touches raw data and the one place a tool can
most easily insult them. A sequence copied out of a FASTA file, an email or a
sequencer listing arrives wrapped, headed and numbered, and "invalid input" in
response to a correct paste is how people stop using a tool.

The rest is about failure being visible: a job that dies must say so, not spin.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from biostruct.config import Settings
from biostruct.db import ensure_indexes
from biostruct.engines.base import Artifact as EngineArtifact
from biostruct.engines.base import JobStatus
from biostruct.engines.prediction import PredictedSample, PredictionJob, StructureMetrics
from biostruct.engines.screening import (
    Adme,
    MoleculeJob,
    MoleculeMetrics,
    MoleculeProgress,
    MoleculeResult,
)
from biostruct.lab.models import SEQUENCE_MIN, JobState, clean_sequence
from biostruct.lab.service import Lab, LabError

USER = "lab-user"

KPC = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMS"
    "TFKVLLCGAVLSRIDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLL"
)


class TestTheSequenceSomeoneActuallyPastes:
    def test_a_plain_sequence_passes_through(self) -> None:
        assert clean_sequence(KPC) == KPC

    def test_a_fasta_header_is_dropped(self) -> None:
        """Copying from a .fasta file takes the ``>`` line with it. Refusing
        that would reject the single most common way of holding a sequence."""
        pasted = f">sp|Q9F663|BLAK_KLEPN Carbapenemase KPC-2\n{KPC}"
        assert clean_sequence(pasted) == KPC

    def test_wrapped_lines_are_joined(self) -> None:
        wrapped = "\n".join(KPC[i : i + 60] for i in range(0, len(KPC), 60))
        assert clean_sequence(wrapped) == KPC

    def test_lower_case_is_accepted(self) -> None:
        assert clean_sequence(KPC.lower()) == KPC

    def test_position_numbers_from_a_listing_are_stripped(self) -> None:
        """Sequencer output and textbook listings number every line."""
        listed = f"   1 {KPC[:30]}\n  31 {KPC[30:60]}\n  61 {KPC[60:]}"
        assert clean_sequence(listed) == KPC

    def test_an_unknown_residue_code_is_allowed(self) -> None:
        """X means "we could not read this one" and appears in real sequencing
        output — which is exactly the data this product is for."""
        assert "X" in clean_sequence(KPC[:40] + "X" + KPC[41:])

    def test_dna_is_refused_with_advice_rather_than_a_shrug(self) -> None:
        """ACGT is technically valid as amino acids, so this cannot be caught
        by the alphabet. What can be caught is the obvious case, and the error
        has to tell someone what to do next."""
        with pytest.raises(ValueError, match="Translate it to amino acids"):
            clean_sequence("ATGCGU" * 20)

    def test_a_real_protein_is_not_mistaken_for_dna(self) -> None:
        """The composition test must not fire on genuine sequences. KPC-2
        contains plenty of A, C, G and T residues."""
        assert clean_sequence(KPC) == KPC

    def test_an_empty_paste_says_so_plainly(self) -> None:
        with pytest.raises(ValueError, match="nothing to read"):
            clean_sequence("   \n\n  ")

    def test_something_far_too_short_is_refused_with_its_length(self) -> None:
        with pytest.raises(ValueError, match=f"at least {SEQUENCE_MIN}"):
            clean_sequence("MSIQHF")

    def test_an_enormous_paste_is_refused_before_it_is_submitted(self) -> None:
        huge = (KPC * 20)[:5000]
        with pytest.raises(ValueError, match="cap a single prediction"):
            clean_sequence(huge)

    def test_a_homopolymer_is_not_mistaken_for_dna(self) -> None:
        """A poly-alanine linker is 100% "A", which is also a base. Real DNA
        uses more than one letter."""
        assert clean_sequence("A" * 60) == "A" * 60


@dataclass
class StubStorage:
    saved: dict[str, bytes]

    async def put_bytes(self, key: str, data: bytes, *, content_type: str = "") -> None:
        self.saved[key] = data

    async def get_bytes(self, key: str) -> bytes:
        return self.saved[key]


class StubEngine:
    """Succeeds, in live mode, with three molecules."""

    livemode = True

    async def start_prediction(self, spec: Any, *, idempotency_key: str) -> PredictionJob:
        self.prediction_key = idempotency_key
        return PredictionJob(job_id="p1", status=JobStatus.pending, livemode=self.livemode)

    async def get_prediction(self, job_id: str) -> PredictionJob:
        return PredictionJob(
            job_id=job_id,
            status=JobStatus.succeeded,
            livemode=self.livemode,
            samples=(
                PredictedSample(
                    metrics=StructureMetrics(structure_confidence=0.87, ptm=0.81),
                    structure=EngineArtifact(url="https://example.test/x.cif", expires_at=None),
                ),
            ),
        )

    async def start_screen(self, spec: Any, *, idempotency_key: str) -> MoleculeJob:
        self.submitted = spec.molecules
        return MoleculeJob(job_id="s1", status=JobStatus.pending, livemode=self.livemode)

    async def get_screen(self, job_id: str) -> MoleculeJob:
        return MoleculeJob(
            job_id=job_id,
            status=JobStatus.succeeded,
            livemode=self.livemode,
            progress=MoleculeProgress(total=3, done=3),
        )

    async def iter_screen_results(self, job_id: str) -> Any:
        # Avibactam is the class A control; it wins here, as it should.
        for rid, smiles, ext, score in [
            ("r1", "CCO", "CHEMBL777", 0.30),
            ("r2", "NC(=O)C1", "CHEMBL1689063", 0.92),
            ("r3", "CCC", "CHEMBL404", None),
        ]:
            yield MoleculeResult(
                result_id=rid,
                smiles=smiles,
                external_id=ext,
                metrics=MoleculeMetrics(binding_confidence=score),
                adme=Adme(lipophilicity=1.5),
            )


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


@pytest.fixture
def no_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """The structure fetch is the only network call in the happy path."""
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


def lab_for(db: AsyncIOMotorDatabase[dict[str, Any]], engine: Any = None) -> tuple[Lab, Any]:
    storage = StubStorage(saved={})
    return Lab(db=db, engine=engine or StubEngine(), storage=storage), storage


async def settle(get: Any, *, limit: float = 6.0) -> Any:
    waited = 0.0
    while waited < limit:
        await asyncio.sleep(0.1)
        waited += 0.1
        item = await get()
        if item and item.state.is_terminal:
            return item
    raise AssertionError("never settled")


class TestFolding:
    async def test_a_pasted_sequence_becomes_a_structure(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        lab, storage = lab_for(db)
        started = await lab.start_prediction(user_id=USER, sequence=KPC, name="KPC-2")
        assert started.state is JobState.pending

        done = await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        assert done.state is JobState.complete
        assert done.structure_confidence == 0.87
        assert done.has_structure
        assert await lab.structure_bytes(started.id, user_id=USER) == b"data_fold\n#\n"
        assert any(k.endswith(".cif") for k in storage.saved)

    async def test_a_bad_sequence_is_refused_before_anything_is_submitted(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        lab, _ = lab_for(db)
        with pytest.raises(LabError, match="at least"):
            await lab.start_prediction(user_id=USER, sequence="MSIQ")

    async def test_an_engine_failure_ends_the_job_with_a_message(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        class Broken(StubEngine):
            async def start_prediction(self, spec: Any, *, idempotency_key: str) -> Any:
                raise RuntimeError("Boltz refused the submission")

        lab, _ = lab_for(db, Broken())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        done = await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        assert done.state is JobState.failed
        assert "refused the submission" in done.error

    async def test_a_terminal_engine_status_is_not_mistaken_for_success(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        class Failing(StubEngine):
            async def get_prediction(self, job_id: str) -> PredictionJob:
                return PredictionJob(
                    job_id=job_id, status=JobStatus.failed, livemode=True, error="out of memory"
                )

        lab, _ = lab_for(db, Failing())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        done = await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        assert done.state is JobState.failed
        assert "out of memory" in done.error

    async def test_the_prediction_id_is_the_idempotency_key(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        """So a retry collides with the first submission instead of starting a
        second billed fold."""
        engine = StubEngine()
        lab, _ = lab_for(db, engine)
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        assert engine.prediction_key == started.id

    async def test_another_researcher_cannot_read_it(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        lab, _ = lab_for(db)
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        assert await lab.get_prediction(started.id, user_id="intruder") is None
        assert await lab.structure_bytes(started.id, user_id="intruder") is None
        assert await lab.list_predictions("intruder") == []


class TestScreening:
    async def _folded(self, db: Any) -> tuple[Lab, str]:
        lab, _ = lab_for(db)
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        return lab, started.id

    async def test_molecules_come_back_ranked_with_the_control_found(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        lab, prediction_id = await self._folded(db)
        started = await lab.start_screen(user_id=USER, prediction_id=prediction_id)
        done = await settle(lambda: lab.get_screen(started.id, user_id=USER), limit=12.0)

        assert done.state is JobState.complete
        assert [h.rank for h in done.hits] == [1, 2, 3]
        assert done.hits[0].binding_confidence == 0.92
        assert done.hits[0].is_control
        assert done.control_rank == 1
        assert done.control_looks_right is True

    async def test_an_unscored_molecule_sorts_last_not_as_zero(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        lab, prediction_id = await self._folded(db)
        started = await lab.start_screen(user_id=USER, prediction_id=prediction_id)
        done = await settle(lambda: lab.get_screen(started.id, user_id=USER), limit=12.0)
        assert done.hits[-1].binding_confidence is None

    async def test_a_screen_needs_a_finished_structure(
        self, db: AsyncIOMotorDatabase[dict[str, Any]]
    ) -> None:
        """Screening against a fold that has not landed would submit an empty
        target and bill for the privilege."""

        class Slow(StubEngine):
            async def get_prediction(self, job_id: str) -> PredictionJob:
                return PredictionJob(job_id=job_id, status=JobStatus.running, livemode=True)

        lab, _ = lab_for(db, Slow())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        with pytest.raises(LabError, match="Wait for the structure"):
            await lab.start_screen(user_id=USER, prediction_id=started.id)

    async def test_screening_someone_elses_structure_is_refused(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        lab, prediction_id = await self._folded(db)
        with pytest.raises(LabError, match="does not exist"):
            await lab.start_screen(user_id="intruder", prediction_id=prediction_id)

    async def test_test_mode_results_are_never_joined_to_our_library(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        """Measured: in test mode the engine discards the ids and SMILES we
        submitted and substitutes its own. Matching them back would label a
        random molecule as the control and make a synthetic run look
        validated."""

        class TestMode(StubEngine):
            livemode = False

        lab, _ = lab_for(db, TestMode())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        screen = await lab.start_screen(user_id=USER, prediction_id=started.id)
        done = await settle(lambda: lab.get_screen(screen.id, user_id=USER), limit=12.0)

        assert done.livemode is False
        assert done.control_rank is None
        assert done.control_looks_right is None
        assert not any(h.is_control for h in done.hits)
        assert all(h.known_potency_nm is None for h in done.hits)

    async def test_the_submitted_library_carries_a_positive_control(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        engine = StubEngine()
        lab, _ = lab_for(db, engine)
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        screen = await lab.start_screen(user_id=USER, prediction_id=started.id)
        await settle(lambda: lab.get_screen(screen.id, user_id=USER), limit=12.0)
        # Avibactam is the shipped class A control and must be in what we sent.
        assert any(m.external_id == "CHEMBL1689063" for m in engine.submitted)

    async def test_the_curated_library_is_not_run_through_structural_filters(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        """Measured on the live API: with the recommended filters on, 6 of 9
        molecules were silently discarded — including avibactam, the positive
        control. Those catalogues exist to strip junk out of million-compound
        vendor libraries; ours is nine hand-picked inhibitors, most of them
        licensed medicines."""
        captured: dict[str, Any] = {}

        class Capturing(StubEngine):
            async def start_screen(self, spec: Any, *, idempotency_key: str) -> MoleculeJob:
                captured["filter_level"] = spec.filter_level
                return await super().start_screen(spec, idempotency_key=idempotency_key)

        lab, _ = lab_for(db, Capturing())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        screen = await lab.start_screen(user_id=USER, prediction_id=started.id)
        await settle(lambda: lab.get_screen(screen.id, user_id=USER), limit=12.0)
        assert captured["filter_level"] == "disabled"

    async def test_a_shortfall_between_submitted_and_returned_is_recorded(
        self, db: AsyncIOMotorDatabase[dict[str, Any]], no_download: None
    ) -> None:
        """The engine can drop molecules without saying which. The counts are
        the only trace, so they are kept and shown rather than letting a
        library quietly shrink."""

        class Dropping(StubEngine):
            async def iter_screen_results(self, job_id: str) -> Any:
                yield MoleculeResult(
                    result_id="r1",
                    smiles="CCO",
                    external_id="CHEMBL777",
                    metrics=MoleculeMetrics(binding_confidence=0.5),
                    adme=Adme(),
                )

        lab, _ = lab_for(db, Dropping())
        started = await lab.start_prediction(user_id=USER, sequence=KPC)
        await settle(lambda: lab.get_prediction(started.id, user_id=USER))
        screen = await lab.start_screen(user_id=USER, prediction_id=started.id)
        done = await settle(lambda: lab.get_screen(screen.id, user_id=USER), limit=12.0)

        assert done.submitted > done.returned
        assert done.returned == 1
        # The control was among the dropped, so there is nothing to judge by.
        assert done.control_rank is None
        assert done.control_looks_right is None

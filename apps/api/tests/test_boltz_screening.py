"""Contract tests for the screening adapter (pipeline step 3).

Fixtures are real responses captured from the live API on 2026-11-17 in test
mode, presigned query strings redacted. They encode several behaviours that no
amount of reading the documentation would have revealed, and that would
otherwise have produced confident nonsense in front of a user.

No network. These run everywhere, always.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from biostruct.engines.base import JobStatus
from biostruct.engines.boltz import BoltzHostedEngine
from biostruct.engines.screening import (
    ADME_BATCH_MAX,
    AdmeSpec,
    Molecule,
    ScreeningEngine,
    ScreenSpec,
    ScreenTarget,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boltz"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


KPC = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMS"
    "TFKVLLCGAVLSRIDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLL"
)
TARGET = ScreenTarget(entities=(("A", KPC),))
AVIBACTAM = Molecule("NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O", "avibactam")
DECOY = Molecule("CCO", "ethanol-decoy")


def engine(handler: Any) -> BoltzHostedEngine:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BoltzHostedEngine(api_key="sk_bc_ws_test_x", client=client)


class TestSpecValidation:
    def test_a_screen_needs_molecules(self) -> None:
        with pytest.raises(ValueError, match="at least one molecule"):
            ScreenSpec(target=TARGET, molecules=())

    def test_a_target_needs_a_chain(self) -> None:
        with pytest.raises(ValueError, match="at least one protein chain"):
            ScreenTarget(entities=())

    def test_duplicate_molecule_ids_are_refused(self) -> None:
        """Ids are the only way results join back to the caller's library."""
        with pytest.raises(ValueError, match="unique"):
            ScreenSpec(
                target=TARGET,
                molecules=(AVIBACTAM, Molecule("CCO", "avibactam")),
            )

    def test_a_pocket_must_name_a_chain_in_the_target(self) -> None:
        with pytest.raises(ValueError, match="not in the target"):
            ScreenTarget(entities=(("A", KPC),), pocket_residues={"B": (70,)})

    def test_adme_batch_is_capped(self) -> None:
        many = tuple(Molecule("CCO", f"m{i}") for i in range(ADME_BATCH_MAX + 1))
        with pytest.raises(ValueError, match="at most"):
            AdmeSpec(molecules=many)

    def test_adme_accepts_a_full_batch(self) -> None:
        many = tuple(Molecule("CCO", f"m{i}") for i in range(ADME_BATCH_MAX))
        assert len(AdmeSpec(molecules=many).molecules) == ADME_BATCH_MAX


class TestPayload:
    def test_the_screen_body_is_flat_and_names_no_model(self) -> None:
        """The prediction endpoint wraps in ``input`` and requires ``model``.

        This one does neither, and sending ``model`` makes the union validator
        demand a ``target_profile`` field that does not exist.
        """
        body = BoltzHostedEngine._screen_payload(ScreenSpec(target=TARGET, molecules=(AVIBACTAM,)))
        assert "model" not in body
        assert "input" not in body
        assert set(body) == {"target", "molecules", "molecule_filters"}

    def test_molecule_ids_are_sent_so_results_can_be_matched_back(self) -> None:
        body = BoltzHostedEngine._screen_payload(
            ScreenSpec(target=TARGET, molecules=(AVIBACTAM, DECOY))
        )
        assert body["molecules"] == [
            {"smiles": AVIBACTAM.smiles, "id": "avibactam"},
            {"smiles": "CCO", "id": "ethanol-decoy"},
        ]

    def test_the_known_attack_site_is_passed_through(self) -> None:
        """Omitting the pocket lets the model guess. When step 2 found the
        catalytic site, guessing is strictly worse."""
        target = ScreenTarget(entities=(("A", KPC),), pocket_residues={"A": (70, 69)})
        body = BoltzHostedEngine._screen_payload(ScreenSpec(target=target, molecules=(AVIBACTAM,)))
        assert body["target"]["pocket_residues"] == {"A": [69, 70]}

    def test_the_filter_level_defaults_to_recommended(self) -> None:
        body = BoltzHostedEngine._screen_payload(ScreenSpec(target=TARGET, molecules=(AVIBACTAM,)))
        assert body["molecule_filters"] == {"boltz_smarts_catalog_filter_level": "recommended"}

    def test_adme_wraps_in_input_and_names_its_model(self) -> None:
        body = BoltzHostedEngine._adme_payload(AdmeSpec(molecules=(AVIBACTAM,)))
        assert body["model"] == "adme-v1"
        assert body["input"]["molecules"] == [{"smiles": AVIBACTAM.smiles, "id": "avibactam"}]


class TestScreenJobParsing:
    def test_a_real_captured_job_parses(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job(fixture("screen_retrieve_succeeded.json"))
        assert job.status is JobStatus.succeeded
        assert job.is_terminal
        assert job.job_id.startswith("sm_scr_")
        assert job.pipeline == "boltzmol"
        assert job.pipeline_version == "1.0"
        assert job.livemode is False

    def test_progress_counts_what_the_library_lost(self) -> None:
        """Filtered molecules vanish from results; this count is the only trace."""
        job = BoltzHostedEngine.parse_molecule_job(fixture("screen_retrieve_succeeded.json"))
        assert job.progress.total == 3
        assert job.progress.done == 3
        assert job.progress.failed == 0
        assert job.progress.percent_complete == 100

    def test_a_partly_filtered_run_reports_the_drop(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job(
            {
                "id": "sm_scr_x",
                "status": "succeeded",
                "progress": {
                    "total_molecules_to_screen": 10,
                    "num_molecules_screened": 7,
                    "num_molecules_failed": 3,
                },
            }
        )
        assert job.progress.failed == 3
        assert job.progress.percent_complete == 100

    def test_an_unknown_status_is_not_treated_as_terminal(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job({"id": "sm_scr_x", "status": "reticulating"})
        assert job.status is JobStatus.running
        assert not job.is_terminal


class TestScreenResultParsing:
    def test_a_real_results_page_parses(self) -> None:
        page = fixture("screen_results_page.json")
        results = [BoltzHostedEngine.parse_molecule_result(row) for row in page["data"]]
        assert len(results) == 2
        first = results[0]
        assert first.result_id
        assert first.smiles
        assert first.metrics.binding_confidence is not None
        assert first.metrics.is_rankable

    def test_binding_confidence_survives_the_wire(self) -> None:
        """The headline metric. If this silently became None, every hit list
        in the product would be empty and nothing would raise."""
        page = fixture("screen_results_page.json")
        row = page["data"][0]
        result = BoltzHostedEngine.parse_molecule_result(row)
        assert result.metrics.binding_confidence == pytest.approx(
            row["metrics"]["binding_confidence"]
        )
        assert result.metrics.optimization_score is not None
        assert result.metrics.structure_confidence is not None

    def test_adme_comes_back_with_the_screen(self) -> None:
        """So a separate paid ADME call is only needed for molecules we did
        not screen."""
        page = fixture("screen_results_page.json")
        result = BoltzHostedEngine.parse_molecule_result(page["data"][0])
        assert not result.adme.is_empty
        assert result.adme.solubility in (
            "high-confidence",
            "medium-confidence",
            "high-risk",
        )
        assert result.adme.permeability is not None

    def test_each_hit_carries_a_structure_to_look_at(self) -> None:
        page = fixture("screen_results_page.json")
        result = BoltzHostedEngine.parse_molecule_result(page["data"][0])
        assert result.structure is not None
        assert result.structure.is_presigned

    def test_a_missing_metric_stays_none_rather_than_zero(self) -> None:
        result = BoltzHostedEngine.parse_molecule_result(
            {"id": "r1", "smiles": "CCO", "metrics": {"binding_confidence": 0.9}}
        )
        assert result.metrics.binding_confidence == pytest.approx(0.9)
        assert result.metrics.iptm is None
        assert result.metrics.structure_confidence is None

    def test_a_result_with_no_binding_score_is_not_rankable(self) -> None:
        result = BoltzHostedEngine.parse_molecule_result({"id": "r1", "metrics": {}})
        assert not result.metrics.is_rankable

    def test_warnings_are_kept(self) -> None:
        result = BoltzHostedEngine.parse_molecule_result(
            {"id": "r1", "warnings": [{"code": "c", "message": "odd geometry"}]}
        )
        assert result.warnings[0].message == "odd geometry"


class TestTestModeIsNotRealData:
    """Test mode does not merely fake the numbers — it replaces the molecules.

    We submitted avibactam, penicillin G and an ethanol decoy. The results came
    back as different compounds entirely, with ids ``mol_0``..``mol_2``. So in
    test mode a result cannot be joined to the caller's library by id, by
    position, or even by SMILES. These tests pin that, because a product that
    quietly matched them would show a researcher a hit for a molecule they
    never submitted.
    """

    def test_the_ids_we_sent_do_not_come_back(self) -> None:
        page = fixture("screen_results_page.json")
        ids = {row.get("external_id") for row in page["data"]}
        assert ids and ids.isdisjoint({"avibactam", "penicillin-g", "ethanol-decoy"})

    def test_the_smiles_we_sent_do_not_come_back(self) -> None:
        page = fixture("screen_results_page.json")
        returned = {row.get("smiles") for row in page["data"]}
        assert returned.isdisjoint({AVIBACTAM.smiles, "CCO"})

    def test_the_job_says_it_is_not_live(self) -> None:
        """The one honest signal, and what the product must branch on."""
        job = BoltzHostedEngine.parse_molecule_job(fixture("screen_retrieve_succeeded.json"))
        assert job.livemode is False

    def test_adme_may_return_fewer_molecules_than_were_sent(self) -> None:
        """Three went in, one came back. A 1:1 assumption would crash or,
        worse, pair the wrong numbers with the wrong molecule."""
        raw = fixture("adme_retrieve_succeeded.json")
        sent = len(raw["input"]["molecules"])
        job = BoltzHostedEngine.parse_adme_job(raw)
        assert sent == 3
        assert len(job.results) < sent
        assert job.livemode is False


class TestAdmeParsing:
    def test_a_real_captured_adme_job_parses(self) -> None:
        job = BoltzHostedEngine.parse_adme_job(fixture("adme_retrieve_succeeded.json"))
        assert job.status is JobStatus.succeeded
        assert job.job_id.startswith("adme_pred_")
        assert job.model == "adme-v1"
        assert job.expires_at is not None

    def test_the_three_values_are_read(self) -> None:
        job = BoltzHostedEngine.parse_adme_job(fixture("adme_retrieve_succeeded.json"))
        result = job.results[0]
        assert result.succeeded
        assert not result.adme.is_empty
        assert result.adme.lipophilicity is not None
        assert result.adme.permeability is not None
        assert result.adme.solubility is not None

    def test_there_is_no_toxicity_field_and_none_is_invented(self) -> None:
        """ADME here is solubility, permeability and lipophilicity. Nothing in
        it is a safety check, and the product must never imply otherwise. If
        Boltz adds toxicity, this fails and we find out."""
        raw = json.dumps(fixture("adme_retrieve_succeeded.json")).lower()
        for term in ("herg", "toxic", "hepato", "cardio"):
            assert term not in raw

    def test_a_failed_molecule_does_not_poison_the_batch(self) -> None:
        job = BoltzHostedEngine.parse_adme_job(
            {
                "id": "adme_pred_x",
                "status": "succeeded",
                "output": {
                    "molecules": [
                        {
                            "id": "m0",
                            "status": "failed",
                            "error": {"message": "unparseable SMILES"},
                        },
                        {
                            "id": "m1",
                            "status": "succeeded",
                            "adme": {"solubility": "high-confidence"},
                        },
                    ]
                },
            }
        )
        assert job.status is JobStatus.succeeded
        assert not job.results[0].succeeded
        assert job.results[0].error == "unparseable SMILES"
        assert job.results[1].succeeded


class TestRequests:
    @pytest.mark.anyio
    async def test_screen_estimate_is_priced_per_molecule(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/small-molecule/library-screen/estimate-cost")
            return httpx.Response(200, json=fixture("screen_estimate.json"))

        est = await engine(handler).estimate_screen_cost(
            ScreenSpec(target=TARGET, molecules=(AVIBACTAM, DECOY))
        )
        assert est.unit_usd == Decimal("0.0250")
        assert est.application == "small_molecule_library_screen"
        assert isinstance(est.total_usd, Decimal)

    @pytest.mark.anyio
    async def test_adme_is_cheaper_than_screening(self) -> None:
        """Which is why it is the way to triage a library before paying to
        screen it."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fixture("adme_estimate.json"))

        est = await engine(handler).estimate_adme_cost(AdmeSpec(molecules=(AVIBACTAM,)))
        assert est.unit_usd == Decimal("0.0100")
        assert est.unit_usd < Decimal("0.0250")

    @pytest.mark.anyio
    async def test_screening_without_an_idempotency_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            await engine(lambda r: httpx.Response(200, json={})).start_screen(
                ScreenSpec(target=TARGET, molecules=(AVIBACTAM,)), idempotency_key=""
            )

    @pytest.mark.anyio
    async def test_adme_without_an_idempotency_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            await engine(lambda r: httpx.Response(200, json={})).start_adme(
                AdmeSpec(molecules=(AVIBACTAM,)), idempotency_key=""
            )

    @pytest.mark.anyio
    async def test_results_page_through_the_cursor(self) -> None:
        pages = [
            {
                "data": [{"id": "r1", "smiles": "CCO", "metrics": {}}],
                "has_more": True,
                "last_id": "r1",
            },
            {
                "data": [{"id": "r2", "smiles": "CCC", "metrics": {}}],
                "has_more": False,
                "last_id": "r2",
            },
        ]
        calls: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            after = request.url.params.get("after_id")
            calls.append(after)
            return httpx.Response(200, json=pages[0] if after is None else pages[1])

        got = [r.result_id async for r in engine(handler).iter_screen_results("sm_scr_x")]
        assert got == ["r1", "r2"]
        assert calls == [None, "r1"]

    @pytest.mark.anyio
    async def test_a_stalled_cursor_does_not_spin_forever(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "r1", "metrics": {}}],
                    "has_more": True,
                    "last_id": None,
                },
            )

        got = [r.result_id async for r in engine(handler).iter_screen_results("sm_scr_x")]
        assert got == ["r1"]

    @pytest.mark.anyio
    async def test_duplicate_results_are_dropped(self) -> None:
        pages = [
            {
                "data": [{"id": "r1", "metrics": {}}],
                "has_more": True,
                "last_id": "r1",
            },
            {
                "data": [{"id": "r1", "metrics": {}}, {"id": "r2", "metrics": {}}],
                "has_more": False,
                "last_id": "r2",
            },
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            after = request.url.params.get("after_id")
            return httpx.Response(200, json=pages[0] if after is None else pages[1])

        got = [r.result_id async for r in engine(handler).iter_screen_results("sm_scr_x")]
        assert got == ["r1", "r2"]

    @pytest.mark.anyio
    async def test_the_hosted_engine_satisfies_the_screening_seam(self) -> None:
        assert isinstance(engine(lambda r: httpx.Response(200, json={})), ScreeningEngine)

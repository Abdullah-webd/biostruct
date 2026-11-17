"""Contract tests for de novo molecule design (pipeline step 3b).

A screen asks "does anything we already know about fit this pocket?". A design
run asks "what *would* fit?" and invents it. The results come back in the
identical shape, so everything downstream — ranking, structures, drug-likeness,
export — is shared with screening, and these tests cover the parts that are not.

Fixtures are real responses captured from the live API on 2026-11-17 in test
mode, presigned query strings redacted. No network.
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
    DESIGN_MOLECULES_MAX,
    DESIGN_MOLECULES_MIN,
    ENGINE_DESIGN_MOLECULES_MAX,
    MoleculeDesignSpec,
    ScreenTarget,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boltz"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


KPC = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMS"
    "TFKVLLCGAVLSRIDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLL"
)
#: The catalytic serine neighbourhood — step 2's answer becomes step 3's input.
POCKET = ScreenTarget(entities=(("A", KPC),), pocket_residues={"A": (68, 69, 70, 129, 130)})


def engine(handler: Any) -> BoltzHostedEngine:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return BoltzHostedEngine(api_key="sk_bc_ws_test_x", client=client)


class TestSpecValidation:
    def test_the_engines_floor_is_enforced(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            MoleculeDesignSpec(target=POCKET, num_molecules=DESIGN_MOLECULES_MIN - 1)

    def test_we_cap_far_below_what_the_engine_would_accept(self) -> None:
        """The engine takes a million molecules, which at $0.0250 each is a
        $25,000 request. The budget ledger is the real guard; this is defence
        in depth, and it fails with a sentence rather than a bill."""
        assert DESIGN_MOLECULES_MAX < ENGINE_DESIGN_MOLECULES_MAX
        with pytest.raises(ValueError, match=r"\$25,000"):
            MoleculeDesignSpec(target=POCKET, num_molecules=ENGINE_DESIGN_MOLECULES_MAX)

    def test_the_cap_itself_is_allowed(self) -> None:
        spec = MoleculeDesignSpec(target=POCKET, num_molecules=DESIGN_MOLECULES_MAX)
        assert spec.num_molecules == DESIGN_MOLECULES_MAX

    def test_the_target_is_validated_like_a_screens(self) -> None:
        with pytest.raises(ValueError, match="not in the target"):
            ScreenTarget(entities=(("A", KPC),), pocket_residues={"B": (70,)})


class TestPayload:
    def test_the_body_is_flat_with_a_molecule_count(self) -> None:
        body = BoltzHostedEngine._molecule_design_payload(
            MoleculeDesignSpec(target=POCKET, num_molecules=25)
        )
        assert set(body) == {"target", "num_molecules"}
        assert body["num_molecules"] == 25
        assert "model" not in body
        assert "input" not in body

    def test_the_known_pocket_is_passed_through(self) -> None:
        """Without it the model picks its own pocket, which is strictly worse
        when step 2 already found the catalytic site."""
        body = BoltzHostedEngine._molecule_design_payload(MoleculeDesignSpec(target=POCKET))
        assert body["target"]["pocket_residues"] == {"A": [68, 69, 70, 129, 130]}

    def test_the_target_schema_is_shared_with_screening(self) -> None:
        design = BoltzHostedEngine._molecule_design_payload(MoleculeDesignSpec(target=POCKET))
        screen = BoltzHostedEngine._screen_target_payload(POCKET)
        assert design["target"] == screen


class TestJobParsing:
    def test_a_real_captured_design_job_parses(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job(fixture("design_sm_retrieve_succeeded.json"))
        assert job.status is JobStatus.succeeded
        assert job.is_terminal
        assert job.job_id.startswith("sm_des")
        assert job.livemode is False

    def test_generated_counts_are_read_from_the_design_naming(self) -> None:
        """A screen reports ``num_molecules_screened``; a design run reports
        ``num_molecules_generated``. One progress type reads both."""
        job = BoltzHostedEngine.parse_molecule_job(fixture("design_sm_retrieve_succeeded.json"))
        assert job.progress.total == 10
        assert job.progress.done == 10
        assert job.progress.percent_complete == 100

    def test_a_half_finished_run_reports_partial_progress(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job(
            {
                "id": "sm_des_v2_x",
                "status": "running",
                "progress": {
                    "total_molecules_to_generate": 100,
                    "num_molecules_generated": 25,
                },
            }
        )
        assert job.progress.percent_complete == 25
        assert not job.is_terminal

    def test_an_unknown_status_is_not_treated_as_terminal(self) -> None:
        job = BoltzHostedEngine.parse_molecule_job({"id": "sm_des_v2_x", "status": "reticulating"})
        assert job.status is JobStatus.running
        assert not job.is_terminal


class TestResultParsing:
    def test_invented_molecules_parse_like_screened_ones(self) -> None:
        page = fixture("design_sm_results_page.json")
        results = [BoltzHostedEngine.parse_molecule_result(r) for r in page["data"]]
        assert len(results) == 2
        first = results[0]
        assert first.smiles
        assert first.metrics.is_rankable
        assert first.metrics.binding_confidence is not None
        assert first.structure is not None

    def test_drug_likeness_comes_with_the_design(self) -> None:
        """So a newly invented molecule can be triaged without a second
        paid ADME call."""
        page = fixture("design_sm_results_page.json")
        result = BoltzHostedEngine.parse_molecule_result(page["data"][0])
        assert not result.adme.is_empty
        assert result.adme.solubility is not None

    def test_an_invented_molecule_has_no_external_id(self) -> None:
        """Nothing was submitted, so there is nothing to match back to —
        unlike a screen, where the id is how a hit finds its library entry."""
        page = fixture("design_sm_results_page.json")
        result = BoltzHostedEngine.parse_molecule_result(page["data"][0])
        assert result.external_id == ""

    def test_the_result_shape_is_identical_to_a_screens(self) -> None:
        """This is why design and screening share ranking, export and the
        whole of the UI below them."""
        design = fixture("design_sm_results_page.json")["data"][0]
        screen = fixture("screen_results_page.json")["data"][0]
        assert set(design["metrics"]) == set(screen["metrics"])
        assert set(design["artifacts"]) <= set(screen["artifacts"]) | {"ligand_structure"}


class TestRequests:
    @pytest.mark.anyio
    async def test_design_is_priced_per_molecule(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/small-molecule/design/estimate-cost")
            return httpx.Response(200, json=fixture("design_sm_estimate.json"))

        est = await engine(handler).estimate_design_cost(
            MoleculeDesignSpec(target=POCKET, num_molecules=10)
        )
        assert est.unit_usd == Decimal("0.0250")
        assert est.units == 10
        assert est.total_usd == Decimal("0.2500")
        assert est.application == "small_molecule_design"

    @pytest.mark.anyio
    async def test_designing_without_an_idempotency_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="idempotency_key"):
            await engine(lambda r: httpx.Response(200, json={})).start_molecule_design(
                MoleculeDesignSpec(target=POCKET), idempotency_key=""
            )

    @pytest.mark.anyio
    async def test_submission_reaches_the_design_endpoint(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/small-molecule/design")
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "sm_des_v2_1", "status": "pending"})

        job = await engine(handler).start_molecule_design(
            MoleculeDesignSpec(target=POCKET, num_molecules=10),
            idempotency_key="run-7",
        )
        assert seen["idempotency_key"] == "run-7"
        assert seen["num_molecules"] == 10
        assert job.job_id == "sm_des_v2_1"

    @pytest.mark.anyio
    async def test_design_results_page_through_their_own_endpoint(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "r1", "smiles": "CCO", "metrics": {}}],
                    "has_more": False,
                    "last_id": "r1",
                },
            )

        got = [r.result_id async for r in engine(handler).iter_design_results("sm_des_v2_1")]
        assert got == ["r1"]
        assert paths[0].endswith("/small-molecule/design/sm_des_v2_1/results")

    @pytest.mark.anyio
    async def test_screening_and_design_do_not_share_an_endpoint(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"data": [], "has_more": False})

        eng = engine(handler)
        _ = [r async for r in eng.iter_screen_results("sm_scr_1")]
        _ = [r async for r in eng.iter_design_results("sm_des_1")]
        assert "library-screen" in paths[0]
        assert "small-molecule/design" in paths[1]

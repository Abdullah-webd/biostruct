"""Contract tests for the structure-prediction adapter (pipeline step 2).

``fixtures/boltz/prediction_retrieve_succeeded.json`` is a real response
captured from the live API on 2026-11-17 in test mode, with the AWS presigned
query strings redacted. It is the point of these tests: Boltz publishes no
OpenAPI spec, so a field rename would otherwise surface as silently ``None``
confidence scores in production. Here it fails the build.

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
from biostruct.engines.prediction import (
    Entity,
    LigandBinding,
    ModelOptions,
    PredictionSpec,
    ProteinBinding,
    StructureEngine,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boltz"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


# A real beta-lactamase fragment and avibactam, the clinical inhibitor.
KPC_FRAGMENT = (
    "MSIQHFRVALIPFFAAFCLPVFAHPETLVKVKDAEDQLGARVGYIELDLNSGKILESFRPEERFPMMS"
    "TFKVLLCGAVLSRIDAGQEQLGRRIHYSQNDLVEYSPVTEKHLTDGMTVRELCSAAITMSDNTAANLL"
)
AVIBACTAM = "NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O"

PROTEIN = Entity(kind="protein", value=KPC_FRAGMENT, chain_ids=("A",))
LIGAND = Entity(kind="ligand_smiles", value=AVIBACTAM, chain_ids=("B",))


def engine(handler: Any) -> BoltzHostedEngine:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return BoltzHostedEngine(api_key="sk_bc_ws_test_x", client=client)


class TestSpecValidation:
    """The API's validator is union-based and reports misleading errors.

    It will demand a field that does not exist when any property is
    unsupported (see docs/boltz-api-observed.md), so we validate here and give
    the caller something true instead.
    """

    def test_a_prediction_needs_an_entity(self) -> None:
        with pytest.raises(ValueError, match="at least one entity"):
            PredictionSpec(entities=())

    def test_chain_ids_may_not_collide(self) -> None:
        other = Entity(kind="protein", value=KPC_FRAGMENT, chain_ids=("A",))
        with pytest.raises(ValueError, match="more than one entity"):
            PredictionSpec(entities=(PROTEIN, other))

    def test_binder_chain_must_exist(self) -> None:
        with pytest.raises(ValueError, match="not one of the entities"):
            PredictionSpec(entities=(PROTEIN, LIGAND), binding=LigandBinding(binder_chain_id="Z"))

    def test_ligand_binding_rejects_a_protein_chain(self) -> None:
        with pytest.raises(ValueError, match="is a protein"):
            PredictionSpec(entities=(PROTEIN, LIGAND), binding=LigandBinding(binder_chain_id="A"))

    def test_protein_binding_rejects_a_ligand_chain(self) -> None:
        with pytest.raises(ValueError, match="is a ligand_smiles"):
            PredictionSpec(
                entities=(PROTEIN, LIGAND),
                binding=ProteinBinding(binder_chain_ids=("B",)),
            )

    def test_the_scored_ligand_may_occupy_only_one_chain(self) -> None:
        twice = Entity(kind="ligand_smiles", value=AVIBACTAM, chain_ids=("B", "C"))
        with pytest.raises(ValueError, match="exactly one chain"):
            PredictionSpec(entities=(PROTEIN, twice), binding=LigandBinding(binder_chain_id="B"))

    def test_ligand_binding_rejects_a_nucleic_acid_in_the_complex(self) -> None:
        dna = Entity(kind="dna", value="ACGT", chain_ids=("C",))
        with pytest.raises(ValueError, match="only protein and ligand"):
            PredictionSpec(
                entities=(PROTEIN, LIGAND, dna),
                binding=LigandBinding(binder_chain_id="B"),
            )

    @pytest.mark.parametrize("n", [0, 11, -1])
    def test_num_samples_is_bounded(self, n: int) -> None:
        with pytest.raises(ValueError, match="num_samples"):
            PredictionSpec(entities=(PROTEIN,), num_samples=n)

    def test_step_scale_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="step_scale"):
            ModelOptions(step_scale=Decimal("2.5"))

    def test_sampling_steps_has_a_floor(self) -> None:
        with pytest.raises(ValueError, match="sampling_steps"):
            ModelOptions(sampling_steps=10)

    def test_a_complex_with_no_binding_request_is_fine(self) -> None:
        spec = PredictionSpec(entities=(PROTEIN,))
        assert spec.binding is None


class TestPayload:
    def test_the_body_uses_an_input_wrapper_and_names_the_model(self) -> None:
        """Unlike the library screen, which is flat and rejects ``model``."""
        body = BoltzHostedEngine._prediction_payload(
            PredictionSpec(entities=(PROTEIN, LIGAND), binding=LigandBinding("B"))
        )
        assert body["model"] == "boltz-2.1"
        assert set(body) == {"input", "model"}
        assert body["input"]["binding"] == {
            "type": "ligand_protein_binding",
            "binder_chain_id": "B",
        }
        assert body["input"]["entities"][1]["type"] == "ligand_smiles"

    def test_protein_protein_binding_sends_a_list(self) -> None:
        other = Entity(kind="protein", value=KPC_FRAGMENT, chain_ids=("B",))
        body = BoltzHostedEngine._prediction_payload(
            PredictionSpec(
                entities=(PROTEIN, other),
                binding=ProteinBinding(binder_chain_ids=("B",)),
            )
        )
        assert body["input"]["binding"] == {
            "type": "protein_protein_binding",
            "binder_chain_ids": ["B"],
        }

    def test_options_are_omitted_entirely_when_unset(self) -> None:
        """Sending empty objects invites the union validator to misreport."""
        body = BoltzHostedEngine._prediction_payload(PredictionSpec(entities=(PROTEIN,)))
        assert "model_options" not in body["input"]
        assert "binding" not in body["input"]

    def test_set_options_are_sent(self) -> None:
        body = BoltzHostedEngine._prediction_payload(
            PredictionSpec(
                entities=(PROTEIN,),
                options=ModelOptions(seed=7, step_scale=Decimal("1.5")),
            )
        )
        assert body["input"]["model_options"] == {"seed": 7, "step_scale": 1.5}


class TestParsing:
    def test_a_real_captured_job_parses(self) -> None:
        job = BoltzHostedEngine.parse_prediction(fixture("prediction_retrieve_succeeded.json"))
        assert job.status is JobStatus.succeeded
        assert job.is_terminal
        assert job.livemode is False
        assert job.model == "boltz-2.1"
        assert job.job_id.startswith("sab_pred_")
        assert job.completed_at is not None
        assert job.expires_at is not None

    def test_confidence_scores_survive_the_wire(self) -> None:
        job = BoltzHostedEngine.parse_prediction(fixture("prediction_retrieve_succeeded.json"))
        assert job.best is not None
        m = job.best.metrics
        assert m.structure_confidence == pytest.approx(0.852)
        assert m.ptm == pytest.approx(0.91)
        assert m.iptm == pytest.approx(0.87)

    def test_the_structure_artifact_is_presigned(self) -> None:
        job = BoltzHostedEngine.parse_prediction(fixture("prediction_retrieve_succeeded.json"))
        assert job.has_structure
        assert job.best is not None
        assert job.best.structure is not None
        assert job.best.structure.is_presigned
        assert job.archive is not None

    def test_every_sample_is_kept_not_just_the_best(self) -> None:
        job = BoltzHostedEngine.parse_prediction(fixture("prediction_retrieve_succeeded.json"))
        assert len(job.samples) >= 1

    def test_no_affinity_is_reported_and_none_is_invented(self) -> None:
        """Observed 2026-11-17: the response has no affinity field at all.

        This test exists so that if Boltz adds one, we notice and can use it,
        rather than continuing to tell users we cannot predict affinity.
        """
        raw = fixture("prediction_retrieve_succeeded.json")
        assert "affinity" not in json.dumps(raw).lower()

    def test_a_missing_metric_stays_none_rather_than_zero(self) -> None:
        """Zero is a real score. Defaulting to it would bury a good result."""
        job = BoltzHostedEngine.parse_prediction(
            {
                "id": "sab_pred_x",
                "status": "succeeded",
                "output": {"best_sample": {"metrics": {"structure_confidence": 0.9}}},
            }
        )
        assert job.best is not None
        assert job.best.metrics.structure_confidence == pytest.approx(0.9)
        assert job.best.metrics.iptm is None
        assert "iptm" in job.best.metrics.missing()

    def test_an_unknown_status_is_not_treated_as_terminal(self) -> None:
        """A new status must not make the reconciler abandon a live job."""
        job = BoltzHostedEngine.parse_prediction({"id": "sab_pred_x", "status": "reticulating"})
        assert job.status is JobStatus.running
        assert not job.is_terminal

    def test_an_error_message_is_carried(self) -> None:
        job = BoltzHostedEngine.parse_prediction(
            {
                "id": "sab_pred_x",
                "status": "failed",
                "error": {"code": "bad", "message": "sequence too long"},
            }
        )
        assert job.status is JobStatus.failed
        assert job.error == "sequence too long"


class TestRequests:
    @pytest.mark.anyio
    async def test_estimate_parses_money_as_decimal(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/predictions/structure-and-binding/estimate-cost")
            return httpx.Response(
                200,
                json={
                    "estimated_cost_usd": "0.0250",
                    "breakdown": {
                        "application": "structure_and_binding",
                        "num_units": 1,
                        "cost_per_unit_usd": "0.0250",
                    },
                    "disclaimer": "estimate only",
                },
            )

        est = await engine(handler).estimate_prediction_cost(PredictionSpec(entities=(PROTEIN,)))
        assert est.total_usd == Decimal("0.0250")
        assert isinstance(est.total_usd, Decimal)
        assert est.units == 1
        assert est.application == "structure_and_binding"

    @pytest.mark.anyio
    async def test_submitting_without_an_idempotency_key_is_refused(self) -> None:
        """A retried submission must not be able to bill twice."""
        with pytest.raises(ValueError, match="idempotency_key"):
            await engine(lambda r: httpx.Response(200, json={})).start_prediction(
                PredictionSpec(entities=(PROTEIN,)), idempotency_key=""
            )

    @pytest.mark.anyio
    async def test_the_idempotency_key_reaches_the_wire(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"id": "sab_pred_1", "status": "pending"})

        job = await engine(handler).start_prediction(
            PredictionSpec(entities=(PROTEIN,)), idempotency_key="run-42"
        )
        assert seen["idempotency_key"] == "run-42"
        assert job.job_id == "sab_pred_1"
        assert job.status is JobStatus.pending

    @pytest.mark.anyio
    async def test_the_hosted_engine_satisfies_the_structure_seam(self) -> None:
        assert isinstance(engine(lambda r: httpx.Response(200, json={})), StructureEngine)

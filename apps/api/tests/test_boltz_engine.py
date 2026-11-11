"""Contract tests for the Boltz adapter.

The fixtures in ``tests/fixtures/boltz/`` are real responses captured from the
live API on 2026-11-11 (test mode), with the AWS presigned query strings
redacted. They are the point of these tests: Boltz publishes no OpenAPI spec and
its SDK is generated, so a field rename would otherwise surface as silently
``None`` metrics in production. Here it fails the build instead.

No network. These run everywhere, always.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from biostruct.engines import (
    BoltzGenLocalEngine,
    BoltzHostedEngine,
    CuratedBinder,
    DesignEngine,
    DesignRules,
    DesignSpec,
    EngineRejected,
    EngineUnavailable,
    JobStatus,
    SequenceTarget,
    StructureTarget,
    TargetChain,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boltz"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


KPC2_EPITOPE = (67, 68, 69, 70, 71, 128, 129, 130, 164, 165, 166, 167, 168, 169, 170)


def kpc2_spec(num_proteins: int = 10) -> DesignSpec:
    return DesignSpec(
        target=StructureTarget(
            structure_url="https://files.rcsb.org/download/2OV5.cif",
            chains=(TargetChain(chain_id="A", epitope_residues=KPC2_EPITOPE),),
        ),
        binder=CuratedBinder(
            kind="nanobody",
            rules=DesignRules(
                excluded_amino_acids=("C",),
                excluded_sequence_motifs=("NXS", "NXT"),
                max_hydrophobic_fraction=Decimal("0.45"),
            ),
        ),
        num_proteins=num_proteins,
    )


def engine_with(handler: Any) -> BoltzHostedEngine:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return BoltzHostedEngine("sk_bc_ws_test_fake", client=client)


# --- the seam itself -------------------------------------------------------


class TestProtocolConformance:
    def test_hosted_engine_satisfies_the_protocol(self) -> None:
        assert isinstance(BoltzHostedEngine("sk_bc_ws_test_fake"), DesignEngine)

    def test_local_stub_is_present_but_refuses(self) -> None:
        """ADR-002 keeps this seam occupied so the on-prem constraint stays visible."""
        assert BoltzGenLocalEngine().name == "boltzgen-local"

    async def test_local_stub_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="not implemented"):
            await BoltzGenLocalEngine().estimate_cost(kpc2_spec())

    def test_api_key_is_required(self) -> None:
        with pytest.raises(ValueError, match="API key"):
            BoltzHostedEngine("")


# --- input validation ------------------------------------------------------


class TestSpecValidation:
    def test_num_proteins_floor_is_enforced(self) -> None:
        """The API's minimum is 10; catching it locally saves a round trip."""
        with pytest.raises(ValueError, match="between 10"):
            kpc2_spec(num_proteins=9)

    def test_num_proteins_ceiling_is_enforced(self) -> None:
        """1,000,000 x $0.05 is a $50,000 typo. This is the first of three guards."""
        with pytest.raises(ValueError, match="between 10"):
            kpc2_spec(num_proteins=1_000_001)

    def test_epitope_and_non_binding_cannot_overlap(self) -> None:
        with pytest.raises(ValueError, match="both epitope and non-binding"):
            TargetChain(chain_id="A", epitope_residues=(5, 6), non_binding_residues=(6, 7))

    def test_epitope_must_be_within_the_crop(self) -> None:
        with pytest.raises(ValueError, match="not in crop_residues"):
            TargetChain(chain_id="A", crop_residues=(1, 2, 3), epitope_residues=(99,))

    def test_negative_residue_indices_rejected(self) -> None:
        """Indices are 0-indexed; a negative one usually means 1-based input."""
        with pytest.raises(ValueError, match="0-indexed"):
            TargetChain(chain_id="A", epitope_residues=(-1,))

    def test_structure_target_needs_exactly_one_source(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            StructureTarget(chains=(TargetChain(chain_id="A"),))
        with pytest.raises(ValueError, match="exactly one"):
            StructureTarget(
                structure_url="https://x/y.cif",
                structure_cif_base64="abc",
                chains=(TargetChain(chain_id="A"),),
            )

    def test_structure_target_needs_a_chain(self) -> None:
        with pytest.raises(ValueError, match="at least one chain"):
            StructureTarget(structure_url="https://x/y.cif")

    def test_rules_reject_a_bad_amino_acid_code(self) -> None:
        with pytest.raises(ValueError, match="one upper-case letter"):
            DesignRules(excluded_amino_acids=("Cys",))

    def test_rules_reject_an_out_of_range_fraction(self) -> None:
        with pytest.raises(ValueError, match="max_hydrophobic_fraction"):
            DesignRules(max_hydrophobic_fraction=Decimal("1.5"))


# --- request shaping -------------------------------------------------------


class TestRequestPayload:
    def test_structure_target_matches_the_captured_request(self) -> None:
        """Compare against the request body that the live API accepted."""
        captured = fixture("design_start_request.json")
        built = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(kpc2_spec())
        assert built["target"]["type"] == captured["target"]["type"]
        assert built["target"]["structure"] == captured["target"]["structure"]
        assert built["target"]["chain_selection"] == captured["target"]["chain_selection"]
        assert built["binder_specification"] == captured["binder_specification"]
        assert built["num_proteins"] == captured["num_proteins"]

    def test_rcsb_url_is_passed_through_untouched(self) -> None:
        """Verified live: Boltz fetches a raw RCSB CIF URL itself, so there is no
        upload step between PDB and design."""
        payload = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(kpc2_spec())
        assert payload["target"]["structure"] == {
            "type": "url",
            "url": "https://files.rcsb.org/download/2OV5.cif",
        }

    def test_residues_are_sorted_and_deduplicated(self) -> None:
        spec = DesignSpec(
            target=StructureTarget(
                structure_url="https://x/y.cif",
                chains=(TargetChain(chain_id="A", epitope_residues=(9, 3, 3, 1)),),
            ),
            binder=CuratedBinder(),
            num_proteins=10,
        )
        payload = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(spec)
        assert payload["target"]["chain_selection"]["A"]["epitope_residues"] == [1, 3, 9]

    def test_sequence_target_shape(self) -> None:
        spec = DesignSpec(
            target=SequenceTarget(sequences=(("A", "MSLYRRLVLLSCLSWPLAGFSATALTN"),)),
            binder=CuratedBinder(),
            num_proteins=10,
        )
        payload = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(spec)
        assert payload["target"]["type"] == "no_template"
        assert payload["target"]["entities"][0]["chain_ids"] == ["A"]

    def test_empty_rules_are_omitted_rather_than_sent_empty(self) -> None:
        spec = DesignSpec(
            target=SequenceTarget(sequences=(("A", "MSLY"),)),
            binder=CuratedBinder(),
            num_proteins=10,
        )
        payload = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(spec)
        assert "rules" not in payload["binder_specification"]

    def test_curated_binder_name_is_prefixed(self) -> None:
        for kind, expected in (("nanobody", "boltz_nanobody"), ("antibody", "boltz_antibody")):
            spec = DesignSpec(
                target=SequenceTarget(sequences=(("A", "MSLY"),)),
                binder=CuratedBinder(kind=kind),  # type: ignore[arg-type]
                num_proteins=10,
            )
            payload = BoltzHostedEngine("sk_bc_ws_test_fake")._design_payload(spec)
            assert payload["binder_specification"]["binder"] == expected


# --- response parsing ------------------------------------------------------


class TestJobParsing:
    def test_parses_the_captured_start_response(self) -> None:
        job = BoltzHostedEngine.parse_job(fixture("design_start.json"))
        assert job.job_id.startswith("prot_des_")
        assert job.status is JobStatus.pending
        assert job.livemode is False
        assert job.total_to_generate == 10
        assert job.generated == 0
        assert job.engine == "boltzprot"

    def test_parses_the_captured_succeeded_response(self) -> None:
        job = BoltzHostedEngine.parse_job(fixture("design_retrieve_succeeded.json"))
        assert job.status is JobStatus.succeeded
        assert job.status.is_terminal is True
        assert job.generated == 10
        assert job.percent_complete == 100
        assert job.completed_at is not None
        assert job.error is None

    def test_test_mode_runs_are_flagged_as_not_live(self) -> None:
        """Synthetic results must never be presented as science, so this flag
        travels from the API all the way to the UI."""
        assert BoltzHostedEngine.parse_job(fixture("design_start.json")).livemode is False

    def test_missing_livemode_defaults_to_test(self) -> None:
        """The dangerous default is assuming results are real."""
        assert BoltzHostedEngine.parse_job({"id": "x", "status": "pending"}).livemode is False

    def test_unknown_status_is_treated_as_running_not_a_crash(self) -> None:
        """A new status value must not take the reconciler down; the next poll
        re-reads the job."""
        job = BoltzHostedEngine.parse_job({"id": "x", "status": "quantum-superposition"})
        assert job.status is JobStatus.running
        assert job.status.is_terminal is False

    def test_percent_complete_is_none_without_a_total(self) -> None:
        job = BoltzHostedEngine.parse_job({"id": "x", "status": "running"})
        assert job.percent_complete is None

    def test_failed_job_carries_its_error(self) -> None:
        job = BoltzHostedEngine.parse_job(
            {"id": "x", "status": "failed", "error": "target chain not found"}
        )
        assert job.status is JobStatus.failed
        assert job.error == "target chain not found"


class TestResultParsing:
    def test_parses_every_result_in_the_captured_page(self) -> None:
        page = fixture("design_results_page.json")
        results = [BoltzHostedEngine.parse_result(r) for r in page["data"]]
        assert len(results) == 3
        assert all(r.result_id for r in results)

    def test_metric_names_match_the_live_api(self) -> None:
        """If Boltz renames a metric, this is where we find out."""
        first = fixture("design_results_page.json")["data"][0]
        m = BoltzHostedEngine.parse_result(first).metrics
        assert m.binding_confidence is not None
        assert m.iptm is not None
        assert m.min_interaction_pae is not None
        assert m.structure_confidence > 0
        assert m.helix_fraction is not None
        assert m.sheet_fraction is not None
        assert m.loop_fraction is not None

    def test_ipsae_min_absent_is_tolerated(self) -> None:
        """It was absent from every result in the verification run. Ranking must
        redistribute its weight, never default it to zero."""
        first = fixture("design_results_page.json")["data"][0]
        m = BoltzHostedEngine.parse_result(first).metrics
        assert m.ipsae_min is None
        assert "ipsae_min" in m.missing_binding_metrics

    def test_binder_is_identified_by_metrics_not_a_type_field(self) -> None:
        """The live API omits the ``type`` discriminator the SDK declares, so
        presence of binding metrics is what tells us this is a binder result."""
        first = fixture("design_results_page.json")["data"][0]
        assert "type" not in first
        assert BoltzHostedEngine.parse_result(first).metrics.is_binder is True

    def test_generic_result_without_binding_metrics_is_not_a_binder(self) -> None:
        result = BoltzHostedEngine.parse_result(
            {"id": "r1", "metrics": {"structure_confidence": 0.8, "helix_fraction": 0.3}}
        )
        assert result.metrics.is_binder is False

    def test_sequences_and_chains_are_extracted(self) -> None:
        first = fixture("design_results_page.json")["data"][0]
        result = BoltzHostedEngine.parse_result(first)
        assert len(result.protein_entities) == 2
        for entity in result.protein_entities:
            assert entity.chain_ids
            assert entity.value.isalpha(), "sequence should be plain amino-acid letters"

    def test_artifacts_are_recognised_as_expiring(self) -> None:
        """url_expires_at is why every artifact is mirrored at harvest time
        (ADR-005). Observed window: about 30 minutes."""
        first = fixture("design_results_page.json")["data"][0]
        result = BoltzHostedEngine.parse_result(first)
        assert result.structure is not None
        assert result.structure.is_presigned is True
        assert result.archive is not None
        assert result.archive.expires_at is not None

    def test_raw_payload_is_retained_verbatim(self) -> None:
        """Keeping the raw payload means an SDK change never forces us to re-run
        a job we already paid for."""
        first = fixture("design_results_page.json")["data"][0]
        assert BoltzHostedEngine.parse_result(first).raw == first

    def test_result_with_no_artifacts_parses_to_none(self) -> None:
        result = BoltzHostedEngine.parse_result({"id": "r", "metrics": {}})
        assert result.structure is None
        assert result.archive is None

    def test_warnings_are_parsed(self) -> None:
        result = BoltzHostedEngine.parse_result(
            {
                "id": "r",
                "metrics": {"structure_confidence": 0.5},
                "warnings": [{"code": "low_confidence", "message": "below threshold"}],
            }
        )
        assert result.warnings[0].code == "low_confidence"


# --- pagination ------------------------------------------------------------


class TestResultPagination:
    async def test_pages_with_the_cursor_until_exhausted(self) -> None:
        """Cursor pagination, verified live: after_id / has_more / last_id."""
        pages = [
            {"data": [{"id": "a"}, {"id": "b"}], "has_more": True, "last_id": "b"},
            {"data": [{"id": "c"}], "has_more": False, "last_id": "c"},
        ]
        seen_cursors: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_cursors.append(request.url.params.get("after_id"))
            return httpx.Response(200, json=pages[len(seen_cursors) - 1])

        engine = engine_with(handler)
        ids = [r.result_id async for r in engine.iter_results("job1", page_size=2)]
        assert ids == ["a", "b", "c"]
        assert seen_cursors == [None, "b"], "second page must be fetched after_id=b"

    async def test_stops_when_the_cursor_does_not_advance(self) -> None:
        """A server that always returns the same last_id would otherwise spin forever."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"id": "a"}], "has_more": True, "last_id": "a"}
            )

        engine = engine_with(handler)
        ids = [r.result_id async for r in engine.iter_results("job1")]
        assert ids == ["a"]

    async def test_empty_page_terminates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [], "has_more": True})

        engine = engine_with(handler)
        assert [r async for r in engine.iter_results("job1")] == []


# --- cost ------------------------------------------------------------------


class TestCostEstimate:
    async def test_parses_money_as_decimal_from_strings(self) -> None:
        """Verified live: costs arrive as strings. Floats would drift the budget."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "estimated_cost_usd": "0.5000",
                    "breakdown": {
                        "application": "protein_design",
                        "num_units": 10,
                        "cost_per_unit_usd": "0.0500",
                    },
                    "disclaimer": "This is an estimate only",
                },
            )

        estimate = await engine_with(handler).estimate_cost(kpc2_spec())
        assert estimate.total_usd == Decimal("0.5000")
        assert estimate.unit_usd == Decimal("0.0500")
        assert isinstance(estimate.total_usd, Decimal)
        assert estimate.units == 10
        assert estimate.disclaimer

    async def test_unit_price_matches_the_published_tier(self) -> None:
        """$0.05/design for a KPC-2-sized target (257-512 crop tier)."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "estimated_cost_usd": "5.0000",
                    "breakdown": {"num_units": 100, "cost_per_unit_usd": "0.0500"},
                },
            )

        estimate = await engine_with(handler).estimate_cost(kpc2_spec(100))
        assert estimate.unit_usd == Decimal("0.0500")
        assert estimate.total_usd == estimate.unit_usd * 100


# --- submission and error handling -----------------------------------------


class TestSubmission:
    async def test_idempotency_key_is_sent(self) -> None:
        """Without it, a retried submission starts and bills a second run."""
        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=fixture("design_start.json"))

        await engine_with(handler).start_design(kpc2_spec(), idempotency_key="run-abc")
        assert bodies[0]["idempotency_key"] == "run-abc"

    async def test_submission_without_an_idempotency_key_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not reach the network")

        with pytest.raises(ValueError, match="idempotency_key"):
            await engine_with(handler).start_design(kpc2_spec(), idempotency_key="")

    async def test_auth_header_is_the_api_key(self) -> None:
        headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers.append(request.headers.get("x-api-key", ""))
            return httpx.Response(200, json={"id": "x", "status": "pending"})

        await engine_with(handler).get_job("x")
        assert headers[0] == "sk_bc_ws_test_fake"


class TestErrorMapping:
    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    async def test_server_errors_are_retryable(self, status_code: int) -> None:
        """EngineUnavailable must never cause the reconciler to fail a run: the
        job may well be fine, and only Boltz can declare it failed."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, text="upstream boom")

        with pytest.raises(EngineUnavailable):
            await engine_with(handler).get_job("job1")

    async def test_rate_limit_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"message": "slow down"})

        with pytest.raises(EngineUnavailable):
            await engine_with(handler).get_job("job1")

    async def test_transport_failure_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(EngineUnavailable):
            await engine_with(handler).get_job("job1")

    async def test_client_errors_are_not_retryable_and_keep_their_detail(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"message": "query.type is required", "code": "VALIDATION_ERROR"},
            )

        with pytest.raises(EngineRejected) as exc:
            await engine_with(handler).get_job("job1")
        assert exc.value.status_code == 400
        assert exc.value.code == "VALIDATION_ERROR"
        assert "query.type is required" in str(exc.value)

    async def test_forbidden_is_reported_plainly(self) -> None:
        """Observed: /admin/usage needs an admin key, workspace keys get 403."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, json={"code": "forbidden", "message": "Admin API key required"}
            )

        with pytest.raises(EngineRejected) as exc:
            await engine_with(handler).get_job("job1")
        assert exc.value.status_code == 403


class TestIdentity:
    async def test_whoami_reports_the_key_mode(self) -> None:
        """The startup guard depends on this: a live key in dev spends real
        credits, a test key in prod fabricates science."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "principal_type": "api_key",
                    "key_type": "workspace",
                    "mode": "test",
                    "workspace_id": "ws_abc",
                    "organization_name": "Example Lab",
                },
            )

        identity = await engine_with(handler).whoami()
        assert identity.mode == "test"
        assert identity.is_live is False
        assert identity.key_type == "workspace"
        assert identity.organization_name == "Example Lab"

    async def test_unrecognised_mode_is_unknown_not_live(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"key_type": "workspace", "mode": "sandbox"})

        identity = await engine_with(handler).whoami()
        assert identity.mode == "unknown"
        assert identity.is_live is False

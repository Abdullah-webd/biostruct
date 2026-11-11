"""Design-run endpoints, with the spending path under test.

This is the first place real money can be spent, so the guards get the most
attention: a confirmed price that must match a fresh estimate, an atomic budget
reservation, a cap well below what the engine would accept, and a per-hour
limit on submissions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from biostruct.engines.base import EngineRejected, EngineUnavailable
from biostruct.models.run import NUM_PROTEINS_UI_MAX, RunStatus

from .conftest import Api
from .stubs import FakeEngine, StubSources

GOOD_PASSWORD = "ribosome velvet cartography"
KPC = {"name": "Carbapenem-resistant Klebsiella pneumoniae"}
EPITOPE = {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 73, 130, 166]}


@pytest.fixture
def engine(client) -> FakeEngine:
    """Swap the app's design engine for a controllable fake."""
    app = client._transport.app
    real = app.state.engine
    fake = FakeEngine()
    app.state.engine = fake
    try:
        yield fake
    finally:
        app.state.engine = real


async def ready_project(api: Api, *, budget: str = "25") -> tuple[str, str]:
    """A project with a target whose epitope is already chosen."""
    assert (await api.register(password=GOOD_PASSWORD)).status_code == 201
    project = await api.post("/projects", {"name": "KPC-2", "infection": KPC, "budget_usd": budget})
    project_id = project.json()["id"]
    target = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
    target_id = target.json()["id"]
    epitope = await api.put(f"/projects/{project_id}/targets/{target_id}/epitope", EPITOPE)
    assert epitope.status_code == 200, epitope.text
    return project_id, target_id


class TestEstimate:
    async def test_estimate_reports_cost_and_budget(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target_id, "num_proteins": 100},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert Decimal(body["total_usd"]) == Decimal("5.00")
        assert Decimal(body["unit_usd"]) == Decimal("0.05")
        assert body["units"] == 100
        assert Decimal(body["budget_remaining_usd"]) == Decimal("25")
        assert body["within_budget"] is True

    async def test_estimate_flags_an_unaffordable_run(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api, budget="1")
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target_id, "num_proteins": 100},
        )
        assert r.json()["within_budget"] is False

    async def test_estimate_requires_an_epitope(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """Without one there is no surface to aim at."""
        assert (await api.register(password=GOOD_PASSWORD)).status_code == 201
        project = await api.post("/projects", {"name": "P", "infection": KPC})
        project_id = project.json()["id"]
        target = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target.json()["id"], "num_proteins": 10},
        )
        assert r.status_code == 409
        assert r.json()["type"].endswith("/epitope_required")

    async def test_num_proteins_is_capped_well_below_the_engine_maximum(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """The engine accepts 1,000,000, which at $0.05 each is a $50,000 typo."""
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target_id, "num_proteins": NUM_PROTEINS_UI_MAX + 1},
        )
        assert r.status_code == 422

    async def test_num_proteins_floor_is_enforced(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target_id, "num_proteins": 9},
        )
        assert r.status_code == 422

    async def test_engine_outage_is_a_503(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)

        async def boom(spec):
            raise EngineUnavailable("engine down")

        engine.estimate_cost = boom  # type: ignore[method-assign]
        r = await api.post(
            f"/projects/{project_id}/runs/estimate",
            {"target_id": target_id, "num_proteins": 10},
        )
        assert r.status_code == 503

    async def test_epitope_is_sent_as_design_indices(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """The converted 0-based indices, not the author numbers."""
        project_id, target_id = await ready_project(api)
        await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        spec, _ = engine.started[0]
        chain = spec.target.chains[0]  # type: ignore[union-attr]
        assert chain.epitope_residues == (39, 42, 99, 135)
        assert chain.chain_id == "A"

    async def test_rcsb_url_goes_straight_to_the_engine(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        spec, _ = engine.started[0]
        assert spec.target.structure_url == "https://files.rcsb.org/download/2OV5.cif"  # type: ignore[union-attr]


class TestSubmissionGuards:
    async def test_submission_reserves_the_budget(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 100},
                "confirmed_cost_usd": "5.00",
            },
        )
        assert r.status_code == 202, r.text
        budget = (await api.get(f"/projects/{project_id}/budget")).json()
        assert Decimal(budget["reserved_usd"]) == Decimal("5.00")
        assert Decimal(budget["remaining_usd"]) == Decimal("20.00")

    async def test_a_stale_confirmed_price_is_refused(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """The researcher must have seen the price being charged."""
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 100},
                "confirmed_cost_usd": "1.00",  # not what it costs
            },
        )
        assert r.status_code == 409
        assert r.json()["type"].endswith("/cost_estimate_stale")
        assert "5.00" in r.json()["detail"]

    async def test_nothing_is_submitted_when_the_price_is_stale(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 100},
                "confirmed_cost_usd": "1.00",
            },
        )
        assert engine.started == []
        budget = (await api.get(f"/projects/{project_id}/budget")).json()
        assert Decimal(budget["reserved_usd"]) == Decimal("0")

    async def test_a_run_over_budget_is_refused_with_the_numbers(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api, budget="1")
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 100},
                "confirmed_cost_usd": "5.00",
            },
        )
        assert r.status_code == 402
        body = r.json()
        assert body["type"].endswith("/budget_exceeded")
        assert Decimal(body["remaining_usd"]) == Decimal("1")
        assert engine.started == [], "must not submit a run it cannot pay for"

    async def test_float_cost_is_rejected(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """Money must arrive as a string, so it cannot drift (ADR-017)."""
        project_id, target_id = await ready_project(api)
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": 0.5,
            },
        )
        assert r.status_code == 422

    async def test_submission_is_rate_limited_per_hour(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """Bounds the blast radius of a loop bug."""
        project_id, target_id = await ready_project(api, budget="1000")
        statuses = []
        for _ in range(7):
            r = await api.post(
                f"/projects/{project_id}/runs",
                {
                    "request": {"target_id": target_id, "num_proteins": 10},
                    "confirmed_cost_usd": "0.50",
                },
            )
            statuses.append(r.status_code)
        assert statuses.count(202) == 5
        assert 429 in statuses

    async def test_submission_failure_releases_the_reservation(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)

        async def boom(spec, *, idempotency_key):
            raise EngineRejected("bad spec", status_code=400)

        engine.start_design = boom  # type: ignore[method-assign]
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 100},
                "confirmed_cost_usd": "5.00",
            },
        )
        assert r.status_code == 202
        assert r.json()["status"] == RunStatus.submit_failed.value
        budget = (await api.get(f"/projects/{project_id}/budget")).json()
        assert Decimal(budget["reserved_usd"]) == Decimal("0")

    async def test_livemode_is_recorded_from_the_engine(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        engine.livemode = False
        r = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        assert r.json()["livemode"] is False


class TestIdempotency:
    async def test_the_same_key_does_not_start_a_second_run(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """A retried submission must not bill twice."""
        project_id, target_id = await ready_project(api)
        body = {
            "request": {"target_id": target_id, "num_proteins": 100},
            "confirmed_cost_usd": "5.00",
        }
        headers = {**await api._headers(), "idempotency-key": "fixed-key-1"}
        first = await api.c.post(f"/api/v1/projects/{project_id}/runs", json=body, headers=headers)
        second = await api.c.post(f"/api/v1/projects/{project_id}/runs", json=body, headers=headers)
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert len(engine.started) == 1, "the engine must be called only once"

    async def test_a_duplicate_submission_does_not_double_reserve(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        body = {
            "request": {"target_id": target_id, "num_proteins": 100},
            "confirmed_cost_usd": "5.00",
        }
        headers = {**await api._headers(), "idempotency-key": "fixed-key-2"}
        for _ in range(3):
            await api.c.post(f"/api/v1/projects/{project_id}/runs", json=body, headers=headers)
        budget = (await api.get(f"/projects/{project_id}/budget")).json()
        assert Decimal(budget["reserved_usd"]) == Decimal("5.00")

    async def test_the_key_is_passed_to_the_engine(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        headers = {**await api._headers(), "idempotency-key": "my-key"}
        await api.c.post(
            f"/api/v1/projects/{project_id}/runs",
            json={
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
            headers=headers,
        )
        assert engine.started[0][1] == "my-key"


class TestReadsAndControl:
    async def test_run_is_listed_and_fetchable(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        created = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        run_id = created.json()["id"]
        assert (await api.get(f"/projects/{project_id}/runs/{run_id}")).status_code == 200
        listed = (await api.get(f"/projects/{project_id}/runs")).json()
        assert [r["id"] for r in listed["items"]] == [run_id]

    async def test_another_users_run_is_404(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        created = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        run_id = created.json()["id"]
        await api.logout()
        await api.register(email="other@example.org", password=GOOD_PASSWORD)
        assert (await api.get(f"/projects/{project_id}/runs/{run_id}")).status_code == 404

    async def test_stop_reaches_the_engine(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, target_id = await ready_project(api)
        created = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        run_id = created.json()["id"]
        r = await api.post(f"/projects/{project_id}/runs/{run_id}/stop")
        assert r.status_code == 200
        assert engine.stopped == ["job_1"]

    async def test_candidates_report_livemode(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        """So a UI cannot present synthetic results as findings."""
        project_id, target_id = await ready_project(api)
        created = await api.post(
            f"/projects/{project_id}/runs",
            {
                "request": {"target_id": target_id, "num_proteins": 10},
                "confirmed_cost_usd": "0.50",
            },
        )
        run_id = created.json()["id"]
        r = await api.get(f"/projects/{project_id}/runs/{run_id}/candidates")
        assert r.status_code == 200
        body = r.json()
        assert body["livemode"] is False
        assert body["total"] == 0  # nothing harvested yet

    async def test_budget_endpoint_reports_the_breakdown(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        project_id, _ = await ready_project(api)
        body = (await api.get(f"/projects/{project_id}/budget")).json()
        assert Decimal(body["limit_usd"]) == Decimal("25")
        assert body["should_warn"] is False

    async def test_requires_authentication(
        self, api: Api, stub_sources: StubSources, engine: FakeEngine
    ) -> None:
        assert (await api.get("/projects/x/runs")).status_code == 401

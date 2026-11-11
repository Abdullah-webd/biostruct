"""Projects CRUD, tenant isolation, and the money-handling contract."""

from __future__ import annotations

from decimal import Decimal

from biostruct import security

from .conftest import Api

GOOD_PASSWORD = "ribosome velvet cartography"
KPC = {
    "name": "Carbapenem-resistant Klebsiella pneumoniae",
    "pathogen": "Klebsiella pneumoniae",
    "amr_context": "KPC-2 carbapenemase; NDM-1 and OXA-181 common in southern Africa.",
}


async def _signed_in(api: Api, email: str = "researcher@example.org") -> str:
    r = await api.register(email=email, password=GOOD_PASSWORD)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


class TestCreate:
    async def test_create_returns_the_project(self, api: Api) -> None:
        await _signed_in(api)
        r = await api.post("/projects", {"name": "KPC-2 binders", "infection": KPC})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "KPC-2 binders"
        assert body["infection"]["pathogen"] == "Klebsiella pneumoniae"
        assert body["id"]

    async def test_default_budget_is_applied(self, api: Api) -> None:
        await _signed_in(api)
        r = await api.post("/projects", {"name": "P", "infection": KPC})
        budget = r.json()["budget"]
        assert Decimal(str(budget["limit_usd"])) == Decimal("25")
        assert Decimal(str(budget["spent_usd"])) == Decimal("0")
        assert Decimal(str(budget["reserved_usd"])) == Decimal("0")

    async def test_explicit_budget_is_respected_and_exact(self, api: Api) -> None:
        """Money must survive the round trip without float drift (ADR-017)."""
        await _signed_in(api)
        r = await api.post("/projects", {"name": "P", "infection": KPC, "budget_usd": "12.35"})
        assert Decimal(str(r.json()["budget"]["limit_usd"])) == Decimal("12.35")

    async def test_default_ranking_weights_are_set(self, api: Api) -> None:
        await _signed_in(api)
        r = await api.post("/projects", {"name": "P", "infection": KPC})
        w = r.json()["ranking_weights"]
        # binding_confidence leads, per the Boltz SDK's own description of it as
        # the primary metric for hit discovery.
        assert Decimal(str(w["binding_confidence"])) == Decimal("0.40")
        total = sum(Decimal(str(v)) for v in w.values())
        assert total == Decimal("1.00")

    async def test_blank_name_rejected(self, api: Api) -> None:
        await _signed_in(api)
        r = await api.post("/projects", {"name": "   ", "infection": KPC})
        assert r.status_code == 422

    async def test_missing_infection_rejected(self, api: Api) -> None:
        await _signed_in(api)
        assert (await api.post("/projects", {"name": "P"})).status_code == 422

    async def test_requires_authentication(self, api: Api) -> None:
        assert (await api.post("/projects", {"name": "P", "infection": KPC})).status_code == 401


class TestListAndGet:
    async def test_list_is_newest_first_and_counts(self, api: Api) -> None:
        await _signed_in(api)
        for n in ("first", "second", "third"):
            assert (await api.post("/projects", {"name": n, "infection": KPC})).status_code == 201
        r = await api.get("/projects")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert [p["name"] for p in body["items"]] == ["third", "second", "first"]

    async def test_pagination(self, api: Api) -> None:
        await _signed_in(api)
        for n in range(5):
            await api.post("/projects", {"name": f"p{n}", "infection": KPC})
        page = await api.c.get("/api/v1/projects?limit=2&skip=1")
        body = page.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5

    async def test_get_by_id(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        r = await api.get(f"/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["id"] == pid

    async def test_unknown_id_is_404(self, api: Api) -> None:
        await _signed_in(api)
        r = await api.get("/projects/00000000-0000-0000-0000-000000000000")
        assert r.status_code == 404
        assert r.json()["type"].endswith("/not_found")


class TestTenantIsolation:
    """The most dangerous possible bug in this product: serving one researcher's
    unpublished target work to another."""

    async def test_another_users_project_is_404_not_403(self, api: Api) -> None:
        """404, deliberately. A 403 would confirm the project exists."""
        await _signed_in(api, "first@example.org")
        pid = (await api.post("/projects", {"name": "Secret", "infection": KPC})).json()["id"]
        await api.logout()

        await _signed_in(api, "second@example.org")
        r = await api.get(f"/projects/{pid}")
        assert r.status_code == 404

    async def test_list_only_shows_own_projects(self, api: Api) -> None:
        await _signed_in(api, "first@example.org")
        await api.post("/projects", {"name": "Mine", "infection": KPC})
        await api.logout()

        await _signed_in(api, "second@example.org")
        body = (await api.get("/projects")).json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_cannot_patch_another_users_project(self, api: Api) -> None:
        await _signed_in(api, "first@example.org")
        pid = (await api.post("/projects", {"name": "Mine", "infection": KPC})).json()["id"]
        await api.logout()

        await _signed_in(api, "second@example.org")
        assert (await api.patch(f"/projects/{pid}", {"name": "Hijacked"})).status_code == 404

        # And the original is untouched.
        await api.logout()
        await api.login("first@example.org", GOOD_PASSWORD)
        assert (await api.get(f"/projects/{pid}")).json()["name"] == "Mine"

    async def test_cannot_delete_another_users_project(self, api: Api) -> None:
        await _signed_in(api, "first@example.org")
        pid = (await api.post("/projects", {"name": "Mine", "infection": KPC})).json()["id"]
        await api.logout()

        await _signed_in(api, "second@example.org")
        assert (await api.delete(f"/projects/{pid}")).status_code == 404

        await api.logout()
        await api.login("first@example.org", GOOD_PASSWORD)
        assert (await api.get(f"/projects/{pid}")).status_code == 200


class TestUpdate:
    async def test_patch_name(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "Old", "infection": KPC})).json()["id"]
        r = await api.patch(f"/projects/{pid}", {"name": "New"})
        assert r.status_code == 200
        assert r.json()["name"] == "New"

    async def test_patch_leaves_other_fields_alone(self, api: Api) -> None:
        await _signed_in(api)
        created = (await api.post("/projects", {"name": "Old", "infection": KPC})).json()
        pid = created["id"]
        patched = (await api.patch(f"/projects/{pid}", {"name": "New"})).json()
        assert patched["infection"] == created["infection"]
        assert patched["budget"] == created["budget"]

    async def test_empty_patch_is_a_no_op(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        r = await api.patch(f"/projects/{pid}", {})
        assert r.status_code == 200
        assert r.json()["name"] == "P"


class TestRankingWeights:
    async def test_weights_can_be_changed(self, api: Api) -> None:
        """Re-ranking must not require recomputation, so weights are just stored."""
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        r = await api.put(
            f"/projects/{pid}/ranking-weights",
            {
                "binding_confidence": "0.50",
                "iptm": "0.20",
                "ipsae_min": "0.20",
                "min_interaction_pae": "0.05",
                "structure_confidence": "0.05",
            },
        )
        assert r.status_code == 200
        assert Decimal(str(r.json()["ranking_weights"]["binding_confidence"])) == Decimal("0.50")

        # And it persisted.
        again = await api.get(f"/projects/{pid}")
        assert Decimal(str(again.json()["ranking_weights"]["iptm"])) == Decimal("0.20")

    async def test_negative_weight_rejected(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        r = await api.put(
            f"/projects/{pid}/ranking-weights",
            {
                "binding_confidence": "-0.5",
                "iptm": "0.2",
                "ipsae_min": "0.2",
                "min_interaction_pae": "0.05",
                "structure_confidence": "0.05",
            },
        )
        assert r.status_code == 422

    async def test_all_zero_weights_rejected(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        r = await api.put(
            f"/projects/{pid}/ranking-weights",
            {
                "binding_confidence": "0",
                "iptm": "0",
                "ipsae_min": "0",
                "min_interaction_pae": "0",
                "structure_confidence": "0",
            },
        )
        assert r.status_code == 422


class TestDelete:
    async def test_delete_removes_the_project(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        assert (await api.delete(f"/projects/{pid}")).status_code == 204
        assert (await api.get(f"/projects/{pid}")).status_code == 404

    async def test_deleting_twice_is_404(self, api: Api) -> None:
        await _signed_in(api)
        pid = (await api.post("/projects", {"name": "P", "infection": KPC})).json()["id"]
        await api.delete(f"/projects/{pid}")
        assert (await api.delete(f"/projects/{pid}")).status_code == 404


class TestCsrfRotation:
    """Regression test for a bug found while building M0.

    Logout used to delete the CSRF cookie as well as the session cookie, which
    left the user on the sign-in page with no token, so their next login was
    refused with a 403 until they reloaded. Logout now rotates the CSRF token
    instead of removing it.
    """

    async def test_login_works_immediately_after_logout(self, api: Api) -> None:
        await _signed_in(api)
        before = api.c.cookies.get(security.CSRF_COOKIE)
        assert before

        assert (await api.logout()).status_code == 204

        after = api.c.cookies.get(security.CSRF_COOKIE)
        assert after, "logout must leave a usable CSRF token behind"
        assert after != before, "the token should rotate on a privilege change"

        r = await api.login(password=GOOD_PASSWORD)
        assert r.status_code == 200, "login right after logout must not need a reload"

"""Target discovery endpoints: search, persist, and choose an epitope.

The epitope tests are the point of this file. They assert that the API converts
between three different residue numbering systems correctly and refuses when it
cannot — because the failure mode is a completed, paid-for design run aimed at
the wrong part of the protein, with no error anywhere.
"""

from __future__ import annotations

from .conftest import Api
from .stubs import StubSources

GOOD_PASSWORD = "ribosome velvet cartography"
KPC = {
    "name": "Carbapenem-resistant Klebsiella pneumoniae",
    "pathogen": "Klebsiella pneumoniae",
}


async def setup_project(api: Api, email: str = "researcher@example.org") -> str:
    assert (await api.register(email=email, password=GOOD_PASSWORD)).status_code == 201
    created = await api.post("/projects", {"name": "KPC-2", "infection": KPC})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def setup_target(api: Api) -> tuple[str, str]:
    project_id = await setup_project(api)
    added = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
    assert added.status_code == 201, added.text
    return project_id, str(added.json()["id"])


class TestSearch:
    async def test_search_returns_candidates(self, api: Api, stub_sources: StubSources) -> None:
        project_id = await setup_project(api)
        r = await api.post(
            f"/projects/{project_id}/targets/search",
            {"query": "carbapenem-hydrolyzing beta-lactamase KPC"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"][0]["accession"] == "Q9F663"
        assert body["items"][0]["evidence_level"] == "reviewed (Swiss-Prot)"
        assert body["degraded_sources"] == []

    async def test_reviewed_only_defaults_on(self, api: Api, stub_sources: StubSources) -> None:
        """Unfiltered, predicted TrEMBL entries bury the curated ones."""
        project_id = await setup_project(api)
        await api.post(f"/projects/{project_id}/targets/search", {"query": "kpc"})
        assert stub_sources.search_calls[0]["reviewed_only"] is True

    async def test_organism_filter_is_passed_through(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id = await setup_project(api)
        await api.post(
            f"/projects/{project_id}/targets/search",
            {"query": "kpc", "organism_id": 573},
        )
        assert stub_sources.search_calls[0]["organism_id"] == 573

    async def test_active_sites_are_offered_as_suggestions(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """UniProt-numbered, which the epitope endpoint converts separately."""
        project_id = await setup_project(api)
        r = await api.post(f"/projects/{project_id}/targets/search", {"query": "kpc"})
        sites = r.json()["items"][0]["active_sites"]
        assert [s["position"] for s in sites] == [69, 72, 129, 165]

    async def test_degraded_source_is_named_not_hidden(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """A partial panel shown as complete is the worst failure here."""
        stub_sources.degraded = {"pubmed": "HTTP 503"}
        project_id = await setup_project(api)
        r = await api.post(f"/projects/{project_id}/targets/search", {"query": "kpc"})
        assert r.status_code == 200
        assert r.json()["degraded_sources"] == ["pubmed"]
        assert r.json()["degraded_detail"]["pubmed"] == "HTTP 503"

    async def test_short_query_rejected(self, api: Api, stub_sources: StubSources) -> None:
        project_id = await setup_project(api)
        r = await api.post(f"/projects/{project_id}/targets/search", {"query": "a"})
        assert r.status_code == 422

    async def test_requires_authentication(self, api: Api, stub_sources: StubSources) -> None:
        assert (await api.post("/projects/x/targets/search", {"query": "kpc"})).status_code == 401

    async def test_another_users_project_is_404(self, api: Api, stub_sources: StubSources) -> None:
        project_id = await setup_project(api, "first@example.org")
        await api.logout()
        second = await api.register(email="second@example.org", password=GOOD_PASSWORD)
        assert second.status_code == 201
        r = await api.post(f"/projects/{project_id}/targets/search", {"query": "kpc"})
        assert r.status_code == 404


class TestAddTarget:
    async def test_target_is_persisted_with_evidence(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.get(f"/projects/{project_id}/targets/{target_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["accession"] == "Q9F663"
        assert body["length"] == 293
        assert len(body["structures"]) == 2
        assert len(body["references"]) == 1
        assert body["epitope"] is None

    async def test_structures_carry_a_cif_url_for_the_design_engine(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        body = (await api.get(f"/projects/{project_id}/targets/{target_id}")).json()
        urls = [s["cif_url"] for s in body["structures"]]
        assert all(u.startswith("https://files.rcsb.org/download/") for u in urls)

    async def test_adding_the_same_accession_twice_is_idempotent(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        again = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
        assert again.status_code == 201
        assert again.json()["id"] == target_id
        assert len((await api.get(f"/projects/{project_id}/targets")).json()["items"]) == 1

    async def test_unknown_accession_is_503_with_the_reason(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id = await setup_project(api)
        r = await api.post(f"/projects/{project_id}/targets", {"accession": "Q00000"})
        assert r.status_code == 503
        assert r.json()["type"].endswith("/source_unavailable")

    async def test_degraded_evidence_is_recorded_on_the_target(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """So a researcher does not later read "no literature" as a finding."""
        stub_sources.degraded = {"pubmed": "HTTP 429"}
        project_id = await setup_project(api)
        added = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
        assert added.json()["degraded_sources"] == ["pubmed"]
        assert added.json()["references"] == []

    async def test_refresh_recovers_a_degraded_panel(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        stub_sources.degraded = {"pubmed": "HTTP 429"}
        project_id = await setup_project(api)
        added = await api.post(f"/projects/{project_id}/targets", {"accession": "Q9F663"})
        target_id = added.json()["id"]
        assert added.json()["degraded_sources"] == ["pubmed"]

        stub_sources.degraded = {}
        refreshed = await api.post(f"/projects/{project_id}/targets/{target_id}/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["degraded_sources"] == []
        assert refreshed.json()["references"]

    async def test_project_holds_many_targets(self, api: Api, stub_sources: StubSources) -> None:
        """One disease, many targets (ADR-014): isolates carry several genes."""
        project_id = await setup_project(api)
        second = stub_sources.targets[0].__class__(
            **{**stub_sources.targets[0].__dict__, "accession": "Q848S6"}
        )
        stub_sources.targets.append(second)
        for accession in ("Q9F663", "Q848S6"):
            r = await api.post(f"/projects/{project_id}/targets", {"accession": accession})
            assert r.status_code == 201, r.text
        listed = (await api.get(f"/projects/{project_id}/targets")).json()
        assert len(listed["items"]) == 2

    async def test_delete_target(self, api: Api, stub_sources: StubSources) -> None:
        project_id, target_id = await setup_target(api)
        assert (await api.delete(f"/projects/{project_id}/targets/{target_id}")).status_code == 204
        assert (await api.get(f"/projects/{project_id}/targets/{target_id}")).status_code == 404

    async def test_target_is_not_visible_to_another_user(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        await api.logout()
        await api.register(email="other@example.org", password=GOOD_PASSWORD)
        assert (await api.get(f"/projects/{project_id}/targets/{target_id}")).status_code == 404


class TestEpitopeAuthorNumbering:
    async def test_author_residues_convert_to_design_indices(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """70 -> 39, pinned against the live API."""
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 73, 130, 166]},
        )
        assert r.status_code == 200, r.text
        epitope = r.json()["epitope"]
        assert epitope["design_indices"] == [39, 42, 99, 135]
        assert epitope["residue_letters"] == "SKSE"

    async def test_residue_letters_make_a_mistake_visible(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """The catalytic residues of KPC-2 spell SKSE. A wrong mapping would not."""
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 166]},
        )
        assert r.json()["epitope"]["residue_letters"] == "SE"

    async def test_mapping_summary_is_returned(self, api: Api, stub_sources: StubSources) -> None:
        """So the conversion is not a black box."""
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70]},
        )
        mapping = r.json()["epitope"]["mapping"]
        assert mapping["modelled_residues"] == 261
        assert mapping["author_numbering"] == "30-292"
        assert mapping["contiguous"] is False
        assert mapping["gaps"] == ["58", "253"]

    async def test_unmodelled_residue_is_refused(self, api: Api, stub_sources: StubSources) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 1]},
        )
        assert r.status_code == 422
        assert r.json()["type"].endswith("/residue_not_modelled")
        assert "30-292" in r.json()["detail"]

    async def test_gap_residue_is_refused_with_an_explanation(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [58]},
        )
        assert r.status_code == 422
        assert "disordered" in r.json()["detail"]

    async def test_nothing_is_saved_when_mapping_fails(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """A partly-valid epitope must not be half-applied."""
        project_id, target_id = await setup_target(api)
        await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 9999]},
        )
        target = (await api.get(f"/projects/{project_id}/targets/{target_id}")).json()
        assert target["epitope"] is None

    async def test_residues_are_required(self, api: Api, stub_sources: StubSources) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A"},
        )
        assert r.status_code == 422


class TestEpitopeUniProtNumbering:
    """UniProt positions are a different system and must not be conflated."""

    async def test_uniprot_positions_convert_correctly(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """UniProt 69 is the residue the literature calls 70."""
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "uniprot_positions": [69, 72, 129, 165]},
        )
        assert r.status_code == 200, r.text
        epitope = r.json()["epitope"]
        assert epitope["design_indices"] == [39, 42, 99, 135]
        assert epitope["residue_letters"] == "SKSE"
        # And the stored author numbers are the ones a reader would recognise.
        assert epitope["author_residues"] == [70, 73, 130, 166]

    async def test_uniprot_and_author_numbering_give_the_same_result(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """The same residues selected either way must land in the same place."""
        project_id, target_id = await setup_target(api)
        by_author = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70, 166]},
        )
        by_uniprot = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "uniprot_positions": [69, 165]},
        )
        assert (
            by_author.json()["epitope"]["design_indices"]
            == by_uniprot.json()["epitope"]["design_indices"]
        )

    async def test_confusing_the_two_systems_lands_elsewhere(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """Passing a UniProt position as an author number selects a different residue,
        which is exactly why the API has two separate fields."""
        project_id, target_id = await setup_target(api)
        wrong = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [69]},
        )
        assert wrong.status_code == 200
        assert wrong.json()["epitope"]["residue_letters"] == "C"  # not the catalytic S

        right = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "uniprot_positions": [69]},
        )
        assert right.json()["epitope"]["residue_letters"] == "S"

    async def test_position_outside_the_structure_is_refused(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "uniprot_positions": [5]},
        )
        assert r.status_code == 422
        assert "modelled region" in r.json()["detail"]

    async def test_both_systems_can_be_combined(self, api: Api, stub_sources: StubSources) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {
                "pdb_id": "2OV5",
                "chain_id": "A",
                "author_residues": [70],
                "uniprot_positions": [165],
            },
        )
        assert r.status_code == 200
        assert r.json()["epitope"]["design_indices"] == [39, 135]


class TestEpitopeVerification:
    async def test_expected_residues_are_checked(self, api: Api, stub_sources: StubSources) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {
                "pdb_id": "2OV5",
                "chain_id": "A",
                "author_residues": [70, 166],
                "expected_residues": {"70": "Ser", "166": "Glu"},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["epitope"]["verified"] is True

    async def test_mismatched_expectation_is_rejected(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {
                "pdb_id": "2OV5",
                "chain_id": "A",
                "author_residues": [70],
                "expected_residues": {"70": "Trp"},
            },
        )
        assert r.status_code == 422
        assert r.json()["type"].endswith("/residue_mismatch")

    async def test_unverified_when_no_expectation_is_given(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70]},
        )
        assert r.json()["epitope"]["verified"] is False


class TestEpitopeFailureModes:
    async def test_missing_residue_map_refuses_to_save(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """Without the mapping, guessing would aim the design at the wrong surface."""
        stub_sources.residue_map_error = "RCSB unreachable"
        project_id, target_id = await setup_target(api)
        r = await api.put(
            f"/projects/{project_id}/targets/{target_id}/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70]},
        )
        assert r.status_code == 503
        assert "was not saved" in r.json()["detail"]
        target = (await api.get(f"/projects/{project_id}/targets/{target_id}")).json()
        assert target["epitope"] is None

    async def test_unknown_target_is_404(self, api: Api, stub_sources: StubSources) -> None:
        project_id = await setup_project(api)
        r = await api.put(
            f"/projects/{project_id}/targets/does-not-exist/epitope",
            {"pdb_id": "2OV5", "chain_id": "A", "author_residues": [70]},
        )
        assert r.status_code == 404


class TestResidueMapInspection:
    async def test_chain_numbering_can_be_inspected(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        """Lets the UI explain why residue 5 is unavailable before the user tries."""
        await setup_project(api)
        r = await api.get("/structures/2OV5/chains/A/residue-map")
        assert r.status_code == 200
        body = r.json()
        assert body["mapping"]["author_numbering"] == "30-292"
        assert len(body["sequence"]) == 261

    async def test_residues_can_be_converted_in_a_dry_run(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        await setup_project(api)
        r = await api.get("/structures/2OV5/chains/A/residue-map?author_residues=70,166")
        body = r.json()
        assert body["design_indices"] == [39, 135]
        assert body["residue_letters"] == "SE"

    async def test_dry_run_reports_unmappable_residues(
        self, api: Api, stub_sources: StubSources
    ) -> None:
        await setup_project(api)
        r = await api.get("/structures/2OV5/chains/A/residue-map?author_residues=70,1")
        assert r.status_code == 422
        assert r.json()["type"].endswith("/residue_not_modelled")

    async def test_requires_authentication(self, api: Api, stub_sources: StubSources) -> None:
        assert (await api.get("/structures/2OV5/chains/A/residue-map")).status_code == 401

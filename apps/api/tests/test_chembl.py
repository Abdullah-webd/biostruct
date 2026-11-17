"""ChEMBL source tests.

Fixtures are real responses captured from the live API on 2026-11-17, so a
schema change at ChEMBL fails the build instead of quietly emptying a library.
No network.

Most of these guard against one specific class of bug: **ChEMBL sends numbers
as strings**, and a string comparison would invert a potency ordering without
raising anything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from biostruct.sources.chembl import (
    POTENCY_TYPES,
    Activity,
    ChemblClient,
    parse_activity,
    parse_molecule,
)
from biostruct.sources.http import CachedHTTPClient

FIXTURES = Path(__file__).parent / "fixtures" / "chembl"

AVIBACTAM = "CHEMBL1689063"
KPC2 = "CHEMBL5437"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def client_for(handler: Any) -> ChemblClient:
    return ChemblClient(
        CachedHTTPClient(
            source="chembl",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            rate_per_second=1000.0,
        )
    )


def route(mapping: dict[str, Any]) -> Any:
    """Serve a fixture per URL fragment, and fail loudly on anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, payload in mapping.items():
            if fragment in str(request.url):
                if isinstance(payload, int):
                    return httpx.Response(payload)
                return httpx.Response(200, json=payload)
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


class TestStringifiedNumbers:
    """The single most dangerous thing about this API."""

    def test_max_phase_arrives_as_a_string_and_leaves_as_a_number(self) -> None:
        raw = fixture("molecule_avibactam.json")
        assert raw["max_phase"] == "4.0", "fixture should capture the string form"
        molecule = parse_molecule(raw)
        assert molecule is not None
        assert molecule.max_phase == 4.0
        assert molecule.is_approved_drug

    def test_properties_are_parsed_out_of_their_strings(self) -> None:
        molecule = parse_molecule(fixture("molecule_avibactam.json"))
        assert molecule is not None
        assert molecule.molecular_weight is not None
        assert 200 < molecule.molecular_weight < 300
        assert molecule.alogp is not None
        assert molecule.qed is not None

    def test_potency_sorts_numerically_not_lexicographically(self) -> None:
        """``"9.0" < "150.0"`` is false as text and true as a number. Getting
        this wrong would put a weak compound at the top of a result list."""
        rows = fixture("activity_kpc2.json")["activities"]
        values = [
            a.value_nm
            for row in rows
            if (a := parse_activity(row)) and a.shows_binding and a.value_nm is not None
        ]
        assert values, "fixture should contain measured potencies"
        assert all(isinstance(v, float) for v in values)
        assert sorted(values) != sorted(str(v) for v in values)  # type: ignore[type-var]

    def test_a_non_numeric_value_becomes_none_rather_than_raising(self) -> None:
        activity = parse_activity(
            {"molecule_chembl_id": "CHEMBL1", "standard_value": "not a number"}
        )
        assert activity is not None
        assert activity.value_nm is None


class TestMoleculeParsing:
    def test_a_real_captured_molecule_parses(self) -> None:
        molecule = parse_molecule(fixture("molecule_avibactam.json"))
        assert molecule is not None
        assert molecule.chembl_id == AVIBACTAM
        assert molecule.name == "AVIBACTAM"
        assert molecule.smiles == "NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O"
        assert molecule.is_screenable

    def test_a_molecule_with_no_structure_is_not_screenable(self) -> None:
        """Observed: CHEMBL3545239 (PF-05019702) has ``molecule_structures:
        null``. Submitting it to a paid screen is impossible, so it must be
        detectable before anyone is charged."""
        rows = fixture("molecule_batch.json")["molecules"]
        unscreenable = [m for row in rows if (m := parse_molecule(row)) and not m.is_screenable]
        assert unscreenable, "fixture should contain a structureless entry"
        assert all(m.smiles == "" for m in unscreenable)

    def test_the_label_falls_back_to_the_id(self) -> None:
        """Most ChEMBL compounds have no ``pref_name``, and a blank row in a
        result table is useless to a researcher."""
        molecule = parse_molecule({"molecule_chembl_id": "CHEMBL999", "pref_name": None})
        assert molecule is not None
        assert molecule.label == "CHEMBL999"

    def test_a_row_without_an_id_is_discarded(self) -> None:
        assert parse_molecule({"pref_name": "mystery"}) is None

    def test_the_parent_defaults_to_the_molecule_itself(self) -> None:
        molecule = parse_molecule({"molecule_chembl_id": "CHEMBL5"})
        assert molecule is not None
        assert molecule.parent_id == "CHEMBL5"


class TestActivityParsing:
    def test_action_type_is_an_object_not_a_string(self) -> None:
        """Observed shape: ``{"action_type": "INHIBITOR", "description": ...}``.
        Rendered naively, a result table would show a dict."""
        rows = fixture("activity_kpc2.json")["activities"]
        with_action = [a for row in rows if (a := parse_activity(row)) and a.action_type]
        assert with_action
        assert all(isinstance(a.action_type, str) for a in with_action)
        assert any(a.action_type == "INHIBITOR" for a in with_action)

    def test_substrate_measurements_are_recognised_as_such(self) -> None:
        """Kcat and Km mean the enzyme destroys the molecule. A library built
        from "things measured against KPC" would otherwise include its food."""
        rows = fixture("activity_kpc2.json")["activities"]
        parsed = [a for row in rows if (a := parse_activity(row))]
        substrates = [a for a in parsed if a.is_substrate_measurement]
        assert substrates, "fixture should contain Kcat/Km rows"
        assert not any(a.shows_binding for a in substrates)

    def test_a_percentage_is_never_treated_as_a_potency(self) -> None:
        """``standard_type: Activity`` with ``standard_units: %`` carries a
        number that must not land in a nanomolar field."""
        activity = parse_activity(
            {
                "molecule_chembl_id": "CHEMBL520642",
                "standard_type": "Activity",
                "standard_value": "42.0",
                "standard_units": "%",
            }
        )
        assert activity is not None
        assert activity.value_nm is None
        assert not activity.shows_binding

    def test_a_greater_than_relation_is_not_evidence_of_binding(self) -> None:
        """``> 100000 nM`` means the assay failed to show binding. Treating it
        as a hit would seed the library with known non-binders."""
        activity = Activity(
            molecule_chembl_id="CHEMBL1",
            smiles="CCO",
            standard_type="Ki",
            value_nm=100000.0,
            relation=">",
            units="nM",
        )
        assert not activity.shows_binding

    def test_a_less_than_relation_is_evidence_of_binding(self) -> None:
        activity = Activity(
            molecule_chembl_id="CHEMBL1",
            smiles="CCO",
            standard_type="Ki",
            value_nm=1.0,
            relation="<",
            units="nM",
        )
        assert activity.shows_binding

    def test_the_potency_note_always_carries_its_units(self) -> None:
        activity = parse_activity(
            {
                "molecule_chembl_id": AVIBACTAM,
                "standard_type": "Ki",
                "standard_value": "11.0",
                "standard_units": "nM",
                "standard_relation": "=",
            }
        )
        assert activity is not None
        assert activity.potency_note() == "Ki 11 nM"

    def test_only_comparable_endpoints_count_as_potency(self) -> None:
        assert "Ki" in POTENCY_TYPES
        assert "MIC" not in POTENCY_TYPES
        assert "Kcat" not in POTENCY_TYPES


class TestActivesAgainst:
    @pytest.mark.anyio
    async def test_the_known_inhibitor_of_kpc2_is_found_and_ranked(self) -> None:
        """Avibactam has a measured Ki of 11 nM against KPC-2 in this captured
        page. That is where the screen's positive control comes from — a
        measured assay, not an assumption."""
        chembl = client_for(route({"activity.json": fixture("activity_kpc2.json")}))
        actives = await chembl.actives_against(KPC2)
        assert actives
        ids = [a.molecule_chembl_id for a in actives]
        assert AVIBACTAM in ids
        avibactam = next(a for a in actives if a.molecule_chembl_id == AVIBACTAM)
        assert avibactam.value_nm == 11.0

    @pytest.mark.anyio
    async def test_results_are_ordered_most_potent_first(self) -> None:
        chembl = client_for(route({"activity.json": fixture("activity_kpc2.json")}))
        actives = await chembl.actives_against(KPC2)
        values = [a.value_nm for a in actives]
        assert values == sorted(v for v in values if v is not None)

    @pytest.mark.anyio
    async def test_one_slot_per_compound_however_often_it_was_assayed(self) -> None:
        """Each duplicate would be another $0.0250 for an answer we already have."""
        chembl = client_for(route({"activity.json": fixture("activity_kpc2.json")}))
        actives = await chembl.actives_against(KPC2)
        keys = [a.parent_molecule_id for a in actives]
        assert len(keys) == len(set(keys))

    @pytest.mark.anyio
    async def test_the_strongest_measurement_wins_for_a_repeated_compound(self) -> None:
        page = {
            "activities": [
                {
                    "molecule_chembl_id": "CHEMBL1",
                    "canonical_smiles": "CCO",
                    "standard_type": "Ki",
                    "standard_value": "900.0",
                    "standard_units": "nM",
                    "standard_relation": "=",
                },
                {
                    "molecule_chembl_id": "CHEMBL1",
                    "canonical_smiles": "CCO",
                    "standard_type": "Ki",
                    "standard_value": "4.0",
                    "standard_units": "nM",
                    "standard_relation": "=",
                },
            ]
        }
        chembl = client_for(route({"activity.json": page}))
        actives = await chembl.actives_against(KPC2)
        assert len(actives) == 1
        assert actives[0].value_nm == 4.0

    @pytest.mark.anyio
    async def test_a_binder_with_no_smiles_is_excluded(self) -> None:
        page = {
            "activities": [
                {
                    "molecule_chembl_id": "CHEMBL1",
                    "standard_type": "Ki",
                    "standard_value": "4.0",
                    "standard_units": "nM",
                    "standard_relation": "=",
                }
            ]
        }
        chembl = client_for(route({"activity.json": page}))
        assert await chembl.actives_against(KPC2) == []


class TestLookups:
    @pytest.mark.anyio
    async def test_a_missing_molecule_is_absence_not_an_outage(self) -> None:
        """ChEMBL answers 404 with an empty body for an id it does not have.
        The shared client calls that ``SourceUnavailable``, which is right for a
        source being down and wrong for a compound not existing."""
        chembl = client_for(route({"molecule/CHEMBL9999999": 404}))
        assert await chembl.molecule("CHEMBL9999999") is None

    @pytest.mark.anyio
    async def test_a_malformed_id_is_refused_without_a_request(self) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        chembl = client_for(explode)
        assert await chembl.molecule("'; DROP TABLE") is None
        assert await chembl.molecule("CHEMBL") is None

    @pytest.mark.anyio
    async def test_missing_ids_are_reported_rather_than_silently_lost(self) -> None:
        """Observed: five ids requested, four returned, ``total_count: 4``, and
        nothing saying which one was absent. A curated library that lost an
        entry would otherwise just be quietly smaller."""
        chembl = client_for(route({"molecule.json": fixture("molecule_batch.json")}))
        found, missing = await chembl.molecules(
            [AVIBACTAM, "CHEMBL777", "CHEMBL3545239", "CHEMBL3989743"]
        )
        assert {m.chembl_id for m in found} == {AVIBACTAM, "CHEMBL777", "CHEMBL3545239"}
        assert missing == ["CHEMBL3989743"]

    @pytest.mark.anyio
    async def test_an_empty_request_makes_no_call(self) -> None:
        def explode(request: httpx.Request) -> httpx.Response:
            raise AssertionError("should not have been called")

        chembl = client_for(explode)
        assert await chembl.molecules([]) == ([], [])

    @pytest.mark.anyio
    async def test_target_search_finds_kpc2_by_name(self) -> None:
        chembl = client_for(route({"target/search.json": fixture("target_search_kpc.json")}))
        targets = await chembl.find_targets("KPC-2")
        assert targets
        assert any(t.target_chembl_id == KPC2 for t in targets)
        assert any(t.is_single_protein for t in targets)

    @pytest.mark.anyio
    async def test_target_search_relevance_cannot_be_trusted_blindly(self) -> None:
        """Live on 2026-11-17, ``"KPC-2"`` returned two KPC enzymes and then
        *Thromboxane-A synthase*, a human protein with nothing to do with
        antibiotic resistance. Taking the top hit and screening against it would
        spend real money on the wrong protein, so the filters exist and the
        agent is required to name its choice."""
        chembl = client_for(route({"target/search.json": fixture("target_search_kpc.json")}))
        unfiltered = await chembl.find_targets("KPC-2", limit=25)
        bacterial = await chembl.find_targets(
            "KPC-2", organism="Klebsiella", proteins_only=True, limit=25
        )
        assert len(bacterial) < len(unfiltered)
        assert bacterial
        assert all("klebsiella" in t.organism.lower() for t in bacterial)
        assert all(t.is_single_protein for t in bacterial)

    @pytest.mark.anyio
    async def test_similarity_returns_the_salts_and_stereoisomers_we_measured(self) -> None:
        """Not a bug to fix here — a fact the library builder has to handle.
        Five entries, one compound, $0.125 of screening for one answer."""
        chembl = client_for(route({"similarity/": fixture("similarity_avibactam.json")}))
        neighbours = await chembl.similar_to("NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O")
        assert len(neighbours) > 1
        assert any("." in m.smiles for m in neighbours), "a salt should be present"
        assert any(m.similarity == 100 for m in neighbours)

    @pytest.mark.anyio
    async def test_similarity_below_the_api_floor_is_clamped(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"molecules": []})

        await client_for(handler).similar_to("CCO", threshold=5)
        assert "/40.json" in seen[0]

    @pytest.mark.anyio
    async def test_a_page_size_above_the_servers_cap_is_clamped(self) -> None:
        """The API clamps silently; clamping here keeps our arithmetic honest."""
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={"activities": []})

        await client_for(handler).activities(KPC2, limit=99999)
        assert seen[0].params["limit"] == "1000"

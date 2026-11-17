"""Library assembly (M5).

The milestone's acceptance criterion is one line — "library assembles with
positive control present" — and most of this file is about the ways that can
fail quietly. A library missing its control still screens, still returns
rankings, and gives the researcher no way to tell whether the ranking means
anything.

No network: ChEMBL is injected.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from biostruct.library import (
    CURATED,
    LIBRARY_SIZE_MAX,
    SCREEN_USD_PER_MOLECULE,
    DropReason,
    LibraryBuilder,
    control_for,
    curated_for,
    salt_free_key,
)
from biostruct.sources.chembl import ChemblClient
from biostruct.sources.http import CachedHTTPClient

FIXTURES = Path(__file__).parent / "fixtures" / "chembl"

AVIBACTAM = "CHEMBL1689063"
TANIBORBACTAM = "CHEMBL4594290"
KPC2 = "CHEMBL5437"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def chembl_for(mapping: dict[str, Any]) -> ChemblClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for fragment, payload in mapping.items():
            if fragment in str(request.url):
                return httpx.Response(200, json=payload)
        return httpx.Response(200, json={})

    return ChemblClient(
        CachedHTTPClient(
            source="chembl",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            rate_per_second=1000.0,
        )
    )


def failing_chembl() -> ChemblClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    return ChemblClient(
        CachedHTTPClient(
            source="chembl",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            rate_per_second=1000.0,
            max_attempts=1,
        )
    )


class TestCuratedSet:
    def test_every_shipped_compound_has_a_structure_and_an_id(self) -> None:
        """These are shipped, not fetched, so a typo here is a typo in
        production. Each id and SMILES was verified against the live ChEMBL API
        on 2026-11-17."""
        for inhibitor in CURATED:
            assert inhibitor.chembl_id.startswith("CHEMBL")
            assert inhibitor.chembl_id[6:].isdigit()
            assert inhibitor.smiles.strip()
            assert inhibitor.name
            assert inhibitor.inhibits

    def test_the_curated_ids_are_unique(self) -> None:
        ids = [c.chembl_id for c in CURATED]
        assert len(ids) == len(set(ids))

    def test_avibactam_is_the_exact_compound_chembl_has(self) -> None:
        """Pinned against the captured response: a control that is not the
        compound we named would make the whole positive-control idea a lie."""
        live = fixture("molecule_avibactam.json")
        shipped = next(c for c in CURATED if c.chembl_id == AVIBACTAM)
        assert shipped.smiles == live["molecule_structures"]["canonical_smiles"]
        assert shipped.name.upper() == live["pref_name"]

    def test_every_ambler_class_has_a_control(self) -> None:
        for target_class in ("A", "B", "C", "D"):
            assert control_for(target_class) is not None, target_class

    def test_avibactam_is_not_offered_as_a_class_b_control(self) -> None:
        """The documented trap: avibactam does not usefully inhibit metallo-
        beta-lactamases, so using it against NDM would make a working screen
        look broken. See docs/research.md section 4."""
        avibactam = next(c for c in CURATED if c.chembl_id == AVIBACTAM)
        assert "B" not in avibactam.inhibits
        assert AVIBACTAM not in {c.chembl_id for c in curated_for("B")}
        control = control_for("B")
        assert control is not None and control.chembl_id == TANIBORBACTAM

    def test_the_class_b_control_is_labelled_as_not_approved(self) -> None:
        """No metallo-beta-lactamase inhibitor is approved anywhere. Presenting
        taniborbactam as a licensed drug would be a false claim."""
        control = control_for("B")
        assert control is not None
        assert control.status == "trial"

    def test_captopril_is_marked_research_grade(self) -> None:
        """It is an approved drug — as a blood-pressure medicine. Calling it an
        approved antibacterial would be wrong in a way a researcher would
        notice and a layman would not."""
        captopril = next(c for c in CURATED if c.name == "Captopril")
        assert captopril.status == "research"
        assert "never as an antibacterial" in captopril.note


class TestSaltKey:
    def test_a_sodium_salt_collapses_onto_its_parent(self) -> None:
        parent = "NC(=O)[C@@H]1CC[C@@H]2CN1C(=O)N2OS(=O)(=O)O"
        assert salt_free_key(f"{parent}.[Na+]") == parent

    def test_a_molecule_without_a_counter_ion_is_unchanged(self) -> None:
        assert salt_free_key("CCO") == "CCO"

    def test_it_does_not_claim_to_normalise_stereochemistry(self) -> None:
        """Enantiomers are genuinely different molecules to a protein. The key
        does not merge them, and ``parent_chembl_id`` is what catches the
        duplicates a similarity search returns."""
        assert salt_free_key("N[C@@H](C)O") != salt_free_key("N[C@H](C)O")


class TestAssembly:
    @pytest.mark.anyio
    async def test_the_milestone_criterion_a_control_is_always_present(self) -> None:
        library = await LibraryBuilder().assemble(target_class="A", max_size=5)
        assert library.has_control
        assert library.controls[0].external_id == AVIBACTAM
        assert library.controls[0].is_control

    @pytest.mark.anyio
    async def test_the_control_survives_a_library_of_one(self) -> None:
        """A cap applied naively would spend the single slot on the first
        ChEMBL hit and silently drop the only molecule that validates the run."""
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=1)
        assert library.has_control

    @pytest.mark.anyio
    async def test_the_control_survives_chembl_being_down(self) -> None:
        """The controls ship in the repository precisely so that an outage
        cannot remove them."""
        library = await LibraryBuilder(failing_chembl()).assemble(
            target_class="A", chembl_target_id=KPC2, max_size=20
        )
        assert library.has_control
        assert library.unavailable == ("ChEMBL",)
        assert "Could not reach ChEMBL" in library.summary()

    @pytest.mark.anyio
    async def test_the_control_keeps_its_measured_potency_against_this_target(self) -> None:
        """The control is added first, from the shipped set, with no assay
        number. ChEMBL then reports that same compound has Ki 11 nM against
        KPC-2 — the most useful number in the library, because it says where the
        control *ought* to rank. The duplicate row is rejected; the number it
        carried must not go with it."""
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=20)
        control = library.controls[0]
        assert control.external_id == AVIBACTAM
        assert control.known_potency_nm == 11.0
        assert "Positive control" in control.note
        assert "11 nM" in control.note

    @pytest.mark.anyio
    async def test_absorbing_a_duplicate_does_not_add_a_slot(self) -> None:
        """The number is kept, the molecule is not screened twice."""
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=20)
        ids = [e.external_id for e in library.entries if e.external_id]
        assert len(ids) == len(set(ids))

    @pytest.mark.anyio
    async def test_measured_binders_against_this_target_are_included(self) -> None:
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=40)
        measured = [e for e in library.entries if e.known_potency_nm is not None]
        assert measured
        assert all("nM" in e.note for e in measured)

    @pytest.mark.anyio
    async def test_a_substrate_never_reaches_the_library(self) -> None:
        """The KPC-2 page includes Kcat and Km rows for compounds the enzyme
        destroys. Paying to ask whether an enzyme's own substrate inhibits it
        is the clearest possible waste."""
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=LIBRARY_SIZE_MAX)
        for entry in library.entries:
            assert "Kcat" not in entry.note
            assert "Km " not in entry.note

    @pytest.mark.anyio
    async def test_nothing_unscreenable_is_submitted(self) -> None:
        library = await LibraryBuilder().assemble(
            target_class="A", max_size=10, extra_smiles=(("", "a compound with no structure"),)
        )
        assert all(e.smiles.strip() for e in library.entries)
        assert any(d.reason == DropReason.NO_STRUCTURE for d in library.dropped)

    @pytest.mark.anyio
    async def test_a_salt_of_an_existing_entry_is_dropped_with_a_reason(self) -> None:
        avibactam = next(c for c in CURATED if c.chembl_id == AVIBACTAM)
        library = await LibraryBuilder().assemble(
            target_class="A",
            max_size=10,
            extra_smiles=((f"{avibactam.smiles}.[Na+]", "avibactam sodium"),),
        )
        assert any(d.reason == DropReason.SALT_OF_ENTRY for d in library.dropped)
        assert sum(1 for e in library.entries if AVIBACTAM in e.external_id) == 1

    @pytest.mark.anyio
    async def test_similarity_expansion_does_not_buy_the_same_compound_five_times(
        self,
    ) -> None:
        """The captured similarity page is five entries of one compound. All
        five would have been billed."""
        library = await LibraryBuilder(
            chembl_for({"similarity/": fixture("similarity_avibactam.json")})
        ).assemble(target_class="A", max_size=30, expand_with_similar=True)
        keys = [salt_free_key(e.smiles) for e in library.entries]
        assert len(keys) == len(set(keys))

    @pytest.mark.anyio
    async def test_every_exclusion_carries_a_reason_a_person_can_read(self) -> None:
        library = await LibraryBuilder().assemble(
            target_class="A", max_size=2, extra_smiles=(("", "no structure"), ("CCO", "ethanol"))
        )
        assert library.dropped
        for drop in library.dropped:
            # Prose, not an enum name: this string is shown to a researcher as
            # the explanation for why a candidate they expected is not there.
            assert " " in drop.reason
            assert "_" not in drop.reason
            assert drop.reason != drop.reason.upper()


class TestCostAndSize:
    @pytest.mark.anyio
    async def test_a_library_prices_itself_before_anything_is_submitted(self) -> None:
        library = await LibraryBuilder().assemble(target_class="A", max_size=4)
        assert library.estimated_cost_usd == SCREEN_USD_PER_MOLECULE * library.size
        assert isinstance(library.estimated_cost_usd, Decimal)

    @pytest.mark.anyio
    async def test_the_size_cap_is_honoured_for_everything_but_the_control(self) -> None:
        library = await LibraryBuilder(
            chembl_for({"activity.json": fixture("activity_kpc2.json")})
        ).assemble(target_class="A", chembl_target_id=KPC2, max_size=6)
        assert library.size <= 6
        assert library.has_control

    @pytest.mark.anyio
    async def test_a_library_larger_than_the_cap_is_refused_with_a_price(self) -> None:
        with pytest.raises(ValueError, match=r"\$50"):
            await LibraryBuilder().assemble(target_class="A", max_size=LIBRARY_SIZE_MAX + 1)

    @pytest.mark.anyio
    async def test_an_empty_library_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            await LibraryBuilder().assemble(target_class="A", max_size=0)

    @pytest.mark.anyio
    async def test_the_summary_states_the_cost_and_names_the_control(self) -> None:
        library = await LibraryBuilder().assemble(target_class="A", max_size=5)
        summary = library.summary()
        assert "$" in summary
        assert "Avibactam" in summary
        assert str(library.size) in summary

    @pytest.mark.anyio
    async def test_an_unknown_enzyme_class_yields_no_control_rather_than_a_wrong_one(
        self,
    ) -> None:
        """Better to tell the researcher we have no control for this class than
        to include one that cannot bind."""
        library = await LibraryBuilder().assemble(target_class="Z", max_size=5)
        assert not library.has_control
        assert library.size == 0

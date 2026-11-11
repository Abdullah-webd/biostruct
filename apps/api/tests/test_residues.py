"""Tests for the author-number to design-index conversion.

The real numbers below come from PDB 2OV5 chain A (KPC-2), captured live on
2026-11-11. They are not invented: 261 residues modelled out of 293, author
numbering 30-292 with gaps at 58 and 253, because class A beta-lactamases use
Ambler numbering.

That makes this the one place where a plausible-looking shortcut produces
designs aimed at the wrong protein surface, with no error raised anywhere. Hence
the number of tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from biostruct.sources.residues import ResidueMap, ResidueMappingError

FIXTURES = Path(__file__).parent / "fixtures" / "rcsb"


def kpc2_map() -> ResidueMap:
    data = json.loads((FIXTURES / "2ov5_chain_a.json").read_text())
    return ResidueMap.from_rcsb(
        pdb_id="2OV5",
        chain_id="A",
        sequence=data["sequence"],
        auth_to_entity_poly_seq_mapping=data["auth_to_entity_poly_seq_mapping"],
    )


# Catalytic and substrate-binding residues of class A carbapenemases, in the
# Ambler numbering the literature uses. Ground truth for the mapping.
KPC2_CATALYTIC = {70: "S", 73: "K", 130: "S", 166: "E", 170: "N", 234: "K", 237: "T"}


class TestRealStructureShape:
    def test_structure_is_partially_modelled(self) -> None:
        """261 of 293 residues: a structure is rarely the whole protein."""
        rmap = kpc2_map()
        assert rmap.length == 261

    def test_numbering_does_not_start_at_one(self) -> None:
        assert kpc2_map().author_range == ("30", "292")

    def test_numbering_is_not_contiguous(self) -> None:
        """The property that defeats offset arithmetic."""
        assert kpc2_map().is_contiguous is False

    def test_gaps_are_reported(self) -> None:
        assert kpc2_map().missing_author_numbers == ("58", "253")

    def test_describe_is_safe_to_show_a_researcher(self) -> None:
        described = kpc2_map().describe()
        assert described["modelled_residues"] == 261
        assert described["author_numbering"] == "30-292"
        assert described["contiguous"] is False
        assert described["gaps"] == ["58", "253"]


class TestCorrectMapping:
    @pytest.mark.parametrize(("author", "expected"), sorted(KPC2_CATALYTIC.items()))
    def test_catalytic_residues_map_to_the_right_amino_acid(
        self, author: int, expected: str
    ) -> None:
        rmap = kpc2_map()
        assert rmap.residue_at_author(author) == expected

    def test_known_indices(self) -> None:
        """Pinned against the live API so a refactor cannot drift them."""
        rmap = kpc2_map()
        assert rmap.to_index(70) == 39
        assert rmap.to_index(73) == 42
        assert rmap.to_index(130) == 99
        assert rmap.to_index(166) == 135
        assert rmap.to_index(170) == 139
        assert rmap.to_index(237) == 206

    def test_accepts_string_author_numbers(self) -> None:
        """JSON bodies arrive with string keys."""
        assert kpc2_map().to_index("70") == 39

    def test_round_trips_back_to_author_numbering(self) -> None:
        """Results are shown in the numbering people use, not our indices."""
        rmap = kpc2_map()
        for author in KPC2_CATALYTIC:
            assert rmap.to_author(rmap.to_index(author)) == str(author)

    def test_to_indices_sorts_and_deduplicates(self) -> None:
        rmap = kpc2_map()
        assert rmap.to_indices([166, 70, 70, 130]) == (39, 99, 135)


class TestTheTrapItself:
    """Each of these is a mistake that would silently produce bad science."""

    def test_naive_offset_is_wrong_after_the_first_gap(self) -> None:
        """index = author - first_author. Right for Ser70 by luck, wrong after."""
        rmap = kpc2_map()
        first = int(rmap.author_range[0])

        # Before the gap at 58... wait, 70 is after it, and still happens to work
        # because the gap shifts by exactly one and Ser70's neighbour is also Ser.
        assert rmap.sequence[70 - first] == "S"  # coincidence, not correctness

        # Everything else is wrong.
        assert rmap.sequence[73 - first] == "G", "Lys73 would be read as Gly"
        assert rmap.sequence[130 - first] == "D", "Ser130 would be read as Asp"
        assert rmap.sequence[166 - first] == "L", "Glu166 would be read as Leu"
        assert rmap.sequence[170 - first] == "S", "Asn170 would be read as Ser"

    def test_passing_the_author_number_as_an_index_is_catastrophic(self) -> None:
        rmap = kpc2_map()
        # Intending the catalytic Ser70 but sending 70 as the index.
        assert rmap.sequence[70] == "A"
        assert rmap.to_author(70) == "101"
        # So the design would be aimed 31 residues away, at a different surface.

    def test_offset_and_correct_mapping_actually_disagree(self) -> None:
        """Guards the whole premise: if these agreed, none of this would matter."""
        rmap = kpc2_map()
        first = int(rmap.author_range[0])
        disagreements = [a for a in KPC2_CATALYTIC if rmap.to_index(a) != a - first]
        assert disagreements, "expected the offset shortcut to be wrong somewhere"


class TestRefusalRatherThanGuessing:
    def test_residue_before_the_modelled_range_is_refused(self) -> None:
        with pytest.raises(ResidueMappingError, match="not modelled"):
            kpc2_map().to_index(1)

    def test_residue_after_the_modelled_range_is_refused(self) -> None:
        with pytest.raises(ResidueMappingError, match="not modelled"):
            kpc2_map().to_index(999)

    def test_error_states_the_available_range(self) -> None:
        """So the researcher can act on it rather than guess again."""
        with pytest.raises(ResidueMappingError, match="30-292"):
            kpc2_map().to_index(1)

    def test_gap_residue_is_refused_with_a_specific_explanation(self) -> None:
        """58 is inside the range but absent: a disordered region."""
        with pytest.raises(ResidueMappingError, match="disordered"):
            kpc2_map().to_index(58)

    def test_all_failures_are_reported_at_once(self) -> None:
        """Otherwise fixing a 10-residue epitope takes 10 round trips."""
        with pytest.raises(ResidueMappingError) as exc:
            kpc2_map().to_indices([70, 1, 999, 166])
        message = str(exc.value)
        assert "1" in message
        assert "999" in message

    def test_partial_success_yields_nothing(self) -> None:
        """A partly-mapped epitope must not be silently accepted."""
        with pytest.raises(ResidueMappingError):
            kpc2_map().to_indices([70, 99999])

    def test_out_of_range_index_is_refused(self) -> None:
        with pytest.raises(ResidueMappingError, match="outside"):
            kpc2_map().to_author(10_000)


class TestVerification:
    def test_correct_expectations_pass(self) -> None:
        kpc2_map().verify_identities(KPC2_CATALYTIC)

    def test_three_letter_codes_are_accepted(self) -> None:
        """The literature writes Ser70, not S70."""
        kpc2_map().verify_identities({70: "Ser", 166: "Glu", 170: "Asn"})

    def test_mixed_case_is_accepted(self) -> None:
        kpc2_map().verify_identities({70: "ser", 166: "GLU"})

    def test_string_keys_are_accepted(self) -> None:
        kpc2_map().verify_identities({"70": "Ser"})

    def test_a_wrong_expectation_fails(self) -> None:
        """If this passed, verification would prove nothing."""
        with pytest.raises(ResidueMappingError, match=r"did not match|expected"):
            kpc2_map().verify_identities({70: "Trp"})

    def test_failure_names_the_residue_and_what_was_found(self) -> None:
        with pytest.raises(ResidueMappingError) as exc:
            kpc2_map().verify_identities({166: "Ala"})
        message = str(exc.value)
        assert "166" in message
        assert "E" in message

    def test_unmappable_residue_fails_verification(self) -> None:
        with pytest.raises(ResidueMappingError):
            kpc2_map().verify_identities({1: "Met"})

    def test_unrecognised_residue_code_is_rejected(self) -> None:
        with pytest.raises(ResidueMappingError, match="unrecognised"):
            kpc2_map().verify_identities({70: "Serine"})

    def test_all_mismatches_reported_together(self) -> None:
        with pytest.raises(ResidueMappingError) as exc:
            kpc2_map().verify_identities({70: "Trp", 166: "Ala"})
        assert "70" in str(exc.value)
        assert "166" in str(exc.value)


class TestConstructionSafety:
    def test_length_mismatch_is_refused(self) -> None:
        """A partial mapping would mis-place every residue after the gap."""
        with pytest.raises(ResidueMappingError, match="Refusing to map"):
            ResidueMap(
                pdb_id="XXXX",
                chain_id="A",
                sequence="ACDEF",
                author_numbers=("1", "2", "3"),
            )

    def test_contiguous_chain_is_recognised(self) -> None:
        rmap = ResidueMap(
            pdb_id="XXXX",
            chain_id="A",
            sequence="ACDEF",
            author_numbers=("1", "2", "3", "4", "5"),
        )
        assert rmap.is_contiguous is True
        assert rmap.missing_author_numbers == ()
        assert rmap.to_index(1) == 0

    def test_insertion_codes_do_not_crash(self) -> None:
        """Author numbering can contain codes like 52A, so int() must not be assumed."""
        rmap = ResidueMap(
            pdb_id="XXXX",
            chain_id="A",
            sequence="ACDE",
            author_numbers=("52", "52A", "53", "54"),
        )
        assert rmap.is_contiguous is False
        assert rmap.missing_author_numbers == ()
        assert rmap.to_index("52A") == 1
        assert rmap.residue_at_author("52A") == "C"

    def test_sequence_newlines_are_stripped(self) -> None:
        """RCSB wraps the one-letter sequence across lines."""
        rmap = ResidueMap.from_rcsb(
            pdb_id="XXXX",
            chain_id="A",
            sequence="AC\nDE",
            auth_to_entity_poly_seq_mapping=[1, 2, 3, 4],
        )
        assert rmap.sequence == "ACDE"
        assert rmap.length == 4


def uniprot_sequence() -> str:
    data = json.loads((Path(__file__).parent / "fixtures" / "uniprot" / "q9f663.json").read_text())
    return str(data["sequence"]["value"])


class TestThirdNumberingSystem:
    """UniProt positions are a third system, and they do not equal author numbers.

    UniProt annotates KPC-2's catalytic nucleophile at position 69. The
    literature and the PDB call the same residue Ser70. The discrepancy appears
    only after the author numbering's gap at 58, so it is not a fixed offset.
    """

    def test_structure_aligns_into_the_uniprot_sequence(self) -> None:
        """The crystal omits 32 of 293 residues, so it does not start at 1."""
        assert kpc2_map().reference_offset(uniprot_sequence()) == 29

    def test_uniprot_active_sites_map_to_the_catalytic_residues(self) -> None:
        """UniProt's curated annotations, converted correctly."""
        rmap, useq = kpc2_map(), uniprot_sequence()
        # (UniProt position, expected residue, the author number people cite)
        for position, expected, author in (
            (69, "S", "70"),
            (72, "K", "73"),
            (129, "S", "130"),
            (165, "E", "166"),
        ):
            index = rmap.index_from_uniprot_position(position, useq, expected=expected)
            assert rmap.sequence[index] == expected
            assert rmap.to_author(index) == author

    def test_uniprot_position_is_not_the_author_number(self) -> None:
        """The whole reason this method exists."""
        rmap, useq = kpc2_map(), uniprot_sequence()
        index = rmap.index_from_uniprot_position(69, useq)
        assert rmap.to_author(index) == "70"
        # Treating 69 as an author number silently selects a different residue.
        assert rmap.residue_at_author(69) == "C"
        assert rmap.sequence[index] == "S"

    def test_author_from_uniprot_position(self) -> None:
        rmap, useq = kpc2_map(), uniprot_sequence()
        assert rmap.author_from_uniprot_position(69, useq) == "70"

    def test_identity_mismatch_is_refused(self) -> None:
        """A wrong expectation must fail, or the check proves nothing."""
        rmap, useq = kpc2_map(), uniprot_sequence()
        with pytest.raises(ResidueMappingError, match="Refusing to guess"):
            rmap.index_from_uniprot_position(69, useq, expected="Trp")

    def test_position_outside_the_modelled_region_is_refused(self) -> None:
        rmap, useq = kpc2_map(), uniprot_sequence()
        with pytest.raises(ResidueMappingError, match="outside the modelled region"):
            rmap.index_from_uniprot_position(5, useq)

    def test_error_states_the_covered_range(self) -> None:
        rmap, useq = kpc2_map(), uniprot_sequence()
        with pytest.raises(ResidueMappingError, match="30-290"):
            rmap.index_from_uniprot_position(1, useq)

    def test_unalignable_reference_is_refused(self) -> None:
        with pytest.raises(ResidueMappingError, match="does not align"):
            kpc2_map().index_from_uniprot_position(70, "WWWWWWWWWWWWWWWWWWWWWWWWWWWWWW")

    def test_empty_reference_is_refused(self) -> None:
        with pytest.raises(ResidueMappingError, match="reference sequence is required"):
            kpc2_map().reference_offset("")

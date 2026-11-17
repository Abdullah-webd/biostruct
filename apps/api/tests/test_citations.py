"""The citation guardrail (M4).

These are adversarial on purpose. The threat is not a buggy parser — it is a
model that produces a plausible, well-formatted, entirely fictional reference,
which a researcher then follows to nothing. A fabricated citation is worse than
no citation, because a reference carries authority.

So most of what is tested here is the behaviour when the model misbehaves.

No network: the resolver is a function, which is also how the real one is
injected.
"""

from __future__ import annotations

from typing import Any

import pytest

from biostruct.agent.citations import (
    Reason,
    extract_markers,
    reference_list,
    verify,
)
from biostruct.sources.europepmc import Paper

REAL = Paper(
    source="MED",
    external_id="42942151",
    title="Carbapenemase gene profiles and ceftazidime-avibactam resistance",
    authors="Okafor A, Bello T",
    journal="Int J Mol Sci",
    year="2026",
    doi="10.3390/ijms27209386",
)
SECOND = Paper(
    source="MED",
    external_id="42942076",
    title="Phenotypic evaluation of carbapenemases in Klebsiella",
    authors="Adeyemi K",
    journal="Int J Mol Sci",
    year="2026",
)
PREPRINT = Paper(
    source="PPR",
    external_id="999001",
    title="An unreviewed claim about resistance",
    year="2026",
)


def resolver_for(*papers: Paper) -> Any:
    """A resolver that knows only these papers — like the real source."""
    known = {p.key: p for p in papers}

    async def resolve(source: str, external_id: str) -> Paper | None:
        return known.get(f"{source}:{external_id}")

    return resolve


class TestMarkers:
    def test_markers_are_extracted_in_order(self) -> None:
        text = "First [cite:MED:1] then [cite:PPR:2]."
        assert extract_markers(text) == [("MED", "1"), ("PPR", "2")]

    def test_a_bare_number_is_not_a_marker(self) -> None:
        """A numbered reference cannot be verified: every integer looks valid.
        The model has to name the record."""
        assert extract_markers("As shown previously [1], resistance spreads.") == []

    def test_prose_that_merely_mentions_a_pmid_is_not_a_marker(self) -> None:
        assert extract_markers("See PMID 42942151 for details.") == []


class TestHonestAnswers:
    @pytest.mark.anyio
    async def test_a_properly_cited_claim_survives_and_is_numbered(self) -> None:
        answer = await verify(
            "KPC cuts carbapenems apart [cite:MED:42942151].",
            available=[REAL],
            resolve=resolver_for(REAL),
        )
        assert answer.text == "KPC cuts carbapenems apart [1]."
        assert answer.papers == (REAL,)
        assert answer.is_clean
        assert answer.warning == ""

    @pytest.mark.anyio
    async def test_repeated_citations_reuse_one_number(self) -> None:
        answer = await verify(
            "One [cite:MED:42942151]. Two [cite:MED:42942076]. Again [cite:MED:42942151].",
            available=[REAL, SECOND],
            resolve=resolver_for(REAL, SECOND),
        )
        assert answer.text == "One [1]. Two [2]. Again [1]."
        assert len(answer.papers) == 2

    @pytest.mark.anyio
    async def test_an_uncited_answer_is_left_alone(self) -> None:
        answer = await verify(
            "Carbapenems are last-resort antibiotics.",
            available=[REAL],
            resolve=resolver_for(REAL),
        )
        assert answer.text == "Carbapenems are last-resort antibiotics."
        assert answer.papers == ()
        assert answer.is_clean


class TestFabrication:
    """The case this module exists for."""

    @pytest.mark.anyio
    async def test_an_invented_paper_is_stripped(self) -> None:
        """No tool returned MED:11111111, so the model made it up — even
        though the format is perfect and the id looks real."""
        answer = await verify(
            "Resistance is rising sharply [cite:MED:11111111].",
            available=[REAL],
            resolve=resolver_for(REAL),
        )
        assert "11111111" not in answer.text
        assert "[cite:" not in answer.text
        assert answer.papers == ()
        assert answer.removed[0].reason == Reason.FABRICATED

    @pytest.mark.anyio
    async def test_the_claim_survives_but_loses_its_false_authority(self) -> None:
        """We do not delete what the model said. A researcher is entitled to
        see the claim, and to see that it was unsupported."""
        answer = await verify(
            "Resistance is rising sharply [cite:MED:11111111].",
            available=[REAL],
            resolve=resolver_for(REAL),
        )
        assert "Resistance is rising sharply" in answer.text
        assert answer.flagged_sentences == ("Resistance is rising sharply.",)

    @pytest.mark.anyio
    async def test_a_real_id_the_model_never_saw_is_still_fabrication(self) -> None:
        """Provenance, not just existence. Citing a genuine paper no tool
        returned means the model is citing from memory, which is exactly the
        behaviour that produces confident nonsense."""
        answer = await verify(
            "A claim [cite:MED:42942076].",
            available=[REAL],
            resolve=resolver_for(REAL, SECOND),
        )
        assert answer.papers == ()
        assert answer.removed[0].reason == Reason.FABRICATED

    @pytest.mark.anyio
    async def test_a_vanished_paper_is_removed_as_unresolvable(self) -> None:
        """Provenance passed but the source no longer has it."""
        answer = await verify(
            "A claim [cite:MED:42942151].",
            available=[REAL],
            resolve=resolver_for(),
        )
        assert answer.papers == ()
        assert answer.removed[0].reason == Reason.UNRESOLVABLE

    @pytest.mark.anyio
    async def test_good_and_bad_citations_in_one_answer(self) -> None:
        answer = await verify(
            "True thing [cite:MED:42942151]. Invented thing [cite:MED:11111111].",
            available=[REAL],
            resolve=resolver_for(REAL),
        )
        assert answer.text == "True thing [1]. Invented thing."
        assert answer.papers == (REAL,)
        assert len(answer.removed) == 1
        assert answer.flagged_sentences == ("Invented thing.",)

    @pytest.mark.anyio
    async def test_the_user_is_told_rather_than_quietly_corrected(self) -> None:
        answer = await verify(
            "A [cite:MED:11111111]. B [cite:MED:22222222].",
            available=[],
            resolve=resolver_for(),
        )
        assert not answer.is_clean
        assert "2 references" in answer.warning
        assert "could not be verified" in answer.warning

    @pytest.mark.anyio
    async def test_punctuation_survives_a_stripped_marker(self) -> None:
        answer = await verify(
            "A claim [cite:MED:11111111], and another thing.",
            available=[],
            resolve=resolver_for(),
        )
        assert answer.text == "A claim, and another thing."


class TestSourceOutage:
    @pytest.mark.anyio
    async def test_a_resolver_crash_drops_the_citation_rather_than_the_answer(
        self,
    ) -> None:
        """Europe PMC being down must not 500 a research turn — but it must
        also not let an unchecked reference through."""

        async def exploding(source: str, external_id: str) -> Paper | None:
            raise RuntimeError("Europe PMC unreachable")

        answer = await verify(
            "A claim [cite:MED:42942151].",
            available=[REAL],
            resolve=exploding,
        )
        assert answer.papers == ()
        assert answer.removed[0].reason == Reason.UNRESOLVABLE
        assert "A claim" in answer.text

    @pytest.mark.anyio
    async def test_each_distinct_paper_is_resolved_once(self) -> None:
        calls: list[str] = []

        async def counting(source: str, external_id: str) -> Paper | None:
            calls.append(f"{source}:{external_id}")
            return REAL if external_id == REAL.external_id else None

        await verify(
            "A [cite:MED:42942151]. B [cite:MED:42942151]. C [cite:MED:42942151].",
            available=[REAL],
            resolve=counting,
        )
        assert calls == ["MED:42942151"]


class TestReferenceList:
    def test_references_are_numbered_and_linkable(self) -> None:
        refs = reference_list([REAL, SECOND])
        assert refs[0]["n"] == "1"
        assert refs[0]["url"].endswith("MED/42942151")
        assert REAL.title in refs[0]["citation"]
        assert refs[1]["n"] == "2"

    def test_a_preprint_is_labelled_as_unreviewed(self) -> None:
        """A reader cannot otherwise tell, and the distinction matters."""
        refs = reference_list([PREPRINT])
        assert refs[0]["note"] == "preprint, not peer reviewed"

    def test_a_peer_reviewed_paper_carries_no_warning(self) -> None:
        assert reference_list([REAL])[0]["note"] == ""

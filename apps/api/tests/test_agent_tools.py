"""The tool layer (M7).

The tests that matter here are the ones about *refusing*: a paid tool must not
spend, an unknown tool must not crash a turn, and a tool failure must reach the
model as something it can act on rather than as a 500 in front of a researcher.

No network: sources are stubs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from biostruct.agent.tools.base import (
    Proposal,
    Registry,
    Tool,
    ToolContext,
    ToolError,
    ToolResultData,
    text,
)
from biostruct.agent.tools.science import build_registry
from biostruct.library import SCREEN_USD_PER_MOLECULE
from biostruct.sources.europepmc import Paper

PAPER = Paper(
    source="MED",
    external_id="42942151",
    title="Carbapenemase gene profiles in Klebsiella",
    journal="Int J Mol Sci",
    year="2026",
)


class StubEuropePMC:
    def __init__(self, papers: list[Paper] | None = None) -> None:
        self._papers = papers if papers is not None else [PAPER]

    async def search(self, query: str, *, limit: int = 10) -> list[Paper]:
        return self._papers

    async def search_for_resistance(
        self, enzyme: str, organism: str = "", *, limit: int = 10
    ) -> list[Paper]:
        return self._papers


def ctx(**kwargs: Any) -> ToolContext:
    return ToolContext(user_id="u", session_id="s", **kwargs)


class TestSchemas:
    def test_every_tool_declares_a_strict_schema(self) -> None:
        """``strict: true`` requires additionalProperties false AND every
        property in required. The API rejects a schema missing either, so the
        registry builds it rather than letting each tool hand-write one."""
        for schema in build_registry().schemas():
            params = schema["parameters"]
            assert schema["strict"] is True
            assert params["additionalProperties"] is False
            assert set(params["required"]) == set(params["properties"])

    def test_tool_names_contain_no_dots(self) -> None:
        """A dotted name is rejected by the API's function-name validation."""
        assert all("." not in name for name in build_registry().names)

    def test_every_tool_description_says_whether_it_costs_money(self) -> None:
        """This is the model's only signal at selection time. A paid tool whose
        description does not say so will be called casually."""
        for tool in [build_registry().get(n) for n in build_registry().names]:
            assert tool is not None
            lowered = tool.description.lower()
            if tool.spend == "paid":
                assert "costs money" in lowered
                assert "proposal" in lowered
            else:
                assert "free" in lowered

    def test_the_literature_tools_state_the_citation_rule(self) -> None:
        """The model must know the rule at the point of use, not only from the
        system prompt."""
        registry = build_registry()
        for name in ("literature_search", "resistance_literature"):
            tool = registry.get(name)
            assert tool is not None
            assert "cite" in tool.description.lower()


class TestPaidToolsCannotSpend:
    @pytest.mark.anyio
    async def test_a_fold_request_returns_a_priced_proposal(self) -> None:
        result = await build_registry().run(
            "propose_fold", {"accession": "Q9F663", "num_samples": 2}, ctx()
        )
        assert isinstance(result, Proposal)
        assert result.estimate_usd == Decimal("0.0500")
        assert "Q9F663" in result.title

    @pytest.mark.anyio
    async def test_a_screen_request_returns_a_priced_proposal(self) -> None:
        result = await build_registry().run(
            "propose_screen", {"accession": "Q9F663", "num_molecules": 20}, ctx()
        )
        assert isinstance(result, Proposal)
        assert result.estimate_usd == SCREEN_USD_PER_MOLECULE * 20

    @pytest.mark.anyio
    async def test_no_paid_tool_ever_returns_a_result(self) -> None:
        """The structural guarantee: not one paid tool has a path that does the
        work. The model cannot phrase its way around this because the
        dispatcher decides, not the prompt."""
        registry = build_registry()
        paid = [registry.get(n) for n in registry.names]
        for tool in paid:
            assert tool is not None
            if tool.spend != "paid":
                continue
            result = await registry.run(
                tool.name, {"accession": "Q9F663", "num_molecules": 12, "num_samples": 1}, ctx()
            )
            assert isinstance(result, Proposal), tool.name

    @pytest.mark.anyio
    async def test_an_absurd_screen_size_is_refused_with_a_price(self) -> None:
        result = await build_registry().run(
            "propose_screen", {"accession": "Q9F663", "num_molecules": 500_000}, ctx()
        )
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "cap a screen" in result.summary

    @pytest.mark.anyio
    async def test_folding_with_neither_sequence_nor_accession_is_refused(self) -> None:
        result = await build_registry().run("propose_fold", {}, ctx())
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "sequence" in result.summary


class TestLiterature:
    @pytest.mark.anyio
    async def test_results_carry_the_exact_markers_the_model_must_copy(self) -> None:
        result = await build_registry().run(
            "literature_search",
            {"query": "KPC Klebsiella", "limit": 5},
            ctx(europepmc=StubEuropePMC()),
        )
        assert isinstance(result, ToolResultData)
        assert "[cite:MED:42942151]" in result.summary
        assert result.papers == (PAPER,)

    @pytest.mark.anyio
    async def test_papers_are_recorded_on_the_context_for_the_verifier(self) -> None:
        """The provenance half of the M4 check reads exactly this list and
        nothing else."""
        context = ctx(europepmc=StubEuropePMC())
        await build_registry().run("literature_search", {"query": "KPC"}, context)
        assert context.papers_seen == [PAPER]

    @pytest.mark.anyio
    async def test_an_empty_result_tells_the_model_not_to_cite(self) -> None:
        """The dangerous case: nothing found, and the model fills the gap from
        memory. The summary has to say so in words the model will follow."""
        result = await build_registry().run(
            "literature_search", {"query": "nothing"}, ctx(europepmc=StubEuropePMC([]))
        )
        assert isinstance(result, ToolResultData)
        assert "Do not cite" in result.summary
        assert result.papers == ()

    @pytest.mark.anyio
    async def test_a_preprint_is_labelled_in_the_summary(self) -> None:
        preprint = Paper(source="PPR", external_id="999", title="Unreviewed", year="2026")
        result = await build_registry().run(
            "literature_search", {"query": "x"}, ctx(europepmc=StubEuropePMC([preprint]))
        )
        assert isinstance(result, ToolResultData)
        assert "not peer reviewed" in result.summary

    @pytest.mark.anyio
    async def test_an_unreachable_source_is_reported_not_papered_over(self) -> None:
        class Broken:
            async def search(self, query: str, *, limit: int = 10) -> list[Paper]:
                from biostruct.sources.http import SourceUnavailable

                raise SourceUnavailable("europepmc", "connection refused")

        result = await build_registry().run(
            "literature_search", {"query": "x"}, ctx(europepmc=Broken())
        )
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "could not be reached" in result.summary
        assert "from memory" in result.summary


class TestLibraryTool:
    @pytest.mark.anyio
    async def test_a_library_comes_back_with_its_control_named(self) -> None:
        result = await build_registry().run(
            "molecules_library", {"ambler_class": "A", "size": 6}, ctx()
        )
        assert isinstance(result, ToolResultData)
        assert "Avibactam" in result.summary
        assert "$" in result.summary
        assert any(e["is_control"] for e in result.payload["entries"])

    @pytest.mark.anyio
    async def test_the_model_is_told_what_a_failed_control_means(self) -> None:
        """Otherwise it reports the other hits as though they meant something."""
        result = await build_registry().run("molecules_library", {"ambler_class": "A"}, ctx())
        assert isinstance(result, ToolResultData)
        assert "not telling us anything" in result.summary

    @pytest.mark.anyio
    async def test_a_trial_stage_control_is_flagged_as_not_approved(self) -> None:
        """Class B's control is taniborbactam, which is in trials. Describing
        it as a licensed drug would be a false claim about a medicine."""
        result = await build_registry().run("molecules_library", {"ambler_class": "B"}, ctx())
        assert isinstance(result, ToolResultData)
        assert "not an approved drug" in result.summary

    @pytest.mark.anyio
    async def test_an_unknown_class_is_refused_rather_than_screened_blind(self) -> None:
        result = await build_registry().run("molecules_library", {"ambler_class": "Z"}, ctx())
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "No positive control" in result.summary

    @pytest.mark.anyio
    async def test_a_library_produces_an_artifact_for_the_canvas(self) -> None:
        result = await build_registry().run("molecules_library", {"ambler_class": "A"}, ctx())
        assert isinstance(result, ToolResultData)
        assert result.artifact is not None
        assert result.artifact["kind"] == "library"


class TestDispatcher:
    @pytest.mark.anyio
    async def test_an_unknown_tool_is_a_result_the_model_can_recover_from(self) -> None:
        """Not an exception: the researcher should not see a crash because the
        model misremembered a name."""
        result = await build_registry().run("make_me_a_sandwich", {}, ctx())
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "no tool called" in result.summary
        assert "target_find" in result.summary, "it should list what does exist"

    @pytest.mark.anyio
    async def test_a_crashing_tool_does_not_take_the_turn_down(self) -> None:
        async def explode(args: dict[str, Any], context: ToolContext) -> ToolResultData:
            raise RuntimeError("divided by zero")

        registry = Registry(
            [
                Tool(
                    name="boom",
                    description="free, for testing",
                    properties={"x": text("anything")},
                    required=(),
                    handler=explode,
                )
            ]
        )
        result = await registry.run("boom", {}, ctx())
        assert isinstance(result, ToolResultData)
        assert not result.ok
        assert "divided by zero" in result.summary
        assert "rather than guessing" in result.summary

    @pytest.mark.anyio
    async def test_a_tool_error_is_phrased_for_the_model(self) -> None:
        async def refuse(args: dict[str, Any], context: ToolContext) -> ToolResultData:
            raise ToolError("accession is required and was empty.")

        registry = Registry(
            [
                Tool(
                    name="picky",
                    description="free",
                    properties={"x": text("anything")},
                    required=(),
                    handler=refuse,
                )
            ]
        )
        result = await registry.run("picky", {}, ctx())
        assert isinstance(result, ToolResultData)
        assert result.summary == "accession is required and was empty."

    def test_two_tools_cannot_share_a_name(self) -> None:
        async def noop(args: dict[str, Any], context: ToolContext) -> ToolResultData:
            return ToolResultData(summary="")

        tool = Tool(name="same", description="free", properties={}, required=(), handler=noop)
        with pytest.raises(ValueError, match="two tools named"):
            Registry([tool, tool])

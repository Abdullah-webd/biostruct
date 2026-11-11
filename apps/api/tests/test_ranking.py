"""Ranking tests, including property tests on the arithmetic.

Ranking is pure code with no model call (ADR-008), which means it can be tested
exhaustively — and must be, because it is the output a researcher would have to
defend. The properties checked here are the ones a reviewer would ask about:
does raising a weight actually favour that metric, is a lower PAE really better,
and does a missing metric avoid being silently scored as zero.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from biostruct.ranking import (
    HIGHER_IS_BETTER,
    RANKED_METRICS,
    MetricInput,
    RankingError,
    Weights,
    explain,
    rank_candidates,
    verdict_for,
)


def candidate(
    cid: str,
    *,
    binding: float | None = 0.7,
    iptm: float | None = 0.7,
    pae: float | None = 5.0,
    structure: float | None = 0.8,
    ipsae: float | None = None,
) -> MetricInput:
    """A candidate shaped like the real ones: ipsae_min absent by default."""
    return MetricInput(
        candidate_id=cid,
        binding_confidence=binding,
        iptm=iptm,
        min_interaction_pae=pae,
        structure_confidence=structure,
        ipsae_min=ipsae,
    )


class TestWeights:
    def test_defaults_sum_to_one(self) -> None:
        assert Weights().total == Decimal("1.00")

    def test_binding_confidence_leads(self) -> None:
        """The SDK calls it the primary metric for hit discovery."""
        weights = Weights()
        assert weights.binding_confidence == max(weights.as_mapping().values())

    def test_negative_weight_is_refused(self) -> None:
        with pytest.raises(RankingError, match="must not be negative"):
            Weights(iptm=Decimal("-0.1"))

    def test_all_zero_is_refused(self) -> None:
        with pytest.raises(RankingError, match="greater than zero"):
            Weights(**{m: Decimal("0") for m in RANKED_METRICS})  # type: ignore[arg-type]

    def test_float_weight_is_refused(self) -> None:
        """Weights must be reproducible, so no binary floats."""
        with pytest.raises(RankingError, match="must be a Decimal"):
            Weights(iptm=0.2)  # type: ignore[arg-type]

    def test_from_mapping_accepts_strings_and_floats(self) -> None:
        weights = Weights.from_mapping({"binding_confidence": "0.5", "iptm": 0.25})
        assert weights.binding_confidence == Decimal("0.5")
        assert weights.iptm == Decimal("0.25")


class TestBasicRanking:
    def test_better_binding_confidence_ranks_higher(self) -> None:
        result = rank_candidates([candidate("low", binding=0.3), candidate("high", binding=0.9)])
        assert [c.candidate_id for c in result.ranked] == ["high", "low"]
        assert result.ranked[0].rank == 1
        assert result.ranked[1].rank == 2

    def test_scores_are_between_zero_and_one(self) -> None:
        result = rank_candidates(
            [candidate(f"c{i}", binding=i / 10, iptm=i / 10) for i in range(1, 10)]
        )
        assert all(Decimal("0") <= c.score <= Decimal("1") for c in result.ranked)

    def test_a_single_candidate_is_ranked_first_at_full_percentile(self) -> None:
        result = rank_candidates([candidate("only")])
        assert result.count == 1
        assert result.ranked[0].rank == 1
        assert result.ranked[0].percentile == Decimal("100.0000")

    def test_empty_input_is_an_empty_result(self) -> None:
        assert rank_candidates([]).count == 0

    def test_ranking_is_deterministic(self) -> None:
        inputs = [candidate(f"c{i}", binding=0.5 + i / 100) for i in range(10)]
        first = rank_candidates(inputs)
        second = rank_candidates(inputs)
        assert [c.candidate_id for c in first.ranked] == [c.candidate_id for c in second.ranked]
        assert [c.score for c in first.ranked] == [c.score for c in second.ranked]

    def test_exact_ties_break_on_id_not_input_order(self) -> None:
        """So the order does not wobble between identical runs."""
        forward = rank_candidates([candidate("b"), candidate("a")])
        backward = rank_candidates([candidate("a"), candidate("b")])
        assert [c.candidate_id for c in forward.ranked] == ["a", "b"]
        assert [c.candidate_id for c in backward.ranked] == ["a", "b"]

    def test_percentiles_span_the_field(self) -> None:
        result = rank_candidates([candidate(f"c{i}", binding=i / 10) for i in range(1, 6)])
        assert result.ranked[0].percentile == Decimal("100.0000")
        assert result.ranked[-1].percentile == Decimal("0.0000")


class TestPaeIsInverted:
    """The one metric where lower is better. Getting it backwards would invert
    the whole ranking for interface quality."""

    def test_metric_direction_is_declared(self) -> None:
        assert HIGHER_IS_BETTER["min_interaction_pae"] is False
        assert all(HIGHER_IS_BETTER[m] for m in RANKED_METRICS if m != "min_interaction_pae")

    def test_lower_pae_ranks_higher(self) -> None:
        result = rank_candidates(
            [
                candidate("bad_pae", pae=20.0),
                candidate("good_pae", pae=2.0),
            ]
        )
        assert [c.candidate_id for c in result.ranked] == ["good_pae", "bad_pae"]

    def test_lowest_pae_normalises_to_one(self) -> None:
        result = rank_candidates([candidate("a", pae=2.0), candidate("b", pae=20.0)])
        best = result.by_id("a")
        assert best is not None
        pae = next(c for c in best.contributions if c.metric == "min_interaction_pae")
        assert pae.normalised == Decimal("1.0000")
        assert pae.higher_is_better is False

    def test_pae_is_the_only_inverted_metric(self) -> None:
        """A sanity check that no other metric got flipped by accident."""
        for metric in RANKED_METRICS:
            if metric == "min_interaction_pae":
                continue
            # binding_confidence is always present, so every candidate counts as
            # a binder; the metric under test is what differs.
            base: dict[str, float] = {"binding_confidence": 0.5}
            worse = MetricInput(candidate_id="worse", **{**base, metric: 0.1})  # type: ignore[arg-type]
            better = MetricInput(candidate_id="better", **{**base, metric: 0.9})  # type: ignore[arg-type]
            result = rank_candidates([worse, better])
            assert result.ranked[0].candidate_id == "better", f"{metric} ranked backwards"


class TestMissingMetrics:
    """ipsae_min was absent from every live result, so this path is the norm."""

    def test_absent_metric_is_not_scored_as_zero(self) -> None:
        """The bug this guards: a missing metric dragging a good candidate down."""
        with_ipsae = candidate("with", ipsae=0.9)
        without = candidate("without", ipsae=None)
        result = rank_candidates([with_ipsae, without])
        scored = result.by_id("without")
        assert scored is not None
        # Everything it does have is identical to the other candidate, so its
        # score must not be dragged down by the absence.
        assert scored.score >= Decimal("0.4")

    def test_weight_is_redistributed_and_reported(self) -> None:
        result = rank_candidates([candidate("a"), candidate("b", binding=0.9)])
        first = result.ranked[0]
        assert "ipsae_min" in first.missing_metrics
        assert first.weight_redistributed is True
        assert first.has_partial_metrics is True

    def test_redistributed_weights_sum_to_one(self) -> None:
        """Otherwise scores from different candidates are not comparable."""
        result = rank_candidates([candidate("a"), candidate("b", binding=0.9)])
        for ranked in result.ranked:
            total = sum((c.weight for c in ranked.contributions), Decimal("0"))
            assert abs(total - Decimal("1")) < Decimal("0.001"), f"{ranked.candidate_id}"

    def test_a_metric_absent_everywhere_is_reported(self) -> None:
        result = rank_candidates([candidate("a"), candidate("b")])
        assert "ipsae_min" in result.metrics_absent_everywhere

    def test_candidate_with_only_one_metric_still_scores(self) -> None:
        result = rank_candidates(
            [
                MetricInput(candidate_id="sparse", binding_confidence=0.8),
                candidate("full"),
            ]
        )
        sparse = result.by_id("sparse")
        assert sparse is not None
        assert len(sparse.contributions) == 1
        assert sparse.score > 0

    def test_zero_weighted_metrics_are_not_reported_as_missing(self) -> None:
        """If the researcher set a weight to zero, its absence is irrelevant."""
        weights = Weights(
            binding_confidence=Decimal("1"),
            iptm=Decimal("0"),
            ipsae_min=Decimal("0"),
            min_interaction_pae=Decimal("0"),
            structure_confidence=Decimal("0"),
        )
        result = rank_candidates([candidate("a"), candidate("b", binding=0.9)], weights)
        assert result.ranked[0].missing_metrics == ()


class TestNonBinders:
    def test_candidates_without_binding_metrics_are_excluded(self) -> None:
        """There is no interface to score, so they are not ranked at all."""
        generic = MetricInput(candidate_id="generic", structure_confidence=0.9)
        result = rank_candidates([generic, candidate("binder")])
        assert [c.candidate_id for c in result.ranked] == ["binder"]
        assert result.excluded[0][0] == "generic"
        assert "binding metrics" in result.excluded[0][1]

    def test_excluded_candidates_do_not_affect_the_ranking(self) -> None:
        binders = [candidate("a", binding=0.9), candidate("b", binding=0.1)]
        with_generic = rank_candidates(
            [*binders, MetricInput(candidate_id="g", structure_confidence=0.5)]
        )
        without = rank_candidates(binders)
        assert [c.score for c in with_generic.ranked] == [c.score for c in without.ranked]

    def test_all_non_binders_yields_nothing_ranked(self) -> None:
        result = rank_candidates(
            [MetricInput(candidate_id=f"g{i}", structure_confidence=0.5) for i in range(3)]
        )
        assert result.count == 0
        assert len(result.excluded) == 3


class TestWeightSensitivity:
    """Changing a weight must actually change the ranking in the right direction.

    This is the property that makes the weights meaningful rather than
    decorative.
    """

    def test_raising_a_weight_favours_candidates_strong_in_that_metric(self) -> None:
        strong_binding = candidate("binding_star", binding=0.95, iptm=0.2)
        strong_iptm = candidate("iptm_star", binding=0.2, iptm=0.95)

        binding_heavy = Weights(
            binding_confidence=Decimal("0.9"),
            iptm=Decimal("0.1"),
            ipsae_min=Decimal("0"),
            min_interaction_pae=Decimal("0"),
            structure_confidence=Decimal("0"),
        )
        iptm_heavy = Weights(
            binding_confidence=Decimal("0.1"),
            iptm=Decimal("0.9"),
            ipsae_min=Decimal("0"),
            min_interaction_pae=Decimal("0"),
            structure_confidence=Decimal("0"),
        )
        assert (
            rank_candidates([strong_binding, strong_iptm], binding_heavy).ranked[0].candidate_id
            == "binding_star"
        )
        assert (
            rank_candidates([strong_binding, strong_iptm], iptm_heavy).ranked[0].candidate_id
            == "iptm_star"
        )

    def test_zero_weight_removes_a_metric_from_consideration(self) -> None:
        only_pae_differs = [candidate("a", pae=1.0), candidate("b", pae=30.0)]
        ignore_pae = Weights(
            binding_confidence=Decimal("0.5"),
            iptm=Decimal("0.5"),
            ipsae_min=Decimal("0"),
            min_interaction_pae=Decimal("0"),
            structure_confidence=Decimal("0"),
        )
        result = rank_candidates(only_pae_differs, ignore_pae)
        assert result.ranked[0].score == result.ranked[1].score

    def test_reranking_is_free_of_the_original_order(self) -> None:
        """Weights are applied to stored metrics, so a re-rank is a recompute."""
        # Binding and PAE must disagree, or both weightings would give the same
        # order and the test would prove nothing.
        inputs = [
            candidate("a", binding=0.9, pae=20.0),  # best binding, worst interface
            candidate("b", binding=0.5, pae=10.0),
            candidate("c", binding=0.1, pae=2.0),  # worst binding, best interface
        ]
        default_order = [c.candidate_id for c in rank_candidates(inputs).ranked]
        pae_only = Weights(
            binding_confidence=Decimal("0"),
            iptm=Decimal("0"),
            ipsae_min=Decimal("0"),
            min_interaction_pae=Decimal("1"),
            structure_confidence=Decimal("0"),
        )
        pae_order = [c.candidate_id for c in rank_candidates(inputs, pae_only).ranked]
        assert default_order[0] == "a", "binding-weighted default should favour a"
        assert pae_order[0] == "c", "PAE-only weighting should favour c"
        assert default_order != pae_order


class TestConstantMetrics:
    def test_an_identical_metric_is_flagged_as_non_discriminating(self) -> None:
        """Mapping it to 0 would punish everyone; to 1 would reward everyone."""
        result = rank_candidates(
            [candidate("a", structure=0.8), candidate("b", structure=0.8, binding=0.9)]
        )
        assert "structure_confidence" in result.constant_metrics
        contribution = next(
            c for c in result.ranked[0].contributions if c.metric == "structure_confidence"
        )
        assert contribution.is_constant is True
        assert contribution.normalised == Decimal("0.5000")

    def test_all_metrics_identical_gives_equal_scores(self) -> None:
        result = rank_candidates([candidate("a"), candidate("b"), candidate("c")])
        assert len({c.score for c in result.ranked}) == 1


class TestContributions:
    def test_contributions_sum_to_the_score(self) -> None:
        """So a researcher can audit where a score came from."""
        result = rank_candidates(
            [candidate("a", binding=0.8, iptm=0.6), candidate("b", binding=0.2)]
        )
        for ranked in result.ranked:
            total = sum((c.contribution for c in ranked.contributions), Decimal("0"))
            assert abs(total - ranked.score) < Decimal("0.001")

    def test_raw_values_are_preserved(self) -> None:
        result = rank_candidates([candidate("a", binding=0.8765), candidate("b")])
        contribution = next(
            c
            for c in result.by_id("a").contributions  # type: ignore[union-attr]
            if c.metric == "binding_confidence"
        )
        assert contribution.raw == 0.8765

    def test_strongest_and_weakest_ignore_constant_metrics(self) -> None:
        result = rank_candidates(
            [candidate("a", binding=0.9, structure=0.8), candidate("b", binding=0.1, structure=0.8)]
        )
        top = result.ranked[0]
        assert top.strongest_metric is not None
        assert top.strongest_metric.is_constant is False
        assert top.weakest_metric is not None


class TestExplanation:
    def test_explanation_quotes_only_real_numbers(self) -> None:
        """What the Scientific Review guardrail checks narratives against."""
        result = rank_candidates([candidate("a", binding=0.9), candidate("b", binding=0.1)])
        top = result.ranked[0]
        text = explain(top, result)
        assert str(top.rank) in text
        assert str(top.score) in text
        assert "0.9" in text

    def test_explanation_states_the_limitation(self) -> None:
        """Never implies the candidate binds or works."""
        result = rank_candidates([candidate("a"), candidate("b", binding=0.9)])
        text = explain(result.ranked[0], result)
        assert "not whether any of them binds" in text
        assert "therapeutic effect" in text

    def test_explanation_mentions_redistribution(self) -> None:
        result = rank_candidates([candidate("a"), candidate("b", binding=0.9)])
        text = explain(result.ranked[0], result)
        assert "redistributed" in text
        assert "rather than counted as zero" in text

    def test_explanation_makes_no_efficacy_claim(self) -> None:
        result = rank_candidates([candidate("a", binding=0.99), candidate("b", binding=0.1)])
        text = explain(result.ranked[0], result).lower()
        for banned in ("will bind", "effective", "potent", "cure", "treat", "drug"):
            assert banned not in text, f"explanation must not say {banned!r}"

    def test_verdict_bands(self) -> None:
        result = rank_candidates([candidate(f"c{i}", binding=i / 10) for i in range(1, 11)])
        # Even the best wording is about ordering a queue, not about efficacy.
        assert verdict_for(result.ranked[0]) == "worth_testing_first"
        # The weakest has a binding confidence of 0.1, which is the moderate
        # floor, so it is weak on the composite rather than a non-binder.
        assert verdict_for(result.ranked[-1]) in ("weak", "mixed")

    def test_being_top_of_a_hopeless_field_is_not_encouraging(self) -> None:
        """A single candidate is always rank 1, which says nothing about quality."""
        result = rank_candidates([candidate("only", binding=1e-6)])
        assert result.ranked[0].rank == 1
        assert result.ranked[0].score >= Decimal("0.5")
        assert verdict_for(result.ranked[0]) == "no_predicted_binding"


class TestRealisticRun:
    """Against the metric shapes the live API actually returned."""

    def test_ten_candidates_rank_sensibly(self) -> None:
        observed = [
            ("res_000", 0.7286, 0.7925, 11.56, 0.8805),
            ("res_001", 0.7889, 0.6566, 4.24, 0.7497),
            ("res_002", 0.7177, 0.8010, 8.10, 0.7585),
            ("res_003", 0.4720, 0.6919, 4.12, 0.9162),
            ("res_004", 0.6283, 0.6492, 6.66, 0.8634),
            ("res_005", 0.4473, 0.5185, 7.47, 0.5884),
            ("res_006", 0.7815, 0.7328, 7.80, 0.5062),
            ("res_007", 0.8338, 0.5436, 3.94, 0.7468),
            ("res_008", 0.5362, 0.7091, 3.32, 0.8975),
            ("res_009", 0.5352, 0.9247, 4.35, 0.6744),
        ]
        result = rank_candidates(
            [
                MetricInput(
                    candidate_id=cid,
                    binding_confidence=binding,
                    iptm=iptm,
                    min_interaction_pae=pae,
                    structure_confidence=structure,
                    ipsae_min=None,  # absent in every live result
                )
                for cid, binding, iptm, pae, structure in observed
            ]
        )
        assert result.count == 10
        assert result.metrics_absent_everywhere == ("ipsae_min",)
        # Ranks are distinct and ordered.
        assert [c.rank for c in result.ranked] == list(range(1, 11))
        scores = [c.score for c in result.ranked]
        assert scores == sorted(scores, reverse=True)
        # The worst candidate on every count should not come first.
        assert result.ranked[-1].candidate_id == "res_005"
        assert explain(result.ranked[0], result)

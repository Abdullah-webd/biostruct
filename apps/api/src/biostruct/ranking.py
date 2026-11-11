"""Candidate ranking: deterministic, explainable, and free of any model call.

No LLM touches this (ADR-008). A ranking a researcher cannot reproduce is one
they cannot defend in a paper, so every number here comes out of arithmetic on
stored metrics. The Evaluation Agent will *narrate* these results; it will not
produce them.

Three rules shape the implementation, each learned from the live API:

* **Only binder results are ranked.** Binder-ness is decided by the presence of
  binding metrics, because the live API does not return the ``type``
  discriminator its SDK declares (docs/boltz-api-observed.md).
* **A missing metric has its weight redistributed**, never defaulted to zero.
  ``ipsae_min`` was absent from every result in the verification run, and
  treating absent as zero would bury otherwise strong candidates.
* **``min_interaction_pae`` is inverted**: lower is better, unlike every other
  metric here.

Normalisation is min-max *within the run*. Absolute metric scales are not
comparable across targets, so a score only means "relative to the other
candidates in this run" — which is what prioritisation is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

# Metric name -> whether a higher raw value is better.
HIGHER_IS_BETTER: dict[str, bool] = {
    "binding_confidence": True,
    "iptm": True,
    "ipsae_min": True,
    "min_interaction_pae": False,  # lower PAE means a more confident interface
    "structure_confidence": True,
}

RANKED_METRICS = tuple(HIGHER_IS_BETTER)

# Plain-language descriptions, so a score can be explained rather than asserted.
METRIC_LABELS: dict[str, str] = {
    "binding_confidence": "binding confidence",
    "iptm": "interface confidence (ipTM)",
    "ipsae_min": "interface confidence (ipSAE)",
    "min_interaction_pae": "interface error (PAE)",
    "structure_confidence": "fold confidence",
}

SCORE_PLACES = Decimal("0.0001")


class RankingError(ValueError):
    """The inputs cannot be ranked as given."""


@dataclass(frozen=True, slots=True)
class Weights:
    """How much each metric counts. Stored per project, editable, visible."""

    binding_confidence: Decimal = Decimal("0.40")
    iptm: Decimal = Decimal("0.20")
    ipsae_min: Decimal = Decimal("0.20")
    min_interaction_pae: Decimal = Decimal("0.10")
    structure_confidence: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for name in RANKED_METRICS:
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise RankingError(f"weight {name} must be a Decimal, got {type(value)!r}")
            if value < 0:
                raise RankingError(f"weight {name} must not be negative")
        if self.total <= 0:
            raise RankingError("at least one weight must be greater than zero")

    @property
    def total(self) -> Decimal:
        return sum((getattr(self, n) for n in RANKED_METRICS), Decimal("0"))

    def as_mapping(self) -> dict[str, Decimal]:
        return {name: getattr(self, name) for name in RANKED_METRICS}

    @classmethod
    def from_mapping(cls, values: dict[str, object]) -> Weights:
        kwargs: dict[str, Decimal] = {}
        for name in RANKED_METRICS:
            if name not in values:
                continue
            raw = values[name]
            if isinstance(raw, Decimal):
                kwargs[name] = raw
            elif isinstance(raw, float):
                # Accept but route via str, so a weight cannot carry binary noise.
                kwargs[name] = Decimal(str(raw))
            else:
                kwargs[name] = Decimal(str(raw))
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class MetricInput:
    """One candidate's metrics, as stored. ``None`` means genuinely absent."""

    candidate_id: str
    structure_confidence: float | None = None
    binding_confidence: float | None = None
    iptm: float | None = None
    min_interaction_pae: float | None = None
    ipsae_min: float | None = None

    def value(self, metric: str) -> float | None:
        return getattr(self, metric, None)

    @property
    def is_binder(self) -> bool:
        """Binding metrics present at all. The live API has no type field."""
        return self.binding_confidence is not None or self.iptm is not None


@dataclass(frozen=True, slots=True)
class MetricContribution:
    """What one metric did to one candidate's score."""

    metric: str
    label: str
    raw: float
    normalised: Decimal
    weight: Decimal
    """The weight *after* redistribution, which is what was actually applied."""
    contribution: Decimal
    higher_is_better: bool
    is_constant: bool = False
    """True when every candidate shared this value, so it could not discriminate."""

    @property
    def share_of_score(self) -> Decimal:
        return self.contribution


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate_id: str
    rank: int
    score: Decimal
    """0 to 1, relative to the other candidates in this run."""
    percentile: Decimal
    contributions: tuple[MetricContribution, ...] = ()
    missing_metrics: tuple[str, ...] = ()
    weight_redistributed: bool = False

    @property
    def has_partial_metrics(self) -> bool:
        return bool(self.missing_metrics)

    @property
    def strongest_metric(self) -> MetricContribution | None:
        scoring = [c for c in self.contributions if not c.is_constant]
        return max(scoring, key=lambda c: c.contribution) if scoring else None

    @property
    def weakest_metric(self) -> MetricContribution | None:
        scoring = [c for c in self.contributions if not c.is_constant]
        return min(scoring, key=lambda c: c.normalised) if scoring else None


@dataclass(frozen=True, slots=True)
class RankingResult:
    ranked: tuple[RankedCandidate, ...] = ()
    excluded: tuple[tuple[str, str], ...] = ()
    """(candidate_id, reason) for candidates that could not be scored."""
    weights: Weights = field(default_factory=Weights)
    constant_metrics: tuple[str, ...] = ()
    """Metrics identical across every candidate, so they did not affect the order."""
    metrics_absent_everywhere: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.ranked)

    def top(self, n: int) -> tuple[RankedCandidate, ...]:
        return self.ranked[:n]

    def by_id(self, candidate_id: str) -> RankedCandidate | None:
        return next((c for c in self.ranked if c.candidate_id == candidate_id), None)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(SCORE_PLACES, rounding=ROUND_HALF_UP)


def _normalise(
    raw: float, low: float, high: float, *, higher_is_better: bool
) -> tuple[Decimal, bool]:
    """Min-max normalise one value to 0..1. Returns (value, is_constant).

    When every candidate shares a value there is no spread to normalise. Mapping
    that to 0 would punish all of them and mapping it to 1 would reward all of
    them; both are misleading, so it maps to the midpoint and is reported as
    constant so the explanation can say it did not discriminate.
    """
    if high <= low:
        return Decimal("0.5"), True
    position = (Decimal(str(raw)) - Decimal(str(low))) / (Decimal(str(high)) - Decimal(str(low)))
    if not higher_is_better:
        position = Decimal("1") - position
    return min(Decimal("1"), max(Decimal("0"), position)), False


def rank_candidates(inputs: Sequence[MetricInput], weights: Weights | None = None) -> RankingResult:
    """Score and order candidates. Pure: same inputs, same output, always."""
    active_weights = weights or Weights()

    binders = [c for c in inputs if c.is_binder]
    excluded = tuple(
        (
            c.candidate_id,
            "no binding metrics, so there is nothing to score an interface on",
        )
        for c in inputs
        if not c.is_binder
    )
    if not binders:
        return RankingResult(ranked=(), excluded=excluded, weights=active_weights)

    # Spread per metric, across the candidates that actually have it.
    ranges: dict[str, tuple[float, float]] = {}
    absent_everywhere: list[str] = []
    for metric in RANKED_METRICS:
        values = [v for c in binders if (v := c.value(metric)) is not None]
        if not values:
            absent_everywhere.append(metric)
            continue
        ranges[metric] = (min(values), max(values))

    ranked: list[RankedCandidate] = []
    constant: set[str] = set()

    for candidate in binders:
        available = [
            m
            for m in RANKED_METRICS
            if m in ranges and candidate.value(m) is not None and getattr(active_weights, m) > 0
        ]
        missing = tuple(
            m
            for m in RANKED_METRICS
            if candidate.value(m) is None and getattr(active_weights, m) > 0
        )

        available_weight = sum((getattr(active_weights, m) for m in available), Decimal("0"))
        if available_weight <= 0:
            excluded = (
                *excluded,
                (
                    candidate.candidate_id,
                    "none of the weighted metrics are present for this candidate",
                ),
            )
            continue

        contributions: list[MetricContribution] = []
        score = Decimal("0")
        for metric in available:
            raw = candidate.value(metric)
            assert raw is not None
            low, high = ranges[metric]
            normalised, is_constant = _normalise(
                raw, low, high, higher_is_better=HIGHER_IS_BETTER[metric]
            )
            if is_constant:
                constant.add(metric)
            # Redistribute the missing metrics' weight proportionally, so a
            # candidate lacking ipsae_min is judged on what it does have.
            effective = getattr(active_weights, metric) / available_weight
            contribution = _quantize(normalised * effective)
            score += normalised * effective
            contributions.append(
                MetricContribution(
                    metric=metric,
                    label=METRIC_LABELS[metric],
                    raw=raw,
                    normalised=_quantize(normalised),
                    weight=_quantize(effective),
                    contribution=contribution,
                    higher_is_better=HIGHER_IS_BETTER[metric],
                    is_constant=is_constant,
                )
            )

        ranked.append(
            RankedCandidate(
                candidate_id=candidate.candidate_id,
                rank=0,  # assigned after sorting
                score=_quantize(score),
                percentile=Decimal("0"),
                contributions=tuple(contributions),
                missing_metrics=missing,
                weight_redistributed=bool(missing),
            )
        )

    # Deterministic order: score descending, then candidate id, so an exact tie
    # does not reorder between runs.
    ranked.sort(key=lambda c: (-c.score, c.candidate_id))

    total = len(ranked)
    finalised = tuple(
        RankedCandidate(
            candidate_id=c.candidate_id,
            rank=index + 1,
            score=c.score,
            # Percentile of the field this candidate is at or above.
            percentile=_quantize(
                Decimal("100")
                if total == 1
                else Decimal(total - index - 1) * Decimal("100") / Decimal(total - 1)
            ),
            contributions=c.contributions,
            missing_metrics=c.missing_metrics,
            weight_redistributed=c.weight_redistributed,
        )
        for index, c in enumerate(ranked)
    )

    return RankingResult(
        ranked=finalised,
        excluded=excluded,
        weights=active_weights,
        constant_metrics=tuple(m for m in RANKED_METRICS if m in constant),
        metrics_absent_everywhere=tuple(absent_everywhere),
    )


# --- explanation -----------------------------------------------------------

Verdict = Literal["promising", "mixed", "weak"]


def verdict_for(candidate: RankedCandidate) -> Verdict:
    """A coarse band, from the score alone.

    Deliberately coarse and deliberately not a claim about efficacy: these are
    model confidences, not measurements.
    """
    if candidate.score >= Decimal("0.7"):
        return "promising"
    if candidate.score >= Decimal("0.4"):
        return "mixed"
    return "weak"


def explain(candidate: RankedCandidate, result: RankingResult) -> str:
    """A factual explanation built only from the numbers that produced the score.

    Every figure quoted here exists in ``candidate``. This is what the
    Scientific Review guardrail checks generated narratives against, so it must
    never reach beyond the arithmetic — no efficacy, no potency, no claim that
    anything will bind.
    """
    parts: list[str] = [
        f"Ranked {candidate.rank} of {result.count} in this run "
        f"(score {candidate.score}, {candidate.percentile}th percentile)."
    ]

    strongest = candidate.strongest_metric
    if strongest is not None:
        parts.append(
            f"Its strongest contribution is {strongest.label} at {strongest.raw}, "
            f"which is {strongest.normalised} on a 0-1 scale relative to the other "
            "candidates here."
        )
    weakest = candidate.weakest_metric
    if weakest is not None and strongest is not None and weakest.metric != strongest.metric:
        direction = "lower" if weakest.higher_is_better else "higher"
        parts.append(
            f"Its weakest is {weakest.label} at {weakest.raw} "
            f"({weakest.normalised} normalised); {direction} values are worse for "
            "this metric."
        )

    if candidate.missing_metrics:
        names = ", ".join(METRIC_LABELS[m] for m in candidate.missing_metrics)
        parts.append(
            f"{names} was not reported for this candidate, so its weight was "
            "redistributed across the metrics that were present rather than "
            "counted as zero."
        )
    if result.constant_metrics:
        names = ", ".join(METRIC_LABELS[m] for m in result.constant_metrics)
        parts.append(
            f"{names} was identical across every candidate, so it did not affect the ordering."
        )

    parts.append(
        "These are computational confidence scores from a predictive model. They "
        "indicate which candidates are worth testing first, not whether any of "
        "them binds the target or has any therapeutic effect."
    )
    return " ".join(parts)

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

# Display bands over the model's own ``binding_confidence``.
#
# These exist because of something a live run revealed and test mode hid.
# Test-mode results report binding_confidence around 0.7; a real run against
# KPC-2 reported values around 1e-6 — roughly 300,000 times smaller. The score
# in this module is normalised *within a run*, so the best of ten hopeless
# designs still scores near 1.0. Reporting that as "promising" would tell a
# researcher that a design with a 0.0000024 binding probability is worth
# pursuing, which is precisely the overstatement this platform exists to avoid.
#
# So the relative score answers "which of these first?" and these bands answer
# "is any of them actually worth anything?". They are presentation thresholds
# over an uncalibrated model output, not validated scientific cutoffs, and the
# raw value is always shown alongside.
BINDING_BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal("0.5"), "high"),
    (Decimal("0.1"), "moderate"),
    (Decimal("0.01"), "low"),
)
NEGLIGIBLE_BAND = "negligible"


def binding_band(binding_confidence: float | None) -> str:
    """Which absolute band a binding confidence falls in."""
    if binding_confidence is None:
        return "not reported"
    value = Decimal(str(binding_confidence))
    for threshold, name in BINDING_BANDS:
        if value >= threshold:
            return name
    return NEGLIGIBLE_BAND


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
    """Relative to the other candidates in this run, 0 to 1.

    **Not** a measure of quality. A run where nothing binds still produces a
    candidate scoring near 1.0, so this must always be read together with
    ``binding_confidence`` and ``binding_band``.
    """
    percentile: Decimal
    binding_confidence: float | None = None
    """The raw, absolute value, carried so nothing has to re-derive it."""
    binding_band: str = "not reported"
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
    best_binding_confidence: float | None = None
    """The highest absolute binding confidence in the run."""

    @property
    def best_binding_band(self) -> str:
        return binding_band(self.best_binding_confidence)

    @property
    def nothing_predicted_to_bind(self) -> bool:
        """True when no candidate reaches even the low band.

        A run can be internally well-ordered and still contain nothing worth
        testing. Saying so is the honest outcome, and it points at the real
        answer, which is to generate far more designs.
        """
        return bool(self.ranked) and self.best_binding_band == NEGLIGIBLE_BAND

    @property
    def absolute_assessment(self) -> str:
        if not self.ranked:
            return "No candidates could be scored."
        best = self.best_binding_confidence
        if best is None:
            return (
                "Binding confidence was not reported for this run, so candidates "
                "are ordered on interface and fold confidence alone."
            )
        if self.nothing_predicted_to_bind:
            return (
                f"No candidate in this run reaches a meaningful predicted binding "
                f"probability; the highest is {best:.2e}. The ordering below shows "
                "which to look at first, but on the model's own output none of them "
                "is expected to bind. A real campaign generates thousands of designs "
                "rather than tens."
            )
        return (
            f"The highest predicted binding confidence in this run is {best:.2e} "
            f"({self.best_binding_band} band)."
        )

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
                binding_confidence=candidate.binding_confidence,
                binding_band=binding_band(candidate.binding_confidence),
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
            binding_confidence=c.binding_confidence,
            binding_band=c.binding_band,
            contributions=c.contributions,
            missing_metrics=c.missing_metrics,
            weight_redistributed=c.weight_redistributed,
        )
        for index, c in enumerate(ranked)
    )

    binding_values = [c.binding_confidence for c in finalised if c.binding_confidence is not None]
    return RankingResult(
        ranked=finalised,
        excluded=excluded,
        weights=active_weights,
        constant_metrics=tuple(m for m in RANKED_METRICS if m in constant),
        metrics_absent_everywhere=tuple(absent_everywhere),
        best_binding_confidence=max(binding_values) if binding_values else None,
    )


# --- explanation -----------------------------------------------------------

Verdict = Literal["worth_testing_first", "mixed", "weak", "no_predicted_binding"]


def verdict_for(candidate: RankedCandidate) -> Verdict:
    """A coarse band from the absolute binding confidence *and* the relative score.

    Relative position alone may never produce an encouraging verdict. Before
    this took the absolute value into account, the best of ten designs with
    binding confidences around 1e-6 was reported as "promising" — a design the
    model puts at roughly a two-in-a-million chance of binding. A live run
    caught it; test mode could not, because its synthetic values sit near 0.7.

    Note the wording: even the best band says "worth testing first", which is a
    statement about ordering a wet-lab queue, not about whether anything works.
    """
    if candidate.binding_band == NEGLIGIBLE_BAND:
        return "no_predicted_binding"
    if candidate.binding_band in ("high", "moderate") and candidate.score >= Decimal("0.6"):
        return "worth_testing_first"
    if candidate.score >= Decimal("0.4"):
        return "mixed"
    return "weak"


def explain(candidate: RankedCandidate, result: RankingResult) -> str:
    """A factual explanation built only from the numbers that produced the score.

    Every figure quoted here exists in ``candidate`` or ``result``. This is what
    the Scientific Review guardrail checks generated narratives against, so it
    must never reach beyond the arithmetic: no efficacy, no potency, no claim
    that anything will bind.

    The absolute binding confidence is stated **first**, before the rank,
    because the rank is the more flattering number and leading with it is how a
    reader ends up believing a 1e-6 binding probability is a result.
    """
    parts: list[str] = []

    if candidate.binding_confidence is not None:
        if candidate.binding_band == NEGLIGIBLE_BAND:
            parts.append(
                f"Predicted binding confidence is {candidate.binding_confidence:.2e}, "
                "which is negligible: on the model's own output this design is not "
                "expected to bind the target."
            )
        else:
            parts.append(
                f"Predicted binding confidence is {candidate.binding_confidence:.4g} "
                f"({candidate.binding_band} band)."
            )

    parts.append(
        f"It ranks {candidate.rank} of {result.count} in this run "
        f"(relative score {candidate.score}, {candidate.percentile}th percentile). "
        "That ordering is within this run only and is not a measure of quality."
    )

    strongest = candidate.strongest_metric
    if strongest is not None:
        parts.append(
            f"Its strongest contribution is {strongest.label} at {strongest.raw:.4g}, "
            f"which is {strongest.normalised} on a 0-1 scale relative to the other "
            "candidates here."
        )
    weakest = candidate.weakest_metric
    if weakest is not None and strongest is not None and weakest.metric != strongest.metric:
        direction = "lower" if weakest.higher_is_better else "higher"
        parts.append(
            f"Its weakest is {weakest.label} at {weakest.raw:.4g} "
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
    if result.nothing_predicted_to_bind:
        parts.append(
            "No candidate in this run reaches a meaningful predicted binding "
            "probability, so the ordering says which to examine first, not which "
            "to pursue. Generating far more designs is the usual next step."
        )

    parts.append(
        "These are computational confidence scores from a predictive model. They "
        "indicate which candidates are worth testing first, not whether any of "
        "them binds the target or has any therapeutic effect."
    )
    return " ".join(parts)

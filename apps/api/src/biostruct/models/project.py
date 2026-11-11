from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Infection(BaseModel):
    """The clinical problem a project starts from.

    Free text is allowed because a researcher's framing ("carbapenem-resistant
    Klebsiella on our ICU") is more useful than forcing them into a taxonomy.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=200)
    pathogen: str | None = Field(default=None, max_length=200)
    amr_context: str | None = Field(default=None, max_length=4000)


class RankingWeights(BaseModel):
    """Weights for the candidate ranking (ARCHITECTURE.md §6.4).

    Stored per project so a re-rank is a recompute over numbers we already hold:
    instant, and it spends nothing. Defaults follow the Boltz SDK's own guidance
    that ``binding_confidence`` is the primary metric for hit discovery.
    """

    binding_confidence: Decimal = Decimal("0.40")
    iptm: Decimal = Decimal("0.20")
    ipsae_min: Decimal = Decimal("0.20")
    min_interaction_pae: Decimal = Decimal("0.10")  # inverted when scoring: lower is better
    structure_confidence: Decimal = Decimal("0.10")

    @field_validator("*", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, float):
            # Weights are money-adjacent in that they must be reproducible.
            # Accept the float but route it through str to avoid binary artefacts.
            return Decimal(str(v))
        return Decimal(str(v))

    @model_validator(mode="after")
    def _non_negative_and_nonzero(self) -> RankingWeights:
        values = [
            self.binding_confidence,
            self.iptm,
            self.ipsae_min,
            self.min_interaction_pae,
            self.structure_confidence,
        ]
        if any(v < 0 for v in values):
            raise ValueError("weights must not be negative")
        if sum(values) <= 0:
            raise ValueError("at least one weight must be greater than zero")
        return self

    def as_mapping(self) -> dict[str, Decimal]:
        return {
            "binding_confidence": self.binding_confidence,
            "iptm": self.iptm,
            "ipsae_min": self.ipsae_min,
            "min_interaction_pae": self.min_interaction_pae,
            "structure_confidence": self.structure_confidence,
        }


class Budget(BaseModel):
    """Money is ``Decimal`` throughout (ADR-017).

    ``reserved`` exists because Boltz bills on "exact token counts computed at
    run time", so an estimate is only an estimate: we reserve the estimate at
    submission and settle the actual afterwards.
    """

    limit_usd: Decimal
    spent_usd: Decimal = Decimal("0")
    reserved_usd: Decimal = Decimal("0")

    @field_validator("*", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, float):
            raise TypeError("money must not be a float; pass a string or Decimal")
        return Decimal(str(v))

    @property
    def committed_usd(self) -> Decimal:
        return self.spent_usd + self.reserved_usd

    @property
    def remaining_usd(self) -> Decimal:
        return self.limit_usd - self.committed_usd

    def can_afford(self, amount_usd: Decimal) -> bool:
        if amount_usd < 0:
            raise ValueError("amount must not be negative")
        return amount_usd <= self.remaining_usd


class ProjectCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    infection: Infection
    budget_usd: Decimal | None = None

    @field_validator("budget_usd", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, float):
            raise TypeError("budget_usd must not be a float; pass a string or Decimal")
        return Decimal(str(v))


class ProjectUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    infection: Infection | None = None
    ranking_weights: RankingWeights | None = None


class Project(BaseModel):
    id: str
    user_id: str
    name: str
    infection: Infection
    ranking_weights: RankingWeights = RankingWeights()
    budget: Budget
    created_at: datetime
    updated_at: datetime
    schema_version: int = 1

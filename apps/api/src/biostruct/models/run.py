from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from biostruct.engines.base import NUM_PROTEINS_MAX, NUM_PROTEINS_MIN

# A UI-level cap well below the API's million. The API maximum times $0.05 is a
# $50,000 typo, and no researcher submits that by hand (ARCHITECTURE.md §7).
NUM_PROTEINS_UI_MAX = 2_000


class RunStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    stopped = "stopped"
    submit_failed = "submit_failed"
    harvesting = "harvesting"
    harvest_partial = "harvest_partial"
    complete = "complete"

    @property
    def is_terminal(self) -> bool:
        """Terminal for the reconciler: nothing further will change on its own."""
        return self in (
            RunStatus.complete,
            RunStatus.failed,
            RunStatus.stopped,
            RunStatus.submit_failed,
            RunStatus.harvest_partial,
        )

    @property
    def is_active(self) -> bool:
        return self in (RunStatus.pending, RunStatus.running, RunStatus.harvesting)


class DesignRules(BaseModel):
    """Filters applied before scoring, so they cost nothing to use."""

    excluded_amino_acids: list[str] = Field(default_factory=list, max_length=20)
    excluded_sequence_motifs: list[str] = Field(default_factory=list, max_length=20)
    max_hydrophobic_fraction: Decimal | None = None

    @field_validator("excluded_amino_acids")
    @classmethod
    def _single_letters(cls, v: list[str]) -> list[str]:
        for code in v:
            if len(code) != 1 or not code.isalpha():
                raise ValueError(f"expected a one-letter amino acid code, got {code!r}")
        return [c.upper() for c in v]

    @field_validator("max_hydrophobic_fraction", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        if isinstance(v, float):
            return Decimal(str(v))
        return Decimal(str(v))


class RunRequest(BaseModel):
    """What a researcher asks for."""

    model_config = ConfigDict(str_strip_whitespace=True)

    target_id: str
    num_proteins: int = Field(
        default=100,
        ge=NUM_PROTEINS_MIN,
        le=NUM_PROTEINS_UI_MAX,
        description=(
            f"Designs to generate. The engine allows up to {NUM_PROTEINS_MAX:,} but this "
            f"API caps it at {NUM_PROTEINS_UI_MAX:,}: at roughly $0.05 per design, the "
            "engine's maximum would be a $50,000 request."
        ),
    )
    modality: str = Field(default="nanobody", pattern="^(nanobody|antibody)$")
    rules: DesignRules = Field(default_factory=DesignRules)
    name: str | None = Field(default=None, max_length=200)


class EstimateResponse(BaseModel):
    total_usd: Decimal
    unit_usd: Decimal
    units: int
    disclaimer: str = ""
    budget_limit_usd: Decimal
    budget_remaining_usd: Decimal
    within_budget: bool
    livemode: bool
    """False means the configured key is a test key: the run is free and its
    results are synthetic."""

    @property
    def shortfall_usd(self) -> Decimal:
        return max(Decimal("0"), self.total_usd - self.budget_remaining_usd)


class RunCreate(BaseModel):
    """Submission. Requires the cost the researcher actually saw.

    Without this, a UI bug or a stale page could submit a run at a different
    price than the one that was confirmed.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    request: RunRequest
    confirmed_cost_usd: Decimal

    @field_validator("confirmed_cost_usd", mode="before")
    @classmethod
    def _as_decimal(cls, v: object) -> Decimal:
        if isinstance(v, float):
            raise ValueError(
                "confirmed_cost_usd must be sent as a string, not a number, so the "
                "amount cannot drift through binary floating point"
            )
        return Decimal(str(v))


class RunProgress(BaseModel):
    total_to_generate: int | None = None
    generated: int = 0
    harvested: int = 0

    @property
    def percent(self) -> int | None:
        if not self.total_to_generate:
            return None
        return min(100, round(100 * self.generated / self.total_to_generate))


class DesignRun(BaseModel):
    id: str
    project_id: str
    target_id: str
    engine: str
    engine_job_id: str | None = None
    status: RunStatus
    livemode: bool = False
    """Carried from the engine. Test-mode runs return synthetic metrics and stub
    structures, and must never be exported as findings (ADR-024)."""
    num_proteins: int
    modality: str = "nanobody"
    name: str | None = None
    epitope_pdb_id: str = ""
    epitope_chain_id: str = ""
    epitope_design_indices: list[int] = Field(default_factory=list)
    progress: RunProgress = Field(default_factory=RunProgress)
    cost_estimate_usd: Decimal = Decimal("0")
    cost_actual_usd: Decimal | None = None
    error: str | None = None
    harvest_cursor: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    schema_version: int = 1

    @property
    def is_exportable(self) -> bool:
        """Only a completed live run yields results worth exporting."""
        return self.status is RunStatus.complete and self.livemode


class CandidateMetrics(BaseModel):
    """Verified metric names. ``ipsae_min`` is genuinely optional."""

    structure_confidence: float
    binding_confidence: float | None = None
    iptm: float | None = None
    min_interaction_pae: float | None = None
    ipsae_min: float | None = None
    helix_fraction: float | None = None
    sheet_fraction: float | None = None
    loop_fraction: float | None = None

    @property
    def is_binder(self) -> bool:
        return self.binding_confidence is not None or self.iptm is not None


class Candidate(BaseModel):
    id: str
    run_id: str
    project_id: str
    engine_result_id: str
    sequences: list[str] = Field(default_factory=list)
    chain_ids: list[str] = Field(default_factory=list)
    metrics: CandidateMetrics
    warnings: list[dict[str, str]] = Field(default_factory=list)
    structure_key: str | None = None
    """Our own storage key. Never a Boltz URL, which expires in ~30 minutes."""
    structure_bytes: int | None = None
    archive_key: str | None = None
    livemode: bool = False
    created_at: datetime

    @property
    def longest_sequence(self) -> str:
        return max(self.sequences, key=len) if self.sequences else ""


class CandidateList(BaseModel):
    items: list[Candidate]
    total: int
    run_status: RunStatus
    livemode: bool

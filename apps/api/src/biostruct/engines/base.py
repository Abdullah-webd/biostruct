"""The design-engine seam.

Everything the rest of the app knows about protein design lives in these types.
No module outside ``biostruct.engines`` imports ``boltz_api``, so the SDK — which
is generated, unversioned in its docs and liable to rename fields — can churn
without reaching the domain (ARCHITECTURE.md §6.1, ADR-012).

The domain types here are shaped by what the live API actually returned on
2026-11-11, not by the SDK's declared types, which differ in several places.
See ``docs/boltz-api-observed.md``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

# --- inputs ----------------------------------------------------------------

Modality = Literal["nanobody", "antibody", "peptide", "custom_protein"]

NUM_PROTEINS_MIN = 10
NUM_PROTEINS_MAX = 1_000_000


@dataclass(frozen=True, slots=True)
class DesignRules:
    """Filters Boltz applies *before* scoring, so they cost nothing to use.

    Verified accepted by the live API.
    """

    excluded_amino_acids: tuple[str, ...] = ()
    excluded_sequence_motifs: tuple[str, ...] = ()
    max_hydrophobic_fraction: Decimal | None = None

    def __post_init__(self) -> None:
        for aa in self.excluded_amino_acids:
            if len(aa) != 1 or not aa.isalpha() or not aa.isupper():
                raise ValueError(f"excluded amino acid must be one upper-case letter: {aa!r}")
        if self.max_hydrophobic_fraction is not None and not (
            Decimal("0") < self.max_hydrophobic_fraction <= Decimal("1")
        ):
            raise ValueError("max_hydrophobic_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class TargetChain:
    """One chain of the target, with the epitope marked.

    **Residue indices are 0-indexed**, which is the API's convention and *not*
    the numbering printed in a PDB file. Converting from author residue numbers
    is the caller's job; ``residues.py`` does it.
    """

    chain_id: str
    crop_residues: tuple[int, ...] | Literal["all"] = "all"
    epitope_residues: tuple[int, ...] = ()
    non_binding_residues: tuple[int, ...] = ()
    flexible_residues: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        overlap = set(self.epitope_residues) & set(self.non_binding_residues)
        if overlap:
            raise ValueError(
                f"chain {self.chain_id}: residues cannot be both epitope and non-binding: "
                f"{sorted(overlap)}"
            )
        if self.crop_residues != "all":
            kept = set(self.crop_residues)
            for name, residues in (
                ("epitope_residues", self.epitope_residues),
                ("non_binding_residues", self.non_binding_residues),
                ("flexible_residues", self.flexible_residues),
            ):
                missing = sorted(set(residues) - kept)
                if missing:
                    raise ValueError(
                        f"chain {self.chain_id}: {name} not in crop_residues: {missing}"
                    )
        for name, residues in (
            ("crop_residues", () if self.crop_residues == "all" else self.crop_residues),
            ("epitope_residues", self.epitope_residues),
            ("non_binding_residues", self.non_binding_residues),
            ("flexible_residues", self.flexible_residues),
        ):
            if any(r < 0 for r in residues):
                raise ValueError(f"chain {self.chain_id}: {name} must be 0-indexed and >= 0")


@dataclass(frozen=True, slots=True)
class StructureTarget:
    """A target defined by a 3D structure.

    ``structure_url`` may be a plain public URL: verified on 2026-11-11 that a
    raw ``files.rcsb.org/download/XXXX.cif`` link is accepted and fetched by
    Boltz at submission time. That is why there is no upload step between PDB
    and design.
    """

    structure_url: str | None = None
    structure_cif_base64: str | None = None
    chains: tuple[TargetChain, ...] = ()

    def __post_init__(self) -> None:
        if bool(self.structure_url) == bool(self.structure_cif_base64):
            raise ValueError("provide exactly one of structure_url or structure_cif_base64")
        if not self.chains:
            raise ValueError("a structure target needs at least one chain selection")

    @property
    def epitope_total(self) -> int:
        return sum(len(c.epitope_residues) for c in self.chains)


@dataclass(frozen=True, slots=True)
class SequenceTarget:
    """A target defined only by sequence, with no structure."""

    sequences: tuple[tuple[str, str], ...]  # (chain_id, amino-acid sequence)

    def __post_init__(self) -> None:
        if not self.sequences:
            raise ValueError("a sequence target needs at least one sequence")


Target = StructureTarget | SequenceTarget


@dataclass(frozen=True, slots=True)
class CuratedBinder:
    """Let Boltz pick from its maintained nanobody or antibody template list."""

    kind: Literal["nanobody", "antibody"] = "nanobody"
    rules: DesignRules = field(default_factory=DesignRules)


@dataclass(frozen=True, slots=True)
class DesignSpec:
    target: Target
    binder: CuratedBinder
    num_proteins: int
    name: str | None = None

    def __post_init__(self) -> None:
        if not NUM_PROTEINS_MIN <= self.num_proteins <= NUM_PROTEINS_MAX:
            raise ValueError(
                f"num_proteins must be between {NUM_PROTEINS_MIN} and "
                f"{NUM_PROTEINS_MAX:,}, got {self.num_proteins}"
            )


# --- outputs ---------------------------------------------------------------


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    stopped = "stopped"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.succeeded, JobStatus.failed, JobStatus.stopped)


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Money is ``Decimal`` and arrives from the API as a string (ADR-017)."""

    total_usd: Decimal
    unit_usd: Decimal
    units: int
    application: str
    disclaimer: str = ""


@dataclass(frozen=True, slots=True)
class EngineJob:
    job_id: str
    status: JobStatus
    livemode: bool
    """False for a test-mode run. Synthetic results must never be presented as
    science, so this is carried all the way to the UI."""
    total_to_generate: int | None = None
    generated: int = 0
    engine: str = ""
    engine_version: str = ""
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def percent_complete(self) -> int | None:
        if not self.total_to_generate:
            return None
        return min(100, round(100 * self.generated / self.total_to_generate))


@dataclass(frozen=True, slots=True)
class Artifact:
    url: str
    expires_at: datetime | None

    @property
    def is_presigned(self) -> bool:
        return self.expires_at is not None


@dataclass(frozen=True, slots=True)
class DesignEntity:
    chain_ids: tuple[str, ...]
    kind: str  # "protein", "rna", "dna", "ligand_ccd", "ligand_smiles", "glycan"
    value: str


# These are the binding metrics. Their presence is what identifies a *binder*
# result: the live API does not return the ``type`` discriminator the SDK
# declares, so it cannot be relied on (observed 2026-11-11).
BINDING_METRICS = ("binding_confidence", "iptm", "min_interaction_pae", "ipsae_min")


@dataclass(frozen=True, slots=True)
class DesignMetrics:
    """Metric names verified against live responses.

    ``ipsae_min`` is genuinely optional — it was absent from every result in the
    verification run. Ranking redistributes its weight rather than defaulting it
    to zero, which would silently bury an otherwise good candidate.
    """

    structure_confidence: float
    helix_fraction: float | None = None
    sheet_fraction: float | None = None
    loop_fraction: float | None = None
    binding_confidence: float | None = None
    iptm: float | None = None
    min_interaction_pae: float | None = None
    ipsae_min: float | None = None

    @property
    def is_binder(self) -> bool:
        """True when binding metrics are present at all."""
        return self.binding_confidence is not None or self.iptm is not None

    @property
    def missing_binding_metrics(self) -> tuple[str, ...]:
        return tuple(m for m in BINDING_METRICS if getattr(self, m) is None)


@dataclass(frozen=True, slots=True)
class DesignWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DesignResult:
    result_id: str
    entities: tuple[DesignEntity, ...]
    metrics: DesignMetrics
    structure: Artifact | None = None
    archive: Artifact | None = None
    warnings: tuple[DesignWarning, ...] = ()
    created_at: datetime | None = None
    raw: dict[str, object] = field(default_factory=dict, repr=False)
    """The verbatim payload. Storage is cheap; re-running a paid job after an SDK
    change because we discarded it would not be."""

    @property
    def protein_entities(self) -> tuple[DesignEntity, ...]:
        return tuple(e for e in self.entities if e.kind == "protein")


# --- errors ----------------------------------------------------------------


class EngineError(RuntimeError):
    """Base for engine failures."""


class EngineUnavailable(EngineError):
    """Transport or 5xx failure. Retryable; must never mark a run as failed."""


class EngineRejected(EngineError):
    """The engine refused the request (4xx). Not retryable without a change."""

    def __init__(self, message: str, *, status_code: int, code: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


# --- the seam itself -------------------------------------------------------


@runtime_checkable
class DesignEngine(Protocol):
    """What the app needs from a protein-design backend.

    Deliberately narrow, and deliberately asynchronous in the right place:
    ``start_design`` returns as soon as the job is accepted. Nothing in this
    interface waits for a design run, because a run takes minutes to hours and
    no request or agent tool may block on one (ARCHITECTURE.md §7).
    """

    name: str

    async def estimate_cost(self, spec: DesignSpec) -> CostEstimate: ...

    async def start_design(self, spec: DesignSpec, *, idempotency_key: str) -> EngineJob: ...

    async def get_job(self, job_id: str) -> EngineJob: ...

    async def iter_results(
        self, job_id: str, *, after_id: str | None = None, page_size: int = 100
    ) -> AsyncIterator[DesignResult]: ...

    async def stop_design(self, job_id: str) -> EngineJob: ...

    async def resume_design(self, job_id: str) -> EngineJob: ...

    async def delete_data(self, job_id: str) -> None: ...

    async def whoami(self) -> EngineIdentity: ...


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    """Who the configured key belongs to, and crucially which mode it is in."""

    key_type: str
    mode: Literal["test", "live", "unknown"]
    workspace_id: str = ""
    organization_name: str = ""

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def curated_sequences(results: Sequence[DesignResult]) -> list[str]:
    """Flatten protein sequences out of results, for a FASTA export."""
    out: list[str] = []
    for r in results:
        out.extend(e.value for e in r.protein_entities)
    return out

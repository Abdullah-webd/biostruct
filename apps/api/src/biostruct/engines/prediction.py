"""The structure-prediction seam — step 2 of the pipeline.

Design (``base.py``) invents new proteins. *Prediction* answers a different
question: given sequences and optionally a small molecule, what shape does the
complex take, and how confident is the model about the interface?

This is the step a researcher reaches for when the protein they care about is a
**mutant from their own sequencing run** — the case no precomputed structure
database can serve, because the thing has never been catalogued.

Shapes here match what the live API returned on 2026-11-17; see
``docs/boltz-api-observed.md`` Part 2 for the capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from biostruct.engines.base import Artifact, CostEstimate, JobStatus

MODEL = "boltz-2.1"

NUM_SAMPLES_MIN = 1
NUM_SAMPLES_MAX = 10

SAMPLING_STEPS_MIN = 50
STEP_SCALE_MIN = Decimal("1.3")
STEP_SCALE_MAX = Decimal("2")

#: Documented cap for a ligand in a ``ligand_protein_binding`` request. Note the
#: open-source Boltz docs quote a far lower figure (128 atoms, 56 recommended)
#: for the affinity module. Where they disagree we follow the hosted API, which
#: is what we actually call, and we do not claim affinity at all (see below).
LIGAND_MAX_HEAVY_ATOMS = 2048

EntityKind = Literal["protein", "rna", "dna", "ligand_smiles", "ligand_ccd", "glycan"]

LIGAND_KINDS: frozenset[str] = frozenset({"ligand_smiles", "ligand_ccd"})
POLYMER_KINDS: frozenset[str] = frozenset({"protein", "rna", "dna"})


@dataclass(frozen=True, slots=True)
class Entity:
    """One molecule in the complex, pinned to the chains it occupies."""

    kind: EntityKind
    value: str
    chain_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError(f"{self.kind} entity needs a value")
        if not self.chain_ids:
            raise ValueError(f"{self.kind} entity needs at least one chain id")
        for cid in self.chain_ids:
            if not cid or not cid.isalnum():
                raise ValueError(f"chain id must be alphanumeric: {cid!r}")

    @property
    def is_ligand(self) -> bool:
        return self.kind in LIGAND_KINDS

    @property
    def is_protein(self) -> bool:
        return self.kind == "protein"


@dataclass(frozen=True, slots=True)
class LigandBinding:
    """Score how a small molecule sits against the protein."""

    binder_chain_id: str


@dataclass(frozen=True, slots=True)
class ProteinBinding:
    """Score a protein-protein interface.

    The hosted API accepts this — verified with a 200 from ``estimate-cost`` on
    2026-11-17 — although the open-source Boltz docs say protein-protein
    affinity is unsupported. Accepted at submission is not the same as useful,
    and we have not yet seen a live result, so nothing in the product presents
    a number from this path as an affinity.
    """

    binder_chain_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.binder_chain_ids:
            raise ValueError("protein binding needs at least one binder chain id")


Binding = LigandBinding | ProteinBinding


@dataclass(frozen=True, slots=True)
class ModelOptions:
    """Sampler knobs. Defaults are the API's own, so omitting this changes nothing."""

    recycling_steps: int | None = None
    sampling_steps: int | None = None
    seed: int | None = None
    step_scale: Decimal | None = None

    def __post_init__(self) -> None:
        if self.recycling_steps is not None and self.recycling_steps < 1:
            raise ValueError("recycling_steps must be at least 1")
        if self.sampling_steps is not None and self.sampling_steps < SAMPLING_STEPS_MIN:
            raise ValueError(f"sampling_steps must be at least {SAMPLING_STEPS_MIN}")
        if self.step_scale is not None and not (
            STEP_SCALE_MIN <= self.step_scale <= STEP_SCALE_MAX
        ):
            raise ValueError(f"step_scale must be in [{STEP_SCALE_MIN}, {STEP_SCALE_MAX}]")


@dataclass(frozen=True, slots=True)
class PredictionSpec:
    """Everything needed to fold one complex.

    Validated here rather than at the wire, because the API's validator is
    union-based and reports failures from whichever branch it tried last — it
    will happily demand a field that does not exist (see
    ``docs/boltz-api-observed.md``). A clear error from our own code beats a
    misleading one from theirs.
    """

    entities: tuple[Entity, ...]
    binding: Binding | None = None
    num_samples: int = 1
    options: ModelOptions | None = None

    def __post_init__(self) -> None:
        if not self.entities:
            raise ValueError("a prediction needs at least one entity")
        if not (NUM_SAMPLES_MIN <= self.num_samples <= NUM_SAMPLES_MAX):
            raise ValueError(f"num_samples must be in [{NUM_SAMPLES_MIN}, {NUM_SAMPLES_MAX}]")

        seen: set[str] = set()
        for entity in self.entities:
            for cid in entity.chain_ids:
                if cid in seen:
                    raise ValueError(f"chain id {cid!r} is used by more than one entity")
                seen.add(cid)

        match self.binding:
            case LigandBinding(binder_chain_id=cid):
                binder = self._entity_for(cid)
                if binder is None:
                    raise ValueError(f"binder chain {cid!r} is not one of the entities")
                if not binder.is_ligand:
                    raise ValueError(
                        f"ligand binding needs a ligand chain; {cid!r} is a {binder.kind}"
                    )
                if len(binder.chain_ids) != 1:
                    # The API scores one copy of the ligand, not several.
                    raise ValueError("the scored ligand must occupy exactly one chain")
                for entity in self.entities:
                    if not (entity.is_ligand or entity.is_protein):
                        raise ValueError(
                            "ligand binding allows only protein and ligand entities, "
                            f"found {entity.kind}"
                        )
            case ProteinBinding(binder_chain_ids=cids):
                for cid in cids:
                    binder = self._entity_for(cid)
                    if binder is None:
                        raise ValueError(f"binder chain {cid!r} is not one of the entities")
                    if not binder.is_protein:
                        raise ValueError(
                            f"protein binding needs protein chains; {cid!r} is a {binder.kind}"
                        )
            case None:
                pass

    def _entity_for(self, chain_id: str) -> Entity | None:
        return next((e for e in self.entities if chain_id in e.chain_ids), None)


@dataclass(frozen=True, slots=True)
class StructureMetrics:
    """Confidence scores for one predicted sample.

    **There is no affinity field, and that is not an omission.** Submitted with
    an explicit ``ligand_protein_binding`` request, the live API returned no
    affinity value and no binding probability — the response did not contain the
    string "affinity" at all (observed 2026-11-17, test mode). Hit discovery
    goes through the library screen instead, whose ``binding_confidence`` the
    API documents as the primary metric for it.

    Everything except ``structure_confidence`` is optional: the API omits
    metrics it did not compute, and defaulting a missing score to zero would
    silently bury a good result.
    """

    structure_confidence: float
    ptm: float | None = None
    iptm: float | None = None
    ligand_iptm: float | None = None
    protein_iptm: float | None = None
    complex_plddt: float | None = None
    complex_iplddt: float | None = None
    complex_pde: float | None = None
    complex_ipde: float | None = None

    def missing(self) -> tuple[str, ...]:
        """Which interface metrics the engine did not report."""
        return tuple(
            name for name in ("ptm", "iptm", "complex_plddt") if getattr(self, name) is None
        )


@dataclass(frozen=True, slots=True)
class PredictedSample:
    """One sampled structure. ``num_samples`` of these come back per job."""

    metrics: StructureMetrics
    structure: Artifact | None = None


@dataclass(frozen=True, slots=True)
class PredictionJob:
    """A folding job, in whatever state the engine last reported."""

    job_id: str
    status: JobStatus
    livemode: bool
    model: str = ""
    version: str = ""
    error: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    best: PredictedSample | None = None
    samples: tuple[PredictedSample, ...] = ()
    archive: Artifact | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def has_structure(self) -> bool:
        return self.best is not None and self.best.structure is not None


@runtime_checkable
class StructureEngine(Protocol):
    """What the app needs from a structure-prediction backend.

    As with ``DesignEngine``, nothing here waits: ``start_prediction`` returns
    once the job is accepted, and the reconciler owns polling. A prediction is
    faster than a design run but still far too slow to block an HTTP request or
    an agent tool call.
    """

    name: str

    async def estimate_prediction_cost(self, spec: PredictionSpec) -> CostEstimate: ...

    async def start_prediction(
        self, spec: PredictionSpec, *, idempotency_key: str
    ) -> PredictionJob: ...

    async def get_prediction(self, job_id: str) -> PredictionJob: ...

    async def delete_prediction_data(self, job_id: str) -> None: ...

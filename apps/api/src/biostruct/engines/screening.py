"""The screening seam — step 3 of the pipeline.

Step 2 asks "what shape is this protein?". Step 3 asks the question a
researcher actually cares about: **which molecules stick to it?** We hand the
engine a library of candidate molecules and a target, and get back a ranked
list with a predicted structure and drug-likeness for each one.

This is the step whose output a lab can act on, and it is the reason the
product exists.

Two engines live here because they answer two halves of the same question:

* ``library-screen`` — does this molecule bind, and what does the complex look
  like? Billed per molecule ($0.0250 as of 2026-11-17). ADME comes back with
  the screen, free of a second call.
* ``adme`` — is this molecule drug-like at all? Billed per molecule ($0.0100),
  so it is the cheap way to triage a library *before* paying to screen it.

Shapes match the live API on 2026-11-17; see ``docs/boltz-api-observed.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from biostruct.engines.base import Artifact, CostEstimate, JobStatus

ADME_MODEL = "adme-v1"

#: The API takes 1-128 molecules per ADME request and returns them in order.
ADME_BATCH_MAX = 128

#: Structural-alert catalogues. ``recommended`` is the API default and screens
#: out PAINS and BRENK-style troublemakers; ``disabled`` screens whatever it is
#: given, which is occasionally what an expert wants.
FilterLevel = Literal["recommended", "extra", "aggressive", "disabled"]

#: How confident the model is that the molecule dissolves well enough to be a
#: drug. ``high-risk`` is a warning, not a verdict.
Solubility = Literal["high-confidence", "medium-confidence", "high-risk"]


@dataclass(frozen=True, slots=True)
class Molecule:
    """One candidate, as the caller knows it.

    ``external_id`` is how a result is joined back to the caller's library.
    **It only survives in live mode** — test mode discards both the id and the
    SMILES and substitutes its own (observed: we sent avibactam, we got aspirin
    back). ``ScreenJob.livemode`` says which world you are in, and nothing in
    the product may join test-mode results to a real library.
    """

    smiles: str
    external_id: str = ""

    def __post_init__(self) -> None:
        if not self.smiles.strip():
            raise ValueError("a molecule needs a SMILES string")


@dataclass(frozen=True, slots=True)
class ScreenTarget:
    """The protein being screened against.

    ``pocket_residues`` is the bridge from step 2's attack site to step 3. Omit
    it and the model finds a pocket itself, which is convenient and is *not*
    what you want when you already know which residues do the damage — so the
    product passes the epitope through whenever it has one.

    Residue numbers here follow the API's own convention; converting from the
    author numbering printed in a PDB file is ``residues.py``'s job.
    """

    entities: tuple[tuple[str, str], ...]
    """``(chain_id, sequence)`` pairs. Protein only; that is what a screen takes."""

    pocket_residues: dict[str, tuple[int, ...]] = field(default_factory=dict)
    reference_ligands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.entities:
            raise ValueError("a screen target needs at least one protein chain")
        seen: set[str] = set()
        for chain_id, sequence in self.entities:
            if not chain_id or not chain_id.isalnum():
                raise ValueError(f"chain id must be alphanumeric: {chain_id!r}")
            if not sequence.strip():
                raise ValueError(f"chain {chain_id} has no sequence")
            if chain_id in seen:
                raise ValueError(f"chain id {chain_id!r} appears twice")
            seen.add(chain_id)
        for chain_id in self.pocket_residues:
            if chain_id not in seen:
                raise ValueError(f"pocket chain {chain_id!r} is not in the target")


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    """A library screen: one target, many molecules.

    Molecules rejected by the filters are **skipped silently and never appear
    in the results** — the only trace is ``progress.num_molecules_failed``. The
    caller is expected to surface that count rather than let a library quietly
    shrink.
    """

    target: ScreenTarget
    molecules: tuple[Molecule, ...]
    filter_level: FilterLevel = "recommended"

    def __post_init__(self) -> None:
        if not self.molecules:
            raise ValueError("a screen needs at least one molecule")
        ids = [m.external_id for m in self.molecules if m.external_id]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "molecule ids must be unique: they are how results are matched back to your library"
            )


@dataclass(frozen=True, slots=True)
class AdmeSpec:
    """Drug-likeness for a batch of molecules, without screening them."""

    molecules: tuple[Molecule, ...]

    def __post_init__(self) -> None:
        if not self.molecules:
            raise ValueError("an ADME request needs at least one molecule")
        if len(self.molecules) > ADME_BATCH_MAX:
            raise ValueError(
                f"ADME takes at most {ADME_BATCH_MAX} molecules per request; "
                f"got {len(self.molecules)}. Split the batch."
            )


@dataclass(frozen=True, slots=True)
class Adme:
    """Tier-1 drug-likeness.

    **Three values, and none of them is toxicity.** There is no hERG, no
    hepatotoxicity, no metabolic stability — not in this API and not anywhere
    free. The product says so rather than letting "ADME" imply a safety check
    it never performed.
    """

    lipophilicity: float | None = None
    permeability: float | None = None
    solubility: Solubility | None = None

    @property
    def is_empty(self) -> bool:
        return self.lipophilicity is None and self.permeability is None and self.solubility is None


@dataclass(frozen=True, slots=True)
class ScreenMetrics:
    """How well one molecule did.

    ``binding_confidence`` is the headline: the API documents it as the primary
    metric for hit discovery, and it is what the product ranks on. Everything
    else is supporting detail, and anything the engine did not report stays
    ``None`` rather than becoming zero — zero is a real score and defaulting to
    it would bury a good molecule.
    """

    binding_confidence: float | None = None
    optimization_score: float | None = None
    structure_confidence: float | None = None
    iptm: float | None = None
    ptm: float | None = None
    complex_plddt: float | None = None
    complex_iplddt: float | None = None

    @property
    def is_rankable(self) -> bool:
        """Can this result be placed in a hit list at all?"""
        return self.binding_confidence is not None


@dataclass(frozen=True, slots=True)
class ScreenWarning:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """One screened molecule."""

    result_id: str
    smiles: str = ""
    external_id: str = ""
    metrics: ScreenMetrics = field(default_factory=ScreenMetrics)
    adme: Adme = field(default_factory=Adme)
    structure: Artifact | None = None
    ligand_structure: Artifact | None = None
    archive: Artifact | None = None
    warnings: tuple[ScreenWarning, ...] = ()
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScreenProgress:
    """How far a screen has got, and how much of the library it dropped."""

    total: int = 0
    screened: int = 0
    failed: int = 0
    latest_result_id: str = ""

    @property
    def percent_complete(self) -> int | None:
        if self.total <= 0:
            return None
        return min(100, round(100 * (self.screened + self.failed) / self.total))


@dataclass(frozen=True, slots=True)
class ScreenJob:
    job_id: str
    status: JobStatus
    livemode: bool
    progress: ScreenProgress = field(default_factory=ScreenProgress)
    pipeline: str = ""
    pipeline_version: str = ""
    error: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


@dataclass(frozen=True, slots=True)
class AdmeResult:
    """One molecule's drug-likeness, as the engine reported it."""

    molecule_id: str
    smiles: str = ""
    external_id: str = ""
    succeeded: bool = True
    error: str = ""
    adme: Adme = field(default_factory=Adme)


@dataclass(frozen=True, slots=True)
class AdmeJob:
    job_id: str
    status: JobStatus
    livemode: bool
    model: str = ""
    version: str = ""
    error: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    results: tuple[AdmeResult, ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


@runtime_checkable
class ScreeningEngine(Protocol):
    """What the app needs to turn a target into a ranked shortlist.

    As everywhere in this package, nothing waits. A screen of a few hundred
    molecules takes minutes to hours; the reconciler owns polling and results
    stream in as they are scored.
    """

    name: str

    async def estimate_screen_cost(self, spec: ScreenSpec) -> CostEstimate: ...

    async def start_screen(self, spec: ScreenSpec, *, idempotency_key: str) -> ScreenJob: ...

    async def get_screen(self, job_id: str) -> ScreenJob: ...

    async def stop_screen(self, job_id: str) -> ScreenJob: ...

    async def estimate_adme_cost(self, spec: AdmeSpec) -> CostEstimate: ...

    async def start_adme(self, spec: AdmeSpec, *, idempotency_key: str) -> AdmeJob: ...

    async def get_adme(self, job_id: str) -> AdmeJob: ...

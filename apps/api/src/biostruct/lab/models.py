"""The two things this product makes.

Deliberately small. A researcher pastes a sequence and gets a structure; they
look at it and ask for molecules that might block it. That is the whole data
model, and anything else here would be weight the demo has to carry.

Both share one lifecycle, because both are the same shape of thing: a job
submitted to Boltz that takes minutes and either produces a result or a reason
it did not.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: The twenty standard amino acids, plus the ambiguity codes a real sequencing
#: readout contains. X is "unknown residue" and appears in genuine data, so
#: rejecting it would turn away exactly the mutants this product is for.
AMINO_ACIDS = set("ACDEFGHIKLMNPQRSTVWYXBZUO")

#: Boltz's own floor and ceiling for a single chain. Below the floor there is
#: nothing to fold; the ceiling is where a prediction stops being affordable
#: or quick enough to wait for.
SEQUENCE_MIN = 20
SEQUENCE_MAX = 2000

#: What a screen returns. Ten is what a bench can realistically follow up, and
#: it is what the brief asked for.
TOP_N = 10

#: The letters that make a sequence look like DNA or RNA rather than protein.
NUCLEOTIDES = frozenset("ACGTUN")

#: Below this length the composition test is meaningless — a short peptide can
#: legitimately be rich in alanine, cysteine and glycine.
NUCLEOTIDE_MIN_CHECK = 30

#: A real protein is not 90% alanine/cysteine/glycine/threonine over 30+
#: residues. Anything that is, is a nucleotide sequence someone pasted by
#: mistake.
NUCLEOTIDE_SHARE = 0.9

#: And it uses more than one base. A run of a single letter is a linker or a
#: stuck key, not a genome.
NUCLEOTIDE_DISTINCT_MIN = 3


class JobState(StrEnum):
    pending = "pending"
    running = "running"
    complete = "complete"
    failed = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobState.complete, JobState.failed)


def clean_sequence(raw: str) -> str:
    """Normalise a pasted sequence, or say exactly what is wrong with it.

    Real sequences arrive copied out of FASTA files, emails and sequencer
    output: wrapped across lines, with a ``>`` header, sometimes lower case,
    often with stray numbers from a numbered listing. All of that is the
    product's problem to handle, not the researcher's — being told "invalid
    input" because you pasted the header too is the kind of thing that makes
    people give up on a tool.
    """
    lines = [ln.strip() for ln in raw.strip().splitlines()]
    # Drop FASTA headers and blank lines.
    body = "".join(ln for ln in lines if ln and not ln.startswith((">", ";")))
    # Sequencer listings interleave position numbers and spaces.
    letters = "".join(ch for ch in body if not ch.isdigit() and not ch.isspace())
    sequence = letters.upper()

    if not sequence:
        raise ValueError("Paste an amino-acid sequence — there was nothing to read.")

    # Pasting DNA is the single most common mistake here, and the alphabet
    # cannot catch it: A, C, G, T and U are all valid amino-acid letters too.
    # What gives it away is composition — a real protein of any length is not
    # made almost entirely of those five. Checked before the length rules so
    # the person gets the useful message rather than "too short".
    if len(sequence) >= NUCLEOTIDE_MIN_CHECK:
        nucleotide_share = sum(ch in NUCLEOTIDES for ch in sequence) / len(sequence)
        # Distinct bases as well as share, so a homopolymer — a poly-alanine
        # linker, or someone holding down a key — is not mistaken for DNA.
        distinct = len(set(sequence) & NUCLEOTIDES)
        if nucleotide_share >= NUCLEOTIDE_SHARE and distinct >= NUCLEOTIDE_DISTINCT_MIN:
            raise ValueError(
                "That looks like a DNA or RNA sequence, not a protein. "
                "Translate it to amino acids first — the structure predictor "
                "folds proteins."
            )

    unknown = sorted(set(sequence) - AMINO_ACIDS)
    if unknown:
        shown = ", ".join(unknown[:5])
        raise ValueError(
            f"That does not look like a protein sequence: it contains {shown}. "
            "If you pasted DNA, translate it to amino acids first."
        )
    if len(sequence) < SEQUENCE_MIN:
        raise ValueError(
            f"That sequence is {len(sequence)} residues. Folding needs at least {SEQUENCE_MIN}."
        )
    if len(sequence) > SEQUENCE_MAX:
        raise ValueError(
            f"That sequence is {len(sequence):,} residues, and we cap a single "
            f"prediction at {SEQUENCE_MAX:,}."
        )
    return sequence


class PredictionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    sequence: str = Field(min_length=1, max_length=40_000)
    name: str = Field(default="", max_length=120)


class Prediction(BaseModel):
    """A folded protein."""

    id: str
    user_id: str
    name: str
    sequence: str
    state: JobState
    error: str = ""
    engine_job_id: str = ""
    livemode: bool = False
    structure_confidence: float | None = None
    ptm: float | None = None
    has_structure: bool = False
    created_at: datetime
    updated_at: datetime

    @property
    def length(self) -> int:
        return len(self.sequence)

    def display_name(self) -> str:
        """Never blank: a list of untitled rows is unusable."""
        return self.name.strip() or f"{self.length}-residue protein"


class Hit(BaseModel):
    """One molecule from a screen, as a bench scientist reads it."""

    rank: int
    smiles: str
    name: str = ""
    external_id: str = ""
    is_control: bool = False
    #: Predicted by the model. Not a measurement, and labelled as such
    #: everywhere it is shown.
    binding_confidence: float | None = None
    structure_confidence: float | None = None
    #: Measured in a laboratory and recorded in ChEMBL, where we have it.
    known_potency_nm: float | None = None
    lipophilicity: float | None = None
    permeability: float | None = None
    solubility: str = ""


class Screen(BaseModel):
    """Molecules ranked against a folded protein."""

    id: str
    user_id: str
    prediction_id: str
    state: JobState
    error: str = ""
    engine_job_id: str = ""
    livemode: bool = False
    ambler_class: str = "A"
    submitted: int = 0
    returned: int = 0
    #: Where the known inhibitor landed. The screen's own report card: if this
    #: is not near the top, the ranking below it means nothing.
    control_rank: int | None = None
    control_name: str = ""
    hits: list[Hit] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @property
    def control_looks_right(self) -> bool | None:
        """``None`` when there was no control to judge by — which only happens
        in test mode, where the engine substitutes its own molecules."""
        if self.control_rank is None:
            return None
        return self.control_rank <= max(3, round(len(self.hits) * 0.2))


__all__ = [
    "AMINO_ACIDS",
    "NUCLEOTIDES",
    "NUCLEOTIDE_DISTINCT_MIN",
    "NUCLEOTIDE_MIN_CHECK",
    "NUCLEOTIDE_SHARE",
    "SEQUENCE_MAX",
    "SEQUENCE_MIN",
    "TOP_N",
    "Hit",
    "JobState",
    "Prediction",
    "PredictionCreate",
    "Screen",
    "clean_sequence",
]

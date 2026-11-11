from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StructureRefOut(BaseModel):
    pdb_id: str
    title: str = ""
    method: str = ""
    resolution_a: float | None = None
    released: str = ""
    cif_url: str
    quality_note: str = ""


class ReferenceOut(BaseModel):
    pmid: str
    title: str
    journal: str = ""
    pubdate: str = ""
    doi: str = ""
    url: str


class ActiveSiteOut(BaseModel):
    """An annotated functional residue, in UniProt (1-based) numbering.

    Offered as an epitope suggestion: a researcher targeting an enzyme usually
    wants the active site, and these come from UniProt's curation rather than
    from us guessing.
    """

    position: int
    description: str = ""


class TargetCandidate(BaseModel):
    """A search hit. Not yet persisted to a project."""

    accession: str
    protein_name: str
    organism: str
    organism_id: int | None = None
    length: int
    reviewed: bool
    evidence_level: str
    function: str = ""
    gene_names: list[str] = Field(default_factory=list)
    pdb_count: int = 0
    active_sites: list[ActiveSiteOut] = Field(default_factory=list)


class TargetSearchResponse(BaseModel):
    items: list[TargetCandidate]
    query: str
    reviewed_only: bool
    degraded_sources: list[str] = Field(default_factory=list)
    degraded_detail: dict[str, str] = Field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return not self.degraded_sources


class TargetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    accession: str = Field(min_length=3, max_length=20)


class EpitopeSelection(BaseModel):
    """Which structure, chain and residues to design against.

    Residues are given in **author numbering** — what the literature and the PDB
    file use, e.g. 70 for the catalytic serine of KPC-2. The server converts
    them to the 0-based indices the design API needs, and refuses rather than
    guesses if any cannot be mapped (ARCHITECTURE.md, ``sources/residues.py``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    pdb_id: str = Field(min_length=4, max_length=8)
    chain_id: str = Field(min_length=1, max_length=4)
    author_residues: list[int] = Field(
        default_factory=list,
        max_length=400,
        description="Residues in PDB author numbering, e.g. 70 for KPC-2's catalytic serine.",
    )
    uniprot_positions: list[int] = Field(
        default_factory=list,
        max_length=400,
        description=(
            "Residues in UniProt numbering, which is a DIFFERENT system: UniProt "
            "annotates KPC-2's catalytic serine at 69, while the PDB and the "
            "literature call it 70. Use this when selecting from the target's "
            "active_sites, which are UniProt-numbered. Converted and identity-checked "
            "server-side."
        ),
    )
    expected_residues: dict[str, str] = Field(
        default_factory=dict,
        description=(
            'Optional author-number to residue-code map, e.g. {"70": "Ser"}. '
            "When supplied, the mapping is verified against it and the request is "
            "rejected on mismatch. This is the only way to prove the conversion "
            "is right rather than merely plausible."
        ),
    )

    @model_validator(mode="after")
    def _needs_residues(self) -> EpitopeSelection:
        if not self.author_residues and not self.uniprot_positions:
            raise ValueError(
                "give author_residues or uniprot_positions (they are different "
                "numbering systems; see the field descriptions)"
            )
        return self


class ResidueMapSummary(BaseModel):
    """Shown to the researcher so the conversion is not a black box."""

    pdb_id: str
    chain_id: str
    modelled_residues: int
    author_numbering: str
    contiguous: bool
    gaps: list[str] = Field(default_factory=list)


class EpitopeOut(BaseModel):
    pdb_id: str
    chain_id: str
    cif_url: str
    author_residues: list[int]
    uniprot_positions: list[int] = Field(default_factory=list)
    design_indices: list[int]
    """0-based indices, which is what gets sent to the design engine."""
    residue_letters: str
    """The amino acids at those indices, so a mistake is visible at a glance."""
    mapping: ResidueMapSummary
    verified: bool = False
    """True when expected_residues was supplied and every one matched."""


class Target(BaseModel):
    id: str
    project_id: str
    accession: str
    protein_name: str
    organism: str
    organism_id: int | None = None
    length: int
    sequence: str
    reviewed: bool
    function: str = ""
    gene_names: list[str] = Field(default_factory=list)
    active_sites: list[ActiveSiteOut] = Field(default_factory=list)
    structures: list[StructureRefOut] = Field(default_factory=list)
    references: list[ReferenceOut] = Field(default_factory=list)
    epitope: EpitopeOut | None = None
    degraded_sources: list[str] = Field(default_factory=list)
    evidence_fetched_at: datetime
    created_at: datetime
    schema_version: int = 1

    @property
    def is_designable(self) -> bool:
        return bool(self.structures)

    @property
    def is_ready_for_design(self) -> bool:
        return self.epitope is not None

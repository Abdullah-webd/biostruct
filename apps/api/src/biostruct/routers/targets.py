"""Target discovery: steps 2 and 3 of the workflow.

The epitope endpoint is the one that matters. It converts author residue
numbers — the numbering in the literature and in PDB files — into the 0-based
indices the design engine needs, and refuses rather than guessing when a residue
cannot be mapped. Getting this wrong aims designs at the wrong surface and
reports nothing, so it is deliberately strict.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from biostruct import errors
from biostruct.deps import (
    CsrfDep,
    CurrentUser,
    ProjectsDep,
    SourcesDep,
    TargetsDep,
)
from biostruct.models.target import (
    ActiveSiteOut,
    EpitopeOut,
    EpitopeSelection,
    ResidueMapSummary,
    Target,
    TargetCandidate,
    TargetCreate,
    TargetSearchResponse,
)
from biostruct.sources.http import SourceUnavailable
from biostruct.sources.residues import ResidueMappingError

router = APIRouter(tags=["targets"], dependencies=[CsrfDep])


class TargetSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(min_length=2, max_length=400)
    organism_id: int | None = Field(
        default=None,
        description="NCBI taxonomy id, e.g. 573 for Klebsiella pneumoniae",
    )
    reviewed_only: bool = Field(
        default=True,
        description=(
            "Restrict to curated Swiss-Prot entries. Off returns predicted TrEMBL "
            "entries too, which look identical in a list but carry much weaker evidence."
        ),
    )
    limit: int = Field(default=25, ge=1, le=100)


class TargetList(BaseModel):
    items: list[Target]


async def _project_or_404(projects: ProjectsDep, project_id: str, user_id: str) -> None:
    if await projects.get(project_id, user_id=user_id) is None:
        raise errors.not_found("Project")


# --- step 2: find a target -------------------------------------------------


@router.post("/projects/{project_id}/targets/search")
async def search_targets(
    project_id: str,
    body: TargetSearchRequest,
    user: CurrentUser,
    projects: ProjectsDep,
    sources: SourcesDep,
) -> TargetSearchResponse:
    """Search UniProt for candidate targets.

    Returns 200 even when a source is degraded, with the failure named, because
    a partial panel labelled as partial is useful while a silently partial one
    is dangerous.
    """
    await _project_or_404(projects, project_id, user.id)
    result = await sources.search_targets(
        body.query,
        organism_id=body.organism_id,
        reviewed_only=body.reviewed_only,
        limit=body.limit,
    )
    return TargetSearchResponse(
        items=[
            TargetCandidate(
                accession=t.accession,
                protein_name=t.protein_name,
                organism=t.organism,
                organism_id=t.organism_id,
                length=t.length,
                reviewed=t.reviewed,
                evidence_level=t.evidence_level,
                function=t.function,
                gene_names=list(t.gene_names),
                pdb_count=len(t.pdb_ids),
                active_sites=[
                    ActiveSiteOut(position=s.position, description=s.description)
                    for s in t.active_sites
                ],
            )
            for t in result.targets
        ],
        query=body.query,
        reviewed_only=body.reviewed_only,
        degraded_sources=result.degradation.sources,
        degraded_detail=dict(result.degradation.failures),
    )


@router.post("/projects/{project_id}/targets", status_code=status.HTTP_201_CREATED)
async def add_target(
    project_id: str,
    body: TargetCreate,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
    sources: SourcesDep,
) -> Target:
    """Persist a chosen target together with its evidence.

    A project holds many targets (ADR-014), because isolates routinely carry
    more than one resistance gene.
    """
    await _project_or_404(projects, project_id, user.id)
    existing = await targets.get_by_accession(body.accession, project_id=project_id)
    if existing is not None:
        return existing
    try:
        evidence = await sources.gather_evidence(body.accession)
    except SourceUnavailable as exc:
        raise errors.ProblemError(
            code="source_unavailable",
            title="Could not retrieve the target",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{exc.source}: {exc.detail}",
        ) from exc
    return await targets.create(project_id=project_id, evidence=evidence)


@router.get("/projects/{project_id}/targets")
async def list_targets(
    project_id: str, user: CurrentUser, projects: ProjectsDep, targets: TargetsDep
) -> TargetList:
    await _project_or_404(projects, project_id, user.id)
    return TargetList(items=await targets.list_for_project(project_id))


@router.get("/projects/{project_id}/targets/{target_id}")
async def get_target(
    project_id: str,
    target_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
) -> Target:
    await _project_or_404(projects, project_id, user.id)
    target = await targets.get(target_id, project_id=project_id)
    if target is None:
        raise errors.not_found("Target")
    return target


@router.post("/projects/{project_id}/targets/{target_id}/refresh")
async def refresh_target(
    project_id: str,
    target_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
    sources: SourcesDep,
) -> Target:
    """Re-fetch evidence, which is how a researcher recovers from a degraded panel."""
    await _project_or_404(projects, project_id, user.id)
    target = await targets.get(target_id, project_id=project_id)
    if target is None:
        raise errors.not_found("Target")
    evidence = await sources.gather_evidence(target.accession)
    refreshed = await targets.refresh_evidence(target_id, project_id=project_id, evidence=evidence)
    if refreshed is None:
        raise errors.not_found("Target")
    return refreshed


@router.delete("/projects/{project_id}/targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_target(
    project_id: str,
    target_id: str,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
) -> Response:
    await _project_or_404(projects, project_id, user.id)
    if not await targets.delete(target_id, project_id=project_id):
        raise errors.not_found("Target")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- step 3: choose the epitope -------------------------------------------


@router.get("/structures/{pdb_id}/chains/{chain_id}/residue-map")
async def get_residue_map(
    pdb_id: str,
    chain_id: str,
    user: CurrentUser,
    sources: SourcesDep,
    author_residues: Annotated[str, Query(description="comma-separated author numbers")] = "",
) -> dict[str, object]:
    """Inspect a chain's numbering before committing to an epitope.

    Lets the UI show that, say, 2OV5 chain A covers author residues 30-292 with
    gaps, so a researcher can see why residue 5 is unavailable.
    """
    try:
        rmap = await sources.residue_map(pdb_id, chain_id)
    except SourceUnavailable as exc:
        raise errors.ProblemError(
            code="source_unavailable",
            title="Residue mapping unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        ) from exc

    out: dict[str, object] = {"mapping": rmap.describe(), "sequence": rmap.sequence}
    if author_residues.strip():
        wanted = [r.strip() for r in author_residues.split(",") if r.strip()]
        try:
            indices = rmap.to_indices(list(wanted))
        except ResidueMappingError as exc:
            raise errors.ProblemError(
                code="residue_not_modelled",
                title="Some residues could not be mapped",
                status_code=422,
                detail=str(exc),
            ) from exc
        out["design_indices"] = list(indices)
        out["residue_letters"] = "".join(rmap.sequence[i] for i in indices)
    return out


@router.put("/projects/{project_id}/targets/{target_id}/epitope")
async def set_epitope(
    project_id: str,
    target_id: str,
    body: EpitopeSelection,
    user: CurrentUser,
    projects: ProjectsDep,
    targets: TargetsDep,
    sources: SourcesDep,
) -> Target:
    """Record which structure, chain and residues to design against.

    Author numbers in, design indices out, with the conversion verified when the
    caller states what residues it expects. Anything unmappable is a 422 naming
    every problem at once, so a ten-residue epitope does not take ten round
    trips to fix.
    """
    await _project_or_404(projects, project_id, user.id)
    target = await targets.get(target_id, project_id=project_id)
    if target is None:
        raise errors.not_found("Target")

    try:
        rmap = await sources.residue_map(body.pdb_id, body.chain_id)
    except SourceUnavailable as exc:
        raise errors.ProblemError(
            code="source_unavailable",
            title="Residue mapping unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"{exc.detail}. Without the mapping an epitope cannot be converted "
                "safely, so the selection was not saved."
            ),
        ) from exc

    # Two numbering systems can arrive, and they are not interchangeable.
    # UniProt positions are converted via sequence alignment against the stored
    # target sequence, with the residue identity checked (see residues.py).
    collected: set[int] = set()
    problems: list[str] = []
    try:
        if body.author_residues:
            collected.update(rmap.to_indices(list(body.author_residues)))
    except ResidueMappingError as exc:
        problems.append(str(exc))
    for position in body.uniprot_positions:
        expected = next(
            (s.description and None for s in target.active_sites if s.position == position),
            None,
        )
        try:
            collected.add(
                rmap.index_from_uniprot_position(position, target.sequence, expected=expected)
            )
        except ResidueMappingError as exc:
            problems.append(str(exc))
    if problems:
        raise errors.ProblemError(
            code="residue_not_modelled",
            title="Some residues are not in this structure",
            status_code=422,
            detail="; ".join(problems),
            mapping=rmap.describe(),
        )
    indices = tuple(sorted(collected))

    verified = False
    if body.expected_residues:
        try:
            rmap.verify_identities(dict(body.expected_residues))
        except ResidueMappingError as exc:
            raise errors.ProblemError(
                code="residue_mismatch",
                title="Residue identities did not match",
                status_code=422,
                detail=str(exc),
                mapping=rmap.describe(),
            ) from exc
        verified = True

    epitope = EpitopeOut(
        pdb_id=body.pdb_id.upper(),
        chain_id=body.chain_id,
        cif_url=f"https://files.rcsb.org/download/{body.pdb_id.upper()}.cif",
        # Record the author numbers for every selected index, whichever
        # numbering the request used, so the stored epitope is unambiguous.
        author_residues=[int(rmap.to_author(i)) for i in indices if rmap.to_author(i).isdigit()],
        uniprot_positions=sorted(set(body.uniprot_positions)),
        design_indices=list(indices),
        residue_letters="".join(rmap.sequence[i] for i in indices),
        mapping=ResidueMapSummary(**rmap.describe()),
        verified=verified,
    )
    updated = await targets.set_epitope(target_id, project_id=project_id, epitope=epitope)
    if updated is None:
        raise errors.not_found("Target")
    return updated

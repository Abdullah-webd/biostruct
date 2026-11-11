"""Projects CRUD.

A project is the unit everything else hangs off: one disease, many targets
(ADR-014). Every handler takes the project id *and* the current user's id, so
authorisation is part of the query rather than a separate check that could be
forgotten.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel

from biostruct import errors
from biostruct.deps import CsrfDep, CurrentUser, ProjectsDep, SettingsDep
from biostruct.models.project import Project, ProjectCreate, ProjectUpdate, RankingWeights

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[CsrfDep])


class ProjectList(BaseModel):
    items: list[Project]
    total: int


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: CurrentUser,
    projects: ProjectsDep,
    settings: SettingsDep,
) -> Project:
    return await projects.create(
        user_id=user.id,
        data=body,
        default_budget_usd=settings.default_project_budget_usd,
    )


@router.get("")
async def list_projects(
    user: CurrentUser,
    projects: ProjectsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> ProjectList:
    items = await projects.list_for_user(user.id, limit=limit, skip=skip)
    total = await projects.count_for_user(user.id)
    return ProjectList(items=items, total=total)


@router.get("/{project_id}")
async def get_project(project_id: str, user: CurrentUser, projects: ProjectsDep) -> Project:
    project = await projects.get(project_id, user_id=user.id)
    if project is None:
        # 404, not 403: another user's project should be indistinguishable from
        # one that does not exist, or the API becomes an existence oracle.
        raise errors.not_found("Project")
    return project


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    user: CurrentUser,
    projects: ProjectsDep,
) -> Project:
    project = await projects.update(project_id, user_id=user.id, data=body)
    if project is None:
        raise errors.not_found("Project")
    return project


@router.put("/{project_id}/ranking-weights")
async def set_ranking_weights(
    project_id: str,
    body: RankingWeights,
    user: CurrentUser,
    projects: ProjectsDep,
) -> Project:
    """Re-rank without recomputation.

    Weights are applied to metrics we already hold, so changing them costs
    nothing and spends no credits (ARCHITECTURE.md §6.4).
    """
    project = await projects.update(
        project_id, user_id=user.id, data=ProjectUpdate(ranking_weights=body)
    )
    if project is None:
        raise errors.not_found("Project")
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, user: CurrentUser, projects: ProjectsDep) -> Response:
    # Later milestones also call Boltz delete_data for this project's runs, so
    # deletion propagates upstream (ARCHITECTURE.md §8 retention).
    deleted = await projects.delete(project_id, user_id=user.id)
    if not deleted:
        raise errors.not_found("Project")
    return Response(status_code=status.HTTP_204_NO_CONTENT)

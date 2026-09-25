from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from testmind.api.deps import ProjectServiceDep, RunServiceDep
from testmind.schemas.analysis_run import AnalysisRunRead
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.project import ProjectCreate, ProjectRead

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Project not found"}
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "Name already taken"},
        422: {"model": ErrorResponse, "description": "Invalid body or unusable source"},
    },
)
async def create_project(
    body: ProjectCreate, service: ProjectServiceDep, response: Response
) -> ProjectRead:
    project = await service.create(body)
    response.headers["Location"] = f"{router.prefix}/{project.id}"
    return ProjectRead.model_validate(project)


@router.get("")
async def list_projects(
    service: ProjectServiceDep, limit: Limit = 20, offset: Offset = 0
) -> Page[ProjectRead]:
    projects, total = await service.list(offset=offset, limit=limit)
    return Page[ProjectRead](
        items=[ProjectRead.model_validate(p) for p in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{project_id}", responses=NOT_FOUND)
async def get_project(project_id: uuid.UUID, service: ProjectServiceDep) -> ProjectRead:
    return ProjectRead.model_validate(await service.get(project_id))


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Project has active runs"},
    },
)
async def delete_project(project_id: uuid.UUID, service: ProjectServiceDep) -> None:
    await service.delete(project_id)


@router.post(
    "/{project_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        503: {"model": ErrorResponse, "description": "Task queue unavailable"},
    },
)
async def create_run(project_id: uuid.UUID, service: RunServiceDep) -> AnalysisRunRead:
    """Queue an analysis run. Poll `GET /api/v1/runs/{id}` for its status."""
    return AnalysisRunRead.model_validate(await service.create(project_id))


@router.get("/{project_id}/runs", responses=NOT_FOUND)
async def list_runs(
    project_id: uuid.UUID, service: RunServiceDep, limit: Limit = 20, offset: Offset = 0
) -> Page[AnalysisRunRead]:
    runs, total = await service.list_for_project(project_id, offset=offset, limit=limit)
    return Page[AnalysisRunRead](
        items=[AnalysisRunRead.model_validate(r) for r in runs],
        total=total,
        limit=limit,
        offset=offset,
    )

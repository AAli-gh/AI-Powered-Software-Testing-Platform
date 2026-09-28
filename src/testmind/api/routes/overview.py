"""Dashboard aggregates."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from testmind.api.deps import OverviewServiceDep
from testmind.schemas.common import ErrorResponse
from testmind.schemas.overview import PlatformOverview, ProjectOverview

router = APIRouter(prefix="/api/v1", tags=["overview"])


@router.get("/overview")
async def platform_overview(service: OverviewServiceDep) -> PlatformOverview:
    """Job counts by status, projects with their latest results, recent executions and pull
    request analyses."""
    return await service.platform()


@router.get(
    "/projects/{project_id}/overview",
    responses={404: {"model": ErrorResponse, "description": "Project not found"}},
)
async def project_overview(project_id: uuid.UUID, service: OverviewServiceDep) -> ProjectOverview:
    """A project's run counts, latest runs, and trends: executions (tests, coverage, API
    coverage), mutation scores, regressions and pull request analyses."""
    return await service.project(project_id)

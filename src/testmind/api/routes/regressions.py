"""Regression analysis between two commits of a project's git repository."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from testmind.api.deps import RegressionServiceDep
from testmind.core.errors import NotFoundError
from testmind.regression.models import RegressionReport, TestChange, TestChangeKind
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.regression import RegressionCreate, RegressionRead
from testmind.services.regression_service import RegressionService

router = APIRouter(prefix="/api/v1", tags=["regression"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"}
}


@router.post(
    "/projects/{project_id}/regressions",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "The project is not a git repository"},
        422: {"model": ErrorResponse, "description": "Unknown or invalid revision"},
        503: {"model": ErrorResponse, "description": "Queue unavailable"},
    },
)
async def create_regression(
    project_id: uuid.UUID, body: RegressionCreate, service: RegressionServiceDep
) -> RegressionRead:
    """Queue a regression analysis from `base` to `head` (both resolved to commits now)."""
    regression = await service.create(project_id, body.base, body.head, body.mode)
    return RegressionRead.from_row(regression)


@router.get("/projects/{project_id}/regressions", responses=NOT_FOUND)
async def list_regressions(
    project_id: uuid.UUID,
    service: RegressionServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RegressionRead]:
    items, total = await service.list_for_project(project_id, offset=offset, limit=limit)
    return Page[RegressionRead](
        items=[RegressionRead.from_row(r) for r in items], total=total, limit=limit, offset=offset
    )


@router.get("/regressions/{regression_id}", responses=NOT_FOUND)
async def get_regression(regression_id: uuid.UUID, service: RegressionServiceDep) -> RegressionRead:
    return RegressionRead.from_row(await service.get(regression_id))


@router.get("/regressions/{regression_id}/report", responses=NOT_FOUND)
async def get_regression_report(
    regression_id: uuid.UUID, service: RegressionServiceDep
) -> RegressionReport:
    """Changes, selection (with reasons), both runs, test changes, failure causes, coverage."""
    return await _report(service, regression_id)


@router.get("/regressions/{regression_id}/tests", responses=NOT_FOUND)
async def list_regression_tests(
    regression_id: uuid.UUID,
    service: RegressionServiceDep,
    change: TestChangeKind | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TestChange]:
    """Per-test comparison; `change=regressed` lists the regressions."""
    tests = (await _report(service, regression_id)).tests
    tests = [t for t in tests if change is None or t.change is change]
    return Page[TestChange].slice(tests, limit=limit, offset=offset)


async def _report(service: RegressionService, regression_id: uuid.UUID) -> RegressionReport:
    regression = await service.get(regression_id)
    if regression.report is None:
        raise NotFoundError(
            f"Regression '{regression_id}' has no report (status: {regression.status.value})"
        )
    return RegressionReport.model_validate(regression.report)

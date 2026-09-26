"""Failure analysis: the deterministic report of an execution, and AI analyses on request."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from testmind.api.deps import FailureAnalysisServiceDep
from testmind.core.errors import NotFoundError
from testmind.failure_engine import FailureCategory, FailureGroup
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.failure_analysis import FailureAnalysisRead, FailureOverview

router = APIRouter(prefix="/api/v1", tags=["failure analysis"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"}
}
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/test-executions/{execution_id}/failures", responses=NOT_FOUND)
async def get_failures(
    execution_id: uuid.UUID, service: FailureAnalysisServiceDep
) -> FailureOverview:
    """Deterministic failure analysis, made with every execution: counts per category and
    how many groups the facts already decide."""
    return FailureOverview.from_report(await service.report(execution_id))


@router.get("/test-executions/{execution_id}/failures/groups", responses=NOT_FOUND)
async def list_failure_groups(
    execution_id: uuid.UUID,
    service: FailureAnalysisServiceDep,
    category: FailureCategory | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[FailureGroup]:
    """Failures grouped by cause, largest group first, with frames, locals and evidence."""
    groups = (await service.report(execution_id)).groups
    groups = [g for g in groups if category is None or g.category is category]
    return Page[FailureGroup].slice(groups, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/failures/groups/{group_id}", responses=NOT_FOUND)
async def get_failure_group(
    execution_id: uuid.UUID, group_id: str, service: FailureAnalysisServiceDep
) -> FailureGroup:
    report = await service.report(execution_id)
    for group in report.groups:
        if group.id == group_id:
            return group
    raise NotFoundError(f"Failure group '{group_id}' not found in this execution")


@router.post(
    "/test-executions/{execution_id}/failure-analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "No failure groups to analyse"},
        503: {"model": ErrorResponse, "description": "No LLM configured, or queue unavailable"},
    },
)
async def create_failure_analysis(
    execution_id: uuid.UUID, service: FailureAnalysisServiceDep
) -> FailureAnalysisRead:
    """Queue an AI analysis of the execution's failure groups (root cause hypotheses with
    verified evidence and confidence)."""
    return FailureAnalysisRead.model_validate(await service.create(execution_id))


@router.get("/test-executions/{execution_id}/failure-analyses", responses=NOT_FOUND)
async def list_failure_analyses(
    execution_id: uuid.UUID,
    service: FailureAnalysisServiceDep,
    limit: Limit = 20,
    offset: Offset = 0,
) -> Page[FailureAnalysisRead]:
    items, total = await service.list_for_execution(execution_id, offset=offset, limit=limit)
    return Page[FailureAnalysisRead](
        items=[FailureAnalysisRead.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/failure-analyses/{analysis_id}", responses=NOT_FOUND)
async def get_failure_analysis(
    analysis_id: uuid.UUID, service: FailureAnalysisServiceDep
) -> FailureAnalysisRead:
    return FailureAnalysisRead.model_validate(await service.get(analysis_id))

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from testmind.analyzers.repository.models import FileEntry, RepositorySummary
from testmind.api.deps import RunServiceDep
from testmind.schemas.analysis_run import AnalysisRunRead
from testmind.schemas.common import ErrorResponse, Page

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Run not found"}
}


@router.get("/{run_id}", responses=NOT_FOUND)
async def get_run(run_id: uuid.UUID, service: RunServiceDep) -> AnalysisRunRead:
    return AnalysisRunRead.model_validate(await service.get(run_id))


@router.post(
    "/{run_id}/cancel",
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Run is no longer queued"},
    },
)
async def cancel_run(run_id: uuid.UUID, service: RunServiceDep) -> AnalysisRunRead:
    """Cancel a run that has not started yet."""
    return AnalysisRunRead.model_validate(await service.cancel(run_id))


ANALYSIS_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Run not found, or it has no analysis yet"}
}


@router.get("/{run_id}/analysis", responses=ANALYSIS_NOT_FOUND)
async def get_analysis(run_id: uuid.UUID, service: RunServiceDep) -> RepositorySummary:
    """Repository analysis of a succeeded run (without the per-file inventory)."""
    return (await service.get_analysis(run_id)).summary()


@router.get("/{run_id}/analysis/files", responses=ANALYSIS_NOT_FOUND)
async def list_analysis_files(
    run_id: uuid.UUID,
    service: RunServiceDep,
    *,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    language: Annotated[str | None, Query(max_length=64)] = None,
    is_test: bool | None = None,
) -> Page[FileEntry]:
    """Files seen by the analysis, sorted by path, optionally filtered."""
    files = (await service.get_analysis(run_id)).files
    if language is not None:
        files = [f for f in files if f.language == language]
    if is_test is not None:
        files = [f for f in files if f.is_test is is_test]
    return Page[FileEntry](
        items=files[offset : offset + limit], total=len(files), limit=limit, offset=offset
    )

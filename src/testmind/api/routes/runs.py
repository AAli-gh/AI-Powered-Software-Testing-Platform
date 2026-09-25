from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from testmind.api.deps import RunServiceDep
from testmind.schemas.analysis_run import AnalysisRunRead
from testmind.schemas.common import ErrorResponse

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

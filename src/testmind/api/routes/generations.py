"""AI test generation for an analysed run."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from testmind.ai_engine.test_generator.models import GenerationOptions
from testmind.api.deps import GenerationServiceDep
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.test_generation import TestGenerationRead

router = APIRouter(prefix="/api/v1", tags=["test generation"])

RUN_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Run not found"}
}


@router.post(
    "/runs/{run_id}/test-generations",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **RUN_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "The run has not succeeded"},
        503: {"model": ErrorResponse, "description": "No LLM configured, or queue unavailable"},
    },
)
async def create_generation(
    run_id: uuid.UUID,
    service: GenerationServiceDep,
    options: Annotated[GenerationOptions | None, Body()] = None,
) -> TestGenerationRead:
    """Queue AI test generation for the run's code. Poll `GET /test-generations/{id}`."""
    generation = await service.create(run_id, options or GenerationOptions())
    return TestGenerationRead.model_validate(generation)


@router.get("/runs/{run_id}/test-generations", responses=RUN_NOT_FOUND)
async def list_generations(
    run_id: uuid.UUID,
    service: GenerationServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TestGenerationRead]:
    items, total = await service.list_for_run(run_id, offset=offset, limit=limit)
    return Page[TestGenerationRead](
        items=[TestGenerationRead.model_validate(g) for g in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/test-generations/{generation_id}",
    responses={404: {"model": ErrorResponse, "description": "Generation not found"}},
)
async def get_generation(
    generation_id: uuid.UUID, service: GenerationServiceDep
) -> TestGenerationRead:
    return TestGenerationRead.model_validate(await service.get(generation_id))

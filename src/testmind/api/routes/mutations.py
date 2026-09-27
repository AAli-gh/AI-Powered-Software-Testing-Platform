"""Mutation testing and AI tests for surviving mutants."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from testmind.api.deps import MutationServiceDep
from testmind.mutation_engine.models import FunctionScore, MutantResult, MutantStatus
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.mutation import MutationRunRead, SuggestionRead
from testmind.services.mutation_service import MutationOptions

router = APIRouter(prefix="/api/v1", tags=["mutation testing"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"}
}
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "/runs/{run_id}/mutation-runs",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Run not succeeded"},
        503: {"model": ErrorResponse, "description": "Queue unavailable"},
    },
)
async def create_mutation_run(
    run_id: uuid.UUID,
    service: MutationServiceDep,
    body: Annotated[MutationOptions | None, Body()] = None,
) -> MutationRunRead:
    """Queue mutation testing of the run's repository (riskiest functions first)."""
    job = await service.create(run_id, body or MutationOptions())
    return MutationRunRead.from_row(job)


@router.get("/runs/{run_id}/mutation-runs", responses=NOT_FOUND)
async def list_mutation_runs(
    run_id: uuid.UUID,
    service: MutationServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Offset = 0,
) -> Page[MutationRunRead]:
    items, total = await service.list_for_run(run_id, offset=offset, limit=limit)
    return Page[MutationRunRead](
        items=[MutationRunRead.from_row(j) for j in items], total=total, limit=limit, offset=offset
    )


@router.get("/mutation-runs/{mutation_run_id}", responses=NOT_FOUND)
async def get_mutation_run(
    mutation_run_id: uuid.UUID, service: MutationServiceDep
) -> MutationRunRead:
    return MutationRunRead.from_row(await service.get(mutation_run_id))


@router.get("/mutation-runs/{mutation_run_id}/mutants", responses=NOT_FOUND)
async def list_mutants(
    mutation_run_id: uuid.UUID,
    service: MutationServiceDep,
    *,
    status: MutantStatus | None = None,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[MutantResult]:
    """Every mutant with its verdict; `status=survived` shows what the tests miss."""
    mutants = (await service.report(mutation_run_id)).mutants
    mutants = [
        m
        for m in mutants
        if (status is None or m.status is status) and (path is None or m.path == path)
    ]
    return Page[MutantResult].slice(mutants, limit=limit, offset=offset)


@router.get("/mutation-runs/{mutation_run_id}/functions", responses=NOT_FOUND)
async def list_function_scores(
    mutation_run_id: uuid.UUID,
    service: MutationServiceDep,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[FunctionScore]:
    """Mutation score per function, weakest first (unscored functions last)."""
    functions = sorted(
        (await service.report(mutation_run_id)).functions,
        key=lambda f: (f.score is None, f.score or 0.0, f.path, f.function),
    )
    return Page[FunctionScore].slice(functions, limit=limit, offset=offset)


@router.post(
    "/mutation-runs/{mutation_run_id}/test-suggestions",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "No surviving mutants"},
        503: {"model": ErrorResponse, "description": "No LLM configured, or queue unavailable"},
    },
)
async def create_suggestions(
    mutation_run_id: uuid.UUID, service: MutationServiceDep
) -> SuggestionRead:
    """Queue AI-written tests for the surviving mutants; each is accepted only if the sandbox
    shows it passes on the original code and kills its mutant."""
    return SuggestionRead.model_validate(await service.suggest(mutation_run_id))


@router.get("/mutation-runs/{mutation_run_id}/test-suggestions", responses=NOT_FOUND)
async def list_suggestions(
    mutation_run_id: uuid.UUID, service: MutationServiceDep
) -> list[SuggestionRead]:
    return [
        SuggestionRead.model_validate(s) for s in await service.suggestions_for(mutation_run_id)
    ]


@router.get("/test-suggestions/{suggestion_id}", responses=NOT_FOUND)
async def get_suggestion(suggestion_id: uuid.UUID, service: MutationServiceDep) -> SuggestionRead:
    return SuggestionRead.model_validate(await service.get_suggestion(suggestion_id))

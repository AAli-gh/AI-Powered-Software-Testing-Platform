"""API testing: the discovered endpoints, API coverage of an execution, probes, AI API tests."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from testmind.api.deps import ApiTestingServiceDep, ExecutionServiceDep, RunServiceDep
from testmind.api_engine.models import (
    ApiGap,
    ApiSurface,
    EndpointCoverage,
    ProbeResult,
    ProbeVerdict,
    Source,
    UnmatchedRequest,
)
from testmind.schemas.api_testing import ApiCoverageOverview, ApiSuggestionRead
from testmind.schemas.common import ErrorResponse, Page

router = APIRouter(prefix="/api/v1", tags=["API testing"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found, or no API coverage (with reason)"}
}
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/runs/{run_id}/api", responses=NOT_FOUND)
async def get_endpoints(run_id: uuid.UUID, service: RunServiceDep) -> ApiSurface:
    """HTTP endpoints found by static discovery (FastAPI, Flask, Django), the applications
    serving them, and what could not be resolved statically."""
    return (await service.get_code_analysis(run_id)).api


@router.get("/test-executions/{execution_id}/api-coverage", responses=NOT_FOUND)
async def get_api_coverage(
    execution_id: uuid.UUID, service: ExecutionServiceDep
) -> ApiCoverageOverview:
    """Endpoints the project's tests call, the first gaps and the probe verdicts."""
    return ApiCoverageOverview.from_report(await service.api_coverage(execution_id))


@router.get("/test-executions/{execution_id}/api-coverage/endpoints", responses=NOT_FOUND)
async def list_api_endpoints(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    tested: bool | None = None,
    method: Annotated[str | None, Query(max_length=10)] = None,
    source: Source | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[EndpointCoverage]:
    endpoints = [
        e
        for e in (await service.api_coverage(execution_id)).endpoints
        if (tested is None or e.tested is tested)
        and (method is None or e.method == method.upper())
        and (source is None or e.source is source)
    ]
    return Page[EndpointCoverage].slice(endpoints, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/api-coverage/gaps", responses=NOT_FOUND)
async def list_api_gaps(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    kind: Annotated[
        str | None, Query(pattern="^(server_error|untested|error_only|success_only)$")
    ] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[ApiGap]:
    """Server errors first, then untested endpoints (by handler complexity), then endpoints
    tested only with errors or only with success."""
    gaps = [g for g in (await service.api_coverage(execution_id)).gaps if kind in {None, g.kind}]
    return Page[ApiGap].slice(gaps, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/api-coverage/probes", responses=NOT_FOUND)
async def list_probes(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    verdict: ProbeVerdict | None = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[ProbeResult]:
    """Deterministic probes (executions created with `api_probes`); `verdict=server_error`
    lists the requests that crashed the application."""
    probes = [
        p for p in (await service.api_coverage(execution_id)).probes if verdict in {None, p.verdict}
    ]
    return Page[ProbeResult].slice(probes, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/api-coverage/unmatched", responses=NOT_FOUND)
async def list_unmatched(
    execution_id: uuid.UUID, service: ExecutionServiceDep, limit: Limit = 100, offset: Offset = 0
) -> Page[UnmatchedRequest]:
    """Requests of the project's tests that no route answered (typos, removed endpoints)."""
    unmatched = (await service.api_coverage(execution_id)).unmatched
    return Page[UnmatchedRequest].slice(unmatched, limit=limit, offset=offset)


@router.post(
    "/test-executions/{execution_id}/api-test-suggestions",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "No endpoint gap a test could close"},
        503: {"model": ErrorResponse, "description": "No LLM configured, or queue unavailable"},
    },
)
async def create_api_suggestions(
    execution_id: uuid.UUID, service: ApiTestingServiceDep
) -> ApiSuggestionRead:
    """Queue AI-written API tests for the endpoint gaps; each is accepted only if it passes
    in the sandbox and its request to the endpoint, recorded by the application, closes the
    gap."""
    return ApiSuggestionRead.model_validate(await service.suggest(execution_id))


@router.get("/test-executions/{execution_id}/api-test-suggestions", responses=NOT_FOUND)
async def list_api_suggestions(
    execution_id: uuid.UUID, service: ApiTestingServiceDep
) -> list[ApiSuggestionRead]:
    return [
        ApiSuggestionRead.model_validate(s) for s in await service.suggestions_for(execution_id)
    ]


@router.get("/api-test-suggestions/{suggestion_id}", responses=NOT_FOUND)
async def get_api_suggestion(
    suggestion_id: uuid.UUID, service: ApiTestingServiceDep
) -> ApiSuggestionRead:
    return ApiSuggestionRead.model_validate(await service.get_suggestion(suggestion_id))

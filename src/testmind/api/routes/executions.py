"""Running a repository's test suite in the sandbox."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from testmind.api.deps import ExecutionServiceDep
from testmind.core.errors import NotFoundError
from testmind.coverage_engine import (
    CoverageDiff,
    CoverageGap,
    FileCoverage,
    FunctionCoverage,
    compare_coverage,
)
from testmind.sandbox.models import Outcome
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.test_execution import (
    CoverageOverview,
    ExecutionLogs,
    TestExecutionCreate,
    TestExecutionRead,
)
from testmind.services.execution_service import ExecutionService
from testmind.test_engine.models import TestCaseReport, TestRunReport

router = APIRouter(prefix="/api/v1", tags=["test execution"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"}
}


@router.post(
    "/runs/{run_id}/test-executions",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **NOT_FOUND,
        409: {"model": ErrorResponse, "description": "Run or generation not succeeded"},
        503: {"model": ErrorResponse, "description": "Queue unavailable"},
    },
)
async def create_execution(
    run_id: uuid.UUID,
    service: ExecutionServiceDep,
    body: Annotated[TestExecutionCreate | None, Body()] = None,
) -> TestExecutionRead:
    """Queue a sandboxed run of the project's test suite (optionally plus generated tests)."""
    body = body or TestExecutionCreate()
    execution = await service.create(run_id, body.generation_id, api_probes=body.api_probes)
    return TestExecutionRead.from_row(execution)


@router.get("/runs/{run_id}/test-executions", responses=NOT_FOUND)
async def list_executions(
    run_id: uuid.UUID,
    service: ExecutionServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TestExecutionRead]:
    items, total = await service.list_for_run(run_id, offset=offset, limit=limit)
    return Page[TestExecutionRead](
        items=[TestExecutionRead.from_row(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/test-executions/{execution_id}", responses=NOT_FOUND)
async def get_execution(execution_id: uuid.UUID, service: ExecutionServiceDep) -> TestExecutionRead:
    return TestExecutionRead.from_row(await service.get(execution_id))


@router.get("/test-executions/{execution_id}/tests", responses=NOT_FOUND)
async def list_tests(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    outcome: Outcome | None = None,
    generated: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TestCaseReport]:
    """Per-test results with message and traceback. `outcome=failed` lists the failures."""
    tests = (await _report(service, execution_id)).tests
    tests = [
        t
        for t in tests
        if (outcome is None or t.outcome is outcome)
        and (generated is None or t.generated is generated)
    ]
    return Page[TestCaseReport].slice(tests, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/logs", responses=NOT_FOUND)
async def get_logs(execution_id: uuid.UUID, service: ExecutionServiceDep) -> ExecutionLogs:
    report = await _report(service, execution_id)
    return ExecutionLogs(
        output=report.output, install_log=report.install.log if report.install else ""
    )


class CoverageSort(StrEnum):
    PATH = "path"
    PERCENT = "percent"  # least covered first


Below = Annotated[
    float | None, Query(ge=0, le=100, description="Only items covered less than this percentage")
]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/test-executions/{execution_id}/coverage", responses=NOT_FOUND)
async def get_coverage(execution_id: uuid.UUID, service: ExecutionServiceDep) -> CoverageOverview:
    """Totals (lines, branches, functions never executed) and the riskiest coverage gaps.
    404 explains why when coverage was not measured."""
    return CoverageOverview.from_report(await service.coverage(execution_id))


@router.get("/test-executions/{execution_id}/coverage/files", responses=NOT_FOUND)
async def list_coverage_files(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    below: Below = None,
    sort: CoverageSort = CoverageSort.PATH,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[FileCoverage]:
    files = (await service.coverage(execution_id)).files
    files = [f for f in files if below is None or f.percent < below]
    if sort is CoverageSort.PERCENT:
        files.sort(key=lambda f: (f.percent, f.path))
    return Page[FileCoverage].slice(files, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/coverage/functions", responses=NOT_FOUND)
async def list_coverage_functions(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    *,
    path: Annotated[str | None, Query(max_length=1024)] = None,
    below: Below = None,
    sort: CoverageSort = CoverageSort.PATH,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[FunctionCoverage]:
    functions = (await service.coverage(execution_id)).functions
    functions = [
        f
        for f in functions
        if (path is None or f.path == path) and (below is None or f.percent < below)
    ]
    if sort is CoverageSort.PERCENT:
        functions.sort(key=lambda f: (f.percent, f.path, f.line))
    return Page[FunctionCoverage].slice(functions, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/coverage/gaps", responses=NOT_FOUND)
async def list_coverage_gaps(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[CoverageGap]:
    """Functions below the threshold, riskiest first: uncovered share x (complexity + 2 x
    findings). Input for coverage-guided test generation."""
    gaps = (await service.coverage(execution_id)).gaps
    return Page[CoverageGap].slice(gaps, limit=limit, offset=offset)


@router.get("/test-executions/{execution_id}/coverage/compare", responses=NOT_FOUND)
async def compare_executions(
    execution_id: uuid.UUID,
    service: ExecutionServiceDep,
    base: Annotated[uuid.UUID, Query(description="The execution to compare against")],
) -> CoverageDiff:
    """How coverage changed from `base` to this execution (e.g. after adding generated tests)."""
    return compare_coverage(await service.coverage(base), await service.coverage(execution_id))


async def _report(service: ExecutionService, execution_id: uuid.UUID) -> TestRunReport:
    execution = await service.get(execution_id)
    if execution.report is None:
        raise NotFoundError(
            f"Test execution '{execution_id}' has no report (status: {execution.status.value})"
        )
    return TestRunReport.model_validate(execution.report)

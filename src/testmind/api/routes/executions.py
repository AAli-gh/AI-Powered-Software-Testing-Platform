"""Running a repository's test suite in the sandbox."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, status

from testmind.api.deps import ExecutionServiceDep
from testmind.core.errors import NotFoundError
from testmind.sandbox.models import Outcome
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.test_execution import ExecutionLogs, TestExecutionCreate, TestExecutionRead
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
    execution = await service.create(run_id, body.generation_id if body else None)
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


async def _report(service: ExecutionService, execution_id: uuid.UUID) -> TestRunReport:
    execution = await service.get(execution_id)
    if execution.report is None:
        raise NotFoundError(
            f"Test execution '{execution_id}' has no report (status: {execution.status.value})"
        )
    return TestRunReport.model_validate(execution.report)

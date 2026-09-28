"""Requesting test executions (API side)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.api_engine.models import ApiCoverageReport
from testmind.core.errors import ConflictError, DispatchError, NotFoundError
from testmind.core.logging import get_logger
from testmind.coverage_engine import CoverageReport
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.repositories.execution_repository import (
    ExecutionRepository,
    ExecutionSummaryRow,
)
from testmind.repositories.generation_repository import GenerationRepository
from testmind.repositories.run_repository import RunRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition
from testmind.test_engine.models import TestRunReport

logger = get_logger(__name__)


class ExecutionService:
    def __init__(self, session: AsyncSession, dispatcher: TaskDispatcher) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._executions = ExecutionRepository(session)
        self._runs = RunRepository(session)
        self._generations = GenerationRepository(session)

    async def create(
        self, run_id: uuid.UUID, generation_id: uuid.UUID | None, *, api_probes: bool = False
    ) -> TestExecution:
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        if run.status is not RunStatus.SUCCEEDED:
            raise ConflictError(
                f"Tests can only be executed for a succeeded run (status: {run.status.value})"
            )
        if generation_id is not None:
            generation = await self._generations.get(generation_id)
            if generation is None or generation.run_id != run_id:
                raise NotFoundError(f"Test generation '{generation_id}' not found for this run")
            if generation.status is not RunStatus.SUCCEEDED:
                raise ConflictError("Only a succeeded test generation can be included")

        execution = await self._executions.add(
            TestExecution(
                run_id=run_id,
                generation_id=generation_id,
                api_probes=api_probes,
                status=RunStatus.QUEUED,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_test_execution(execution.id)
        except Exception:
            logger.exception("execution_dispatch_failed", execution_id=str(execution.id))
            locked = await self._executions.get_for_update(execution.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return execution

    async def get(self, execution_id: uuid.UUID) -> TestExecution:
        execution = await self._executions.get(execution_id)
        if execution is None:
            raise NotFoundError(f"Test execution '{execution_id}' not found")
        return execution

    async def summary(self, execution_id: uuid.UUID) -> ExecutionSummaryRow:
        """The execution without its documents: the report's summary fields only."""
        row = await self._executions.summary(execution_id)
        if row is None:
            raise NotFoundError(f"Test execution '{execution_id}' not found")
        return row

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[ExecutionSummaryRow], int]:
        """Newest first, as summaries (see `summary`)."""
        if await self._runs.get(run_id) is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        return await self._executions.summaries_for_run(run_id, offset=offset, limit=limit)

    async def coverage(self, execution_id: uuid.UUID) -> CoverageReport:
        execution = await self.get(execution_id)
        if execution.coverage is None:
            raise NotFoundError(
                f"Test execution '{execution_id}' has no coverage report: "
                + _coverage_missing_reason(execution)
            )
        return CoverageReport.model_validate(execution.coverage)

    async def api_coverage(self, execution_id: uuid.UUID) -> ApiCoverageReport:
        execution = await self.get(execution_id)
        if execution.api_coverage is None:
            reason = (
                f"it has not finished (status: {execution.status.value})"
                if execution.report is None
                else "the repository has no HTTP endpoints and its tests sent no requests, "
                "or the tests did not run"
            )
            raise NotFoundError(f"Test execution '{execution_id}' has no API coverage: {reason}")
        return ApiCoverageReport.model_validate(execution.api_coverage)


def _coverage_missing_reason(execution: TestExecution) -> str:
    if execution.report is None:
        return f"it has not finished (status: {execution.status.value})"
    report = TestRunReport.model_validate(execution.report)
    return report.coverage_error or "coverage was not measured"

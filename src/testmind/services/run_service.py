from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.analyzers.code import CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.core.errors import DispatchError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import (
    AnalysisRun,
    ApiTestSuggestion,
    FailureAnalysis,
    MutantTestSuggestion,
    MutationRun,
    Regression,
    TestExecution,
    TestGeneration,
)
from testmind.domain.enums import RunStatus
from testmind.domain.run_lifecycle import ensure_transition
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.project_repository import ProjectRepository
from testmind.repositories.run_repository import RunRepository
from testmind.services.dispatcher import TaskDispatcher

logger = get_logger(__name__)

DISPATCH_FAILED_MESSAGE = "The run could not be handed to the task queue"


def apply_transition(
    run: (
        AnalysisRun
        | TestGeneration
        | TestExecution
        | FailureAnalysis
        | Regression
        | MutationRun
        | MutantTestSuggestion
        | ApiTestSuggestion
    ),
    target: RunStatus,
    *,
    error_message: str | None = None,
) -> None:
    """Validate and apply a status change, maintaining the timestamp invariants.

    Used for every job-like row (analysis runs, test generations, test executions): they share
    one lifecycle.
    """
    ensure_transition(run.status, target)
    now = datetime.now(UTC)
    run.status = target
    if target is RunStatus.RUNNING:
        run.started_at = now
    if target.is_terminal:
        run.finished_at = now
    if error_message is not None:
        run.error_message = error_message


class RunService:
    """Creating, querying and cancelling runs (used by the API)."""

    def __init__(self, session: AsyncSession, dispatcher: TaskDispatcher) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._projects = ProjectRepository(session)
        self._runs = RunRepository(session)

    async def create(self, project_id: uuid.UUID) -> AnalysisRun:
        if await self._projects.get(project_id) is None:
            raise NotFoundError(f"Project '{project_id}' not found")

        run = await self._runs.add(AnalysisRun(project_id=project_id, status=RunStatus.QUEUED))
        # Commit before dispatching: the worker must be able to see the row.
        await self._session.commit()

        try:
            await self._dispatcher.dispatch_analysis_run(run.id)
        except Exception:
            logger.exception("run_dispatch_failed", run_id=str(run.id))
            await self._mark_dispatch_failed(run.id)
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None

        logger.info("run_queued", run_id=str(run.id), project_id=str(project_id))
        return run

    async def get(self, run_id: uuid.UUID) -> AnalysisRun:
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        return run

    async def get_analysis(self, run_id: uuid.UUID) -> RepositoryProfile:
        run = await self.get(run_id)
        analysis = await AnalysisRepository(self._session).get_for_run(run_id)
        if analysis is None:
            raise NotFoundError(
                f"Run '{run_id}' has no repository analysis (status: {run.status.value})"
            )
        return RepositoryProfile.model_validate(analysis.profile)

    async def get_code_analysis(self, run_id: uuid.UUID) -> CodeProfile:
        run = await self.get(run_id)
        analysis = await AnalysisRepository(self._session).get_code_for_run(run_id)
        if analysis is None:
            raise NotFoundError(f"Run '{run_id}' has no code analysis (status: {run.status.value})")
        return CodeProfile.model_validate(analysis.profile)

    async def list_for_project(
        self, project_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[AnalysisRun], int]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError(f"Project '{project_id}' not found")
        runs = await self._runs.list_for_project(project_id, offset=offset, limit=limit)
        return runs, await self._runs.count_for_project(project_id)

    async def cancel(self, run_id: uuid.UUID) -> AnalysisRun:
        run = await self._runs.get_for_update(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        try:
            apply_transition(run, RunStatus.CANCELLED)
        except Exception:
            await self._session.rollback()  # release the row lock
            raise
        await self._session.commit()
        logger.info("run_cancelled", run_id=str(run_id))
        return run

    async def _mark_dispatch_failed(self, run_id: uuid.UUID) -> None:
        run = await self._runs.get_for_update(run_id)
        if run is not None and run.status is RunStatus.QUEUED:
            apply_transition(run, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
        await self._session.commit()

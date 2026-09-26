"""Requesting test executions (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import ConflictError, DispatchError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.repositories.run_repository import RunRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition

logger = get_logger(__name__)


class ExecutionService:
    def __init__(self, session: AsyncSession, dispatcher: TaskDispatcher) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._executions = ExecutionRepository(session)
        self._runs = RunRepository(session)
        self._generations = GenerationRepository(session)

    async def create(self, run_id: uuid.UUID, generation_id: uuid.UUID | None) -> TestExecution:
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
            TestExecution(run_id=run_id, generation_id=generation_id, status=RunStatus.QUEUED)
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

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[TestExecution], int]:
        if await self._runs.get(run_id) is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        return await self._executions.list_for_run(run_id, offset=offset, limit=limit)

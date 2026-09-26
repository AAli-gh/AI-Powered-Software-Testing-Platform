"""Requesting AI test generations (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.ai_engine.llm.factory import llm_available
from testmind.ai_engine.test_generator.models import GenerationOptions
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import TestGeneration
from testmind.domain.enums import RunStatus
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.repositories.run_repository import RunRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition

logger = get_logger(__name__)


class GenerationService:
    def __init__(
        self, session: AsyncSession, dispatcher: TaskDispatcher, settings: Settings
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._settings = settings
        self._generations = GenerationRepository(session)
        self._runs = RunRepository(session)
        self._executions = ExecutionRepository(session)

    async def create(self, run_id: uuid.UUID, options: GenerationOptions) -> TestGeneration:
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        if run.status is not RunStatus.SUCCEEDED:
            raise ConflictError(
                f"Tests can only be generated for a succeeded run (status: {run.status.value})"
            )
        if options.coverage_execution_id is not None:
            await self._check_coverage_source(run_id, options.coverage_execution_id)
        if not llm_available(self._settings):
            raise LLMUnavailableError(
                "No LLM provider is configured (set ANTHROPIC_API_KEY; see .env.example)"
            )

        generation = await self._generations.add(
            TestGeneration(
                run_id=run_id,
                status=RunStatus.QUEUED,
                options=options.model_dump(mode="json"),
                provider=self._settings.llm_provider,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_test_generation(generation.id)
        except Exception:
            logger.exception("generation_dispatch_failed", generation_id=str(generation.id))
            locked = await self._generations.get_for_update(generation.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return generation

    async def _check_coverage_source(self, run_id: uuid.UUID, execution_id: uuid.UUID) -> None:
        execution = await self._executions.get(execution_id)
        if execution is None or execution.run_id != run_id:
            raise NotFoundError(f"Test execution '{execution_id}' not found for this run")
        if execution.coverage is None:
            raise ConflictError(
                "Coverage-guided generation needs a test execution with a coverage report"
            )

    async def get(self, generation_id: uuid.UUID) -> TestGeneration:
        generation = await self._generations.get(generation_id)
        if generation is None:
            raise NotFoundError(f"Test generation '{generation_id}' not found")
        return generation

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[TestGeneration], int]:
        if await self._runs.get(run_id) is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        return await self._generations.list_for_run(run_id, offset=offset, limit=limit)

"""Requesting AI API tests for the endpoint gaps of an execution (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.ai_engine.llm.factory import llm_available
from testmind.api_engine.suggester import TARGET_KINDS
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import ApiTestSuggestion
from testmind.domain.enums import RunStatus
from testmind.repositories.api_suggestion_repository import ApiSuggestionRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.execution_service import ExecutionService
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition

logger = get_logger(__name__)


class ApiTestingService:
    def __init__(
        self, session: AsyncSession, dispatcher: TaskDispatcher, settings: Settings
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._settings = settings
        self._executions = ExecutionService(session, dispatcher)
        self._jobs = ApiSuggestionRepository(session)

    async def suggest(self, execution_id: uuid.UUID) -> ApiTestSuggestion:
        report = await self._executions.api_coverage(execution_id)
        if not any(g.kind in TARGET_KINDS for g in report.gaps):
            raise ConflictError(
                "The execution's API coverage has no endpoint gaps a test could close"
            )
        if not llm_available(self._settings):
            raise LLMUnavailableError(
                "No LLM provider is configured (set ANTHROPIC_API_KEY; see .env.example)"
            )
        job = await self._jobs.add(
            ApiTestSuggestion(
                execution_id=execution_id,
                status=RunStatus.QUEUED,
                provider=self._settings.llm_provider,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_api_suggestions(job.id)
        except Exception:
            logger.exception("api_suggestion_dispatch_failed", job_id=str(job.id))
            locked = await self._jobs.lock(job.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return job

    async def get_suggestion(self, suggestion_id: uuid.UUID) -> ApiTestSuggestion:
        job = await self._jobs.get(suggestion_id)
        if job is None:
            raise NotFoundError(f"API test suggestion '{suggestion_id}' not found")
        return job

    async def suggestions_for(self, execution_id: uuid.UUID) -> Sequence[ApiTestSuggestion]:
        await self._executions.get(execution_id)
        return await self._jobs.for_execution(execution_id)

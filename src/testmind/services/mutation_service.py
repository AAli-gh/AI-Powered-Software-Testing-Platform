"""Requesting mutation runs and AI test suggestions for their survivors (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.ai_engine.llm.factory import llm_available
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import MutantTestSuggestion, MutationRun
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.models import MutantStatus, MutationReport
from testmind.repositories.mutation_repository import MutationRepository
from testmind.repositories.run_repository import RunRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition

logger = get_logger(__name__)

JobT = TypeVar("JobT", MutationRun, MutantTestSuggestion)


class MutationOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_mutants: int | None = Field(
        default=None, ge=1, le=5000, description="At most this many (riskiest first)"
    )


class MutationService:
    def __init__(
        self, session: AsyncSession, dispatcher: TaskDispatcher, settings: Settings
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._settings = settings
        self._jobs = MutationRepository(session)
        self._runs = RunRepository(session)

    async def create(self, run_id: uuid.UUID, options: MutationOptions) -> MutationRun:
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        if run.status is not RunStatus.SUCCEEDED:
            raise ConflictError(
                f"Mutation testing needs a succeeded run (status: {run.status.value})"
            )
        limit = min(
            options.max_mutants or self._settings.mutation_max_mutants,
            self._settings.mutation_max_mutants,
        )
        job = await self._jobs.add(
            MutationRun(
                run_id=run_id,
                status=RunStatus.QUEUED,
                options=MutationOptions(max_mutants=limit).model_dump(mode="json"),
            )
        )
        return await self._dispatch(job, self._dispatcher.dispatch_mutation_run)

    async def get(self, mutation_run_id: uuid.UUID) -> MutationRun:
        job = await self._jobs.get(mutation_run_id)
        if job is None:
            raise NotFoundError(f"Mutation run '{mutation_run_id}' not found")
        return job

    async def report(self, mutation_run_id: uuid.UUID) -> MutationReport:
        job = await self.get(mutation_run_id)
        if job.report is None:
            raise NotFoundError(
                f"Mutation run '{mutation_run_id}' has no report (status: {job.status.value})"
            )
        return MutationReport.model_validate(job.report)

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[MutationRun], int]:
        if await self._runs.get(run_id) is None:
            raise NotFoundError(f"Run '{run_id}' not found")
        return await self._jobs.list_for_run(run_id, offset=offset, limit=limit)

    # ---------------------------------------------------------------------- AI suggestions
    async def suggest(self, mutation_run_id: uuid.UUID) -> MutantTestSuggestion:
        report = await self.report(mutation_run_id)
        if not any(m.status is MutantStatus.SURVIVED for m in report.mutants):
            raise ConflictError("The mutation run has no surviving mutants")
        if not llm_available(self._settings):
            raise LLMUnavailableError(
                "No LLM provider is configured (set ANTHROPIC_API_KEY; see .env.example)"
            )
        job = await self._jobs.add(
            MutantTestSuggestion(
                mutation_run_id=mutation_run_id,
                status=RunStatus.QUEUED,
                provider=self._settings.llm_provider,
            )
        )
        return await self._dispatch(job, self._dispatcher.dispatch_mutant_suggestions)

    async def get_suggestion(self, suggestion_id: uuid.UUID) -> MutantTestSuggestion:
        job = await self._jobs.get_suggestion(suggestion_id)
        if job is None:
            raise NotFoundError(f"Test suggestion '{suggestion_id}' not found")
        return job

    async def suggestions_for(self, mutation_run_id: uuid.UUID) -> Sequence[MutantTestSuggestion]:
        await self.get(mutation_run_id)
        return await self._jobs.suggestions_for(mutation_run_id)

    async def _dispatch(self, job: JobT, send: Callable[[uuid.UUID], Awaitable[None]]) -> JobT:
        await self._session.commit()
        try:
            await send(job.id)
        except Exception:
            logger.exception("mutation_dispatch_failed", job_id=str(job.id))
            locked = await self._jobs.lock(type(job), job.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return job

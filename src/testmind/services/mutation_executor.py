"""Executes queued mutation runs and mutant test suggestions (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError, LLMProvider
from testmind.analyzers.code import CodeProfile
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.db.models import MutantTestSuggestion, MutationRun
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.engine import MutationEngine
from testmind.mutation_engine.models import MutationReport
from testmind.mutation_engine.suggester import (
    MutantTestSuggester,
    SuggestionResult,
    SuggestionSandbox,
)
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.repositories.mutation_repository import MutationRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.test_engine.runner import ImageBuilder

logger = get_logger(__name__)

NO_ANALYSIS = "The run has no repository and code analysis to mutate"
NO_REPORT = "The mutation run has no report"

JobT = TypeVar("JobT", MutationRun, MutantTestSuggestion)


async def _finish(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[JobT],
    job_id: uuid.UUID,
    status: RunStatus,
    *,
    error_message: str | None,
    fields: dict[str, Any] | None = None,
) -> RunStatus:
    async with session_factory() as session:
        job = await MutationRepository(session).lock(model, job_id)
        if job is None:
            return status
        apply_transition(job, status, error_message=error_message)
        for name, value in (fields or {}).items():
            setattr(job, name, value)
        await session.commit()
    logger.info(
        "mutation_job_finished", table=model.__tablename__, job_id=str(job_id), status=status.value
    )
    return status


class MutationExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        engine_factory: Callable[[int], MutationEngine],
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._engine_factory = engine_factory  # max_mutants -> engine

    async def execute(self, mutation_run_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            job = await MutationRepository(session).lock(MutationRun, mutation_run_id)
            if job is None:
                logger.warning("mutation_run_not_found", mutation_run_id=str(mutation_run_id))
                return None
            if job.status is not RunStatus.QUEUED:
                return job.status
            location = await GenerationRepository(session).source_location(job.run_id)
            analyses = AnalysisRepository(session)
            repository = await analyses.get_for_run(job.run_id)
            code = await analyses.get_code_for_run(job.run_id)
            max_mutants = int(job.options["max_mutants"])
            apply_transition(job, RunStatus.RUNNING)
            await session.commit()

        try:
            if repository is None or code is None or location is None:
                return await self._done(mutation_run_id, RunStatus.FAILED, NO_ANALYSIS)
            root = resolve_local_source(self._repos_root, location)
            report = await asyncio.to_thread(
                self._engine_factory(max_mutants).run,
                root,
                RepositoryProfile.model_validate(repository.profile),
                CodeProfile.model_validate(code.profile),
            )
        except AppError as exc:
            return await self._done(mutation_run_id, RunStatus.FAILED, exc.message)
        except Exception:
            logger.exception("mutation_run_crashed", mutation_run_id=str(mutation_run_id))
            await self._done(mutation_run_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        fields = {"report": report.model_dump(mode="json"), "score": report.totals.score}
        return await self._done(mutation_run_id, RunStatus.SUCCEEDED, None, fields)

    async def _done(
        self,
        job_id: uuid.UUID,
        status: RunStatus,
        error: str | None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        return await _finish(
            self._session_factory, MutationRun, job_id, status, error_message=error, fields=fields
        )


class SuggestionExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        *,
        provider_factory: Callable[[], LLMProvider],
        sandbox: SuggestionSandbox,
        images: ImageBuilder,
        max_tokens: int,
        max_suggestions: int,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._provider_factory = provider_factory
        self._sandbox = sandbox
        self._images = images
        self._max_tokens = max_tokens
        self._max_suggestions = max_suggestions

    async def execute(self, suggestion_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            jobs = MutationRepository(session)
            job = await jobs.lock(MutantTestSuggestion, suggestion_id)
            if job is None:
                logger.warning("suggestion_not_found", suggestion_id=str(suggestion_id))
                return None
            if job.status is not RunStatus.QUEUED:
                return job.status
            mutation_run = await jobs.get(job.mutation_run_id)
            report = (
                MutationReport.model_validate(mutation_run.report)
                if mutation_run is not None and mutation_run.report is not None
                else None
            )
            run_id = mutation_run.run_id if mutation_run else None
            location = (
                await GenerationRepository(session).source_location(run_id) if run_id else None
            )
            analysis = await AnalysisRepository(session).get_for_run(run_id) if run_id else None
            apply_transition(job, RunStatus.RUNNING)
            await session.commit()

        try:
            if report is None or location is None or analysis is None:
                return await self._done(suggestion_id, RunStatus.FAILED, NO_REPORT)
            root = resolve_local_source(self._repos_root, location)
            repository = RepositoryProfile.model_validate(analysis.profile)
            result = await asyncio.to_thread(self._suggest, root, report, repository)
        except (AppError, LLMError) as exc:
            return await self._done(suggestion_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("suggestion_crashed", suggestion_id=str(suggestion_id))
            await self._done(suggestion_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        fields = {
            "result": result.model_dump(mode="json"),
            "model": result.model,
            "prompt_version": result.prompt_version,
        }
        return await self._done(suggestion_id, RunStatus.SUCCEEDED, None, fields)

    def _suggest(
        self, root: Path, report: MutationReport, repository: RepositoryProfile
    ) -> SuggestionResult:
        provider = self._provider_factory()  # fails fast, before an image build, without a key
        image = self._images.ensure(repository.dependencies).tag
        env = (
            {"DJANGO_SETTINGS_MODULE": repository.django_settings_module}
            if repository.django_settings_module
            else {}
        )
        suggester = MutantTestSuggester(
            provider,
            self._sandbox,
            image=image,
            max_tokens=self._max_tokens,
            max_mutants=self._max_suggestions,
            env=env,
        )
        return suggester.suggest(root, report)

    async def _done(
        self,
        job_id: uuid.UUID,
        status: RunStatus,
        error: str | None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        return await _finish(
            self._session_factory, MutantTestSuggestion, job_id, status, error_message=error,
            fields=fields,
        )  # fmt: skip

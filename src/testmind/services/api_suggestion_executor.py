"""Executes queued AI API test suggestions (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError, LLMProvider
from testmind.analyzers.code import CodeProfile
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.api_engine.models import ApiCoverageReport
from testmind.api_engine.suggester import (
    ApiSuggestionResult,
    ApiSuggestionSandbox,
    ApiTestSuggester,
)
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.api_suggestion_repository import ApiSuggestionRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.test_engine.runner import ImageBuilder

logger = get_logger(__name__)

NO_API_COVERAGE = "The execution has no API coverage report and analyses to work from"


class ApiSuggestionExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        *,
        provider_factory: Callable[[], LLMProvider],
        sandbox: ApiSuggestionSandbox,
        images: ImageBuilder,
        max_tokens: int,
        max_endpoints: int,
        imports_root: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._provider_factory = provider_factory
        self._sandbox = sandbox
        self._images = images
        self._max_tokens = max_tokens
        self._max_endpoints = max_endpoints

    async def execute(self, suggestion_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            job = await ApiSuggestionRepository(session).lock(suggestion_id)
            if job is None:
                logger.warning("api_suggestion_not_found", suggestion_id=str(suggestion_id))
                return None
            if job.status is not RunStatus.QUEUED:
                return job.status
            execution = await session.get(TestExecution, job.execution_id)
            report = (
                ApiCoverageReport.model_validate(execution.api_coverage)
                if execution is not None and execution.api_coverage is not None
                else None
            )
            run_id = execution.run_id if execution is not None else None
            analyses = AnalysisRepository(session)
            location = (
                await GenerationRepository(session).source_location(run_id) if run_id else None
            )
            repository = await analyses.get_for_run(run_id) if run_id else None
            code = await analyses.get_code_for_run(run_id) if run_id else None
            apply_transition(job, RunStatus.RUNNING)
            await session.commit()

        try:
            if report is None or location is None or repository is None or code is None:
                return await self._done(suggestion_id, RunStatus.FAILED, NO_API_COVERAGE)
            root = resolve_local_source(self._repos_root, location, imports_root=self._imports_root)
            result = await asyncio.to_thread(
                self._suggest,
                root,
                report,
                RepositoryProfile.model_validate(repository.profile),
                CodeProfile.model_validate(code.profile),
            )
        except (AppError, LLMError) as exc:
            return await self._done(suggestion_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("api_suggestion_crashed", suggestion_id=str(suggestion_id))
            await self._done(suggestion_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        fields = {
            "result": result.model_dump(mode="json"),
            "model": result.model,
            "prompt_version": result.prompt_version,
        }
        return await self._done(suggestion_id, RunStatus.SUCCEEDED, None, fields)

    def _suggest(
        self,
        root: Path,
        report: ApiCoverageReport,
        repository: RepositoryProfile,
        code: CodeProfile,
    ) -> ApiSuggestionResult:
        provider = self._provider_factory()  # fails fast, before an image build, without a key
        image = self._images.ensure(repository.dependencies, python=repository.python).tag
        env = (
            {"DJANGO_SETTINGS_MODULE": repository.django_settings_module}
            if repository.django_settings_module
            else {}
        )
        suggester = ApiTestSuggester(
            provider,
            self._sandbox,
            image=image,
            max_tokens=self._max_tokens,
            max_endpoints=self._max_endpoints,
            env=env,
        )
        return suggester.suggest(root, report, code.api, code)

    async def _done(
        self,
        job_id: uuid.UUID,
        status: RunStatus,
        error: str | None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            job = await ApiSuggestionRepository(session).lock(job_id)
            if job is None:
                return status
            apply_transition(job, status, error_message=error)
            for name, value in (fields or {}).items():
                setattr(job, name, value)
            await session.commit()
        logger.info("api_suggestion_finished", job_id=str(job_id), status=status.value)
        return status

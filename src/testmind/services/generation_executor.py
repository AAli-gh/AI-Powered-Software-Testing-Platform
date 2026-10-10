"""Executes a queued test generation (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError, LLMProvider
from testmind.ai_engine.test_generator.generator import TestGenerator, TestSandbox
from testmind.ai_engine.test_generator.models import GenerationOptions, GenerationResult
from testmind.analyzers.code import CodeProfile
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.coverage_engine import CoverageReport
from testmind.domain.enums import RunStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.sandbox.runner_image import DependencyInstallError
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.test_engine.runner import ImageBuilder

logger = get_logger(__name__)

NO_CODE_ANALYSIS = "The run has no code analysis to generate tests from"
NO_COVERAGE = "The test execution chosen for coverage-guided generation has no coverage report"


class GenerationExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        *,
        provider_factory: Callable[[], LLMProvider],
        sandbox: TestSandbox | None,
        max_iterations: int,
        max_tokens: int,
        max_targets: int,
        images: ImageBuilder | None = None,
        imports_root: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._provider_factory = provider_factory
        self._sandbox = sandbox
        self._images = images
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._max_targets = max_targets

    async def execute(self, generation_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            repo = GenerationRepository(session)
            generation = await repo.get_for_update(generation_id)
            if generation is None:
                logger.warning("generation_not_found", generation_id=str(generation_id))
                return None
            if generation.status is not RunStatus.QUEUED:
                return generation.status
            run_id = generation.run_id
            options = GenerationOptions.model_validate(generation.options)
            location = await repo.source_location(run_id)
            analyses = AnalysisRepository(session)
            code = await analyses.get_code_for_run(run_id)
            repository = await analyses.get_for_run(run_id)
            profile = CodeProfile.model_validate(code.profile) if code else None
            environment = (
                RepositoryProfile.model_validate(repository.profile) if repository else None
            )
            coverage = await _coverage(ExecutionRepository(session), options)
            apply_transition(generation, RunStatus.RUNNING)
            await session.commit()

        try:
            if profile is None or location is None:
                return await self._finish(generation_id, RunStatus.FAILED, NO_CODE_ANALYSIS)
            if options.coverage_execution_id is not None and coverage is None:
                return await self._finish(generation_id, RunStatus.FAILED, NO_COVERAGE)
            root = resolve_local_source(self._repos_root, location, imports_root=self._imports_root)
            provider = self._provider_factory()
            result = await asyncio.to_thread(
                self._generate, root, profile, options, provider, environment, coverage=coverage
            )
        except (AppError, LLMError) as exc:
            return await self._finish(generation_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("generation_crashed", generation_id=str(generation_id))
            await self._finish(generation_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._finish(generation_id, RunStatus.SUCCEEDED, None, result)

    def _generate(
        self,
        root: Path,
        profile: CodeProfile,
        options: GenerationOptions,
        provider: LLMProvider,
        environment: RepositoryProfile | None,
        *,
        coverage: CoverageReport | None,
    ) -> GenerationResult:
        generator = TestGenerator(
            provider,
            self._sandbox,
            max_iterations=self._max_iterations,
            max_tokens=self._max_tokens,
            image=self._runner_image(environment),
        )
        return generator.generate(
            root, profile, options, max_targets=self._max_targets, coverage=coverage
        )

    def _runner_image(self, environment: RepositoryProfile | None) -> str | None:
        """The project's dependencies are installed so generated tests can import its code.
        If that fails, the base image is used and missing imports surface as
        `environment_error` for the affected files."""
        if self._images is None or self._sandbox is None or not self._sandbox.available():
            return None
        try:
            if environment is None:
                return self._images.ensure([]).tag
            return self._images.ensure(environment.dependencies, python=environment.python).tag
        except DependencyInstallError:
            logger.warning("runner_image_unavailable_for_generation")
            return None

    async def _finish(
        self,
        generation_id: uuid.UUID,
        status: RunStatus,
        error_message: str | None,
        result: GenerationResult | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            generation = await GenerationRepository(session).get_for_update(generation_id)
            if generation is None:
                return status
            apply_transition(generation, status, error_message=error_message)
            if result is not None:
                generation.result = result.model_dump(mode="json")
                generation.model = result.model
                generation.prompt_version = result.prompt_version
            await session.commit()
        logger.info("generation_finished", generation_id=str(generation_id), status=status.value)
        return status


async def _coverage(
    executions: ExecutionRepository, options: GenerationOptions
) -> CoverageReport | None:
    if options.coverage_execution_id is None:
        return None
    execution = await executions.get(options.coverage_execution_id)
    if execution is None or execution.coverage is None:  # deleted since the request
        return None
    return CoverageReport.model_validate(execution.coverage)

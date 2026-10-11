"""Executes a queued test execution (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationResult
from testmind.analyzers.code.models import CodeProfile
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.test_engine.runner import SuiteResult, TestSuiteRunner

logger = get_logger(__name__)

NO_REPOSITORY_ANALYSIS = "The run has no repository analysis to execute tests from"


class ExecutionExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        runner: TestSuiteRunner,
        *,
        imports_root: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._runner = runner

    async def execute(self, execution_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            execution = await ExecutionRepository(session).get_for_update(execution_id)
            if execution is None:
                logger.warning("execution_not_found", execution_id=str(execution_id))
                return None
            if execution.status is not RunStatus.QUEUED:
                return execution.status
            generations = GenerationRepository(session)
            location = await generations.source_location(execution.run_id)
            analyses = AnalysisRepository(session)
            analysis = await analyses.get_for_run(execution.run_id)
            profile = RepositoryProfile.model_validate(analysis.profile) if analysis else None
            code_analysis = await analyses.get_code_for_run(execution.run_id)
            code = CodeProfile.model_validate(code_analysis.profile) if code_analysis else None
            generated = await generated_files(generations, execution.generation_id)
            api_probes = execution.api_probes
            apply_transition(execution, RunStatus.RUNNING)
            await session.commit()

        try:
            if profile is None or location is None:
                return await self._finish(execution_id, RunStatus.FAILED, NO_REPOSITORY_ANALYSIS)
            root = resolve_local_source(self._repos_root, location, imports_root=self._imports_root)
            result = await asyncio.to_thread(
                lambda: self._runner.run(
                    root,
                    profile,
                    generated,
                    code=code,
                    api_probes=api_probes,
                    test_contexts=True,  # per-test coverage: fault localisation
                )
            )
        except AppError as exc:
            return await self._finish(execution_id, RunStatus.FAILED, exc.message)
        except Exception:
            logger.exception("execution_crashed", execution_id=str(execution_id))
            await self._finish(execution_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._finish(execution_id, RunStatus.SUCCEEDED, None, result)

    async def _finish(
        self,
        execution_id: uuid.UUID,
        status: RunStatus,
        error_message: str | None,
        result: SuiteResult | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            execution = await ExecutionRepository(session).get_for_update(execution_id)
            if execution is None:
                return status
            apply_transition(execution, status, error_message=error_message)
            if result is not None:
                execution.report = result.report.model_dump(mode="json")
                execution.outcome = result.report.outcome
                if result.failures is not None:
                    execution.failures = result.failures.model_dump(mode="json")
                if result.coverage is not None:
                    execution.coverage = result.coverage.model_dump(mode="json")
                    execution.coverage_percent = result.coverage.totals.percent
                if result.spectrum is not None:
                    execution.spectrum = result.spectrum.to_json()
                if result.api is not None:
                    execution.api_coverage = result.api.model_dump(mode="json")
                    execution.api_coverage_percent = result.api.totals.percent
            await session.commit()
        logger.info("execution_finished", execution_id=str(execution_id), status=status.value)
        return status


async def generated_files(
    generations: GenerationRepository, generation_id: uuid.UUID | None
) -> dict[str, str]:
    """Generated test modules (sandbox path -> source) of an included generation."""
    if generation_id is None:
        return {}
    generation = await generations.get(generation_id)
    if generation is None or generation.result is None:
        return {}
    result = GenerationResult.model_validate(generation.result)
    return {f.path.removeprefix("tests/"): f.code for f in result.files if f.code}

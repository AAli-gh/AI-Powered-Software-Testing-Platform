"""Executes a queued AI failure analysis (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError, LLMProvider
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.failure_engine import FailureReport
from testmind.failure_engine.explainer import FailureExplainer
from testmind.failure_engine.models import FailureAnalysisResult
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.failure_analysis_repository import FailureAnalysisRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.services.execution_executor import generated_files
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source

logger = get_logger(__name__)

NO_FAILURE_REPORT = "The test execution has no failure report to analyse"


class FailureAnalysisExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        *,
        provider_factory: Callable[[], LLMProvider],
        max_tokens: int,
        max_groups: int,
        imports_root: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._provider_factory = provider_factory
        self._max_tokens = max_tokens
        self._max_groups = max_groups

    async def execute(self, analysis_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            analysis = await FailureAnalysisRepository(session).get_for_update(analysis_id)
            if analysis is None:
                logger.warning("failure_analysis_not_found", analysis_id=str(analysis_id))
                return None
            if analysis.status is not RunStatus.QUEUED:
                return analysis.status
            execution = await ExecutionRepository(session).get(analysis.execution_id)
            report = (
                FailureReport.model_validate(execution.failures)
                if execution is not None and execution.failures is not None
                else None
            )
            generations = GenerationRepository(session)
            location = await generations.source_location(execution.run_id) if execution else None
            generated = (
                await generated_files(generations, execution.generation_id) if execution else {}
            )
            apply_transition(analysis, RunStatus.RUNNING)
            await session.commit()

        try:
            if report is None or location is None:
                return await self._finish(analysis_id, RunStatus.FAILED, NO_FAILURE_REPORT)
            root = resolve_local_source(self._repos_root, location, imports_root=self._imports_root)
            explainer = FailureExplainer(
                self._provider_factory(), max_tokens=self._max_tokens, max_groups=self._max_groups
            )
            # Generated modules are reported as tests/<path>.
            sources = {f"tests/{path}": code for path, code in generated.items()}
            result = await asyncio.to_thread(explainer.explain, root, report, sources)
        except (AppError, LLMError) as exc:
            return await self._finish(analysis_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("failure_analysis_crashed", analysis_id=str(analysis_id))
            await self._finish(analysis_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._finish(analysis_id, RunStatus.SUCCEEDED, None, result)

    async def _finish(
        self,
        analysis_id: uuid.UUID,
        status: RunStatus,
        error_message: str | None,
        result: FailureAnalysisResult | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            analysis = await FailureAnalysisRepository(session).get_for_update(analysis_id)
            if analysis is None:
                return status
            apply_transition(analysis, status, error_message=error_message)
            if result is not None:
                analysis.result = result.model_dump(mode="json")
                analysis.model = result.model
                analysis.prompt_version = result.prompt_version
            await session.commit()
        logger.info("failure_analysis_finished", analysis_id=str(analysis_id), status=status.value)
        return status

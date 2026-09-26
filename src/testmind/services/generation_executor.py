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
from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.repositories.generation_repository import GenerationRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source

logger = get_logger(__name__)

NO_CODE_ANALYSIS = "The run has no code analysis to generate tests from"


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
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._provider_factory = provider_factory
        self._sandbox = sandbox
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
            code = await AnalysisRepository(session).get_code_for_run(run_id)
            profile = CodeProfile.model_validate(code.profile) if code else None
            apply_transition(generation, RunStatus.RUNNING)
            await session.commit()

        try:
            if profile is None or location is None:
                return await self._finish(generation_id, RunStatus.FAILED, NO_CODE_ANALYSIS)
            root = resolve_local_source(self._repos_root, location)
            generator = TestGenerator(
                self._provider_factory(),
                self._sandbox,
                max_iterations=self._max_iterations,
                max_tokens=self._max_tokens,
            )
            result = await asyncio.to_thread(
                generator.generate, root, profile, options, max_targets=self._max_targets
            )
        except (AppError, LLMError) as exc:
            return await self._finish(generation_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("generation_crashed", generation_id=str(generation_id))
            await self._finish(generation_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._finish(generation_id, RunStatus.SUCCEEDED, None, result)

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

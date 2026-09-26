from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from testmind.ai_engine.llm.factory import create_provider
from testmind.core.config import Settings
from testmind.db.session import create_engine, create_session_factory
from testmind.domain.enums import RunStatus
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.services.generation_executor import GenerationExecutor
from testmind.services.run_executor import RunExecutor

EXECUTE_ANALYSIS_RUN = "testmind.analysis_run.execute"
GENERATE_TESTS = "testmind.test_generation.execute"


def register_tasks(app: Celery, settings: Settings) -> None:
    # shared=False: Celery would otherwise attach this closure (and its `settings`) to every
    # Celery app created later in the process, where it would shadow that app's own task.
    @app.task(name=EXECUTE_ANALYSIS_RUN, shared=False)
    def execute_analysis_run(run_id: str) -> str | None:
        status = asyncio.run(_execute_analysis_run(settings, uuid.UUID(run_id)))
        return status.value if status else None

    @app.task(name=GENERATE_TESTS, shared=False)
    def generate_tests(generation_id: str) -> str | None:
        status = asyncio.run(_generate_tests(settings, uuid.UUID(generation_id)))
        return status.value if status else None


async def _execute_analysis_run(settings: Settings, run_id: uuid.UUID) -> RunStatus | None:
    # A fresh, unpooled engine per task: asyncio.run() creates a new event loop each time and
    # asyncpg connections are bound to the loop that opened them.
    engine = create_engine(settings, pooled=False)
    try:
        executor = RunExecutor(create_session_factory(engine), settings.repos_root)
        return await executor.execute(run_id)
    finally:
        await engine.dispose()


def build_sandbox(settings: Settings) -> DockerSandbox:
    return DockerSandbox(
        settings.sandbox_image,
        timeout_seconds=settings.sandbox_timeout_seconds,
        memory=settings.sandbox_memory,
        cpus=settings.sandbox_cpus,
    )


async def _generate_tests(settings: Settings, generation_id: uuid.UUID) -> RunStatus | None:
    engine = create_engine(settings, pooled=False)
    try:
        executor = GenerationExecutor(
            create_session_factory(engine),
            settings.repos_root,
            provider_factory=lambda: create_provider(settings),
            sandbox=build_sandbox(settings),
            max_iterations=settings.generation_max_iterations,
            max_tokens=settings.llm_max_tokens,
            max_targets=settings.generation_max_targets,
        )
        return await executor.execute(generation_id)
    finally:
        await engine.dispose()

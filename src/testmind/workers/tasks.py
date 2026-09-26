from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from testmind.ai_engine.llm.factory import create_provider
from testmind.core.config import Settings
from testmind.db.session import create_engine, create_session_factory
from testmind.domain.enums import RunStatus
from testmind.regression.engine import RegressionEngine
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.failure_analysis_executor import FailureAnalysisExecutor
from testmind.services.generation_executor import GenerationExecutor
from testmind.services.regression_executor import RegressionExecutor
from testmind.services.run_executor import RunExecutor
from testmind.test_engine.runner import TestSuiteRunner

EXECUTE_ANALYSIS_RUN = "testmind.analysis_run.execute"
GENERATE_TESTS = "testmind.test_generation.execute"
EXECUTE_TESTS = "testmind.test_execution.execute"
ANALYZE_FAILURES = "testmind.failure_analysis.execute"
ANALYZE_REGRESSION = "testmind.regression.execute"


def register_tasks(app: Celery, settings: Settings) -> None:
    # shared=False: Celery would otherwise attach this closure (and its `settings`) to every
    # Celery app created later in the process, where it would shadow that app's own task.
    @app.task(name=EXECUTE_ANALYSIS_RUN, shared=False)
    def execute_analysis_run(run_id: str) -> str | None:
        status = asyncio.run(_execute_analysis_run(settings, uuid.UUID(run_id)))
        return status.value if status else None

    @app.task(name=EXECUTE_TESTS, shared=False)
    def execute_tests(execution_id: str) -> str | None:
        status = asyncio.run(_execute_tests(settings, uuid.UUID(execution_id)))
        return status.value if status else None

    @app.task(name=ANALYZE_REGRESSION, shared=False)
    def analyze_regression(regression_id: str) -> str | None:
        status = asyncio.run(_analyze_regression(settings, uuid.UUID(regression_id)))
        return status.value if status else None

    @app.task(name=ANALYZE_FAILURES, shared=False)
    def analyze_failures(analysis_id: str) -> str | None:
        status = asyncio.run(_analyze_failures(settings, uuid.UUID(analysis_id)))
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
            images=build_image_builder(settings),
        )
        return await executor.execute(generation_id)
    finally:
        await engine.dispose()


def build_image_builder(settings: Settings) -> RunnerImageBuilder:
    return RunnerImageBuilder(
        settings.sandbox_image, timeout_seconds=settings.sandbox_build_timeout_seconds
    )


async def _execute_tests(settings: Settings, execution_id: uuid.UUID) -> RunStatus | None:
    engine = create_engine(settings, pooled=False)
    try:
        executor = ExecutionExecutor(
            create_session_factory(engine), settings.repos_root, build_runner(settings)
        )
        return await executor.execute(execution_id)
    finally:
        await engine.dispose()


async def _analyze_failures(settings: Settings, analysis_id: uuid.UUID) -> RunStatus | None:
    engine = create_engine(settings, pooled=False)
    try:
        executor = FailureAnalysisExecutor(
            create_session_factory(engine),
            settings.repos_root,
            provider_factory=lambda: create_provider(settings),
            max_tokens=settings.llm_max_tokens,
            max_groups=settings.failure_analysis_max_groups,
        )
        return await executor.execute(analysis_id)
    finally:
        await engine.dispose()


def build_runner(settings: Settings) -> TestSuiteRunner:
    return TestSuiteRunner(
        build_sandbox(settings),
        build_image_builder(settings),
        coverage_threshold=settings.coverage_threshold,
    )


async def _analyze_regression(settings: Settings, regression_id: uuid.UUID) -> RunStatus | None:
    engine = create_engine(settings, pooled=False)
    try:
        executor = RegressionExecutor(
            create_session_factory(engine),
            settings.repos_root,
            RegressionEngine(build_runner(settings)),
        )
        return await executor.execute(regression_id)
    finally:
        await engine.dispose()

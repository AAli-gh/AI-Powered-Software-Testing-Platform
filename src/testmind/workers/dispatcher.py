from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from testmind.workers.tasks import (
    ANALYZE_FAILURES,
    ANALYZE_PULL_REQUEST,
    ANALYZE_REGRESSION,
    EXECUTE_ANALYSIS_RUN,
    EXECUTE_TESTS,
    GENERATE_TESTS,
    IMPORT_GITHUB_REPOSITORY,
    RUN_MUTATIONS,
    SCAN_WEBSITE,
    SUGGEST_API_TESTS,
    SUGGEST_MUTANT_TESTS,
)


class CeleryTaskDispatcher:
    """`TaskDispatcher` backed by Celery. Publishing is blocking I/O, so it runs in a thread."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, EXECUTE_ANALYSIS_RUN, args=[str(run_id)])

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, GENERATE_TESTS, args=[str(generation_id)])

    async def dispatch_test_execution(self, execution_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, EXECUTE_TESTS, args=[str(execution_id)])

    async def dispatch_failure_analysis(self, analysis_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, ANALYZE_FAILURES, args=[str(analysis_id)])

    async def dispatch_regression(self, regression_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, ANALYZE_REGRESSION, args=[str(regression_id)])

    async def dispatch_mutation_run(self, mutation_run_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, RUN_MUTATIONS, args=[str(mutation_run_id)])

    async def dispatch_mutant_suggestions(self, suggestion_id: uuid.UUID) -> None:
        await asyncio.to_thread(
            self._app.send_task, SUGGEST_MUTANT_TESTS, args=[str(suggestion_id)]
        )

    async def dispatch_api_suggestions(self, suggestion_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, SUGGEST_API_TESTS, args=[str(suggestion_id)])

    async def dispatch_pull_analysis(self, analysis_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, ANALYZE_PULL_REQUEST, args=[str(analysis_id)])

    async def dispatch_github_import(self, import_id: uuid.UUID) -> None:
        await asyncio.to_thread(
            self._app.send_task, IMPORT_GITHUB_REPOSITORY, args=[str(import_id)]
        )

    async def dispatch_web_scan(self, scan_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, SCAN_WEBSITE, args=[str(scan_id)])

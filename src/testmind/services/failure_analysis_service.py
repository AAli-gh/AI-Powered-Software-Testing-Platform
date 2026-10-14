"""Failure analysis (API side): the deterministic report and requested AI analyses."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.ai_engine.llm.factory import ai_unavailable_reason
from testmind.core.config import AIFeature, Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import FailureAnalysis, TestExecution
from testmind.domain.enums import RunStatus
from testmind.failure_engine import FailureReport
from testmind.fault_localization import Localization, Spectrum, localize
from testmind.fault_localization.config import select_scorer
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.repositories.failure_analysis_repository import FailureAnalysisRepository
from testmind.sandbox.models import Outcome
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition
from testmind.test_engine.models import TestRunReport

logger = get_logger(__name__)


class FailureAnalysisService:
    def __init__(
        self, session: AsyncSession, dispatcher: TaskDispatcher, settings: Settings
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._settings = settings
        self._executions = ExecutionRepository(session)
        self._analyses = FailureAnalysisRepository(session)

    async def report(self, execution_id: uuid.UUID) -> FailureReport:
        """The deterministic analysis stored with the execution."""
        execution = await self._execution(execution_id)
        if execution.failures is None:
            raise NotFoundError(
                f"Test execution '{execution_id}' has no failure report "
                f"(status: {execution.status.value}, outcome: "
                f"{execution.outcome.value if execution.outcome else 'none'})"
            )
        return FailureReport.model_validate(execution.failures)

    async def suspects(
        self, execution_id: uuid.UUID, tests: Sequence[str], *, limit: int
    ) -> Localization:
        """Fault localisation for any failing tests of the execution (all of them by default),
        from its stored spectrum. No traceback is used: the tests need not share a failure."""
        execution = await self._execution(execution_id)
        if execution.spectrum is None or execution.report is None:
            raise NotFoundError(
                f"Test execution '{execution_id}' has no per-test coverage to localise faults "
                "with (it did not run, or it ran before fault localisation existed)"
            )
        report = TestRunReport.model_validate(execution.report)
        failed = {t.nodeid for t in report.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}}
        passing = {t.nodeid for t in report.tests if t.outcome is Outcome.PASSED}
        chosen = [t for t in tests if t in failed] if tests else sorted(failed)
        scorer = select_scorer(
            self._settings.fault_localization_ranker, self._settings.fault_localization_model
        )
        result = localize(
            Spectrum.from_json(execution.spectrum), chosen, passing, limit=limit, scorer=scorer
        )
        ignored = sorted(set(tests) - failed)
        if ignored:
            note = f"Not failing tests of this execution, ignored: {', '.join(ignored[:10])}"
            result = result.model_copy(update={"notes": [note, *result.notes]})
        return result

    async def create(self, execution_id: uuid.UUID) -> FailureAnalysis:
        report = await self.report(execution_id)
        if not report.groups:
            raise ConflictError("The execution has no failure groups to analyse")
        reason = ai_unavailable_reason(self._settings, AIFeature.FAILURE_EXPLANATION)
        if reason is not None:
            raise LLMUnavailableError(
                f"{reason}. The deterministic analysis is available at /failures"
            )
        analysis = await self._analyses.add(
            FailureAnalysis(
                execution_id=execution_id,
                status=RunStatus.QUEUED,
                provider=self._settings.llm_provider,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_failure_analysis(analysis.id)
        except Exception:
            logger.exception("failure_analysis_dispatch_failed", analysis_id=str(analysis.id))
            locked = await self._analyses.get_for_update(analysis.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return analysis

    async def get(self, analysis_id: uuid.UUID) -> FailureAnalysis:
        analysis = await self._analyses.get(analysis_id)
        if analysis is None:
            raise NotFoundError(f"Failure analysis '{analysis_id}' not found")
        return analysis

    async def list_for_execution(
        self, execution_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[FailureAnalysis], int]:
        await self._execution(execution_id)
        return await self._analyses.list_for_execution(execution_id, offset=offset, limit=limit)

    async def _execution(self, execution_id: uuid.UUID) -> TestExecution:
        execution = await self._executions.get(execution_id)
        if execution is None:
            raise NotFoundError(f"Test execution '{execution_id}' not found")
        return execution

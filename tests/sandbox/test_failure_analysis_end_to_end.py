"""Failure analysis with the real database and the real Docker sandbox; only the LLM is
scripted. examples/failing_suite plus one generated test are executed, the deterministic
report is stored, and an AI analysis is requested and checked."""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions
from testmind.core.config import Settings
from testmind.db.models import FailureAnalysis, TestGeneration
from testmind.domain.enums import RunStatus
from testmind.failure_engine import Blame, FailureCategory
from testmind.failure_engine.models import AnalysisStatus, FailureAnalysisResult
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.schemas.project import ProjectCreate
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.failure_analysis_executor import FailureAnalysisExecutor
from testmind.services.failure_analysis_service import FailureAnalysisService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import RecordingDispatcher, ScriptedLLM
from tests.integration.test_execution_service import _file
from tests.integration.test_generation_service import llm_configured  # noqa: F401  (fixture)
from tests.sandbox.test_suite_runner import EXAMPLES
from tests.unit.failure_engine.test_explainer import GENERATED_SOURCE

pytestmark = pytest.mark.integration

GENERATED_PATH = "tests/testmind/test_gen.py"


def reply(evidence: list[dict[str, object]]) -> str:
    return (
        "```json\n"
        + json.dumps(
            {
                "blame": "code",
                "root_cause": "average() divides by count and every caller passes count = 0.",
                "evidence": evidence,
                "suggested_fix": "Reject or handle count == 0 in average().",
                "confidence": 0.85,
            }
        )
        + "\n```"
    )


@pytest.mark.usefixtures("llm_configured")
async def test_failures_are_grouped_stored_and_explained(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    sandbox: DockerSandbox,
    settings: Settings,
) -> None:
    shutil.copytree(EXAMPLES / "failing_suite", repos_root / "failing")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="failing", source_location="failing")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    async with session_factory() as session:
        generation = TestGeneration(
            run_id=run.id, status=RunStatus.SUCCEEDED, provider="anthropic",
            options=GenerationOptions().model_dump(mode="json"),
            result={
                "files": [_file(GENERATED_PATH, "shop.stock", "passed", GENERATED_SOURCE)],
                "model": "m", "prompt_version": "v1", "input_tokens": 1, "output_tokens": 1,
                "targets_selected": 1, "targets_available": 1,
            },
        )  # fmt: skip
        session.add(generation)
        await session.commit()
        execution = await ExecutionService(session, RecordingDispatcher()).create(
            run.id, generation.id
        )
    runner = TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image))
    assert (
        await ExecutionExecutor(session_factory, repos_root, runner).execute(execution.id)
        is RunStatus.SUCCEEDED
    )

    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = FailureAnalysisService(session, dispatcher, settings)
        report = await service.report(execution.id)
        analysis = await service.create(execution.id)
    assert (report.failed_tests, report.analysed_tests, len(report.groups)) == (14, 14, 10)
    zero = report.groups[0]
    assert zero.category is FailureCategory.EXCEPTION_IN_CODE
    assert len(zero.tests) == 4

    # Group 1 cites real lines (the crash line and a generated test line), then an invented
    # one; the next group gets an answer without evidence; the rest are over the limit.
    llm = ScriptedLLM(
        [
            reply(
                [
                    {"path": "shop/stock.py", "line": 5, "explanation": "total / count"},
                    {"path": "tests/test_stock.py", "line": 11, "explanation": "count = 0"},
                    {"path": "shop/stock.py", "line": 99, "explanation": "invented"},
                ]
            ),
            reply([]),
        ]
    )
    executor = FailureAnalysisExecutor(
        session_factory, repos_root, provider_factory=lambda: llm, max_tokens=1000, max_groups=2
    )
    assert await executor.execute(analysis.id) is RunStatus.SUCCEEDED

    async with session_factory() as session:
        row = await session.get(FailureAnalysis, analysis.id)
    assert row is not None
    result = FailureAnalysisResult.model_validate(row.result)
    by_group = {h.group_id: h for h in result.hypotheses}
    first = by_group[zero.id]
    assert (first.status, first.blame) == (AnalysisStatus.ANALYSED, Blame.CODE)
    assert [e.verified for e in first.evidence] == [True, True, False]
    assert first.confidence == round(0.85 * 2 / 3, 2)
    statuses = sorted(h.status.value for h in result.hypotheses)
    assert statuses == ["analysed"] * 2 + ["deterministic"] * 3 + ["skipped"] * 5
    assert "    5 |     return total / count" in llm.requests[0].prompt
    assert isinstance(uuid.UUID(str(row.execution_id)), uuid.UUID)

"""The coverage loop with the real database and the real Docker sandbox; only the LLM is scripted.

baseline execution (coverage) → coverage-guided generation → execution with the generated
tests → comparison. The comparison must show that coverage really went up.
"""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions, GenerationResult
from testmind.core.config import Settings
from testmind.coverage_engine import compare_coverage
from testmind.db.models import TestGeneration
from testmind.domain.enums import RunStatus
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.schemas.project import ProjectCreate
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.generation_executor import GenerationExecutor
from testmind.services.generation_service import GenerationService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import RecordingDispatcher, ScriptedLLM
from tests.integration.test_generation_service import llm_configured  # noqa: F401  (fixture)
from tests.sandbox.test_suite_runner import EXAMPLES

pytestmark = pytest.mark.integration

REPLY = """```python
import pytest

from inventory.pricing import price_for


@pytest.mark.parametrize(
    ("quantity", "expected"), [(200, 160.0), (60, 51.0), (10, 9.0)]
)
def test_wholesale_tiers(quantity, expected):
    assert price_for("wholesale", quantity, 1.0) == pytest.approx(expected)


def test_long_time_retail_member():
    assert price_for("retail", 20, 1.0, member_years=6) == pytest.approx(19.0)


def test_coupon_and_holiday():
    assert price_for("retail", 1, 100.0, coupon="SAVE10") == pytest.approx(90.0)
    assert price_for("retail", 1, 100.0, season="holiday") == pytest.approx(110.0)
```"""


@pytest.mark.usefixtures("llm_configured")
async def test_generated_tests_raise_measured_coverage(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    sandbox: DockerSandbox,
    settings: Settings,
) -> None:
    shutil.copytree(EXAMPLES / "buggy_inventory", repos_root / "buggy")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="buggy", source_location="buggy")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    images = RunnerImageBuilder(sandbox.image, timeout_seconds=600)
    executions = ExecutionExecutor(session_factory, repos_root, TestSuiteRunner(sandbox, images))

    async def execute(generation_id: uuid.UUID | None) -> uuid.UUID:
        async with session_factory() as session:
            created = await ExecutionService(session, RecordingDispatcher()).create(
                run.id, generation_id
            )
        assert await executions.execute(created.id) is RunStatus.SUCCEEDED
        return created.id

    baseline = await execute(None)

    async with session_factory() as session:
        generation = await GenerationService(session, RecordingDispatcher(), settings).create(
            run.id, GenerationOptions(coverage_execution_id=baseline, max_targets=1)
        )
    llm = ScriptedLLM([REPLY])
    generator = GenerationExecutor(
        session_factory, repos_root, provider_factory=lambda: llm, sandbox=sandbox,
        max_iterations=1, max_tokens=1000, max_targets=5, images=images,
    )  # fmt: skip
    assert await generator.execute(generation.id) is RunStatus.SUCCEEDED
    async with session_factory() as session:
        stored = await session.get(TestGeneration, generation.id)
        assert stored is not None
    [file] = GenerationResult.model_validate(stored.result).files
    # The riskiest gap: complexity 15, a finding, and never executed by the project's tests.
    assert file.targets == ["price_for"]
    assert file.status.value == "passed", file.attempts
    assert "- price_for: 0.0% covered; lines never executed: 2-24" in llm.requests[0].prompt

    with_generated = await execute(generation.id)

    async with session_factory() as session:
        service = ExecutionService(session, RecordingDispatcher())
        diff = compare_coverage(
            await service.coverage(baseline), await service.coverage(with_generated)
        )
    # Deterministic (coverage.py is pinned in the sandbox image).
    assert (diff.base_percent, diff.head_percent) == (8.47, 38.98)
    assert (diff.newly_covered_lines, diff.newly_missed_lines) == (20, 0)
    assert [(f.path, f.head_percent) for f in diff.files] == [("inventory/pricing.py", 70.59)]
    # price_for: lines 14, 18, 19 and 23 still never run -> (18 + 16 branches) / (22 + 22).
    assert [(f.qualname, f.base_percent, f.head_percent) for f in diff.functions] == [
        ("price_for", 0.0, 77.27)
    ]

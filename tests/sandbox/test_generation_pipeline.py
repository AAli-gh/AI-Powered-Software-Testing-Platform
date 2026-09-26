"""Whole pipeline with the real database and the real Docker sandbox: analysis run →
generation → validation loop → stored result. Only the LLM is scripted."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions, GenerationResult
from testmind.db.models import TestGeneration
from testmind.domain.enums import RunStatus
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.services.generation_executor import GenerationExecutor
from tests.doubles import ScriptedLLM
from tests.integration.test_generation_service import add_generation, analysed_run

pytestmark = pytest.mark.integration

REPLY = """```python
from main import greet

def test_greet():
    assert greet() == "hi"
```"""


async def test_generated_tests_are_run_in_the_sandbox(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, sandbox: DockerSandbox
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    generation_id = await add_generation(session_factory, run_id)
    llm = ScriptedLLM([REPLY])

    executor = GenerationExecutor(
        session_factory, repos_root, provider_factory=lambda: llm, sandbox=sandbox,
        max_iterations=2, max_tokens=1000, max_targets=5,
    )  # fmt: skip
    assert await executor.execute(generation_id) is RunStatus.SUCCEEDED

    async with session_factory() as session:
        stored = await session.get(TestGeneration, generation_id)
        assert stored is not None
        assert stored.result is not None
    result = GenerationResult.model_validate(stored.result)
    [file] = result.files
    assert file.status.value == "passed"
    assert file.run is not None
    assert file.run.passed == 1
    assert GenerationOptions.model_validate(stored.options).kinds
    assert isinstance(uuid.UUID(str(stored.run_id)), uuid.UUID)

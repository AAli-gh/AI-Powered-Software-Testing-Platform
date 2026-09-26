import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMNotConfiguredError
from testmind.ai_engine.test_generator.models import FileStatus, GenerationOptions, TestKind
from testmind.analyzers.repository.models import Dependency
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.db.models import AnalysisRun, TestGeneration
from testmind.domain.enums import RunStatus
from testmind.sandbox.models import Outcome
from testmind.sandbox.runner_image import DependencyInstallError, RunnerImage
from testmind.schemas.project import ProjectCreate
from testmind.services.generation_executor import NO_CODE_ANALYSIS, GenerationExecutor
from testmind.services.generation_service import GenerationService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, RunService
from tests.doubles import FailingDispatcher, RecordingDispatcher, ScriptedLLM
from tests.unit.ai_engine.test_generator_loop import CannedSandbox
from tests.unit.ai_engine.test_generator_loop import result as sandbox_result

REPLY = "```python\nfrom main import greet\n\ndef test_greet():\n    assert greet() == 'hi'\n```"


@pytest.fixture
def llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")


async def analysed_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    (repos_root / "sample" / "main.py").write_text("def greet():\n    return 'hi'\n")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    return run.id


def executor(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, llm: ScriptedLLM
) -> GenerationExecutor:
    return GenerationExecutor(
        session_factory,
        repos_root,
        provider_factory=lambda: llm,
        sandbox=None,
        max_iterations=2,
        max_tokens=1000,
        max_targets=5,
    )


@pytest.mark.usefixtures("llm_configured")
async def test_create_and_execute(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        generation = await GenerationService(session, dispatcher, settings).create(
            run_id, GenerationOptions(kinds=[TestKind.UNIT])
        )
    assert dispatcher.generations == [generation.id]
    assert generation.status is RunStatus.QUEUED
    assert generation.provider == "anthropic"

    llm = ScriptedLLM([REPLY])
    status = await executor(session_factory, repos_root, llm).execute(generation.id)

    assert status is RunStatus.SUCCEEDED
    async with session_factory() as session:
        stored = await session.get(TestGeneration, generation.id)
        assert stored is not None
        assert stored.model == "scripted-model"
        assert stored.prompt_version == "v1"
        assert stored.started_at is not None
        assert stored.finished_at is not None
        assert stored.result is not None
        [file] = stored.result["files"]
        assert file["status"] == FileStatus.UNVERIFIED.value  # no sandbox in this test
        assert file["targets"] == ["greet"]
    assert "- unit:" in llm.requests[0].prompt
    assert "- negative:" not in llm.requests[0].prompt

    # Re-delivery of the same message does nothing.
    assert await executor(session_factory, repos_root, ScriptedLLM([])).execute(generation.id) is (
        RunStatus.SUCCEEDED
    )


async def test_requires_llm(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        with pytest.raises(LLMUnavailableError):
            await GenerationService(session, RecordingDispatcher(), settings).create(
                run_id, GenerationOptions()
            )


@pytest.mark.usefixtures("llm_configured")
async def test_requires_succeeded_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
        service = GenerationService(session, RecordingDispatcher(), settings)
        with pytest.raises(ConflictError, match="status: queued"):
            await service.create(run.id, GenerationOptions())
        with pytest.raises(NotFoundError):
            await service.create(uuid.uuid4(), GenerationOptions())


@pytest.mark.usefixtures("llm_configured")
async def test_dispatch_failure(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await GenerationService(session, FailingDispatcher(), settings).create(
                run_id, GenerationOptions()
            )
        items, total = await GenerationService(
            session, RecordingDispatcher(), settings
        ).list_for_run(run_id, offset=0, limit=10)
    assert total == 1
    assert items[0].status is RunStatus.FAILED
    assert items[0].error_message == DISPATCH_FAILED_MESSAGE


async def test_get_and_list_missing(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with session_factory() as session:
        service = GenerationService(session, RecordingDispatcher(), settings)
        with pytest.raises(NotFoundError):
            await service.get(uuid.uuid4())
        with pytest.raises(NotFoundError):
            await service.list_for_run(uuid.uuid4(), offset=0, limit=10)


async def add_generation(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> uuid.UUID:
    async with session_factory() as session:
        generation = TestGeneration(
            run_id=run_id,
            status=RunStatus.QUEUED,
            options=GenerationOptions().model_dump(mode="json"),
            provider="anthropic",
        )
        session.add(generation)
        await session.commit()
        return generation.id


async def failed_message(
    session_factory: async_sessionmaker[AsyncSession], generation_id: uuid.UUID
) -> str | None:
    async with session_factory() as session:
        stored = await session.get(TestGeneration, generation_id)
        assert stored is not None
        assert stored.status is RunStatus.FAILED
        return stored.error_message


async def test_llm_not_configured_in_worker_fails_the_generation(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    generation_id = await add_generation(session_factory, run_id)

    def no_provider() -> ScriptedLLM:
        raise LLMNotConfiguredError("No LLM provider is configured")

    gen_executor = GenerationExecutor(
        session_factory, repos_root, provider_factory=no_provider, sandbox=None,
        max_iterations=1, max_tokens=10, max_targets=1,
    )  # fmt: skip
    assert await gen_executor.execute(generation_id) is RunStatus.FAILED
    assert await failed_message(session_factory, generation_id) == "No LLM provider is configured"


async def test_run_without_code_analysis(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
        run = AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED)
        session.add(run)
        await session.commit()
        run_id = run.id
    generation_id = await add_generation(session_factory, run_id)
    assert (
        await executor(session_factory, repos_root, ScriptedLLM([])).execute(generation_id)
        is RunStatus.FAILED
    )
    assert await failed_message(session_factory, generation_id) == NO_CODE_ANALYSIS


async def test_unexpected_error_fails_and_propagates(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    generation_id = await add_generation(session_factory, run_id)

    def broken() -> ScriptedLLM:
        raise RuntimeError("bug")

    gen_executor = GenerationExecutor(
        session_factory, repos_root, provider_factory=broken, sandbox=None,
        max_iterations=1, max_tokens=10, max_targets=1,
    )  # fmt: skip
    with pytest.raises(RuntimeError):
        await gen_executor.execute(generation_id)
    assert await failed_message(session_factory, generation_id) == UNEXPECTED_ERROR_MESSAGE


async def test_unknown_generation(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    assert (
        await executor(session_factory, repos_root, ScriptedLLM([])).execute(uuid.uuid4()) is None
    )


async def test_generation_vanishing_before_finish_is_tolerated(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    generation_id = await add_generation(session_factory, run_id)

    class Deleting(GenerationExecutor):
        async def _finish(self, generation_id, status, error_message, result=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(TestGeneration, generation_id))
                await session.commit()
            return await super()._finish(generation_id, status, error_message, result)

    deleting = Deleting(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([REPLY]), sandbox=None,
        max_iterations=1, max_tokens=10, max_targets=1,
    )  # fmt: skip
    assert await deleting.execute(generation_id) is RunStatus.SUCCEEDED


@pytest.mark.usefixtures("llm_configured")
async def test_dispatch_failure_after_concurrent_delete(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed_run(session_factory, repos_root)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(TestGeneration, generation_id))
                await other.commit()
            await super().dispatch_test_generation(generation_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await GenerationService(session, DeleteThenFail(), settings).create(
                run_id, GenerationOptions()
            )


class RecordingImages:
    def __init__(self, error: bool = False) -> None:
        self.error = error
        self.calls: list[list[str]] = []

    def ensure(self, dependencies: list[Dependency]) -> RunnerImage:
        self.calls.append([d.name for d in dependencies])
        if self.error:
            raise DependencyInstallError("no wheel", "log", ())
        return RunnerImage("testmind-runner:deps", (), False, "", ())


@pytest.mark.parametrize(
    ("fails", "expected_image"), [(False, "testmind-runner:deps"), (True, None)]
)
async def test_generated_tests_run_in_the_projects_runner_image(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    fails: bool,
    expected_image: str | None,
) -> None:
    (repos_root / "sample" / "requirements.txt").write_text("attrs\n")
    run_id = await analysed_run(session_factory, repos_root)
    generation_id = await add_generation(session_factory, run_id)
    images = RecordingImages(error=fails)
    sandbox = CannedSandbox([sandbox_result(Outcome.PASSED)])
    gen_executor = GenerationExecutor(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([REPLY]), sandbox=sandbox,
        max_iterations=1, max_tokens=10, max_targets=1, images=images,
    )  # fmt: skip
    assert await gen_executor.execute(generation_id) is RunStatus.SUCCEEDED
    assert images.calls == [["attrs"]]
    assert sandbox.images == [expected_image]

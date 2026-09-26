"""Regression jobs against the real database. The engine is real (git objects, diff,
selection, comparison); only the sandbox is canned here. The real sandbox path is covered by
tests/sandbox/test_regression_engine.py and test_regression_end_to_end.py."""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import (
    DispatchError,
    InvalidRevisionError,
    NotFoundError,
    NotVersionedError,
)
from testmind.db.models import Regression
from testmind.domain.enums import RunStatus
from testmind.regression.engine import RegressionEngine
from testmind.regression.models import RegressionMode, RegressionReport, Verdict
from testmind.sandbox.models import Outcome
from testmind.schemas.project import ProjectCreate
from testmind.schemas.regression import RegressionRead
from testmind.services.project_service import ProjectService
from testmind.services.regression_executor import RegressionExecutor
from testmind.services.regression_service import RegressionService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import FailingDispatcher, RecordingDispatcher
from tests.regression_fixture import make_shop_repo
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result


async def shop_project(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> tuple[uuid.UUID, list[str]]:
    shas = make_shop_repo(repos_root / "shop")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="shop", source_location="shop")
        )
    return project.id, shas


def executor(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> RegressionExecutor:
    canned = result(case("test_discount", Outcome.PASSED, file="tests/test_pricing.py"))
    runner = TestSuiteRunner(Sandbox(canned), Images())
    return RegressionExecutor(session_factory, repos_root, RegressionEngine(runner))


async def stored(
    session_factory: async_sessionmaker[AsyncSession], regression_id: uuid.UUID
) -> Regression:
    async with session_factory() as session:
        row = await session.get(Regression, regression_id)
        assert row is not None
        return row


async def test_create_resolves_the_revisions_now(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, shas = await shop_project(session_factory, repos_root)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = RegressionService(session, dispatcher, repos_root)
        regression = await service.create(project_id, "v1", "main", RegressionMode.SELECTED)
        listed, total = await service.list_for_project(project_id, offset=0, limit=10)
    assert dispatcher.regressions == [regression.id]
    assert (regression.base_commit, regression.head_commit) == (shas[0], shas[1])
    assert (regression.base_ref, regression.head_ref, regression.mode) == (
        "v1",
        "main",
        RegressionMode.SELECTED,
    )
    assert (total, [r.id for r in listed]) == (1, [regression.id])
    read = RegressionRead.from_row(regression)
    assert (read.status, read.summary) == (RunStatus.QUEUED, None)


async def test_request_errors(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, _ = await shop_project(session_factory, repos_root)
    async with session_factory() as session:
        plain = await ProjectService(session, repos_root).create(
            ProjectCreate(name="plain", source_location="sample")
        )
        service = RegressionService(session, RecordingDispatcher(), repos_root)
        with pytest.raises(InvalidRevisionError, match="Unknown revision 'nope'"):
            await service.create(project_id, "nope", "HEAD", RegressionMode.FULL)
        with pytest.raises(InvalidRevisionError, match="are the same commit"):
            await service.create(project_id, "v1", "HEAD~1", RegressionMode.FULL)
        with pytest.raises(NotVersionedError, match="Not a git repository"):
            await service.create(plain.id, "HEAD~1", "HEAD", RegressionMode.FULL)
        with pytest.raises(NotFoundError):
            await service.create(uuid.uuid4(), "HEAD~1", "HEAD", RegressionMode.FULL)
        with pytest.raises(NotFoundError):
            await service.list_for_project(uuid.uuid4(), offset=0, limit=1)
        with pytest.raises(NotFoundError):
            await service.get(uuid.uuid4())


async def test_dispatch_failure(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, _ = await shop_project(session_factory, repos_root)
    async with session_factory() as session:
        service = RegressionService(session, FailingDispatcher(), repos_root)
        with pytest.raises(DispatchError):
            await service.create(project_id, "v1", "HEAD", RegressionMode.FULL)
        [regression], _ = await service.list_for_project(project_id, offset=0, limit=1)
    assert regression.status is RunStatus.FAILED


async def test_worker_runs_the_engine_and_stores_the_report(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, shas = await shop_project(session_factory, repos_root)
    async with session_factory() as session:
        regression = await RegressionService(session, RecordingDispatcher(), repos_root).create(
            project_id, "v1", "HEAD", RegressionMode.FULL
        )
    worker = executor(session_factory, repos_root)
    assert await worker.execute(regression.id) is RunStatus.SUCCEEDED
    assert await worker.execute(regression.id) is RunStatus.SUCCEEDED  # re-delivery: no rerun

    row = await stored(session_factory, regression.id)
    report = RegressionReport.model_validate(row.report)
    assert (report.base.ref, report.base.sha, report.head.ref) == ("v1", shas[0], "HEAD")
    assert [f.qualname for f in report.changes.functions] == ["discount", "rebate", "helper"]
    # The canned sandbox answers the same at base and head: nothing regressed.
    assert row.verdict is report.verdict is Verdict.CLEAN
    summary = RegressionRead.from_row(row).summary
    assert summary is not None
    assert (summary.changed_files, summary.changed_functions, summary.regressed) == (2, 3, 0)
    assert summary.missed_by_selection == 0
    assert await worker.execute(uuid.uuid4()) is None


async def test_worker_failures(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, _ = await shop_project(session_factory, repos_root)

    async def queued() -> uuid.UUID:
        async with session_factory() as session:
            created = await RegressionService(session, RecordingDispatcher(), repos_root).create(
                project_id, "v1", "HEAD", RegressionMode.FULL
            )
            return created.id

    first, second = await queued(), await queued()

    class Crashing(RegressionEngine):
        def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("bug")

    crashing = RegressionExecutor(session_factory, repos_root, Crashing(None))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await crashing.execute(first)
    assert (await stored(session_factory, first)).error_message == UNEXPECTED_ERROR_MESSAGE

    shutil.rmtree(repos_root / "shop" / ".git")  # the repository changed after the request
    assert await executor(session_factory, repos_root).execute(second) is RunStatus.FAILED
    assert (await stored(session_factory, second)).error_message is not None


async def test_regression_vanishing_before_finish_is_tolerated(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, _ = await shop_project(session_factory, repos_root)
    async with session_factory() as session:
        regression = await RegressionService(session, RecordingDispatcher(), repos_root).create(
            project_id, "v1", "HEAD", RegressionMode.SELECTED
        )

    class Deleting(RegressionExecutor):
        async def _finish(self, regression_id, status, error_message, report=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(Regression, regression_id))
                await session.commit()
            return await super()._finish(regression_id, status, error_message, report)

    canned = result(case("t", Outcome.PASSED))
    engine = RegressionEngine(TestSuiteRunner(Sandbox(canned), Images()))
    deleting = Deleting(session_factory, repos_root, engine)
    assert await deleting.execute(regression.id) is RunStatus.SUCCEEDED


async def test_dispatch_failure_after_concurrent_delete(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    project_id, _ = await shop_project(session_factory, repos_root)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_regression(self, regression_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(Regression, regression_id))
                await other.commit()
            await super().dispatch_regression(regression_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await RegressionService(session, DeleteThenFail(), repos_root).create(
                project_id, "v1", "HEAD", RegressionMode.FULL
            )

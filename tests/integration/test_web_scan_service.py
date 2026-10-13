"""Website scans against the real database: the service queues and reads them, and the
executor runs a crawl (through a fake browser) and stores the SiteScan."""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from types import TracebackType

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import DispatchError, InvalidTargetError, NotFoundError
from testmind.db.models import WebScan
from testmind.domain.enums import RunStatus
from testmind.schemas.web_scan import WebScanCreate
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.web_scan_executor import WebScanExecutor
from testmind.services.web_scan_service import WebScanService
from testmind.web_engine.crawler import Browser, PageVisit
from testmind.web_engine.network_policy import NetworkPolicy
from tests.doubles import FailingDispatcher, RecordingDispatcher


class FakeBrowser:
    def __init__(self, visits: dict[str, PageVisit]) -> None:
        self._visits = visits

    def visit(self, url: str) -> PageVisit:
        return self._visits.get(url, PageVisit(url=url, status=404))


class FakeSession(AbstractContextManager[Browser]):
    def __init__(self, browser: Browser) -> None:
        self._browser = browser

    def __enter__(self) -> Browser:
        return self._browser

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def browser_factory(browser: Browser) -> object:
    def factory(policy: NetworkPolicy, *, interact: bool) -> AbstractContextManager[Browser]:
        return FakeSession(browser)

    return factory


class Boom(AbstractContextManager[Browser]):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __enter__(self) -> Browser:
        raise self._exc

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


async def test_create_queues_and_dispatches(session: AsyncSession) -> None:
    dispatcher = RecordingDispatcher()
    service = WebScanService(session, dispatcher)
    scan = await service.create(WebScanCreate(target_url="https://example.com/", interact=True))
    assert scan.status is RunStatus.QUEUED
    assert scan.interact is True
    assert scan.allow_internal is False  # public scans never reach internal hosts
    assert dispatcher.web_scans == [scan.id]


async def test_an_internal_target_is_refused(session: AsyncSession) -> None:
    service = WebScanService(session, RecordingDispatcher())
    with pytest.raises(InvalidTargetError):
        await service.create(WebScanCreate(target_url="http://169.254.169.254/latest/"))
    with pytest.raises(InvalidTargetError):
        await service.create(WebScanCreate(target_url="ftp://example.com/"))


async def test_a_dispatch_failure_marks_the_scan_failed(session: AsyncSession) -> None:
    service = WebScanService(session, FailingDispatcher())
    with pytest.raises(DispatchError):
        await service.create(WebScanCreate(target_url="https://example.com/"))
    scans, total = await service.page(limit=10, offset=0)
    assert total == 1
    assert scans[0].status is RunStatus.FAILED


async def test_get_unknown_scan_is_not_found(session: AsyncSession) -> None:
    service = WebScanService(session, RecordingDispatcher())
    with pytest.raises(NotFoundError):
        await service.get(uuid.uuid4())


async def _queue(
    session_factory: async_sessionmaker[AsyncSession], *, status: RunStatus = RunStatus.QUEUED
) -> uuid.UUID:
    async with session_factory() as session:
        scan = WebScan(target_url="https://example.com/", status=status,
                       max_pages=50, max_depth=3, interact=False, allow_internal=False)  # fmt: skip
        session.add(scan)
        await session.commit()
        return scan.id


async def test_executor_runs_the_crawl_and_stores_the_scan(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scan_id = await _queue(session_factory)
    browser = FakeBrowser(
        {
            "https://example.com/": PageVisit(
                url="https://example.com/", status=200, links=["/boom"]
            ),
            "https://example.com/boom": PageVisit(url="https://example.com/boom", status=500),
        }
    )
    executor = WebScanExecutor(session_factory, browser_factory=browser_factory(browser))  # type: ignore[arg-type]
    assert await executor.execute(scan_id) is RunStatus.SUCCEEDED

    async with session_factory() as session:
        scan = await session.get(WebScan, scan_id)
        assert scan is not None
        assert scan.status is RunStatus.SUCCEEDED
        assert scan.result is not None
        assert scan.result["start_url"] == "https://example.com/"
        kinds = {i["kind"] for i in scan.result["issues"]}
        assert "server_error" in kinds


async def test_executor_reports_a_known_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scan_id = await _queue(session_factory)

    def factory(policy: NetworkPolicy, *, interact: bool) -> AbstractContextManager[Browser]:
        return Boom(InvalidTargetError("the browser refused the target"))

    executor = WebScanExecutor(session_factory, browser_factory=factory)
    assert await executor.execute(scan_id) is RunStatus.FAILED
    async with session_factory() as session:
        scan = await session.get(WebScan, scan_id)
        assert scan is not None
        assert scan.status is RunStatus.FAILED
        assert scan.error_message == "the browser refused the target"


async def test_executor_reraises_an_unexpected_error_but_marks_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scan_id = await _queue(session_factory)

    def factory(policy: NetworkPolicy, *, interact: bool) -> AbstractContextManager[Browser]:
        return Boom(RuntimeError("driver crashed"))

    executor = WebScanExecutor(session_factory, browser_factory=factory)
    with pytest.raises(RuntimeError, match="driver crashed"):
        await executor.execute(scan_id)
    async with session_factory() as session:
        scan = await session.get(WebScan, scan_id)
        assert scan is not None
        assert scan.status is RunStatus.FAILED
        assert scan.error_message == UNEXPECTED_ERROR_MESSAGE


async def test_executor_ignores_a_missing_or_finished_scan(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    executor = WebScanExecutor(session_factory, browser_factory=browser_factory(FakeBrowser({})))  # type: ignore[arg-type]
    assert await executor.execute(uuid.uuid4()) is None  # not found
    scan_id = await _queue(session_factory, status=RunStatus.SUCCEEDED)
    assert await executor.execute(scan_id) is RunStatus.SUCCEEDED  # already terminal, untouched

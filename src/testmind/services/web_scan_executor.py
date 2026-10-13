"""Executes queued website scans (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AbstractContextManager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.web_scan_repository import WebScanRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.web_engine.crawler import Browser, crawl
from testmind.web_engine.models import SiteScan
from testmind.web_engine.network_policy import NetworkPolicy

logger = get_logger(__name__)


class BrowserFactory(Protocol):
    """Opens a browser bound to a network policy. The production factory is
    `testmind.web_engine.browser.playwright_session`; tests pass a fake."""

    def __call__(
        self, policy: NetworkPolicy, *, interact: bool
    ) -> AbstractContextManager[Browser]: ...


class WebScanExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        browser_factory: BrowserFactory,
    ) -> None:
        self._session_factory = session_factory
        self._browser_factory = browser_factory

    async def execute(self, scan_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            scan = await WebScanRepository(session).lock(scan_id)
            if scan is None:
                logger.warning("web_scan_not_found", scan_id=str(scan_id))
                return None
            if scan.status is not RunStatus.QUEUED:
                return scan.status
            target, allow_internal, interact = scan.target_url, scan.allow_internal, scan.interact
            max_pages, max_depth = scan.max_pages, scan.max_depth
            apply_transition(scan, RunStatus.RUNNING)
            await session.commit()

        try:
            result = await asyncio.to_thread(
                self._scan, target, allow_internal, interact, max_pages, max_depth
            )
        except AppError as exc:
            return await self._done(scan_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("web_scan_crashed", scan_id=str(scan_id))
            await self._done(scan_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._done(
            scan_id, RunStatus.SUCCEEDED, None, {"result": result.model_dump(mode="json")}
        )

    def _scan(
        self, target: str, allow_internal: bool, interact: bool, max_pages: int, max_depth: int
    ) -> SiteScan:
        policy = NetworkPolicy.for_target(target, allow_internal=allow_internal)
        with self._browser_factory(policy, interact=interact) as browser:
            return crawl(target, browser, policy=policy, max_pages=max_pages, max_depth=max_depth)

    async def _done(
        self,
        scan_id: uuid.UUID,
        status: RunStatus,
        error: str | None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            scan = await WebScanRepository(session).lock(scan_id)
            if scan is None:
                return status
            apply_transition(scan, status, error_message=error)
            for name, value in (fields or {}).items():
                setattr(scan, name, value)
            await session.commit()
        logger.info("web_scan_finished", scan_id=str(scan_id), status=status.value)
        return status


def _production_browser_factory() -> BrowserFactory:
    # Imported lazily: Playwright is only needed in the worker, never in the API process.
    from testmind.web_engine.browser import playwright_session  # noqa: PLC0415

    def factory(policy: NetworkPolicy, *, interact: bool) -> AbstractContextManager[Browser]:
        return playwright_session(policy, interact=interact)

    return factory


def build_web_scan_executor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    browser_factory: BrowserFactory | None = None,
) -> WebScanExecutor:
    return WebScanExecutor(
        session_factory, browser_factory=browser_factory or _production_browser_factory()
    )

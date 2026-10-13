"""Executes queued website scans (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.authorized_target_repository import AuthorizedTargetRepository
from testmind.repositories.web_scan_repository import WebScanRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.web_engine import active, ownership
from testmind.web_engine.active import ActiveProber
from testmind.web_engine.crawler import Browser, crawl
from testmind.web_engine.models import SiteScan
from testmind.web_engine.network_policy import NetworkPolicy

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _ScanSpec:
    target: str
    allow_internal: bool
    interact: bool
    security: bool
    active: bool  # already AND-ed with a run-time authorization recheck
    max_pages: int
    max_depth: int


class BrowserFactory(Protocol):
    """Opens a browser bound to a network policy. The production factory is
    `testmind.web_engine.browser.playwright_session`; tests pass a fake."""

    def __call__(
        self, policy: NetworkPolicy, *, interact: bool, security: bool
    ) -> AbstractContextManager[Browser]: ...


class WebScanExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        browser_factory: BrowserFactory,
        active_prober: ActiveProber,
    ) -> None:
        self._session_factory = session_factory
        self._browser_factory = browser_factory
        self._active_prober = active_prober

    async def execute(self, scan_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            scan = await WebScanRepository(session).lock(scan_id)
            if scan is None:
                logger.warning("web_scan_not_found", scan_id=str(scan_id))
                return None
            if scan.status is not RunStatus.QUEUED:
                return scan.status
            # Re-check ownership at run time: a host may have lapsed since the scan was queued.
            run_active = scan.active and await _authorized(session, scan.target_url)
            spec = _ScanSpec(
                target=scan.target_url,
                allow_internal=scan.allow_internal,
                interact=scan.interact,
                security=scan.security,
                active=run_active,
                max_pages=scan.max_pages,
                max_depth=scan.max_depth,
            )
            apply_transition(scan, RunStatus.RUNNING)
            await session.commit()

        try:
            result = await asyncio.to_thread(self._scan, spec)
        except AppError as exc:
            return await self._done(scan_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("web_scan_crashed", scan_id=str(scan_id))
            await self._done(scan_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._done(
            scan_id, RunStatus.SUCCEEDED, None, {"result": result.model_dump(mode="json")}
        )

    def _scan(self, spec: _ScanSpec) -> SiteScan:
        policy = NetworkPolicy.for_target(spec.target, allow_internal=spec.allow_internal)
        browser_cm = self._browser_factory(policy, interact=spec.interact, security=spec.security)
        with browser_cm as browser:
            scan = crawl(
                spec.target, browser, policy=policy, max_pages=spec.max_pages,
                max_depth=spec.max_depth,
            )  # fmt: skip
        if not spec.active:
            return scan
        # Crafted-request checks against the pages reached, bounded by the network policy.
        found = active.scan_active(
            [page.url for page in scan.pages], self._active_prober, allows=policy.allows
        )
        return scan.model_copy(update={"issues": [*scan.issues, *found]})

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


async def _authorized(session: AsyncSession, target_url: str) -> bool:
    target = await AuthorizedTargetRepository(session).by_host(ownership.normalize_host(target_url))
    return target is not None and target.verified


def _production_browser_factory() -> BrowserFactory:
    # Imported lazily: Playwright is only needed in the worker, never in the API process.
    from testmind.web_engine.browser import playwright_session  # noqa: PLC0415

    def factory(
        policy: NetworkPolicy, *, interact: bool, security: bool
    ) -> AbstractContextManager[Browser]:
        return playwright_session(policy, interact=interact, security=security)

    return factory


ACTIVE_TIMEOUT_SECONDS = 10.0
MAX_ACTIVE_BODY = 20000  # chars of the response body kept for the checks to read


def _production_active_prober() -> ActiveProber:
    import httpx  # noqa: PLC0415 - only needed in the worker that runs active checks

    def send(url: str) -> active.Probed | None:
        try:
            # follow_redirects=False so the open-redirect check sees the Location itself.
            response = httpx.get(url, timeout=ACTIVE_TIMEOUT_SECONDS, follow_redirects=False)
        except httpx.HTTPError:
            return None
        return active.Probed(
            status=response.status_code,
            body=response.text[:MAX_ACTIVE_BODY],
            location=response.headers.get("location"),
        )

    return send


def build_web_scan_executor(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    browser_factory: BrowserFactory | None = None,
    active_prober: ActiveProber | None = None,
) -> WebScanExecutor:
    return WebScanExecutor(
        session_factory,
        browser_factory=browser_factory or _production_browser_factory(),
        active_prober=active_prober or _production_active_prober(),
    )

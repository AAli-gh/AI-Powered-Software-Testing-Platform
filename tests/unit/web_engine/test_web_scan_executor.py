"""The web-scan executor's wiring: the production browser factory builds a real PlaywrightSession
(constructed, not launched — Playwright is only imported when the session is entered)."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.services.web_scan_executor import (
    WebScanExecutor,
    _production_browser_factory,
    build_web_scan_executor,
)
from testmind.web_engine.browser import PlaywrightSession
from testmind.web_engine.network_policy import NetworkPolicy


def test_production_factory_builds_a_playwright_session() -> None:
    factory = _production_browser_factory()
    session = factory(NetworkPolicy.for_target("https://a.test/"), interact=True, security=True)
    assert isinstance(session, PlaywrightSession)  # not entered: no browser launched


def test_build_defaults_to_the_production_factory() -> None:
    dummy = cast(async_sessionmaker[AsyncSession], object())
    executor = build_web_scan_executor(dummy)
    assert isinstance(executor, WebScanExecutor)


def test_build_accepts_an_injected_factory() -> None:
    marker: Any = object()
    executor = build_web_scan_executor(
        cast(async_sessionmaker[AsyncSession], object()), browser_factory=marker
    )
    assert executor._browser_factory is marker

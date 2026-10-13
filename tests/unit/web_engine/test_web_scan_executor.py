"""The web-scan executor's wiring: the production browser factory builds a real PlaywrightSession
(constructed, not launched — Playwright is only imported when the session is entered)."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.services.web_scan_executor import (
    WebScanExecutor,
    _production_active_prober,
    _production_browser_factory,
    build_web_scan_executor,
)
from testmind.web_engine.browser import PlaywrightSession
from testmind.web_engine.network_policy import NetworkPolicy


def test_production_factory_builds_a_playwright_session() -> None:
    factory = _production_browser_factory()
    session = factory(
        NetworkPolicy.for_target("https://a.test/"),
        interact=True,
        security=True,
        screenshots=True,
    )
    assert isinstance(session, PlaywrightSession)  # not entered: no browser launched


def test_build_defaults_to_the_production_factory() -> None:
    dummy = cast(async_sessionmaker[AsyncSession], object())
    executor = build_web_scan_executor(dummy)
    assert isinstance(executor, WebScanExecutor)


def test_build_accepts_injected_factories() -> None:
    browser: Any = object()
    prober: Any = object()
    executor = build_web_scan_executor(
        cast(async_sessionmaker[AsyncSession], object()),
        browser_factory=browser,
        active_prober=prober,
    )
    assert executor._browser_factory is browser
    assert executor._active_prober is prober


def test_production_active_prober_reads_status_body_and_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        assert kwargs["follow_redirects"] is False  # so the open-redirect check sees Location
        if "boom" in url:
            raise httpx.ConnectError("no route")
        return httpx.Response(302, headers={"location": "https://elsewhere/"}, text="body")

    monkeypatch.setattr(httpx, "get", fake_get)
    prober = _production_active_prober()
    probed = prober("https://a.test/?q=1")
    assert probed is not None
    assert probed.status == 302
    assert probed.location == "https://elsewhere/"
    assert probed.body == "body"
    assert prober("https://boom.test/") is None  # transport errors are swallowed

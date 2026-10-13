"""The authorized-target service against the real database, plus the production prober."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.services.authorized_target_service import AuthorizedTargetService, build_prober
from testmind.web_engine.ownership import WELL_KNOWN_PATH


async def test_is_authorized_only_after_verification(session: AsyncSession) -> None:
    published: dict[str, str] = {}
    service = AuthorizedTargetService(session, published.get)  # type: ignore[arg-type]
    target = await service.register("example.com")

    assert await service.is_authorized("example.com") is False
    assert await service.is_authorized("other.com") is False  # never registered

    published[f"https://example.com{WELL_KNOWN_PATH}"] = target.token
    await service.verify(target.id)
    assert await service.is_authorized("https://example.com/some/path") is True  # normalized host


def test_build_prober_reads_200_and_swallows_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        calls["url"] = url
        if "ok" in url:
            return httpx.Response(200, text="the body")
        if "missing" in url:
            return httpx.Response(404, text="nope")
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", fake_get)
    prober = build_prober()
    assert prober("https://ok.test/f") == "the body"
    assert prober("https://missing.test/f") is None  # non-200
    assert prober("https://down.test/f") is None  # transport error

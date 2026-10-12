"""The browser driver's pure helpers and guards, without launching a browser (Playwright is
imported only when a session is entered)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from testmind.web_engine import browser as b
from testmind.web_engine.network_policy import NetworkPolicy


@dataclass
class FakeRequest:
    url: str
    method: str = "GET"
    failure: str | None = None
    redirected_from: FakeRequest | None = None


@dataclass
class FakeResponse:
    url: str
    status: int
    request: FakeRequest


def test_append_caps_and_dedups() -> None:
    items: list[str] = []
    b._append(items, "x")
    b._append(items, "x")  # duplicate ignored
    b._append(items, "y")
    assert items == ["x", "y"]
    full = ["v"] * b.MAX_EVENTS
    b._append(full, "overflow")  # at the cap: dropped
    assert "overflow" not in full


def req(url: str, method: str = "GET", failure: str | None = None) -> Any:
    return FakeRequest(url, method, failure)


def resp(url: str, status: int, request: FakeRequest | None = None) -> Any:
    return FakeResponse(url, status, request or FakeRequest(url))


def test_failure_and_bad_response() -> None:
    assert b._failure(req("https://a/x", "POST", "net::ERR")) == "POST https://a/x — net::ERR"
    assert b._failure(req("https://a/x")) == "GET https://a/x — failed"
    failed: list[str] = []
    main = "https://a/"
    b._record_bad_response(resp("https://a/api", 503), main, failed)
    b._record_bad_response(resp("https://a/ok", 200), main, failed)  # not an error
    b._record_bad_response(resp(main, 500), main, failed)  # the document itself, skipped
    assert failed == ["GET https://a/api — 503"]


def test_redirect_count_follows_the_chain() -> None:
    first = FakeRequest("https://a/1")
    second = FakeRequest("https://a/2", redirected_from=first)
    third = FakeRequest("https://a/3", redirected_from=second)
    assert b._redirect_count(resp("https://a/3", 200, third)) == 2
    assert b._redirect_count(None) == 0


def test_safe_swallows_errors() -> None:
    assert b._safe(lambda: 7) == 7
    assert b._safe(lambda: 1 / 0) is None


def test_reason_is_the_first_line_bounded() -> None:
    assert b._reason(ValueError("line one\nline two")) == "line one"
    assert b._reason(RuntimeError("")) == "RuntimeError"
    assert len(b._reason(ValueError("z" * 500))) == 200


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = FakeRequest(url)
        self.action: str | None = None

    def continue_(self) -> None:
        self.action = "continue"

    def abort(self) -> None:
        self.action = "abort"


def test_gate_aborts_out_of_scope_requests() -> None:
    session = b.PlaywrightSession(NetworkPolicy.for_target("https://a.test/"))
    allowed = FakeRoute("https://a.test/style.css")
    session._gate(allowed)
    assert allowed.action == "continue"
    blocked = FakeRoute("https://tracker.example/pixel.gif")
    session._gate(blocked)
    assert blocked.action == "abort"
    assert any("tracker.example" in d for d in session._policy.denied)


def test_visit_before_enter_is_an_error_and_exit_is_safe() -> None:
    session = b.PlaywrightSession(NetworkPolicy.for_target("https://a.test/"))
    with pytest.raises(RuntimeError, match="context manager"):
        session.visit("https://a.test/")
    session.__exit__(None, None, None)  # nothing launched: a no-op, not a crash

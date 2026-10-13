"""The browser driver's pure helpers and guards, without launching a browser (Playwright is
imported only when a session is entered)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

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


class FakeResponseHeaders:
    def __init__(self, entries: list[dict[str, str]]) -> None:
        self._entries = entries

    def headers_array(self) -> list[dict[str, str]]:
        return self._entries


def test_security_facts_split_headers_from_cookies() -> None:
    assert b._security_facts(None) == ({}, [])
    response = FakeResponseHeaders(
        [
            {"name": "Content-Type", "value": "text/html"},
            {"name": "Set-Cookie", "value": "a=1; Path=/"},
            {"name": "Set-Cookie", "value": "b=2; Secure"},
        ]
    )
    headers, cookies = b._security_facts(cast(Any, response))
    assert headers == {"content-type": "text/html"}  # lowercased, cookies excluded
    assert cookies == ["a=1; Path=/", "b=2; Secure"]


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


class FakeLocator:
    """A stand-in for a Playwright Locator: the label/name helpers only read text and attrs."""

    def __init__(self, text: str = "", attrs: dict[str, str] | None = None) -> None:
        self._text = text
        self._attrs = attrs or {}

    def inner_text(self, timeout: int = 0) -> str:
        if not self._text:
            raise RuntimeError("no text node")  # Playwright raises rather than returning ""
        return self._text

    def get_attribute(self, name: str) -> str | None:
        return self._attrs.get(name)


def loc(text: str = "", **attrs: str) -> Any:
    return FakeLocator(text, attrs)


def test_label_prefers_text_then_value_then_falls_back() -> None:
    assert b._label(loc("Sign in")) == "Sign in"
    assert b._label(loc(value="Send")) == "Send"  # no inner text: the value attribute
    assert b._label(loc()) == "(unlabelled button)"
    assert len(b._label(loc("z" * 500))) == 120  # bounded


def test_form_name_prefers_name_then_id_then_action() -> None:
    assert b._form_name(loc(name="signup", id="f1")) == "signup"
    assert b._form_name(loc(id="f1", action="/go")) == "f1"
    assert b._form_name(loc(action="/go")) == "/go"
    assert b._form_name(loc()) == "(unnamed form)"


def test_visit_before_enter_is_an_error_and_exit_is_safe() -> None:
    session = b.PlaywrightSession(NetworkPolicy.for_target("https://a.test/"))
    with pytest.raises(RuntimeError, match="context manager"):
        session.visit("https://a.test/")
    session.__exit__(None, None, None)  # nothing launched: a no-op, not a crash

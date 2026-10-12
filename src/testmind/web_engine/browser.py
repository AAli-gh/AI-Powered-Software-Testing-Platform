"""The real browser behind the crawl: a headless Chromium driven by Playwright.

`PlaywrightSession` is a `Browser` (crawler.py): it launches Chromium once and visits URLs,
reporting each page's status, title, links, console errors, failed sub-requests and load time.

It is also the second line of the network gate. Every request the page makes is routed through
`policy`: anything the policy refuses is aborted in the browser, so a redirect, a script or an
image cannot pull the browser to an out-of-scope or internal host even if the crawl logic let
it through.

Playwright is only a test/sandbox dependency; importing this module does not require it. The
browser binary is found automatically inside the sandbox image, or by `executable_path` /
`TESTMIND_CHROMIUM_PATH` when running against a system Chromium.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar

from testmind.web_engine.crawler import PageVisit
from testmind.web_engine.login import Credentials, perform_login
from testmind.web_engine.network_policy import NetworkPolicy

if TYPE_CHECKING:
    from playwright.sync_api import Browser as PwBrowser
    from playwright.sync_api import (
        BrowserContext,
        ConsoleMessage,
        Page,
        Playwright,
        Request,
        Response,
    )

DEFAULT_TIMEOUT_MS = 15_000
MAX_LINKS = 500
MAX_EVENTS = 50  # console errors / failed requests kept per page


class PlaywrightSession(AbstractContextManager["PlaywrightSession"]):
    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        executable_path: str | None = None,
        login: Credentials | None = None,
    ) -> None:
        self._policy = policy
        self._timeout = timeout_ms
        self._executable = executable_path or os.environ.get("TESTMIND_CHROMIUM_PATH") or None
        self._login = login
        self._playwright: Playwright | None = None
        self._browser: PwBrowser | None = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> PlaywrightSession:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415 - optional dependency

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(executable_path=self._executable or None)
        # One context for the whole scan, so a login's session cookie carries to every page.
        self._context = self._browser.new_context()
        self._context.set_default_timeout(self._timeout)
        self._context.route("**/*", self._gate)
        if self._login is not None:
            self._authenticate(self._login)
        return self

    def _authenticate(self, creds: Credentials) -> None:
        assert self._context is not None  # noqa: S101 - set in __enter__ before this is called
        page = self._context.new_page()
        try:
            perform_login(_PlaywrightDriver(page), creds)
        finally:
            page.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def visit(self, url: str) -> PageVisit:
        if self._context is None:
            raise RuntimeError("Use PlaywrightSession as a context manager before visiting")
        page = self._context.new_page()
        console: list[str] = []
        failed: list[str] = []

        def on_console(message: ConsoleMessage) -> None:
            if message.type == "error":
                _append(console, message.text)

        page.on("pageerror", lambda e: _append(console, str(e)))
        page.on("console", on_console)
        page.on("requestfailed", lambda r: _append(failed, _failure(r)))
        page.on("response", lambda r: _record_bad_response(r, url, failed))
        try:
            started = time.monotonic()
            response = page.goto(url, wait_until="load")
            load_ms = (time.monotonic() - started) * 1000
            final_url = page.url
            title = _safe(page.title)
            links = _safe(lambda: page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)")) or []  # fmt: skip
            buttons = _safe(lambda: page.eval_on_selector_all(
                "button, [role=button], input[type=submit], input[type=button]",
                "els => els.length")) or 0  # fmt: skip
            visit = PageVisit(
                url=final_url, status=response.status if response else None, title=title,
                load_ms=round(load_ms, 1), links=list(links)[:MAX_LINKS], buttons=int(buttons),
                console_errors=console[:MAX_EVENTS], failed_requests=failed[:MAX_EVENTS],
                redirects=_redirect_count(response),
            )  # fmt: skip
        except Exception as exc:
            visit = PageVisit(url=url, status=None, error=_reason(exc),
                              console_errors=console[:MAX_EVENTS])  # fmt: skip
        finally:
            page.close()
        return visit

    def _gate(self, route: object) -> None:
        request: Request = route.request  # type: ignore[attr-defined]
        if self._policy.allows(request.url):
            route.continue_()  # type: ignore[attr-defined]
        else:
            self._policy.record(request.url, self._policy.refusal(request.url) or "out of scope")
            route.abort()  # type: ignore[attr-defined]


def _append(items: list[str], value: str) -> None:
    if len(items) < MAX_EVENTS and value not in items:
        items.append(value)


def _failure(request: Request) -> str:
    failure = request.failure or "failed"
    return f"{request.method} {request.url} — {failure}"


def _record_bad_response(response: Response, main_url: str, failed: list[str]) -> None:
    # A sub-request (not the main document) that the server answered with an error.
    if response.url != main_url and response.status >= 500:  # noqa: PLR2004
        _append(failed, f"{response.request.method} {response.url} — {response.status}")


def _redirect_count(response: Response | None) -> int:
    count = 0
    request = response.request if response else None
    while request is not None and (previous := request.redirected_from) is not None:
        count += 1
        request = previous
    return count


_T = TypeVar("_T")


def _safe(call: Callable[[], _T]) -> _T | None:
    try:
        return call()
    except Exception:
        return None


def _reason(exc: Exception) -> str:
    text = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return text[:200]


class _PlaywrightDriver:
    """Adapts a Playwright page to the `LoginDriver` protocol the login logic uses."""

    def __init__(self, page: Page) -> None:
        self._page = page

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="load")

    def fill(self, selector: str, value: str) -> None:
        self._page.locator(selector).first.fill(value)

    def click(self, selector: str) -> None:
        self._page.locator(selector).first.click()

    def wait_idle(self) -> None:
        self._page.wait_for_load_state("networkidle")

    def url(self) -> str:
        return self._page.url

    def has(self, selector: str) -> bool:
        return self._page.query_selector(selector) is not None

    def body(self) -> str:
        return _safe(self._page.content) or ""

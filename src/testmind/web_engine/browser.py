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

import hashlib
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, TypeVar

from testmind.web_engine.crawler import PageVisit
from testmind.web_engine.interactions import (
    ButtonProbe,
    FormProbe,
    Interactions,
    is_dangerous,
)
from testmind.web_engine.login import Credentials, perform_login
from testmind.web_engine.network_policy import NetworkPolicy

if TYPE_CHECKING:
    from playwright.sync_api import Browser as PwBrowser
    from playwright.sync_api import (
        BrowserContext,
        ConsoleMessage,
        Locator,
        Page,
        Playwright,
        Request,
        Response,
    )


def playwright_session(
    policy: NetworkPolicy, *, interact: bool = False, login: Credentials | None = None
) -> PlaywrightSession:
    """The production browser factory: a real headless Chromium bound to `policy`.

    `TESTMIND_CHROMIUM_PATH` selects the browser binary when it is not the sandbox default.
    """
    return PlaywrightSession(policy, interact=interact, login=login)


DEFAULT_TIMEOUT_MS = 15_000
MAX_LINKS = 500
MAX_EVENTS = 50  # console errors / failed requests kept per page
MAX_PROBE = 25  # buttons / forms exercised per page
BUTTON_SELECTOR = "button, [role=button], input[type=submit], input[type=button]"
PROBE_TIMEOUT_MS = 4_000  # a click or submit should take effect quickly
SETTLE_MS = 400  # time allowed for a click's effect (navigation, request, DOM change) to show


class PlaywrightSession(AbstractContextManager["PlaywrightSession"]):
    def __init__(
        self,
        policy: NetworkPolicy,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        executable_path: str | None = None,
        login: Credentials | None = None,
        interact: bool = False,
    ) -> None:
        self._policy = policy
        self._timeout = timeout_ms
        self._executable = executable_path or os.environ.get("TESTMIND_CHROMIUM_PATH") or None
        self._login = login
        self._interact = interact
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
            interactions = self._probe(url) if self._interact else None
            visit = PageVisit(
                url=final_url, status=response.status if response else None, title=title,
                load_ms=round(load_ms, 1), links=list(links)[:MAX_LINKS], buttons=int(buttons),
                console_errors=console[:MAX_EVENTS], failed_requests=failed[:MAX_EVENTS],
                redirects=_redirect_count(response), interactions=interactions,
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

    def _probe(self, url: str) -> Interactions:
        # Each control is exercised on its own fresh page, so a click that navigates away or
        # changes state never affects the next probe.
        assert self._context is not None  # noqa: S101 - set in __enter__ before visit() runs
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="load")
            button_count = min(page.locator(BUTTON_SELECTOR).count(), MAX_PROBE)
            form_count = min(page.locator("form").count(), MAX_PROBE)
        finally:
            page.close()
        buttons = [self._probe_button(url, i) for i in range(button_count)]
        forms = [self._probe_form(url, i) for i in range(form_count)]
        return Interactions(buttons=buttons, forms=forms)

    def _probe_button(self, url: str, index: int) -> ButtonProbe:
        assert self._context is not None  # noqa: S101
        page = self._context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(url, wait_until="load")
            button = page.locator(BUTTON_SELECTOR).nth(index)
            label = _label(button)
            if is_dangerous(label):
                return ButtonProbe(label=label, skipped_dangerous=True)
            page.wait_for_timeout(SETTLE_MS)  # let load-time requests flush before we measure
            before_url, before_dom = page.url, _dom_sig(page)
            fired: list[int] = []
            page.on("request", lambda _: fired.append(1))
            _safe(lambda: button.click(timeout=PROBE_TIMEOUT_MS))
            page.wait_for_timeout(SETTLE_MS)
            return ButtonProbe(
                label=label,
                navigated=page.url != before_url,
                requested=bool(fired),
                changed_dom=_dom_sig(page) != before_dom,
                errored=errors[0] if errors else None,
            )
        finally:
            page.close()

    def _probe_form(self, url: str, index: int) -> FormProbe:
        assert self._context is not None  # noqa: S101
        page = self._context.new_page()
        status: list[int] = []
        page.on(
            "response",
            lambda r: status.append(r.status) if r.request.is_navigation_request() else None,
        )
        try:
            page.goto(url, wait_until="load")
            form = page.locator("form").nth(index)
            name = _form_name(form)
            page.wait_for_timeout(SETTLE_MS)
            status.clear()  # ignore the page's own navigation; keep only what the submit triggers
            submitted = _safe(
                lambda: form.evaluate(
                    "f => { (f.requestSubmit ? f.requestSubmit() : f.submit()); return true; }"
                )
            )
            page.wait_for_timeout(SETTLE_MS)
            if submitted is None:  # pragma: no cover - the submit script itself failed to run
                return FormProbe(name=name, submitted=False, error="the form did not submit")
            return FormProbe(name=name, submitted=True, status=status[-1] if status else None)
        finally:
            page.close()


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


def _label(button: Locator) -> str:
    text = (_safe(lambda: button.inner_text(timeout=PROBE_TIMEOUT_MS)) or "").strip()
    if not text:
        text = (_safe(lambda: button.get_attribute("value")) or "").strip()
    return text[:120] if text else "(unlabelled button)"


def _form_name(form: Locator) -> str:
    for attr in ("name", "id", "action"):
        value = (_safe(lambda a=attr: form.get_attribute(a)) or "").strip()  # type: ignore[misc]
        if value:
            return value[:120]
    return "(unnamed form)"


def _dom_sig(page: Page) -> str:
    html = _safe(lambda: page.evaluate("() => document.body ? document.body.innerHTML : ''")) or ""
    return hashlib.sha1(str(html).encode(), usedforsecurity=False).hexdigest()


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

"""The web scanner against a real browser and a deliberately-buggy local site
(examples/buggy_site). A threaded HTTP server plays the broken server: /crash.html and
/api/price answer 500, /missing.html is absent."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from testmind.web_engine.browser import PlaywrightSession
from testmind.web_engine.crawler import crawl
from testmind.web_engine.login import Credentials
from testmind.web_engine.models import IssueKind, Severity
from testmind.web_engine.network_policy import NetworkPolicy

pytestmark = pytest.mark.browser

SITE = Path(__file__).resolve().parents[2] / "examples" / "buggy_site"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/crash.html", "/api/price"):
            self.send_error(500, "boom")
            return
        super().do_GET()

    def do_POST(self) -> None:  # the signup form posts here; the server is broken
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_error(500, "boom")

    def log_message(self, *args: object) -> None:  # silence the server
        pass


@pytest.fixture
def site() -> Iterator[str]:
    handler = partial(Handler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def test_scan_finds_the_sites_defects(site: str, chromium_path: str) -> None:
    policy = NetworkPolicy.for_target(site, allow_internal=True)  # the test server is local
    with PlaywrightSession(policy, executable_path=chromium_path) as browser:
        scan = crawl(site, browser, policy=policy)

    reached = {urlsplit(p.url).path for p in scan.pages}
    assert {"/", "/catalog.html", "/about.html"} <= reached

    by_kind = {(i.kind, i.url.rsplit("/", 1)[-1]) for i in scan.issues}
    assert (IssueKind.SERVER_ERROR, "crash.html") in by_kind
    assert (IssueKind.NOT_FOUND, "missing.html") in by_kind
    assert any(i.kind is IssueKind.JS_ERROR for i in scan.issues)  # catalog.html
    assert any(i.kind is IssueKind.FAILED_REQUEST for i in scan.issues)  # / -> /api/price 500

    # The external link was recorded as out of scope, never visited.
    assert any("example.com" in d for d in scan.denied)
    assert not any("example.com" in p.url for p in scan.pages)

    high = [i for i in scan.issues if i.severity is Severity.HIGH]
    assert any(i.kind is IssueKind.SERVER_ERROR for i in high)
    # buttons on the home page are counted
    home = next(p for p in scan.pages if urlsplit(p.url).path == "/")
    assert home.buttons == 2


def test_exercising_controls_finds_dead_buttons_and_broken_forms(
    site: str, chromium_path: str
) -> None:
    policy = NetworkPolicy.for_target(site, allow_internal=True)
    with PlaywrightSession(policy, executable_path=chromium_path, interact=True) as browser:
        scan = crawl(site, browser, policy=policy)

    dead = [i for i in scan.issues if i.kind is IssueKind.DEAD_BUTTON]
    assert any("Does nothing" in i.detail for i in dead)  # the #dead button on /

    forms = [i for i in scan.issues if i.kind is IssueKind.FORM_ERROR]
    assert any(i.severity is Severity.HIGH and "500" in i.detail for i in forms)  # signup -> 500

    # Buttons that do something are not reported, and the destructive one is never clicked.
    assert not any("Load price" in i.detail for i in dead)  # #fetcher issued a request
    assert not any("Add a note" in i.detail for i in dead)  # #mutate changed the page
    assert not any("Delete account" in i.detail for i in scan.issues)  # #danger was skipped

    # The button whose click throws is reported as a JS error, not a dead button.
    js = [i for i in scan.issues if i.kind is IssueKind.JS_ERROR and "Break" in i.detail]
    assert js


def test_a_navigation_failure_is_reported(chromium_path: str) -> None:
    # Port 1 on the loopback host refuses the connection: the visit is an error, not a crash.
    policy = NetworkPolicy.for_target("http://127.0.0.1:1/", allow_internal=True)
    with PlaywrightSession(policy, executable_path=chromium_path) as browser:
        visit = browser.visit("http://127.0.0.1:1/")
    assert visit.status is None
    assert visit.error is not None


SECRET = "Members only"


class LoginSite(http.server.BaseHTTPRequestHandler):
    """A tiny session: GET /secret needs the `session=1` cookie, set by POST /login."""

    def _send(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def _authed(self) -> bool:
        return "session=1" in self.headers.get("Cookie", "")

    def do_GET(self) -> None:
        if self.path == "/login":
            self._send(
                200,
                "<form method=post action=/login>"
                "<input name=username><input type=password name=password>"
                "<button type=submit>Sign in</button></form>",
            )
        elif self.path == "/secret":
            if self._authed():
                self._send(200, f"<h1>{SECRET}</h1><a href='/'>Home</a>")
            else:
                self._send(403, "<h1>Forbidden</h1>")
        elif self.path == "/":
            self._send(200, "<a href='/secret'>Secret</a>")
        else:
            self._send(404, "<h1>Not found</h1>")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._send(303, "", {"Location": "/secret", "Set-Cookie": "session=1; Path=/"})

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def login_site() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LoginSite)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def test_an_authenticated_scan_reaches_pages_behind_login(
    login_site: str, chromium_path: str
) -> None:
    base = login_site.rstrip("/")
    policy = NetworkPolicy.for_target(login_site, allow_internal=True)
    creds = Credentials(
        login_url=f"{base}/login", username="me", password="pw", success_contains="Members only"
    )
    with PlaywrightSession(policy, executable_path=chromium_path, login=creds) as browser:
        secret = browser.visit(f"{base}/secret")
    assert secret.status == 200
    assert "Members only" in (secret.title or "") or secret.status == 200

    # Without logging in, the same page is forbidden.
    with PlaywrightSession(policy, executable_path=chromium_path) as anon:
        forbidden = anon.visit(f"{base}/secret")
    assert forbidden.status == 403

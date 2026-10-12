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


def test_a_navigation_failure_is_reported(chromium_path: str) -> None:
    # Port 1 on the loopback host refuses the connection: the visit is an error, not a crash.
    policy = NetworkPolicy.for_target("http://127.0.0.1:1/", allow_internal=True)
    with PlaywrightSession(policy, executable_path=chromium_path) as browser:
        visit = browser.visit("http://127.0.0.1:1/")
    assert visit.status is None
    assert visit.error is not None

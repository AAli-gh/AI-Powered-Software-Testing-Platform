"""The crawl loop and the rules that turn page facts into issues, with a fake browser."""

from __future__ import annotations

from testmind.web_engine.crawler import PageVisit, crawl
from testmind.web_engine.interactions import ButtonProbe, FormProbe, Interactions
from testmind.web_engine.models import IssueKind, IssueSeverity
from testmind.web_engine.network_policy import NetworkPolicy


class FakeBrowser:
    """Serves canned visits by URL; records the order it was asked to visit."""

    def __init__(self, visits: dict[str, PageVisit]) -> None:
        self._visits = visits
        self.visited: list[str] = []

    def visit(self, url: str) -> PageVisit:
        self.visited.append(url)
        return self._visits.get(url, PageVisit(url=url, status=404))


def visit(url: str, **kw: object) -> PageVisit:
    return PageVisit(url=url, status=kw.pop("status", 200), **kw)  # type: ignore[arg-type]


def test_crawl_follows_in_scope_links_and_dedups() -> None:
    browser = FakeBrowser(
        {
            "https://a.test/": visit("https://a.test/", links=["/a", "/b", "https://x.test/out"]),
            "https://a.test/a": visit("https://a.test/a", links=["/", "/b"]),  # back-links
            "https://a.test/b": visit("https://a.test/b", links=[]),
        }
    )
    scan = crawl("https://a.test/", browser)
    assert browser.visited == ["https://a.test/", "https://a.test/a", "https://a.test/b"]
    assert {p.url for p in scan.pages} == {
        "https://a.test/",
        "https://a.test/a",
        "https://a.test/b",
    }
    assert scan.issues == []  # all 200
    assert any("x.test" in d for d in scan.denied)  # external link recorded, not visited


def test_server_error_and_not_found() -> None:
    browser = FakeBrowser(
        {
            "https://a.test/": visit("https://a.test/", links=["/boom", "/missing"]),
            "https://a.test/boom": visit("https://a.test/boom", status=500),
            "https://a.test/missing": visit("https://a.test/missing", status=404),
        }
    )
    scan = crawl("https://a.test/", browser)
    kinds = {i.url: (i.kind, i.severity) for i in scan.issues}
    assert kinds["https://a.test/boom"] == (IssueKind.SERVER_ERROR, IssueSeverity.HIGH)
    assert kinds["https://a.test/missing"] == (IssueKind.NOT_FOUND, IssueSeverity.MEDIUM)
    assert "linked from https://a.test/" in next(
        i.detail for i in scan.issues if i.kind is IssueKind.NOT_FOUND
    )


def test_js_errors_failed_requests_redirects_and_slow() -> None:
    browser = FakeBrowser(
        {
            "https://a.test/": visit(
                "https://a.test/",
                console_errors=["TypeError: x is not a function", "ReferenceError: y"],
                failed_requests=["GET https://a.test/api/data — 503"],
                redirects=12,
                load_ms=12_500.0,
            ),
        }
    )
    scan = crawl("https://a.test/", browser)
    by_kind = {i.kind for i in scan.issues}
    assert IssueKind.JS_ERROR in by_kind
    assert IssueKind.FAILED_REQUEST in by_kind
    assert IssueKind.REDIRECT_LOOP in by_kind
    assert IssueKind.SLOW_PAGE in by_kind
    assert sum(i.kind is IssueKind.JS_ERROR for i in scan.issues) == 2  # one per console error
    assert scan.by_severity == {"high": 0, "medium": 4, "low": 1}


def test_navigation_failure_is_a_broken_link_and_stops_descent() -> None:
    browser = FakeBrowser(
        {
            "https://a.test/": visit("https://a.test/", links=["/dead"]),
            "https://a.test/dead": PageVisit(
                url="https://a.test/dead",
                status=None,
                error="net::ERR_CONNECTION_REFUSED",
                links=["/never"],
            ),
        }
    )
    scan = crawl("https://a.test/", browser)
    [broken] = [i for i in scan.issues if i.kind is IssueKind.BROKEN_LINK]
    assert broken.severity is IssueSeverity.HIGH
    assert "linked from https://a.test/" in broken.detail
    assert "https://a.test/never" not in browser.visited  # links of a failed page are not followed


def test_page_budget_is_reported() -> None:
    links = [f"/p{i}" for i in range(5)]
    browser = FakeBrowser({"https://a.test/": visit("https://a.test/", links=links)})
    scan = crawl("https://a.test/", browser, max_pages=3)
    assert len(scan.pages) == 3
    assert scan.notes
    assert "page budget 3" in scan.notes[0]


def test_interaction_probes_become_issues() -> None:
    browser = FakeBrowser(
        {
            "https://a.test/": visit(
                "https://a.test/",
                interactions=Interactions(
                    buttons=[ButtonProbe("Dead"), ButtonProbe("Fine", navigated=True)],
                    forms=[FormProbe("signup", submitted=True, status=500)],
                ),
            ),
        }
    )
    scan = crawl("https://a.test/", browser)
    kinds = {i.kind for i in scan.issues}
    assert kinds == {IssueKind.DEAD_BUTTON, IssueKind.FORM_ERROR}
    assert all(i.url == "https://a.test/" for i in scan.issues)


def test_an_out_of_scope_or_internal_start_url_is_refused() -> None:
    scan = crawl("http://169.254.169.254/latest/meta-data/",
                 FakeBrowser({}),
                 policy=NetworkPolicy.for_target("http://169.254.169.254/"))  # fmt: skip
    assert scan.pages == []
    assert scan.notes
    assert "could not be crawled" in scan.notes[0]
    assert "internal" in scan.notes[0]

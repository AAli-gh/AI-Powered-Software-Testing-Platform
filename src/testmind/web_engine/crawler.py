"""Drive a browser across a site and turn what each page reports into issues.

The crawl loop here is pure: it asks a `Browser` (a thin protocol) to visit a URL, reads back a
`PageVisit`, feeds the page's links to the frontier, and derives issues from the facts. The
real browser (Playwright) lives in `browser.py` and is the only part that needs the sandbox,
so the loop and the issue rules are unit-tested with a fake browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from testmind.web_engine.frontier import Frontier
from testmind.web_engine.interactions import Interactions, interaction_issues
from testmind.web_engine.models import IssueKind, IssueSeverity, Page, PageIssue, SiteScan
from testmind.web_engine.network_policy import NetworkPolicy
from testmind.web_engine.security import security_issues

SERVER_ERROR = 500
CLIENT_ERROR = 400
SLOW_MS = 10_000
MAX_REDIRECTS = 10

# Security findings repeat across pages (every page lacks the same header); report each once.
SECURITY_KINDS = frozenset(
    {
        IssueKind.INSECURE_HEADER,
        IssueKind.INSECURE_COOKIE,
        IssueKind.MIXED_CONTENT,
        IssueKind.INSECURE_TRANSPORT,
        IssueKind.INFO_DISCLOSURE,
    }
)


@dataclass(frozen=True, slots=True)
class PageVisit:
    """What the browser reports after loading one URL."""

    url: str  # the URL after redirects
    status: int | None
    title: str | None = None
    load_ms: float | None = None
    links: list[str] = field(default_factory=list)  # hrefs found on the page
    buttons: int = 0
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)  # "GET url — reason"
    redirects: int = 0
    error: str | None = None  # the navigation itself failed (DNS, connection, timeout)
    interactions: Interactions | None = None  # button/form probes, when the scan exercised them
    headers: dict[str, str] = field(default_factory=dict)  # main response headers (lowercased)
    set_cookies: list[str] = field(default_factory=list)  # raw Set-Cookie values of the response
    insecure_requests: list[str] = field(default_factory=list)  # http sub-resources, https page
    security: bool = False  # whether the browser gathered security facts for this page
    screenshot: str | None = None  # a JPEG data: URL of the page, when one was captured


class Browser(Protocol):
    def visit(self, url: str) -> PageVisit: ...


def crawl(
    start_url: str,
    browser: Browser,
    *,
    policy: NetworkPolicy | None = None,
    max_pages: int = 100,
    max_depth: int = 5,
) -> SiteScan:
    policy = policy or NetworkPolicy.for_target(start_url)
    frontier = Frontier(policy, max_pages=max_pages, max_depth=max_depth)
    pages: list[Page] = []
    issues: list[PageIssue] = []
    seen_security: set[tuple[str, str]] = set()
    if not frontier.add(start_url, 0):
        reason = policy.refusal(start_url) or "the start URL is not crawlable"
        return SiteScan(start_url=start_url, pages=[], issues=[], denied=list(policy.denied),
                        notes=[f"The start URL could not be crawled: {reason}"])  # fmt: skip

    while (item := frontier.next()) is not None:
        url, depth, from_url = item
        visit = browser.visit(url)
        pages.append(_page(url, visit, depth, from_url))
        for found in _issues(url, visit, from_url):
            # Stamp the page's screenshot onto its issues as visual evidence.
            issue = (
                found.model_copy(update={"screenshot": visit.screenshot})
                if visit.screenshot is not None and found.screenshot is None
                else found
            )
            if issue.kind in SECURITY_KINDS:
                key = (issue.kind, issue.detail)  # the same weakness recurs on every page
                if key in seen_security:
                    continue
                seen_security.add(key)
            issues.append(issue)
        if visit.error is None:
            frontier.add_links(visit.url, visit.links, depth)

    notes = []
    if frontier.seen > len(pages):
        notes.append(
            f"{frontier.seen - len(pages)} more in-scope URLs were found but not visited "
            f"(page budget {max_pages})"
        )
    return SiteScan(
        start_url=start_url, pages=pages, issues=issues, denied=list(policy.denied), notes=notes
    )


def _page(url: str, visit: PageVisit, depth: int, from_url: str | None) -> Page:
    return Page(
        url=url, status=visit.status, title=visit.title, depth=depth, from_url=from_url,
        load_ms=visit.load_ms, links=len(visit.links), buttons=visit.buttons,
        screenshot=visit.screenshot,
    )  # fmt: skip


def _issues(url: str, visit: PageVisit, from_url: str | None) -> list[PageIssue]:
    found: list[PageIssue] = []
    if visit.error is not None:
        source = f" (linked from {from_url})" if from_url else ""
        found.append(PageIssue(
            kind=IssueKind.BROKEN_LINK, severity=IssueSeverity.HIGH, url=url,
            detail=f"The page could not be loaded: {visit.error}{source}",
        ))  # fmt: skip
        return found
    if visit.status is not None and visit.status >= SERVER_ERROR:
        found.append(PageIssue(
            kind=IssueKind.SERVER_ERROR, severity=IssueSeverity.HIGH, url=url,
            detail=f"The server returned {visit.status}",
        ))  # fmt: skip
    elif visit.status is not None and visit.status >= CLIENT_ERROR:
        source = f", linked from {from_url}" if from_url else ""
        found.append(PageIssue(
            kind=IssueKind.NOT_FOUND, severity=IssueSeverity.MEDIUM, url=url,
            detail=f"The server returned {visit.status}{source}",
        ))  # fmt: skip
    if visit.redirects > MAX_REDIRECTS:
        found.append(PageIssue(
            kind=IssueKind.REDIRECT_LOOP, severity=IssueSeverity.MEDIUM, url=url,
            detail=f"{visit.redirects} redirects before the page loaded",
        ))  # fmt: skip
    for message in visit.console_errors:
        found.append(PageIssue(
            kind=IssueKind.JS_ERROR, severity=IssueSeverity.MEDIUM, url=url,
            detail="Uncaught error in the page", evidence=[message],
        ))  # fmt: skip
    if visit.failed_requests:
        found.append(PageIssue(
            kind=IssueKind.FAILED_REQUEST, severity=IssueSeverity.MEDIUM, url=url,
            detail=f"{len(visit.failed_requests)} request(s) the page made failed",
            evidence=visit.failed_requests[:20],
        ))  # fmt: skip
    if visit.load_ms is not None and visit.load_ms > SLOW_MS:
        found.append(PageIssue(
            kind=IssueKind.SLOW_PAGE, severity=IssueSeverity.LOW, url=url,
            detail=f"The page took {visit.load_ms / 1000:.1f}s to load",
        ))  # fmt: skip
    if visit.interactions is not None:
        found.extend(interaction_issues(
            url, visit.interactions.buttons, visit.interactions.forms
        ))  # fmt: skip
    if visit.security:
        found.extend(security_issues(
            visit.url, visit.headers, visit.set_cookies, visit.insecure_requests
        ))  # fmt: skip
    return found

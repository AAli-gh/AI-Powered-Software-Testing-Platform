"""The web-scan report model: severity counts and issue shape."""

from __future__ import annotations

from testmind.web_engine.models import IssueKind, IssueSeverity, Page, PageIssue, SiteScan


def test_by_severity_counts_every_level() -> None:
    scan = SiteScan(
        start_url="https://a.test/",
        pages=[Page(url="https://a.test/", status=200, depth=0, links=3, buttons=1)],
        issues=[
            PageIssue(kind=IssueKind.SERVER_ERROR, severity=IssueSeverity.HIGH, url="https://a.test/x",
                  detail="500"),
            PageIssue(kind=IssueKind.DEAD_BUTTON, severity=IssueSeverity.MEDIUM, url="https://a.test/",
                  detail="Save does nothing", evidence=["clicked #save", "no navigation, no XHR"]),
            PageIssue(kind=IssueKind.JS_ERROR, severity=IssueSeverity.MEDIUM, url="https://a.test/",
                  detail="TypeError"),
        ],
    )  # fmt: skip
    assert scan.by_severity == {"high": 1, "medium": 2, "low": 0}
    assert scan.pages[0].buttons == 1
    assert scan.issues[1].evidence[1] == "no navigation, no XHR"

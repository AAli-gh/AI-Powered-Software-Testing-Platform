"""Active security checks (pure): planning crafted requests and judging the responses.

No request is sent here — the plan is built and canned responses are classified."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from testmind.web_engine.active import (
    MAX_PARAMS,
    REDIRECT_MARKER_HOST,
    Probe,
    Probed,
    active_issues,
    classify,
    plan,
    sql_error_signature,
    xss_marker,
)
from testmind.web_engine.models import IssueKind, IssueSeverity


def test_a_url_without_query_parameters_has_no_probes() -> None:
    assert plan("https://a.test/page") == []


def test_plan_covers_each_parameter_with_xss_and_sql_and_redirect_where_apt() -> None:
    probes = plan("https://a.test/s?q=hello&next=/home")
    checks = {(p.param, p.check) for p in probes}
    assert ("q", "xss") in checks
    assert ("q", "sql") in checks
    assert ("next", "xss") in checks
    assert ("next", "sql") in checks
    assert ("next", "redirect") in checks  # 'next' is a redirect-like name
    assert ("q", "redirect") not in checks  # 'q' is not


def test_sql_probe_appends_a_single_quote_to_the_existing_value() -> None:
    [sql] = [p for p in plan("https://a.test/s?id=42") if p.check == "sql"]
    assert parse_qs(urlsplit(sql.url).query)["id"] == ["42'"]


def test_redirect_probe_points_at_the_marker_host() -> None:
    [probe] = [p for p in plan("https://a.test/go?url=/x") if p.check == "redirect"]
    assert parse_qs(urlsplit(probe.url).query)["url"] == [f"https://{REDIRECT_MARKER_HOST}/"]


def test_plan_caps_the_number_of_parameters() -> None:
    query = "&".join(f"p{i}=1" for i in range(MAX_PARAMS + 5))
    probed_params = {p.param for p in plan(f"https://a.test/s?{query}")}
    assert len(probed_params) == MAX_PARAMS


def test_markers_are_unique_and_break_html() -> None:
    a, b = xss_marker(), xss_marker()
    assert a != b
    assert a.startswith("<tmxss")
    assert a.endswith(">")


def test_reflected_marker_is_an_xss_finding() -> None:
    probe = Probe("https://a.test/s?q=x", "xss", "q", marker="<tmxss1234>")
    issue = classify(probe, Probed(status=200, body="<p>you said <tmxss1234></p>"))
    assert issue is not None
    assert issue.kind is IssueKind.REFLECTED_XSS
    assert issue.severity is IssueSeverity.HIGH
    assert issue.evidence == ["<tmxss1234>"]


def test_an_escaped_marker_is_not_a_finding() -> None:
    probe = Probe("https://a.test/s?q=x", "xss", "q", marker="<tmxss1234>")
    escaped = Probed(status=200, body="<p>you said &lt;tmxss1234&gt;</p>")
    assert classify(probe, escaped) is None


def test_a_database_error_is_a_sql_finding() -> None:
    probe = Probe("https://a.test/s?id=1'", "sql", "id")
    body = 'Traceback: sqlite3.OperationalError: near "\'": syntax error'
    issue = classify(probe, Probed(status=500, body=body))
    assert issue is not None
    assert issue.kind is IssueKind.SQL_ERROR
    assert "sqlite3.OperationalError" in issue.evidence[0]


def test_a_clean_response_is_not_a_sql_finding() -> None:
    probe = Probe("https://a.test/s?id=1'", "sql", "id")
    assert classify(probe, Probed(status=200, body="<h1>Item 1</h1>")) is None


def test_an_off_site_redirect_to_the_marker_is_a_finding() -> None:
    probe = Probe("https://a.test/go?url=...", "redirect", "url")
    issue = classify(probe, Probed(status=302, location=f"https://{REDIRECT_MARKER_HOST}/"))
    assert issue is not None
    assert issue.kind is IssueKind.OPEN_REDIRECT
    assert issue.severity is IssueSeverity.MEDIUM


def test_a_same_site_or_non_redirect_is_not_a_finding() -> None:
    probe = Probe("https://a.test/go?url=...", "redirect", "url")
    assert classify(probe, Probed(status=302, location="https://a.test/home")) is None
    assert classify(probe, Probed(status=200, location=None)) is None


def test_sql_error_signature_matches_several_engines() -> None:
    assert sql_error_signature("You have an error in your SQL syntax near")
    assert sql_error_signature("ORA-01756: quoted string not properly terminated")
    assert sql_error_signature("psycopg2.errors.SyntaxError")
    assert sql_error_signature("nothing wrong here") is None


def test_active_issues_collects_only_the_findings() -> None:
    results = [
        (Probe("u1", "xss", "q", marker="<tmxssAA>"), Probed(200, body="x <tmxssAA> y")),
        (Probe("u2", "sql", "id"), Probed(200, body="all good")),  # no finding
        (Probe("u3", "redirect", "next"), Probed(301, location=f"https://{REDIRECT_MARKER_HOST}/")),
    ]
    kinds = {i.kind for i in active_issues(results)}
    assert kinds == {IssueKind.REFLECTED_XSS, IssueKind.OPEN_REDIRECT}

"""Passive security checks: headers, cookies, mixed content, transport, version disclosure."""

from __future__ import annotations

from collections.abc import Sequence

from testmind.web_engine.models import IssueKind, IssueSeverity
from testmind.web_engine.security import security_issues

# A page that does everything right: no findings.
STRONG = {
    "strict-transport-security": "max-age=63072000",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


def kinds(issues: Sequence[object]) -> set[str]:
    return {i.kind for i in issues}  # type: ignore[attr-defined]


def test_a_well_configured_https_page_has_no_findings() -> None:
    assert security_issues("https://a.test/", STRONG) == []


def test_missing_headers_are_each_reported() -> None:
    issues = security_issues("https://a.test/", {})
    by_detail = {i.detail: i for i in issues}
    assert any("Strict-Transport-Security" in d for d in by_detail)
    assert any("Content-Security-Policy" in d for d in by_detail)
    assert any("nosniff" in d for d in by_detail)
    assert any("clickjacking" in d for d in by_detail)
    assert any("Referrer-Policy" in d for d in by_detail)
    # HSTS is a transport finding; the rest are header findings.
    assert IssueKind.INSECURE_TRANSPORT in kinds(issues)
    assert IssueKind.INSECURE_HEADER in kinds(issues)


def test_frame_ancestors_in_csp_counts_as_clickjacking_protection() -> None:
    headers = {**STRONG}
    del headers["x-frame-options"]  # CSP frame-ancestors still protects
    assert not any("clickjacking" in i.detail for i in security_issues("https://a.test/", headers))


def test_an_http_page_is_flagged_as_insecure_transport() -> None:
    issues = security_issues("http://a.test/", {})
    transport = [i for i in issues if i.kind is IssueKind.INSECURE_TRANSPORT]
    assert len(transport) == 1
    assert "HTTP, not HTTPS" in transport[0].detail
    assert transport[0].severity is IssueSeverity.MEDIUM


def test_insecure_cookies_are_reported_by_missing_attribute() -> None:
    issues = security_issues(
        "https://a.test/",
        STRONG,
        set_cookies=[
            "sid=abc; Path=/",  # no Secure → medium
            "pref=1; Secure",  # Secure but no HttpOnly → low
            "ok=1; Secure; HttpOnly",  # fine
        ],
    )
    cookies = [i for i in issues if i.kind is IssueKind.INSECURE_COOKIE]
    assert {i.severity for i in cookies} == {IssueSeverity.MEDIUM, IssueSeverity.LOW}
    assert any("'sid'" in i.detail and "Secure" in i.detail for i in cookies)
    assert any("'pref'" in i.detail and "HttpOnly" in i.detail for i in cookies)


def test_samesite_none_without_secure_is_flagged() -> None:
    issues = security_issues("https://a.test/", STRONG, set_cookies=["t=1; SameSite=None"])
    assert any("SameSite=None" in i.detail for i in issues)


def test_a_nameless_set_cookie_is_ignored() -> None:
    assert security_issues("https://a.test/", STRONG, set_cookies=["=orphan; Path=/"]) == []


def test_mixed_content_is_high() -> None:
    issues = security_issues(
        "https://a.test/", STRONG, mixed_content=["http://a.test/logo.png", "http://a.test/app.js"]
    )
    [mixed] = [i for i in issues if i.kind is IssueKind.MIXED_CONTENT]
    assert mixed.severity is IssueSeverity.HIGH
    assert mixed.evidence == ["http://a.test/logo.png", "http://a.test/app.js"]


def test_version_disclosure_only_when_a_version_is_present() -> None:
    disclosed = security_issues(
        "https://a.test/", {**STRONG, "server": "nginx/1.25.3", "x-powered-by": "PHP/8.2"}
    )
    revealed = [i for i in disclosed if i.kind is IssueKind.INFO_DISCLOSURE]
    assert len(revealed) == 2
    # A bare product name with no version is not reported.
    quiet = security_issues("https://a.test/", {**STRONG, "server": "cloudflare"})
    assert not any(i.kind is IssueKind.INFO_DISCLOSURE for i in quiet)

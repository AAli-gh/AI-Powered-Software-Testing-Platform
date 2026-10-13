"""Passive security checks: read a page's response and report weak protections.

Nothing here attacks the site. Each check looks at the main document's response headers, the
cookies it set, and whether an https page pulled in http sub-resources, and turns a weakness
into a `PageIssue`. The judgement is pure, so it is unit-tested without a browser; the browser
(browser.py) gathers the facts when a scan asks for security checks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from testmind.web_engine.models import IssueKind, IssueSeverity, PageIssue

MAX_EVIDENCE = 20
_VERSION = re.compile(r"\d")  # a header value carrying a digit is treated as a version disclosure


def _issue(kind: IssueKind, severity: IssueSeverity, url: str, detail: str,
           evidence: list[str] | None = None) -> PageIssue:  # fmt: skip
    return PageIssue(kind=kind, severity=severity, url=url, detail=detail, evidence=evidence or [])


def _cookie(raw: str) -> tuple[str, set[str], str]:
    """A Set-Cookie value → (name, lowercased flag names, samesite value)."""
    parts = [p.strip() for p in raw.split(";")]
    name = parts[0].split("=", 1)[0].strip() if parts else ""
    flags: set[str] = set()
    samesite = ""
    for attr in parts[1:]:
        key, _, value = attr.partition("=")
        key = key.strip().lower()
        flags.add(key)
        if key == "samesite":
            samesite = value.strip().lower()
    return name, flags, samesite


def _cookie_issues(url: str, set_cookies: Sequence[str], *, https: bool) -> list[PageIssue]:
    issues: list[PageIssue] = []
    for raw in set_cookies:
        name, flags, samesite = _cookie(raw)
        if not name:
            continue
        if https and "secure" not in flags:
            issues.append(_issue(
                IssueKind.INSECURE_COOKIE, IssueSeverity.MEDIUM, url,
                f"The cookie {name!r} is set without the Secure attribute",
            ))  # fmt: skip
        elif "httponly" not in flags:
            issues.append(_issue(
                IssueKind.INSECURE_COOKIE, IssueSeverity.LOW, url,
                f"The cookie {name!r} is set without the HttpOnly attribute",
            ))  # fmt: skip
        if samesite == "none" and "secure" not in flags:
            issues.append(_issue(
                IssueKind.INSECURE_COOKIE, IssueSeverity.MEDIUM, url,
                f"The cookie {name!r} is SameSite=None but not Secure",
            ))  # fmt: skip
    return issues


def security_issues(
    url: str,
    headers: Mapping[str, str],
    set_cookies: Sequence[str] = (),
    mixed_content: Sequence[str] = (),
) -> list[PageIssue]:
    """Security findings for one page. `headers` keys are lowercased."""
    https = url.lower().startswith("https://")
    issues: list[PageIssue] = []

    if not https:
        issues.append(_issue(
            IssueKind.INSECURE_TRANSPORT, IssueSeverity.MEDIUM, url,
            "The page is served over HTTP, not HTTPS",
        ))  # fmt: skip
    elif "strict-transport-security" not in headers:
        issues.append(_issue(
            IssueKind.INSECURE_TRANSPORT, IssueSeverity.MEDIUM, url,
            "No Strict-Transport-Security response header (HSTS)",
        ))  # fmt: skip

    csp = headers.get("content-security-policy", "")
    if not csp:
        issues.append(_issue(
            IssueKind.INSECURE_HEADER, IssueSeverity.MEDIUM, url,
            "No Content-Security-Policy response header",
        ))  # fmt: skip
    if headers.get("x-content-type-options", "").lower() != "nosniff":
        issues.append(_issue(
            IssueKind.INSECURE_HEADER, IssueSeverity.LOW, url,
            "No X-Content-Type-Options: nosniff response header",
        ))  # fmt: skip
    if "x-frame-options" not in headers and "frame-ancestors" not in csp.lower():
        issues.append(_issue(
            IssueKind.INSECURE_HEADER, IssueSeverity.LOW, url,
            "No clickjacking protection (X-Frame-Options or CSP frame-ancestors)",
        ))  # fmt: skip
    if "referrer-policy" not in headers:
        issues.append(_issue(
            IssueKind.INSECURE_HEADER, IssueSeverity.LOW, url,
            "No Referrer-Policy response header",
        ))  # fmt: skip

    issues.extend(_cookie_issues(url, set_cookies, https=https))

    if mixed_content:
        issues.append(_issue(
            IssueKind.MIXED_CONTENT, IssueSeverity.HIGH, url,
            f"An HTTPS page loaded {len(mixed_content)} resource(s) over HTTP",
            list(mixed_content)[:MAX_EVIDENCE],
        ))  # fmt: skip

    for header in ("server", "x-powered-by"):
        value = headers.get(header, "")
        if value and _VERSION.search(value):
            issues.append(_issue(
                IssueKind.INFO_DISCLOSURE, IssueSeverity.LOW, url,
                f"The {header!r} response header reveals {value!r}",
            ))  # fmt: skip

    return issues

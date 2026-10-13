"""Active security checks: craft a request to a URL's parameters and read the reply.

These run **only** against a host whose ownership has been verified, and only when a scan opts
in. Nothing here is destructive: each check uses GET, injects either a unique harmless marker
(not a working script) or a single quote, and only reads the response — it never writes data,
calls a destructive endpoint, or sends a real exploit. The planning and judgement are pure and
unit-tested; the service sends the planned requests through the network policy.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from testmind.web_engine.models import IssueKind, IssueSeverity, PageIssue

MAX_PARAMS = 10  # query parameters probed on one URL
REDIRECT_MARKER_HOST = "testmind-redirect.example"
REDIRECT_STATUS_MIN = 300
REDIRECT_STATUS_MAX = 400

# Parameter names that commonly carry a post-action destination (open-redirect candidates).
REDIRECT_PARAM_NAMES = frozenset(
    {
        "next", "url", "redirect", "redirect_uri", "redirecturl", "return", "returnurl",
        "return_to", "returnto", "dest", "destination", "continue", "goto", "to", "target",
    }
)  # fmt: skip

# Signatures of a database error surfacing in the response body (a single quote broke a query).
_SQL_ERRORS = re.compile(
    "|".join(
        [
            r"you have an error in your sql syntax",
            r"warning:\s*\w*_?mysqli?",
            r"unclosed quotation mark after the character string",
            r"quoted string not properly terminated",
            r"sqlite3?\.(operational|programming)error",
            r"sqlite_error",
            r"psycopg2\.",
            r"pg::\w*error",
            r"postgresql.{0,40}error",
            r"ora-\d{5}",
            r"sqlstate\[",
            r'near ".*": syntax error',
            r"system\.data\.sqlclient",
            r"org\.hibernate",
            r"unterminated quoted string",
        ]
    ),
    re.IGNORECASE,
)


def xss_marker() -> str:
    """A unique, HTML-breaking but harmless marker. If it comes back with its angle brackets
    intact, the parameter is reflected unescaped (a real payload would then run)."""
    return f"<tmxss{secrets.token_hex(4)}>"


def sql_error_signature(body: str) -> str | None:
    match = _SQL_ERRORS.search(body)
    return match.group(0) if match else None


@dataclass(frozen=True, slots=True)
class Probe:
    """One crafted request the service should send."""

    url: str  # the mutated URL
    check: str  # "xss" | "sql" | "redirect"
    param: str  # the parameter that was mutated
    marker: str = ""  # the injected XSS marker, for the reflection check


@dataclass(frozen=True, slots=True)
class Probed:
    """What a check reads back from a probe's response."""

    status: int
    body: str = ""
    location: str | None = None


def plan(url: str) -> list[Probe]:
    """The probes for one URL: nothing is planned for a URL without query parameters."""
    parts = urlsplit(url)
    params = parse_qsl(parts.query, keep_blank_values=True)
    first: dict[str, str] = {}
    for name, value in params:
        first.setdefault(name, value)
    probes: list[Probe] = []
    for name in list(first)[:MAX_PARAMS]:
        marker = xss_marker()
        probes.append(Probe(_mutate(parts, params, name, marker), "xss", name, marker))
        probes.append(Probe(_mutate(parts, params, name, first[name] + "'"), "sql", name))
        if name.lower() in REDIRECT_PARAM_NAMES:
            target = f"https://{REDIRECT_MARKER_HOST}/"
            probes.append(Probe(_mutate(parts, params, name, target), "redirect", name))
    return probes


def _mutate(parts: tuple[str, ...], params: list[tuple[str, str]], name: str, value: str) -> str:
    mutated = [(n, value if n == name else v) for n, v in params]
    scheme, netloc, path = parts[0], parts[1], parts[2]
    return urlunsplit((scheme, netloc, path, urlencode(mutated), parts[4]))


def classify(probe: Probe, response: Probed) -> PageIssue | None:
    if probe.check == "xss":
        if probe.marker and probe.marker in response.body:
            return PageIssue(
                kind=IssueKind.REFLECTED_XSS, severity=IssueSeverity.HIGH, url=probe.url,
                detail=f"The parameter {probe.param!r} is reflected into the page unescaped",
                evidence=[probe.marker],
            )  # fmt: skip
        return None
    if probe.check == "sql":
        signature = sql_error_signature(response.body)
        if signature is not None:
            return PageIssue(
                kind=IssueKind.SQL_ERROR, severity=IssueSeverity.HIGH, url=probe.url,
                detail=f"A single quote in {probe.param!r} triggered a database error",
                evidence=[signature],
            )  # fmt: skip
        return None
    if _is_external_redirect(response):
        return PageIssue(
            kind=IssueKind.OPEN_REDIRECT, severity=IssueSeverity.MEDIUM, url=probe.url,
            detail=f"The parameter {probe.param!r} redirects off-site",
            evidence=[response.location or ""],
        )  # fmt: skip
    return None


def _is_external_redirect(response: Probed) -> bool:
    if not (REDIRECT_STATUS_MIN <= response.status < REDIRECT_STATUS_MAX and response.location):
        return False
    host = urlsplit(response.location).netloc.lower()
    return host == REDIRECT_MARKER_HOST or host.endswith(f".{REDIRECT_MARKER_HOST}")


def active_issues(results: list[tuple[Probe, Probed]]) -> list[PageIssue]:
    found = [classify(probe, response) for probe, response in results]
    return [issue for issue in found if issue is not None]

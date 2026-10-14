"""What a web scan produces: the pages it reached and the issues it found."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class IssueSeverity(StrEnum):
    HIGH = "high"  # a broken page or a server error: the user hits a wall
    MEDIUM = "medium"  # a dead button, a failed request, a console error
    LOW = "low"  # a smell worth noting (a slow page, a redirect chain)


class IssueKind(StrEnum):
    SERVER_ERROR = "server_error"  # a 5xx response
    NOT_FOUND = "not_found"  # a 4xx response for a link the site offers
    BROKEN_LINK = "broken_link"  # a link that does not resolve
    REDIRECT_LOOP = "redirect_loop"
    DEAD_BUTTON = "dead_button"  # a control that does nothing when activated
    JS_ERROR = "js_error"  # an uncaught exception in the page
    FAILED_REQUEST = "failed_request"  # a network request the page made that failed
    FORM_ERROR = "form_error"  # a form that could not be submitted or errored on the server
    SLOW_PAGE = "slow_page"
    # Passive security findings (no attack is performed; these read the response only).
    INSECURE_HEADER = "insecure_header"  # a missing or weak security response header
    INSECURE_COOKIE = "insecure_cookie"  # a cookie set without Secure / HttpOnly / SameSite
    MIXED_CONTENT = "mixed_content"  # an https page loads an http sub-resource
    INSECURE_TRANSPORT = "insecure_transport"  # the page is served over http, or HSTS is absent
    INFO_DISCLOSURE = "info_disclosure"  # a response header reveals server/software versions
    # Active findings (crafted requests; only ever against an ownership-verified host).
    REFLECTED_XSS = "reflected_xss"  # a parameter is reflected into the page unescaped
    SQL_ERROR = "sql_error"  # a parameter makes the server emit a database error
    OPEN_REDIRECT = "open_redirect"  # a parameter controls a redirect to an external host


class PageIssue(_Model):
    kind: IssueKind
    severity: IssueSeverity
    url: str = Field(description="Where the issue was found")
    detail: str
    evidence: list[str] = Field(
        default_factory=list, description="Console messages, request lines, or steps to reproduce"
    )
    screenshot: str | None = Field(
        default=None,
        description="A JPEG data: URL of the page the issue was found on, when the scan "
        "captured one — visual evidence of where the issue is",
    )


class Page(_Model):
    url: str
    status: int | None = Field(description="HTTP status of the main response, if one was seen")
    title: str | None = None
    depth: int = Field(description="Clicks from the start page")
    from_url: str | None = Field(default=None, description="The page a link to this one came from")
    load_ms: float | None = None
    links: int = 0
    buttons: int = 0
    screenshot: str | None = Field(
        default=None, description="A JPEG data: URL of the page, when the scan captured one"
    )


class SiteScan(_Model):
    start_url: str
    pages: list[Page]
    issues: list[PageIssue]
    denied: list[str] = Field(
        default_factory=list, description="URLs the network policy refused (out of scope, internal)"
    )
    notes: list[str] = Field(default_factory=list)

    @property
    def by_severity(self) -> dict[str, int]:
        counts = dict.fromkeys(IssueSeverity, 0)
        for issue in self.issues:
            counts[issue.severity] += 1
        return {s.value: counts[s] for s in IssueSeverity}

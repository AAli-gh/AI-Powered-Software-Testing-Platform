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


class PageIssue(_Model):
    kind: IssueKind
    severity: IssueSeverity
    url: str = Field(description="Where the issue was found")
    detail: str
    evidence: list[str] = Field(
        default_factory=list, description="Console messages, request lines, or steps to reproduce"
    )
    screenshot: str | None = Field(
        default=None, description="Published path of a screenshot, when one was captured"
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

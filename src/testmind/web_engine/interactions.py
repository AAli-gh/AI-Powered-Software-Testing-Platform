"""Exercising a page's controls: which buttons do nothing, and which forms break.

The browser (browser.py) clicks each safe button and submits each form, recording what happened
as a `ButtonProbe` / `FormProbe`. The judgement here is pure: a button that navigated nowhere,
sent no request and changed nothing on the page is **dead**; a form that errored on submit or
was rejected without telling the user is a problem. Dangerous controls (delete, pay, log out)
are never clicked — their labels are matched here so the browser can skip them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from testmind.web_engine.models import Issue, IssueKind, Severity

# A control whose label matches is not activated automatically: it may change or destroy data.
DANGER_WORDS = (
    "delete", "remove", "destroy", "pay", "buy", "checkout", "order", "purchase", "subscribe",
    "confirm", "submit order", "log out", "logout", "sign out", "deactivate", "cancel account",
    "transfer", "withdraw", "send money",
)  # fmt: skip
_DANGER = re.compile("|".join(re.escape(word) for word in DANGER_WORDS))


def is_dangerous(label: str) -> bool:
    return bool(_DANGER.search(label.lower()))


@dataclass(frozen=True, slots=True)
class ButtonProbe:
    label: str  # the button's visible text or accessible name
    navigated: bool = False  # the URL changed
    requested: bool = False  # the page sent a network request
    changed_dom: bool = False  # the page's DOM changed
    errored: str | None = None  # clicking raised a page error
    skipped_dangerous: bool = False  # not clicked (a destructive label)

    @property
    def had_effect(self) -> bool:
        return self.navigated or self.requested or self.changed_dom


@dataclass(frozen=True, slots=True)
class FormProbe:
    name: str  # the form's name, id or action
    submitted: bool
    status: int | None = None  # the response status after submit, if a request was seen
    error: str | None = None  # the submit could not be performed
    accepted_empty: bool = False  # a required-looking form accepted an empty submission


def button_issue(url: str, probe: ButtonProbe) -> Issue | None:
    if probe.skipped_dangerous:
        return None
    if probe.errored is not None:
        return Issue(
            kind=IssueKind.JS_ERROR, severity=Severity.MEDIUM, url=url,
            detail=f"Clicking {probe.label!r} raised an error", evidence=[probe.errored],
        )  # fmt: skip
    if not probe.had_effect:
        return Issue(
            kind=IssueKind.DEAD_BUTTON, severity=Severity.MEDIUM, url=url,
            detail=f"The control {probe.label!r} does nothing when clicked",
            evidence=["no navigation, no network request, no change to the page"],
        )  # fmt: skip
    return None


def form_issue(url: str, probe: FormProbe) -> Issue | None:
    if probe.error is not None:
        return Issue(
            kind=IssueKind.FORM_ERROR, severity=Severity.MEDIUM, url=url,
            detail=f"The form {probe.name!r} could not be submitted", evidence=[probe.error],
        )  # fmt: skip
    if probe.status is not None and probe.status >= 500:  # noqa: PLR2004
        return Issue(
            kind=IssueKind.FORM_ERROR, severity=Severity.HIGH, url=url,
            detail=f"Submitting the form {probe.name!r} returned {probe.status}",
        )  # fmt: skip
    return None


def interaction_issues(url: str, buttons: list[ButtonProbe], forms: list[FormProbe]) -> list[Issue]:
    issues = [button_issue(url, b) for b in buttons] + [form_issue(url, f) for f in forms]
    return [i for i in issues if i is not None]


@dataclass(frozen=True, slots=True)
class Interactions:
    """The control probes gathered on one page (carried on a PageVisit)."""

    buttons: list[ButtonProbe] = field(default_factory=list)
    forms: list[FormProbe] = field(default_factory=list)

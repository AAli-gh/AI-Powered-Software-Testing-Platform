"""The pure judgement of a page's controls: dead buttons, dangerous labels, broken forms.

The browser (browser.py) clicks and submits; this module decides what the result means. The
logic is tested here with hand-built probes, no browser involved."""

from __future__ import annotations

import pytest

from testmind.web_engine.interactions import (
    ButtonProbe,
    FormProbe,
    Interactions,
    button_issue,
    form_issue,
    interaction_issues,
    is_dangerous,
)
from testmind.web_engine.models import IssueKind, IssueSeverity


@pytest.mark.parametrize(
    "label",
    ["Delete", "remove item", "Pay now", "Log out", "Checkout", "Withdraw funds", "CANCEL ACCOUNT"],
)
def test_dangerous_labels_are_recognised(label: str) -> None:
    assert is_dangerous(label)


@pytest.mark.parametrize("label", ["Show more", "Next", "Add to cart", "Search", "Go to About"])
def test_ordinary_labels_are_safe(label: str) -> None:
    assert not is_dangerous(label)


def test_had_effect_is_any_observable_change() -> None:
    assert not ButtonProbe("x").had_effect
    assert ButtonProbe("x", navigated=True).had_effect
    assert ButtonProbe("x", requested=True).had_effect
    assert ButtonProbe("x", changed_dom=True).had_effect


def test_a_button_with_no_effect_is_dead() -> None:
    issue = button_issue("https://a.test/", ButtonProbe("Does nothing"))
    assert issue is not None
    assert issue.kind is IssueKind.DEAD_BUTTON
    assert issue.severity is IssueSeverity.MEDIUM
    assert "Does nothing" in issue.detail
    assert issue.evidence  # says what was observed (nothing)


def test_a_button_that_does_something_is_fine() -> None:
    assert button_issue("https://a.test/", ButtonProbe("Go", navigated=True)) is None
    assert button_issue("https://a.test/", ButtonProbe("Load", requested=True)) is None
    assert button_issue("https://a.test/", ButtonProbe("Expand", changed_dom=True)) is None


def test_a_button_that_errors_is_a_js_error_not_a_dead_button() -> None:
    issue = button_issue("https://a.test/", ButtonProbe("Oops", errored="TypeError: x"))
    assert issue is not None
    assert issue.kind is IssueKind.JS_ERROR
    assert issue.evidence == ["TypeError: x"]


def test_a_dangerous_button_is_never_flagged() -> None:
    # It was not clicked, so "no effect" must not be reported as a dead button.
    assert button_issue("https://a.test/", ButtonProbe("Delete", skipped_dangerous=True)) is None


def test_a_form_that_could_not_be_submitted_is_reported() -> None:
    issue = form_issue("https://a.test/", FormProbe("signup", submitted=False, error="no submit"))
    assert issue is not None
    assert issue.kind is IssueKind.FORM_ERROR
    assert issue.severity is IssueSeverity.MEDIUM
    assert issue.evidence == ["no submit"]


def test_a_form_that_returns_a_server_error_is_high() -> None:
    issue = form_issue("https://a.test/", FormProbe("signup", submitted=True, status=500))
    assert issue is not None
    assert issue.kind is IssueKind.FORM_ERROR
    assert issue.severity is IssueSeverity.HIGH
    assert "500" in issue.detail


def test_a_form_that_submits_cleanly_is_fine() -> None:
    assert form_issue("https://a.test/", FormProbe("signup", submitted=True, status=200)) is None
    assert form_issue("https://a.test/", FormProbe("signup", submitted=True, status=302)) is None


def test_interaction_issues_collects_only_the_problems() -> None:
    buttons = [
        ButtonProbe("Dead"),  # flagged
        ButtonProbe("Fine", navigated=True),  # not flagged
        ButtonProbe("Delete", skipped_dangerous=True),  # not flagged
    ]
    forms = [
        FormProbe("ok", submitted=True, status=200),  # not flagged
        FormProbe("boom", submitted=True, status=503),  # flagged
    ]
    issues = interaction_issues("https://a.test/", buttons, forms)
    kinds = sorted(i.kind for i in issues)
    assert kinds == [IssueKind.DEAD_BUTTON, IssueKind.FORM_ERROR]


def test_interactions_defaults_to_empty() -> None:
    empty = Interactions()
    assert empty.buttons == []
    assert empty.forms == []

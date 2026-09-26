"""Compare the test results and failure causes of two runs."""

from __future__ import annotations

from testmind.failure_engine import FailureGroup, FailureReport
from testmind.regression.models import (
    FailureChanges,
    FailureGroupChange,
    RegressionMode,
    Selection,
    SelectionCheck,
    TestChange,
    TestChangeKind,
)
from testmind.regression.selection import is_selected
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport

FAILING = frozenset({Outcome.FAILED, Outcome.ERROR})


def compare_tests(
    base: list[TestCaseReport],
    head: list[TestCaseReport],
    *,
    selection: Selection,
    mode: RegressionMode,
    head_test_files: set[str],
) -> list[TestChange]:
    before = {t.nodeid: t.outcome for t in base}
    after = {t.nodeid: t.outcome for t in head}
    changes = []
    for nodeid in sorted(before.keys() | after.keys()):
        old, new = before.get(nodeid), after.get(nodeid)
        selected = selection.everything or is_selected(selection, nodeid)
        changes.append(
            TestChange(
                nodeid=nodeid,
                base=old,
                head=new,
                change=_kind(old, new, nodeid, mode=mode, head_test_files=head_test_files),
                selected=selected,
            )
        )
    return changes


def _kind(
    old: Outcome | None,
    new: Outcome | None,
    nodeid: str,
    *,
    mode: RegressionMode,
    head_test_files: set[str],
) -> TestChangeKind:
    if new is None:
        exists = nodeid.partition("::")[0] in head_test_files
        if mode is RegressionMode.SELECTED and exists:
            return TestChangeKind.NOT_RUN
        return TestChangeKind.REMOVED
    if old is None:
        return TestChangeKind.ADDED
    if new in FAILING:
        return TestChangeKind.STILL_FAILING if old in FAILING else TestChangeKind.REGRESSED
    return TestChangeKind.FIXED if old in FAILING else TestChangeKind.UNCHANGED


def compare_failures(base: FailureReport | None, head: FailureReport | None) -> FailureChanges:
    """By stable group id (category, exception, location). A group whose code moved to another
    line gets a new id: it then shows as resolved at base and new at head."""
    before = {g.id: g for g in (base.groups if base else [])}
    after = {g.id: g for g in (head.groups if head else [])}
    return FailureChanges(
        new=[_group(after[i]) for i in after if i not in before],
        resolved=[_group(before[i]) for i in before if i not in after],
        persisting=[_group(after[i]) for i in after if i in before],
    )


def check_selection(changes: list[TestChange], total_tests: int) -> SelectionCheck:
    regressed = [c for c in changes if c.change is TestChangeKind.REGRESSED]
    return SelectionCheck(
        regressed=len(regressed),
        missed=[c.nodeid for c in regressed if not c.selected],
        selected_tests=sum(1 for c in changes if c.selected and c.head is not None),
        total_tests=total_tests,
    )


def _group(group: FailureGroup) -> FailureGroupChange:
    crash = f"{group.crash.path}:{group.crash.line}" if group.crash else None
    return FailureGroupChange(
        id=group.id,
        category=group.category.value,
        exception_type=group.exception_type,
        crash=crash,
        tests=len(group.tests),
    )

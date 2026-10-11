"""Fault localisation for each failure group of a run.

A group is one failure (the same exception at the same place), so its failing tests are the
group's tests, against every test that passed. The traceback's production frames are mapped
to the functions of the code analysis, innermost first.
"""

from __future__ import annotations

from collections import defaultdict

from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.coverage_engine.impact import TestImpact
from testmind.failure_engine.models import FailureGroup, FailureReport, Frame, FrameLocation
from testmind.fault_localization.ranking import localize
from testmind.fault_localization.spectrum import Spectrum
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport


def localize_failures(
    failures: FailureReport, spectrum: Spectrum, tests: list[TestCaseReport], code: CodeProfile
) -> FailureReport:
    passing = {t.nodeid for t in tests if t.outcome is Outcome.PASSED}
    functions = _functions_by_path(code)
    groups = [
        group.model_copy(
            update={
                "localization": localize(
                    spectrum,
                    {t.nodeid for t in group.tests},
                    passing,
                    traceback=_traceback(group, functions),
                )
            }
        )
        for group in failures.groups
    ]
    return failures.model_copy(update={"groups": groups})


def _functions_by_path(code: CodeProfile) -> dict[str, list[FunctionInfo]]:
    by_path: dict[str, list[FunctionInfo]] = defaultdict(list)
    for function in code.functions:
        by_path[function.path].append(function)
    return by_path


def _traceback(group: FailureGroup, functions: dict[str, list[FunctionInfo]]) -> list[str]:
    """'path::qualname' of the group's production frames, innermost first."""
    keys = []
    for frame in reversed(group.frames):
        if frame.location is FrameLocation.PRODUCTION:
            function = _innermost(frame, functions.get(frame.path, []))
            if function is not None:
                keys.append(TestImpact.key(function.path, function.qualname))
    return keys


def _innermost(frame: Frame, functions: list[FunctionInfo]) -> FunctionInfo | None:
    containing = [f for f in functions if f.line <= frame.line <= f.end_line]
    return min(containing, key=lambda f: f.end_line - f.line, default=None)

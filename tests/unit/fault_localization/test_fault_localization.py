"""Spectrum-based fault localisation: formulas by hand, ranking rules, the stored spectrum."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer
from testmind.coverage_engine.impact import TestImpact
from testmind.failure_engine.models import (
    FailedTest,
    FailureCategory,
    FailureGroup,
    FailureReport,
    Frame,
    FrameLocation,
)
from testmind.fault_localization import (
    FORMULAS,
    Confidence,
    Spectrum,
    dstar,
    localize,
    ochiai,
    tarantula,
)
from testmind.fault_localization.failures import localize_failures
from testmind.fault_localization.spectrum import SpectrumError
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport
from tests.unit.analyzers.helpers import make_repo


def test_formulas_by_hand() -> None:
    # 2 of 2 failing tests and 1 of 3 passing tests executed the function.
    assert ochiai(2, 1, 2, 3) == pytest.approx(2 / math.sqrt(2 * 3))
    assert dstar(2, 1, 2, 3) == pytest.approx(4 / 1)
    assert tarantula(2, 1, 2, 3) == pytest.approx(1 / (1 + 1 / 3))
    # Not executed by a failing test: never suspicious.
    assert {name: f(0, 3, 2, 3) for name, f in FORMULAS.items()} == {
        "ochiai": 0.0, "dstar": 0.0, "tarantula": 0.0,
    }  # fmt: skip
    assert dstar(2, 0, 2, 3) == math.inf  # every failing test, no passing one
    assert ochiai(1, 0, 0, 0) == 0.0  # no failing tests at all
    assert tarantula(1, 0, 0, 0) == 0.0
    assert tarantula(1, 0, 1, 0) == 1.0  # no passing tests


def spectrum(functions: dict[str, set[str]]) -> Spectrum:
    tests = sorted({t for ts in functions.values() for t in ts})
    index = {t: i for i, t in enumerate(tests)}
    return Spectrum(
        tuple(tests),
        {key: (10, frozenset(index[t] for t in ts)) for key, ts in functions.items()},
    )


FAILING = {"t.py::test_f1", "t.py::test_f2"}
PASSING = {"t.py::test_p1", "t.py::test_p2", "t.py::test_p3"}
APP = spectrum(
    {
        "app.py::buggy": {"t.py::test_f1", "t.py::test_f2", "t.py::test_p1"},
        "app.py::helper": FAILING | PASSING,
        "app.py::other": {"t.py::test_p2"},
    }
)


def test_an_assertion_failure_is_localised_by_the_spectrum() -> None:
    """No traceback frame in production code (an assertion in the test): the spectrum alone
    ranks the function only the failing tests (and one passing test) executed first."""
    result = localize(APP, FAILING, PASSING)
    assert [(s.qualname, s.rank, s.score, s.failed, s.passed) for s in result.suspects] == [
        ("buggy", 1, round(2 / math.sqrt(6), 4), 2, 1),
        ("helper", 2, round(2 / math.sqrt(10), 4), 2, 3),
    ]  # `other` was executed by no failing test: not a suspect
    assert result.confidence is Confidence.HIGH
    assert (result.failing_tests, result.passing_tests) == (2, 3)
    assert result.suspects[0].path == "app.py"
    assert result.suspects[0].line == 10


def test_traceback_frames_come_first_innermost_first() -> None:
    frames = [("app.py::helper", 10), ("app.py::helper", 10)]
    result = localize(APP, FAILING, PASSING, traceback=frames)
    assert [(s.qualname, s.rank, s.in_traceback) for s in result.suspects] == [
        ("helper", 1, True),
        ("buggy", 2, False),
    ]
    assert result.confidence is Confidence.HIGH
    assert result.notes == []


def test_a_traceback_frame_is_a_suspect_even_without_coverage() -> None:
    """Regression (PySnooper-3 on BugsInPy): the project installs its own tracer, so coverage
    records nothing of the functions it runs, yet the traceback proves they ran."""
    frames = [("app.py::hidden", 42), ("app.py::helper", 10)]
    result = localize(APP, FAILING, PASSING, traceback=frames)
    hidden = result.suspects[0]
    assert (hidden.qualname, hidden.line, hidden.rank, hidden.in_traceback) == (
        "hidden",
        42,
        1,
        True,
    )
    assert (hidden.failed, hidden.passed, hidden.score) == (2, 0, 1.0)
    assert [s.qualname for s in result.suspects[1:]] == ["helper", "buggy"]
    assert result.notes == [
        "1 function(s) of the traceback had no coverage for the failing tests (another tracer "
        "may have hidden them); counted as executed by them"
    ]
    # No failing test has coverage at all: the traceback still ranks.
    alone = localize(APP, {"t.py::unknown"}, PASSING, traceback=[("app.py::other", 5)])
    assert [(s.qualname, s.failed, s.passed) for s in alone.suspects] == [("other", 1, 1)]
    assert alone.confidence is Confidence.HIGH


def test_ties_get_the_worst_rank_and_lower_the_confidence() -> None:
    two = spectrum({"m.py::a": {"t.py::f"}, "m.py::b": {"t.py::f"}})
    result = localize(two, {"t.py::f"}, set())
    assert [(s.qualname, s.rank) for s in result.suspects] == [("a", 2), ("b", 2)]
    assert result.confidence is Confidence.MEDIUM
    many = spectrum({f"m.py::f{i}": {"t.py::f"} for i in range(5)})
    crowded = localize(many, {"t.py::f"}, set(), limit=3)
    # cut at the limit, but the rank still says that five functions share the score
    assert [s.rank for s in crowded.suspects] == [5, 5, 5]
    assert crowded.confidence is Confidence.LOW


def test_limit_and_no_failing_coverage() -> None:
    ranked = spectrum(
        {f"m.py::f{i}": {"t.py::f"} | {f"t.py::p{j}" for j in range(i)} for i in range(4)}
    )
    passing = {f"t.py::p{j}" for j in range(4)}
    assert [s.qualname for s in localize(ranked, {"t.py::f"}, passing, limit=2).suspects] == [
        "f0", "f1",
    ]  # fmt: skip
    empty = localize(APP, {"t.py::unknown"}, PASSING)
    assert (empty.suspects, empty.confidence, empty.failing_tests) == ([], Confidence.NONE, 0)
    assert empty.notes == ["No failing test has per-test coverage, so there is nothing to rank"]


def test_failing_tests_that_executed_no_production_function() -> None:
    result = localize(APP, {"t.py::test_p2"}, set())
    assert [(s.qualname, s.rank) for s in result.suspects] == [("helper", 2), ("other", 2)]
    nothing = spectrum({"app.py::f": {"t.py::p"}, "app.py::g": set()})
    lonely = localize(Spectrum(("t.py::p", "t.py::f"), nothing.functions), {"t.py::f"}, {"t.py::p"})
    assert (lonely.suspects, lonely.confidence, lonely.failing_tests) == ([], Confidence.NONE, 1)


def test_a_failing_test_is_never_counted_as_passing() -> None:
    result = localize(APP, FAILING, PASSING | FAILING)
    assert result.passing_tests == 3


def test_spectrum_from_coverage_contexts(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"app/m.py": "def f():\n    return 1\n\n\ndef g():\n    return 2\n"})
    code = CodeAnalyzer().analyze(root, ["app/m.py"])
    impact = TestImpact(
        functions={
            # contexts name the class; TestMind's reports do not
            "app/m.py::f": ["tests/t.py::TestF::test_f", "tests/t.py::test_both"],
            "app/m.py::g": ["tests/t.py::test_both"],
            "app/m.py::vanished": ["tests/t.py::test_both"],  # not in the code analysis
        },
        tests=2,
    )
    built = Spectrum.build(impact, code)
    assert built.tests == ("tests/t.py::test_both", "tests/t.py::test_f")
    assert built.executed_by("app/m.py::f") == {"tests/t.py::test_f", "tests/t.py::test_both"}
    assert built.executed_by("app/m.py::nothing") == frozenset()
    assert (built.functions["app/m.py::g"][0], built.functions["app/m.py::vanished"][0]) == (5, 0)
    assert Spectrum.from_json(built.to_json()) == built


@pytest.mark.parametrize(
    "data",
    [
        {"functions": {}},
        {"tests": ["a"], "functions": {"k": [1]}},
        {"tests": ["a"], "functions": {"k": ["x", [0]]}},
        {"tests": ["a"], "functions": {"k": [1, [1]]}},  # index out of range
    ],
)
def test_invalid_stored_spectra_are_refused(data: dict[str, object]) -> None:
    with pytest.raises(SpectrumError):
        Spectrum.from_json(data)


def test_failure_groups_get_their_ranking_with_traceback_first(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "app/m.py": (
                "X = 1\n\n\ndef f():\n    def inner():\n        return 1 / 0\n    return inner()\n"
            )
        },
    )
    code = CodeAnalyzer().analyze(root, ["app/m.py"])

    def frame(line: int, location: FrameLocation = FrameLocation.PRODUCTION) -> Frame:
        return Frame(path="app/m.py", line=line, function="f", code="", location=location)

    group = FailureGroup(
        id="g", category=FailureCategory.EXCEPTION_IN_CODE, exception_type="ZeroDivisionError",
        message="", crash=None, raised_at=None,
        frames=[frame(1, FrameLocation.TEST), frame(1), frame(7), frame(6)],  # outermost first
        locals={}, chain=[], function=None, evidence=[], blame=None, blame_reason=None,
        tests=[FailedTest(nodeid="t.py::test_f", file="t.py", name="test_f", line=None,
                          phase="call", generated=False)],
    )  # fmt: skip
    report = FailureReport(
        groups=[group], failed_tests=1, analysed_tests=1, unanalysed_tests=[], notes=[]
    )
    tests = [
        TestCaseReport(nodeid=n, file="t.py", line=None, name=n, outcome=o, duration_seconds=0,
                       message=None, traceback=None, generated=False)
        for n, o in (("t.py::test_f", Outcome.FAILED), ("t.py::test_ok", Outcome.PASSED))
    ]  # fmt: skip
    executed = spectrum(
        {
            "app/m.py::f": {"t.py::test_f", "t.py::test_ok"},
            "app/m.py::f.<locals>.inner": {"t.py::test_f"},
        }
    )

    [located] = localize_failures(report, executed, tests, code).groups

    assert located.localization is not None
    # line 6 is in inner (innermost function), line 7 in f; line 1 is module level: no function
    assert [(s.qualname, s.rank, s.in_traceback) for s in located.localization.suspects] == [
        ("f.<locals>.inner", 1, True),
        ("f", 2, True),
    ]

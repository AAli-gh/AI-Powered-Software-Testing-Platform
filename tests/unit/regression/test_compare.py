from testmind.analyzers.code import CodeAnalyzer
from testmind.analyzers.repository import RepositoryAnalyzer
from testmind.failure_engine import FailureAnalyzer, FailureReport, parse_failures_json
from testmind.regression.compare import check_selection, compare_failures, compare_tests
from testmind.regression.models import (
    RegressionMode,
    SelectedTarget,
    Selection,
    TestChangeKind,
)
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport
from tests.unit.failure_engine.test_analyzer import CAPTURE, EXAMPLE, failed_tests


def case(nodeid: str, outcome: Outcome) -> TestCaseReport:
    file, _, name = nodeid.partition("::")
    return TestCaseReport(
        nodeid=nodeid, file=file, line=1, name=name, outcome=outcome, duration_seconds=0,
        message=None, traceback=None, generated=False,
    )  # fmt: skip


BASE = [
    case("t.py::stays_green", Outcome.PASSED),
    case("t.py::breaks", Outcome.PASSED),
    case("t.py::gets_fixed", Outcome.FAILED),
    case("t.py::stays_red", Outcome.ERROR),
    case("t.py::skipped_then_fails", Outcome.SKIPPED),
    case("gone.py::deleted", Outcome.PASSED),
    case("t.py::not_selected", Outcome.PASSED),
]
HEAD = [
    case("t.py::stays_green", Outcome.PASSED),
    case("t.py::breaks", Outcome.FAILED),
    case("t.py::gets_fixed", Outcome.PASSED),
    case("t.py::stays_red", Outcome.FAILED),
    case("t.py::skipped_then_fails", Outcome.ERROR),
    case("t.py::brand_new", Outcome.XFAILED),
]
SELECTION = Selection(
    everything=False,
    targets=[SelectedTarget(target=f"t.py::{n}", reasons=["r"]) for n in ("breaks", "brand_new")],
    notes=[],
)


def test_test_changes() -> None:
    changes = compare_tests(
        BASE, HEAD, selection=SELECTION, mode=RegressionMode.SELECTED, head_test_files={"t.py"}
    )
    assert {c.nodeid: (c.change, c.selected) for c in changes} == {
        "t.py::stays_green": (TestChangeKind.UNCHANGED, False),
        "t.py::breaks": (TestChangeKind.REGRESSED, True),
        "t.py::gets_fixed": (TestChangeKind.FIXED, False),
        "t.py::stays_red": (TestChangeKind.STILL_FAILING, False),
        "t.py::skipped_then_fails": (TestChangeKind.REGRESSED, False),
        "t.py::brand_new": (TestChangeKind.ADDED, True),
        "gone.py::deleted": (TestChangeKind.REMOVED, False),  # its file is gone at head
        "t.py::not_selected": (TestChangeKind.NOT_RUN, False),  # still exists, not run
    }

    check = check_selection(changes, total_tests=len(HEAD))
    assert (check.regressed, check.missed) == (2, ["t.py::skipped_then_fails"])
    assert (check.selected_tests, check.total_tests) == (2, 6)


def test_in_full_mode_absent_tests_are_removed() -> None:
    changes = compare_tests(
        BASE, HEAD, selection=SELECTION, mode=RegressionMode.FULL, head_test_files={"t.py"}
    )
    assert {c.nodeid: c.change for c in changes}["t.py::not_selected"] is TestChangeKind.REMOVED
    everything = Selection(everything=True, targets=[], notes=[])
    changes = compare_tests(
        BASE, HEAD, selection=everything, mode=RegressionMode.FULL, head_test_files={"t.py"}
    )
    assert all(c.selected for c in changes)


def test_failure_changes() -> None:
    repository = RepositoryAnalyzer().analyze(EXAMPLE)
    python = [f.path for f in repository.files if f.language == "Python"]
    code = CodeAnalyzer().analyze(EXAMPLE, python, repository.test_files)
    capture = parse_failures_json(CAPTURE.read_text())
    full = FailureAnalyzer(repository, code).analyze(capture, failed_tests())
    fewer = FailureAnalyzer(repository, code).analyze(
        capture.model_copy(update={"failures": capture.failures[:4]}), failed_tests()
    )

    changes = compare_failures(fewer, full)
    assert len(changes.persisting) == len(fewer.groups)
    assert len(changes.new) == len(full.groups) - len(fewer.groups)
    assert changes.resolved == []
    zero = next(
        g for g in changes.persisting + changes.new if g.exception_type == "ZeroDivisionError"
    )
    assert (zero.crash, zero.tests) == ("shop/stock.py:5", 4)

    gone = compare_failures(full, None)
    assert (len(gone.resolved), gone.new, gone.persisting) == (len(full.groups), [], [])
    empty = FailureReport(
        groups=[], failed_tests=0, analysed_tests=0, unanalysed_tests=[], notes=[]
    )
    assert compare_failures(None, empty).new == []

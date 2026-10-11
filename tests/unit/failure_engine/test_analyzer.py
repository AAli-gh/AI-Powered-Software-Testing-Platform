"""Golden test: the failure groups of examples/failing_suite (EXPECTED_FAILURES.md).

`data/capture.json` is what the sandbox's pytest plugin recorded for that suite plus one
generated test; tests/sandbox/test_failure_capture.py checks it against a live run.
"""

from pathlib import Path

from testmind.analyzers.code import CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.failure_engine import (
    Blame,
    FailureAnalyzer,
    FailureCategory,
    FailureGroup,
    FailureReport,
    parse_failures_json,
)
from testmind.failure_engine.models import Frame, FrameLocation
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "failing_suite"
CAPTURE = Path(__file__).parent / "data" / "capture.json"

FAILED = [
    ("tests/test_missing.py", "tests.test_missing", Outcome.ERROR),
    ("tests/test_syntax.py", "tests.test_syntax", Outcome.ERROR),
    *[
        ("tests/test_stock.py", name, Outcome.FAILED)
        for name in (
            "test_assertion",
            "test_zero_division",
            "test_zero_division_again",
            "test_library",
            "test_in_test",
            "test_chain",
            "test_strict_xpass",
            "test_param[1]",
            "test_param[2]",
            "test_method",
        )
    ],
    ("tests/test_stock.py", "test_fixture", Outcome.ERROR),
    ("tests/testmind/test_gen.py", "test_gen", Outcome.FAILED),
]


def failed_tests() -> list[TestCaseReport]:
    return [
        TestCaseReport(
            nodeid=f"{file}::{name}",
            file=file,
            line=None,
            name=name,
            outcome=outcome,
            duration_seconds=0.0,
            message=None,
            traceback=None,
            generated=file.startswith("tests/testmind/"),
        )
        for file, name, outcome in FAILED
    ]


def group(report: FailureReport, category: FailureCategory, path: str) -> FailureGroup:
    [found] = [
        g
        for g in report.groups
        if g.category is category and g.crash is not None and g.crash.path == path
    ] or [g for g in report.groups if g.category is category and g.tests[0].file == path]
    return found


def test_every_failure_is_analysed(report: FailureReport) -> None:
    assert (report.failed_tests, report.analysed_tests) == (14, 14)
    assert report.unanalysed_tests == []
    assert report.notes == []
    assert len(report.groups) == 10


def test_same_exception_at_the_same_place_is_one_group(report: FailureReport) -> None:
    zero = report.groups[0]  # the largest group comes first
    assert zero.category is FailureCategory.EXCEPTION_IN_CODE
    assert zero.exception_type == "ZeroDivisionError"
    assert zero.crash is not None
    assert (zero.crash.path, zero.crash.line, zero.crash.function) == (
        "shop/stock.py",
        5,
        "average",
    )
    assert zero.crash.location is FrameLocation.PRODUCTION
    assert [t.nodeid for t in zero.tests] == [
        "tests/test_stock.py::test_zero_division",
        "tests/test_stock.py::test_zero_division_again",
        "tests/test_stock.py::test_method",
        "tests/testmind/test_gen.py::test_gen",
    ]
    assert [t.generated for t in zero.tests] == [False, False, False, True]
    assert zero.locals == {"total": "10", "count": "0"}
    assert zero.function is not None
    assert (zero.function.qualname, zero.function.complexity) == ("average", 1)
    assert zero.function.findings == ["TM040"]
    assert zero.blame is None  # the code or the tests? Not decided by the facts
    kinds = [e.kind for e in zero.evidence]
    assert kinds == ["traceback", "locals", "finding", "repetition"]
    assert zero.evidence[2].detail.startswith("Static analysis flagged the failing line: TM040")
    assert zero.evidence[3].detail == (
        "4 tests fail the same way at the same place (1 of them generated)"
    )


def test_parametrized_assertions_are_grouped(report: FailureReport) -> None:
    params = [g for g in report.groups if len(g.tests) == 2]
    [assertion] = params
    assert assertion.category is FailureCategory.ASSERTION_FAILURE
    assert assertion.crash is not None
    assert (assertion.crash.path, assertion.crash.line) == ("tests/test_stock.py", 47)
    assert assertion.crash.location is FrameLocation.TEST
    assert assertion.function is None  # not production code
    assert assertion.evidence[1].kind == "assertion"
    assert "assert 'positive' == 'negative'" in assertion.evidence[1].detail


def test_library_exception_points_at_the_calling_code(report: FailureReport) -> None:
    library = group(report, FailureCategory.EXCEPTION_IN_LIBRARY, "shop/stock.py")
    assert library.exception_type == "json.decoder.JSONDecodeError"
    assert library.raised_at is not None
    assert library.raised_at.location is FrameLocation.EXTERNAL
    assert library.raised_at.path.endswith("json/decoder.py")
    assert library.crash is not None
    assert library.crash.line == 9
    assert library.function is not None
    assert library.function.qualname == "load"
    assert library.evidence[0].detail.startswith(
        "json.decoder.JSONDecodeError raised inside /usr/local/lib/python3.12/json/decoder.py"
    )


def test_chained_exception(report: FailureReport) -> None:
    chained = next(g for g in report.groups if g.exception_type == "RuntimeError")
    assert chained.category is FailureCategory.EXCEPTION_IN_CODE
    assert chained.chain == ["ValueError: invalid literal for int() with base 10: 'x'"]
    assert chained.function is not None
    assert chained.function.qualname == "Wrapper.run"
    assert "Caused by ValueError" in next(e.detail for e in chained.evidence if e.kind == "chain")


def test_test_side_categories(report: FailureReport) -> None:
    by_category = {g.category: g for g in report.groups if len(g.tests) == 1}
    in_test = by_category[FailureCategory.EXCEPTION_IN_TEST]
    assert (in_test.exception_type, in_test.locals) == ("IndexError", {"items": "[]"})
    fixture = by_category[FailureCategory.FIXTURE_ERROR]
    assert fixture.crash is not None
    assert (fixture.crash.function, fixture.tests[0].phase) == ("broken", "setup")


def test_facts_decide_blame_where_they_can(report: FailureReport) -> None:
    decided = {g.category: (g.blame, g.tests[0].nodeid) for g in report.groups if g.blame}
    assert decided == {
        FailureCategory.UNEXPECTED_PASS: (Blame.TEST, "tests/test_stock.py::test_strict_xpass"),
        FailureCategory.IMPORT_ERROR: (Blame.ENVIRONMENT, "tests/test_missing.py"),
        FailureCategory.SYNTAX_ERROR: (Blame.TEST, "tests/test_syntax.py"),
    }
    missing = next(g for g in report.groups if g.category is FailureCategory.IMPORT_ERROR)
    assert missing.exception_type == "ModuleNotFoundError"
    assert missing.evidence[-1].kind == "dependency"
    assert "no dependency with that name is declared" in missing.evidence[-1].detail
    syntax = next(g for g in report.groups if g.category is FailureCategory.SYNTAX_ERROR)
    assert syntax.crash is not None
    assert (syntax.crash.path, syntax.crash.line) == ("tests/test_syntax.py", 1)


def test_group_ids_are_stable(profiles: tuple[RepositoryProfile, CodeProfile]) -> None:
    repository, code = profiles
    capture = parse_failures_json(CAPTURE.read_text())
    first = FailureAnalyzer(repository, code).analyze(capture, failed_tests())
    second = FailureAnalyzer(repository, code).analyze(capture, failed_tests())
    assert [g.id for g in first.groups] == [g.id for g in second.groups]
    assert len({g.id for g in first.groups}) == 10


def test_an_assertion_helper_failing_in_a_test_is_an_assertion_failure() -> None:
    """Regression (youtube-dl on BugsInPy): unittest's self.assertEqual raises AssertionError
    inside unittest/case.py; that is the test's assertion, not an exception in a library."""

    def frame(path: str, location: FrameLocation) -> Frame:
        return Frame(path=path, line=1, function="f", code="", location=location)

    in_test = frame("test/helper.py", FrameLocation.TEST)
    unittest = frame("/usr/lib/python3.7/unittest/case.py", FrameLocation.EXTERNAL)
    category = FailureAnalyzer._category
    assert category("call", "AssertionError", in_test, unittest) is (
        FailureCategory.ASSERTION_FAILURE
    )
    generated = frame("tests/testmind/test_g.py", FrameLocation.GENERATED)
    assert category("call", "AssertionError", generated, unittest) is (
        FailureCategory.ASSERTION_FAILURE
    )
    # An AssertionError raised by a library called from production code stays a library one.
    in_code = frame("shop/stock.py", FrameLocation.PRODUCTION)
    assert category("call", "AssertionError", in_code, unittest) is (
        FailureCategory.EXCEPTION_IN_LIBRARY
    )
    assert category("call", "ValueError", in_test, unittest) is (
        FailureCategory.EXCEPTION_IN_LIBRARY
    )

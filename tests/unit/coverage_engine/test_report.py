"""Mapping coverage onto modules and functions (deterministic, no sandbox needed).

The same computation is cross-checked against coverage.py itself in tests/sandbox/test_coverage.py.
"""

from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.coverage_engine import RawCoverage, build_coverage_report
from testmind.coverage_engine.parser import RawFile
from testmind.coverage_engine.report import is_production, line_ranges, percent
from tests.unit.analyzers.helpers import make_repo

SOURCE = """\
def risky(x, items, scale):
    if x is None:
        raise ValueError("x")
    total = 0
    for item in items:
        total += item / scale
    return total


def simple(a):
    return a + 1


def outer():
    def inner():
        return 2

    return inner()


def empty():
    '''Only a docstring.'''
"""


def raw_file(executed: set[int], missing: set[int], taken=(), missed=()) -> RawFile:  # type: ignore[no-untyped-def]
    return RawFile(
        executed=frozenset(executed),
        missing=frozenset(missing),
        executed_branches=frozenset(taken),
        missing_branches=frozenset(missed),
    )


@pytest.fixture
def code(tmp_path: Path) -> CodeProfile:
    root = make_repo(
        tmp_path,
        {
            "app/core.py": SOURCE,
            "app/__init__.py": "",
            "app/extra.py": "def unused():\n    return 1\n",
            "conftest.py": "import pytest\n",
            "tests/helpers.py": "def make():\n    return 1\n",
        },
    )
    files = ["app/core.py", "app/__init__.py", "app/extra.py", "conftest.py", "tests/helpers.py"]
    return CodeAnalyzer().analyze(root, files)


def coverage(**files: RawFile) -> RawCoverage:
    return RawCoverage(
        tool="coverage.py 7.16.1",
        branch_coverage=True,
        files={path.replace("__", "/") + ".py": data for path, data in files.items()},
    )


def core() -> RawFile:
    # risky(1, [], 2): statements 2-7; x is not None (2->4) and the list is empty (5->7),
    # so 3 and 6 never ran.  simple: 11 ran.  outer / inner: 15 (def inner), 16, 18 never ran.
    return raw_file(
        executed={1, 2, 4, 5, 7, 10, 11, 14, 21},
        missing={3, 6, 15, 16, 18},
        taken={(2, 4), (5, 7)},
        missed={(2, 3), (5, 6)},
    )


def test_file_and_function_coverage(code: CodeProfile) -> None:
    report = build_coverage_report(coverage(app__core=core()), code)

    [file] = report.files
    assert file.path == "app/core.py"
    counts = (file.statements, file.covered_lines, file.branches, file.covered_branches)
    assert counts == (14, 9, 4, 2)
    assert file.percent == percent(9 + 2, 14 + 4)
    assert file.missing == ["3", "6", "15-18"]  # 17 is blank: 15-18 is one range
    assert file.missing_lines == [3, 6, 15, 16, 18]
    assert file.missing_branches_detail == ["2->3", "5->6"]

    functions = {f.qualname: f for f in report.functions}
    risky = functions["risky"]
    counts = (risky.statements, risky.covered_lines, risky.branches, risky.covered_branches)
    assert counts == (6, 4, 4, 2)
    assert risky.percent == 60.0
    assert risky.missing == ["3", "6"]
    assert risky.findings == ["TM040"]  # `scale` is an unvalidated divisor
    assert functions["simple"].percent == 100.0
    # outer's own statements exclude the body of inner (16) but include `def inner` (15).
    assert (functions["outer"].statements, functions["outer"].covered_lines) == (2, 0)
    assert (
        functions["outer.<locals>.inner"].statements,
        functions["outer.<locals>.inner"].covered_lines,
    ) == (1, 0)
    assert functions["empty"].statements == 0
    assert functions["empty"].percent == 100.0


def test_unmeasured_and_non_production_modules(code: CodeProfile) -> None:
    report = build_coverage_report(coverage(app__core=core(), conftest=raw_file({1}, set())), code)
    assert [f.path for f in report.files] == ["app/core.py"]
    # Never imported, so coverage.py reported nothing: listed, not counted as 0 or 100.
    assert report.unmeasured_files == ["app/__init__.py", "app/extra.py"]
    assert report.totals.files == 1


def test_totals(code: CodeProfile) -> None:
    extra = raw_file(executed={1, 2}, missing=set())
    report = build_coverage_report(coverage(app__core=core(), app__extra=extra), code)
    totals = report.totals
    counts = (totals.statements, totals.covered_lines, totals.branches, totals.covered_branches)
    assert counts == (16, 11, 4, 2)
    assert totals.line_percent == 68.75
    assert totals.branch_percent == 50.0
    assert totals.percent == 65.0
    assert (totals.files, totals.functions, totals.functions_never_executed) == (2, 6, 2)


def test_gaps_are_ranked_by_risk(code: CodeProfile) -> None:
    report = build_coverage_report(coverage(app__core=core()), code, threshold=90)
    assert [(g.qualname, g.risk) for g in report.gaps] == [
        # uncovered share x (complexity + 2 x findings)
        ("risky", 2.0),  # (1 - 0.6) x (3 + 2 x 1)
        ("outer", 1.0),
        ("outer.<locals>.inner", 1.0),
    ]
    risky = report.gaps[0]
    assert risky.reasons == [
        "2 of 6 statements not executed",
        "2 of 4 branches never taken",
        "cyclomatic complexity 3",
        "static-analysis findings: TM040",
    ]
    assert report.gaps[1].reasons == ["never executed by the test suite"]


def test_threshold_decides_what_is_a_gap(code: CodeProfile) -> None:
    report = build_coverage_report(coverage(app__core=core()), code, threshold=50)
    assert [g.qualname for g in report.gaps] == ["outer", "outer.<locals>.inner"]
    assert report.threshold == 50


@pytest.mark.parametrize(
    ("path", "production"),
    [
        ("app/core.py", True),
        ("posts/tests_utils.py", True),
        ("conftest.py", False),
        ("app/conftest.py", False),
        ("tests/helpers.py", False),
        ("src/pkg/test/fixtures.py", False),
    ],
)
def test_production_modules(path: str, production: bool) -> None:
    assert is_production(path) is production


@pytest.mark.parametrize(
    ("missing", "statements", "expected"),
    [
        ([], [1, 2], []),
        ([3], [1, 3], ["3"]),
        ([3, 4, 5], [1, 3, 4, 5], ["3-5"]),
        ([3, 7], [3, 7], ["3-7"]),  # no executed statement in between: one range
        ([3, 7], [3, 5, 7], ["3", "7"]),
        ([2, 3, 9], [1, 2, 3, 4, 9], ["2-3", "9"]),
    ],
)
def test_line_ranges(missing: list[int], statements: list[int], expected: list[str]) -> None:
    assert line_ranges(missing, statements) == expected


def test_percent_of_nothing_is_full() -> None:
    assert percent(0, 0) == 100.0
    assert percent(1, 3) == 33.33

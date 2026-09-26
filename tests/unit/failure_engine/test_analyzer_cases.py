"""Analyzer cases the golden suite does not cover, with hand-built plugin records."""

from pathlib import Path
from typing import Any

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.coverage_engine import RawCoverage, build_coverage_report
from testmind.coverage_engine.parser import RawFile
from testmind.failure_engine import Blame, FailureAnalyzer, FailureCategory, RawCapture
from testmind.failure_engine.analyzer import NO_CAPTURE
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport
from tests.unit.analyzers.helpers import analyze

SOURCE = (
    "import pickle\n\n\n"
    "def check(x):\n    assert x > 0\n    return x\n\n\n"
    "def load(blob, count):\n    data = pickle.loads(blob)\n    return data / count\n"
)


@pytest.fixture
def profiles(tmp_path: Path) -> tuple[RepositoryProfile, CodeProfile]:
    repository = analyze(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core.py": SOURCE,
            "tests/test_core.py": "def test_a(): pass\n",
            "requirements.txt": "flask\nrequests>=2\n",
        },
    )
    python = [f.path for f in repository.files if f.language == "Python"]
    return repository, CodeAnalyzer().analyze(tmp_path, python, repository.test_files)


def capture(*failures: dict[str, Any], dropped: int = 0) -> RawCapture:
    return RawCapture.model_validate(
        {"version": 1, "rootdir": "/workspace/src", "failures": list(failures), "dropped": dropped}
    )


def call(
    name: str,
    exception: str,
    message: str = "",
    *,
    frames: list[tuple[str, int, str]] = (),  # type: ignore[assignment]
    phase: str = "call",
) -> dict[str, Any]:
    return {
        "phase": phase,
        "path": "/workspace/src/tests/test_core.py",
        "name": name,
        "line": 1,
        "exception": {"type": exception, "message": message},
        "frames": [{"path": p, "line": n, "function": f, "code": ""} for p, n, f in frames],
    }


def collect(nodeid: str, longrepr: str) -> dict[str, Any]:
    return {"phase": "collect", "nodeid": nodeid, "longrepr": longrepr}


def failing(*names: str) -> list[TestCaseReport]:
    return [
        TestCaseReport(
            nodeid=f"tests/test_core.py::{name}", file="tests/test_core.py", line=1, name=name,
            outcome=Outcome.FAILED, duration_seconds=0, message=None, traceback=None,
            generated=False,
        )
        for name in names
    ]  # fmt: skip


TEST = "/workspace/src/tests/test_core.py"
CORE = "/workspace/src/app/core.py"


def test_no_capture_lists_failures_without_guessing(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    analyzer = FailureAnalyzer(*profiles)
    report = analyzer.analyze(None, failing("test_a"))
    assert (report.groups, report.unanalysed_tests, report.notes) == (
        [],
        ["tests/test_core.py::test_a"],
        [NO_CAPTURE],
    )
    assert analyzer.analyze(None, []).notes == []


def test_unmatched_and_dropped_failures_are_reported(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    report = FailureAnalyzer(*profiles).analyze(
        capture(call("test_a", "ValueError", frames=[(TEST, 3, "test_a")]), dropped=2),
        failing("test_a", "test_b"),
    )
    assert report.unanalysed_tests == ["tests/test_core.py::test_b"]
    assert report.notes == ["2 further failures were not recorded (limit reached)"]


def test_assert_in_production_code_is_a_code_exception(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    repository, code = profiles
    # check(): line 5 ran, line 6 did not.
    core = RawFile(frozenset({1, 4, 5}), frozenset({6}), frozenset(), frozenset())
    coverage = build_coverage_report(RawCoverage("coverage.py", True, {"app/core.py": core}), code)
    failure = call("test_a", "AssertionError", frames=[(TEST, 3, "test_a"), (CORE, 5, "check")])
    [group] = FailureAnalyzer(repository, code, coverage).analyze(capture(failure), []).groups
    assert group.category is FailureCategory.EXCEPTION_IN_CODE
    assert group.function is not None
    assert (group.function.qualname, group.function.coverage_percent) == ("check", 50.0)


@pytest.mark.parametrize(
    ("record", "category"),
    [
        (call("t", "AssertionError", frames=[]), FailureCategory.ASSERTION_FAILURE),
        (call("t", "KeyError", frames=[]), FailureCategory.EXCEPTION_IN_TEST),
        (
            call("t", "KeyError", frames=[(TEST, 9, "fixture")], phase="teardown"),
            FailureCategory.FIXTURE_ERROR,
        ),
        (
            call("t", "ModuleNotFoundError", "No module named 'requests'", frames=[(TEST, 2, "t")]),
            FailureCategory.IMPORT_ERROR,
        ),
        (
            collect("tests/test_core.py", "conftest.py:3: in <module>\nE   ValueError: boom"),
            FailureCategory.COLLECTION_ERROR,
        ),
        (collect("tests/test_core.py", "internal error"), FailureCategory.COLLECTION_ERROR),
    ],
)
def test_categories(
    profiles: tuple[RepositoryProfile, CodeProfile],
    record: dict[str, Any],
    category: FailureCategory,
) -> None:
    [group] = FailureAnalyzer(*profiles).analyze(capture(record), []).groups
    assert group.category is category


def test_missing_declared_dependency(profiles: tuple[RepositoryProfile, CodeProfile]) -> None:
    record = call("t", "ModuleNotFoundError", "No module named 'requests.adapters'")
    [group] = FailureAnalyzer(*profiles).analyze(capture(record), []).groups
    assert group.blame is Blame.ENVIRONMENT
    assert group.evidence[-1].detail == (
        "'requests' is not part of the repository; a dependency named 'requests' is declared "
        "in requirements.txt but could not be imported"
    )


def test_missing_module_inside_the_repository_is_not_blamed_on_the_environment(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    records = [
        call("t1", "ModuleNotFoundError", "No module named 'app.missing'"),
        call("t2", "ImportError", "cannot import name 'nope' from 'app.core'"),
    ]
    groups = FailureAnalyzer(*profiles).analyze(capture(*records), []).groups
    assert [(g.category, g.blame) for g in groups] == [(FailureCategory.IMPORT_ERROR, None)] * 2
    missing = next(g for g in groups if g.exception_type == "ModuleNotFoundError")
    assert missing.evidence[-1].detail == (
        "'app' is a package of the repository, so 'app.missing' should exist in it"
    )


def test_syntax_error_in_production_code_blames_the_code(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    longrepr = (
        'E     File "/workspace/src/app/core.py", line 5\nE       def (:\n'
        "E   SyntaxError: invalid syntax"
    )
    [group] = (
        FailureAnalyzer(*profiles)
        .analyze(capture(collect("tests/test_core.py", longrepr)), [])
        .groups
    )
    assert (group.category, group.blame) == (FailureCategory.SYNTAX_ERROR, Blame.CODE)
    assert group.blame_reason == "app/core.py is production code that does not parse"
    assert group.tests[0].nodeid == "tests/test_core.py"


def test_findings_elsewhere_in_the_failing_function_are_evidence(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    failure = call("t", "ZeroDivisionError", frames=[(TEST, 3, "t"), (CORE, 11, "load")])
    [group] = FailureAnalyzer(*profiles).analyze(capture(failure), []).groups
    findings = [e for e in group.evidence if e.kind == "finding"]
    assert [(e.line, e.detail.split(":")[0]) for e in findings] == [
        (10, "Static analysis flagged line 10 of load"),  # pickle.loads, not the crash line
        (11, "Static analysis flagged the failing line"),  # the unvalidated divisor
    ]


def test_module_level_code_has_no_linked_function(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    failure = call("t", "ImportError", "boom", frames=[(CORE, 1, "<module>")])
    [group] = FailureAnalyzer(*profiles).analyze(capture(failure), []).groups
    assert group.crash is not None
    assert group.crash.path == "app/core.py"
    assert group.function is None


def test_syntax_error_outside_the_repository_is_not_blamed(
    profiles: tuple[RepositoryProfile, CodeProfile],
) -> None:
    longrepr = 'E     File "/usr/lib/python3.12/bad.py", line 1\nE   SyntaxError: invalid syntax'
    [group] = (
        FailureAnalyzer(*profiles)
        .analyze(capture(collect("tests/test_core.py", longrepr)), [])
        .groups
    )
    assert (group.category, group.blame) == (FailureCategory.SYNTAX_ERROR, None)

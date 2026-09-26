"""Outcome mapping of TestSuiteRunner (canned sandbox; the real one is in tests/sandbox/)."""

import json
from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.analyzers.repository.models import Dependency
from testmind.sandbox.docker_sandbox import PytestJob
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError, TestCaseResult
from testmind.sandbox.runner_image import DependencyInstallError, RunnerImage
from testmind.test_engine.models import ExecutionOutcome, TestRunReport
from testmind.test_engine.runner import TestSuiteRunner
from tests.unit.analyzers.helpers import analyze


class Sandbox:
    def __init__(
        self, result: SandboxResult | Exception | None = None, available: bool = True
    ) -> None:
        self.result = result
        self._available = available
        self.jobs: list[PytestJob] = []

    def available(self, image: str | None = None) -> bool:
        return self._available

    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult:
        self.jobs.append(job)
        if isinstance(self.result, Exception):
            raise self.result
        assert self.result is not None
        return self.result


class Images:
    def __init__(self, error: DependencyInstallError | None = None) -> None:
        self.error = error

    def ensure(self, dependencies: list[Dependency]) -> RunnerImage:
        if self.error:
            raise self.error
        return RunnerImage("testmind-runner:abc", ("flask",), False, "installed", ("note",))


def case(name: str, outcome: Outcome, file: str | None = "tests/test_a.py") -> TestCaseResult:
    return TestCaseResult(
        name=name, classname="tests.test_a", file=file, line=3, outcome=outcome, message="m"
    )


def result(
    *tests: TestCaseResult,
    exit_code: int = 0,
    timed_out: bool = False,
    coverage_json: str | None = None,
    coverage_error: str | None = None,
    failures_json: str | None = None,
) -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=1.5,
        tests=list(tests),
        output="out",
        coverage_json=coverage_json,
        coverage_error=coverage_error,
        failures_json=failures_json,
    )


@pytest.fixture
def profile(tmp_path: Path) -> RepositoryProfile:
    return analyze(
        tmp_path, {"tests/test_a.py": "def test_a(): pass\n", "requirements.txt": "flask\n"}
    )


def run(
    profile: RepositoryProfile,
    sandbox: Sandbox,
    images: Images | None = None,
    generated: dict[str, str] | None = None,
) -> TestRunReport:
    return (
        TestSuiteRunner(sandbox, images or Images()).run(Path("/repo"), profile, generated).report
    )


@pytest.mark.parametrize(
    ("sandbox_result", "outcome", "meaning"),
    [
        (result(case("t", Outcome.PASSED)), ExecutionOutcome.PASSED, "all tests passed"),
        (
            result(case("t", Outcome.FAILED), exit_code=1),
            ExecutionOutcome.FAILED,
            "some tests failed",
        ),
        (
            result(case("t", Outcome.ERROR), exit_code=1),
            ExecutionOutcome.ERROR,
            "some tests failed",
        ),
        (
            result(exit_code=2),
            ExecutionOutcome.ERROR,
            "test execution was interrupted (usually a collection error)",
        ),
        (result(exit_code=5), ExecutionOutcome.NO_TESTS, "no tests were collected"),
        (result(exit_code=0), ExecutionOutcome.NO_TESTS, "all tests passed"),
        (result(exit_code=-1, timed_out=True), ExecutionOutcome.TIMED_OUT, None),
        (result(exit_code=137), ExecutionOutcome.ERROR, None),
    ],
)
def test_outcomes(
    profile: RepositoryProfile,
    sandbox_result: SandboxResult,
    outcome: ExecutionOutcome,
    meaning: str | None,
) -> None:
    report = run(profile, Sandbox(sandbox_result))
    assert report.outcome is outcome
    assert report.exit_meaning == meaning


def test_report_contents(profile: RepositoryProfile) -> None:
    sandbox = Sandbox(
        result(
            case("test_a", Outcome.PASSED),
            case("test_gen", Outcome.FAILED, file="../tests/testmind/test_x.py"),
            case("test_x", Outcome.XFAILED, file=None),
            exit_code=1,
        )
    )
    report = run(profile, sandbox, generated={"testmind/test_x.py": "code"})

    assert [(t.nodeid, t.generated) for t in report.tests] == [
        ("tests/test_a.py::test_a", False),
        ("tests/testmind/test_x.py::test_gen", True),
        ("tests.test_a::test_x", False),
    ]
    assert report.totals.model_dump() == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "xfailed": 1,
    }
    assert [t.name for t in report.failures()] == ["test_gen"]
    assert report.install is not None
    assert (report.install.image, report.install.notes) == ("testmind-runner:abc", ["note"])
    assert report.project_test_files == ["tests/test_a.py"]
    assert report.generated_test_files == ["testmind/test_x.py"]
    [job] = sandbox.jobs
    assert (job.image, job.project_tests, job.test_files) == (
        "testmind-runner:abc",
        ("tests/test_a.py",),
        {"testmind/test_x.py": "code"},
    )


def test_django_settings_are_passed_to_the_sandbox(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "manage.py": (
                'import os\nos.environ.setdefault("DJANGO_SETTINGS_MODULE", "site.settings")\n'
            ),
            "app/tests.py": "def test_a(): pass\n",
        },
    )
    sandbox = Sandbox(result(case("t", Outcome.PASSED)))
    report = run(profile, sandbox)
    assert sandbox.jobs[0].env == {"DJANGO_SETTINGS_MODULE": "site.settings"}
    assert report.environment == {"DJANGO_SETTINGS_MODULE": "site.settings"}


def test_no_tests_skips_the_sandbox(tmp_path: Path) -> None:
    sandbox = Sandbox()
    report = run(analyze(tmp_path, {"main.py": "x = 1\n"}), sandbox)
    assert report.outcome is ExecutionOutcome.NO_TESTS
    assert sandbox.jobs == []


def test_sandbox_unavailable(profile: RepositoryProfile) -> None:
    assert run(profile, Sandbox(available=False)).outcome is ExecutionOutcome.SANDBOX_UNAVAILABLE
    report = run(profile, Sandbox(SandboxUnavailableError("image vanished")))
    assert report.outcome is ExecutionOutcome.SANDBOX_UNAVAILABLE
    assert report.output == "image vanished"


def test_install_failure(profile: RepositoryProfile) -> None:
    error = DependencyInstallError("Installing failed", "pip log", ("n",))
    report = run(profile, Sandbox(), Images(error))
    assert report.outcome is ExecutionOutcome.INSTALL_FAILED
    assert report.install is not None
    assert (report.install.log, report.install.notes) == ("pip log", ["n"])
    assert report.output == "Installing failed"


def test_generated_only_run_paths_are_relative_to_the_tests_directory(tmp_path: Path) -> None:
    """Regression: pytest reports `file` relative to its rootdir, not as an absolute path."""
    sandbox = Sandbox(result(case("test_g", Outcome.PASSED, file="testmind/test_g.py")))
    report = run(
        analyze(tmp_path, {"main.py": "x = 1\n"}), sandbox, generated={"testmind/test_g.py": "c"}
    )
    [test] = report.tests
    assert (test.nodeid, test.generated) == ("tests/testmind/test_g.py::test_g", True)


# ---------------------------------------------------------------------------------- coverage
COVERAGE_JSON = json.dumps(
    {
        "meta": {"version": "7.16.1", "branch_coverage": True},
        "files": {
            "app.py": {
                "executed_lines": [1, 2],
                "missing_lines": [3],
                "executed_branches": [[2, 3]],
                "missing_branches": [[2, -1]],
            },
            "tests/test_a.py": {"executed_lines": [1], "missing_lines": []},
        },
    }
)


def code_for(tmp_path: Path) -> tuple[RepositoryProfile, CodeProfile]:
    files = {
        "app.py": "def f(x):\n    if x:\n        return 1\n",
        "tests/test_a.py": "def test_a(): pass\n",
    }
    profile = analyze(tmp_path, files)
    code = CodeAnalyzer().analyze(tmp_path, ["app.py", "tests/test_a.py"], profile.test_files)
    return profile, code


def test_coverage_is_measured_and_mapped_to_production_code(tmp_path: Path) -> None:
    profile, code = code_for(tmp_path)
    sandbox = Sandbox(result(case("t", Outcome.PASSED), coverage_json=COVERAGE_JSON))
    suite = TestSuiteRunner(sandbox, Images(), coverage_threshold=90).run(
        tmp_path, profile, code=code
    )

    assert sandbox.jobs[0].coverage is True
    assert suite.report.coverage_error is None
    assert suite.coverage is not None
    assert [f.path for f in suite.coverage.files] == ["app.py"]  # the test module is not counted
    assert suite.coverage.threshold == 90
    [function] = suite.coverage.functions
    assert (function.qualname, function.statements, function.covered_lines) == ("f", 2, 1)
    assert [g.qualname for g in suite.coverage.gaps] == ["f"]


@pytest.mark.parametrize(
    ("coverage_json", "coverage_error", "with_code", "expected"),
    [
        (None, "coverage.py wrote no report (timed out)", True, "wrote no report (timed out)"),
        (None, None, True, "coverage.py wrote no report"),
        (COVERAGE_JSON, None, False, "no code analysis"),
        ('{"files": 3}', None, True, "Not a coverage.py JSON report"),
    ],
)
def test_missing_coverage_is_explained(
    tmp_path: Path,
    coverage_json: str | None,
    coverage_error: str | None,
    with_code: bool,
    expected: str,
) -> None:
    profile, code = code_for(tmp_path)
    sandbox = Sandbox(
        result(
            case("t", Outcome.PASSED), coverage_json=coverage_json, coverage_error=coverage_error
        )
    )
    suite = TestSuiteRunner(sandbox, Images()).run(
        tmp_path, profile, code=code if with_code else None
    )
    assert suite.coverage is None
    assert suite.report.coverage_error is not None
    assert expected in suite.report.coverage_error


def test_no_run_means_no_coverage(profile: RepositoryProfile) -> None:
    report = run(profile, Sandbox(available=False))
    assert report.coverage_error == "Coverage was not measured: sandbox unavailable"


# ---------------------------------------------------------------------------------- failures
FAILURES_JSON = json.dumps(
    {
        "version": 1,
        "rootdir": "/workspace/src",
        "failures": [
            {
                "phase": "call",
                "path": "/workspace/src/tests/test_a.py",
                "name": "t",
                "line": 3,
                "exception": {"type": "ZeroDivisionError", "message": "division by zero"},
                "frames": [
                    {"path": "/workspace/src/tests/test_a.py", "line": 4, "function": "t"},
                    {"path": "/workspace/src/app.py", "line": 3, "function": "f"},
                ],
            }
        ],
    }
)


def test_failures_are_analysed_after_every_run(tmp_path: Path) -> None:
    profile, code = code_for(tmp_path)
    sandbox = Sandbox(result(case("t", Outcome.FAILED), exit_code=1, failures_json=FAILURES_JSON))
    suite = TestSuiteRunner(sandbox, Images()).run(tmp_path, profile, code=code)

    job = sandbox.jobs[0]
    assert (job.capture_failures, job.continue_on_collection_errors) == (True, True)
    assert suite.failures is not None
    [group] = suite.failures.groups
    assert group.exception_type == "ZeroDivisionError"
    assert group.function is not None
    assert group.function.qualname == "f"
    assert suite.failures.unanalysed_tests == []


def test_unreadable_failure_capture_is_noted(tmp_path: Path) -> None:
    profile, code = code_for(tmp_path)
    sandbox = Sandbox(result(case("t", Outcome.FAILED), exit_code=1, failures_json="{}"))
    suite = TestSuiteRunner(sandbox, Images()).run(tmp_path, profile, code=code)
    assert suite.failures is not None
    assert suite.failures.groups == []
    assert suite.failures.unanalysed_tests == ["tests/test_a.py::t"]
    assert suite.failures.notes[0].startswith("Not a TestMind failure capture")


def test_no_sandbox_run_means_no_failure_report(profile: RepositoryProfile) -> None:
    suite = TestSuiteRunner(Sandbox(available=False), Images()).run(Path("/repo"), profile)
    assert suite.failures is None


def test_contexts_and_selection_are_passed_to_the_sandbox(tmp_path: Path) -> None:
    profile, code = code_for(tmp_path)
    contexts = json.loads(COVERAGE_JSON)
    contexts["files"]["app.py"]["contexts"] = {"2": ["tests/test_a.py::t|run"]}
    sandbox = Sandbox(result(case("t", Outcome.PASSED), coverage_json=json.dumps(contexts)))
    suite = TestSuiteRunner(sandbox, Images()).run(
        tmp_path, profile, code=code, test_contexts=True, select=("tests/test_a.py::t",)
    )
    job = sandbox.jobs[0]
    assert (job.test_contexts, job.select) == (True, ("tests/test_a.py::t",))
    assert suite.impact is not None
    assert suite.impact.functions == {"app.py::f": ["tests/test_a.py::t"]}
    plain = TestSuiteRunner(
        Sandbox(result(case("t", Outcome.PASSED), coverage_json=COVERAGE_JSON)), Images()
    )
    assert plain.run(tmp_path, profile, code=code).impact is None

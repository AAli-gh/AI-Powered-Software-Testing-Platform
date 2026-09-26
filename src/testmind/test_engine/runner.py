"""Run a repository's test suite (plus optional generated tests) in the sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from testmind.analyzers.code.models import CodeProfile
from testmind.analyzers.repository.models import Dependency, RepositoryProfile
from testmind.coverage_engine import (
    CoverageParseError,
    CoverageReport,
    build_coverage_report,
    parse_coverage_json,
)
from testmind.coverage_engine.report import DEFAULT_THRESHOLD
from testmind.failure_engine import (
    FailureAnalyzer,
    FailureCaptureError,
    FailureReport,
    parse_failures_json,
)
from testmind.sandbox.docker_sandbox import PytestJob
from testmind.sandbox.models import (
    Outcome,
    SandboxResult,
    SandboxUnavailableError,
    TestCaseResult,
)
from testmind.sandbox.paths import GENERATED_PREFIX, PROJECT_PREFIX, report_path
from testmind.sandbox.runner_image import DependencyInstallError, RunnerImage
from testmind.test_engine.models import (
    PYTEST_EXIT_CODES,
    ExecutionOutcome,
    InstallReport,
    TestCaseReport,
    TestRunReport,
    TestTotals,
)

EXIT_NO_TESTS_COLLECTED = 5
NO_CODE_ANALYSIS = "The run has no code analysis to map coverage onto"


class Sandbox(Protocol):
    def available(self, image: str | None = None) -> bool: ...

    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult: ...


class ImageBuilder(Protocol):
    def ensure(self, dependencies: list[Dependency]) -> RunnerImage: ...


@dataclass(frozen=True, slots=True)
class SuiteResult:
    report: TestRunReport
    coverage: CoverageReport | None  # None: see report.coverage_error
    failures: FailureReport | None = None  # None when no tests ran


class TestSuiteRunner:
    __test__ = False

    def __init__(
        self,
        sandbox: Sandbox,
        images: ImageBuilder,
        *,
        coverage_threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._sandbox = sandbox
        self._images = images
        self._threshold = coverage_threshold

    def run(
        self,
        root: Path,
        profile: RepositoryProfile,
        generated: dict[str, str] | None = None,
        *,
        code: CodeProfile | None = None,
    ) -> SuiteResult:
        """Run the suite with coverage and structured failure capture. `code` (the run's code
        analysis) maps coverage and failures onto production modules and functions."""
        generated = generated or {}
        project_tests = list(profile.test_files)
        env = (
            {"DJANGO_SETTINGS_MODULE": profile.django_settings_module}
            if profile.django_settings_module
            else {}
        )
        base = _Base(project_tests, sorted(generated), env)

        if not project_tests and not generated:
            return SuiteResult(base.report(ExecutionOutcome.NO_TESTS, install=None), None)
        if not self._sandbox.available():
            return SuiteResult(
                base.report(ExecutionOutcome.SANDBOX_UNAVAILABLE, install=None), None
            )

        try:
            image = self._images.ensure(profile.dependencies)
        except DependencyInstallError as exc:
            install = InstallReport(
                image="", requirements=[], from_cache=False, notes=list(exc.notes), log=exc.log
            )
            report = base.report(ExecutionOutcome.INSTALL_FAILED, install=install, output=str(exc))
            return SuiteResult(report, None)
        install = InstallReport(
            image=image.tag,
            requirements=list(image.requirements),
            from_cache=image.from_cache,
            notes=list(image.notes),
            log=image.log,
        )

        job = PytestJob(
            test_files=generated,
            project_tests=tuple(project_tests),
            image=image.tag,
            env=env,
            coverage=True,
            capture_failures=True,
            continue_on_collection_errors=True,
        )
        try:
            result = self._sandbox.run(root, job)
        except SandboxUnavailableError as exc:
            report = base.report(
                ExecutionOutcome.SANDBOX_UNAVAILABLE, install=install, output=str(exc)
            )
            return SuiteResult(report, None)
        coverage, coverage_error = self._coverage(result, code, base.rootdir)
        report = base.from_result(result, install, coverage_error)
        failures = _failures(result, report, profile, code, coverage)
        return SuiteResult(report, coverage, failures)

    def _coverage(
        self, result: SandboxResult, code: CodeProfile | None, rootdir: str
    ) -> tuple[CoverageReport | None, str | None]:
        if result.coverage_json is None:
            return None, result.coverage_error or "coverage.py wrote no report"
        if code is None:
            return None, NO_CODE_ANALYSIS
        try:
            raw = parse_coverage_json(result.coverage_json, rootdir=rootdir)
        except CoverageParseError as exc:
            return None, str(exc)
        return build_coverage_report(raw, code, threshold=self._threshold), None


def _failures(
    result: SandboxResult,
    report: TestRunReport,
    profile: RepositoryProfile,
    code: CodeProfile | None,
    coverage: CoverageReport | None,
) -> FailureReport:
    """Deterministic failure analysis; runs after every execution (it needs no LLM)."""
    analyzer = FailureAnalyzer(profile, code, coverage)
    if result.failures_json is None:
        return analyzer.analyze(None, report.tests)
    try:
        capture = parse_failures_json(result.failures_json)
    except FailureCaptureError as exc:
        failed = analyzer.analyze(None, report.tests)
        return failed.model_copy(update={"notes": [str(exc)]})
    return analyzer.analyze(capture, report.tests)


class _Base:
    def __init__(self, project_tests: list[str], generated: list[str], env: dict[str, str]) -> None:
        self.project_tests = project_tests
        self.generated = generated
        self.env = env

    @property
    def rootdir(self) -> str:
        """Where pytest runs: the repository, or the generated tests when it has none."""
        return PROJECT_PREFIX if self.project_tests else GENERATED_PREFIX

    def report(
        self, outcome: ExecutionOutcome, *, install: InstallReport | None, output: str = ""
    ) -> TestRunReport:
        return TestRunReport(
            outcome=outcome,
            exit_code=None,
            exit_meaning=None,
            totals=TestTotals(),
            duration_seconds=0.0,
            tests=[],
            output=output,
            install=install,
            project_test_files=self.project_tests,
            generated_test_files=self.generated,
            environment=self.env,
            coverage_error=f"Coverage was not measured: {outcome.value.replace('_', ' ')}",
        )

    def from_result(
        self, result: SandboxResult, install: InstallReport, coverage_error: str | None
    ) -> TestRunReport:
        tests = [_case(t, self.rootdir) for t in result.tests]
        totals = TestTotals(
            total=len(tests),
            passed=result.count(Outcome.PASSED),
            failed=result.count(Outcome.FAILED),
            errors=result.count(Outcome.ERROR),
            skipped=result.count(Outcome.SKIPPED),
            xfailed=result.count(Outcome.XFAILED),
        )
        return TestRunReport(
            outcome=_outcome(result, totals),
            exit_code=result.exit_code,
            exit_meaning=PYTEST_EXIT_CODES.get(result.exit_code),
            totals=totals,
            duration_seconds=result.duration_seconds,
            tests=tests,
            output=result.output,
            install=install,
            project_test_files=self.project_tests,
            generated_test_files=self.generated,
            environment=self.env,
            coverage_error=coverage_error,
        )


def _outcome(result: SandboxResult, totals: TestTotals) -> ExecutionOutcome:
    if result.timed_out:
        return ExecutionOutcome.TIMED_OUT
    if result.exit_code == EXIT_NO_TESTS_COLLECTED or (result.exit_code == 0 and totals.total == 0):
        return ExecutionOutcome.NO_TESTS
    if result.exit_code == 0:
        return ExecutionOutcome.PASSED
    if result.exit_code == 1 and totals.errors == 0:
        return ExecutionOutcome.FAILED
    return ExecutionOutcome.ERROR


def _case(test: TestCaseResult, rootdir: str) -> TestCaseReport:
    # pytest reports `file` relative to its rootdir (e.g. "../tests/testmind/test_x.py").
    located = report_path(test.file, rootdir) if test.file else None
    file, generated = located if located else (test.file, False)
    nodeid = f"{file}::{test.name}" if file else f"{test.classname}::{test.name}"
    return TestCaseReport(
        nodeid=nodeid,
        file=file,
        line=test.line,
        name=test.name,
        outcome=test.outcome,
        duration_seconds=test.duration_seconds,
        message=test.message,
        traceback=test.details,
        generated=generated,
    )

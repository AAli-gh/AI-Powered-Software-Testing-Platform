"""Run a repository's test suite (plus optional generated tests) in the sandbox."""

from __future__ import annotations

import posixpath
from pathlib import Path
from typing import Protocol

from testmind.analyzers.repository.models import Dependency, RepositoryProfile
from testmind.sandbox.docker_sandbox import PytestJob
from testmind.sandbox.models import (
    Outcome,
    SandboxResult,
    SandboxUnavailableError,
    TestCaseResult,
)
from testmind.sandbox.runner_image import DependencyInstallError, RunnerImage
from testmind.test_engine.models import (
    PYTEST_EXIT_CODES,
    ExecutionOutcome,
    InstallReport,
    TestCaseReport,
    TestRunReport,
    TestTotals,
)

GENERATED_PREFIX = "/workspace/tests/"
PROJECT_PREFIX = "/workspace/src/"
EXIT_NO_TESTS_COLLECTED = 5


class Sandbox(Protocol):
    def available(self, image: str | None = None) -> bool: ...

    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult: ...


class ImageBuilder(Protocol):
    def ensure(self, dependencies: list[Dependency]) -> RunnerImage: ...


class TestSuiteRunner:
    __test__ = False

    def __init__(self, sandbox: Sandbox, images: ImageBuilder) -> None:
        self._sandbox = sandbox
        self._images = images

    def run(
        self,
        root: Path,
        profile: RepositoryProfile,
        generated: dict[str, str] | None = None,
    ) -> TestRunReport:
        generated = generated or {}
        project_tests = list(profile.test_files)
        env = (
            {"DJANGO_SETTINGS_MODULE": profile.django_settings_module}
            if profile.django_settings_module
            else {}
        )
        base = _Base(project_tests, sorted(generated), env)

        if not project_tests and not generated:
            return base.report(ExecutionOutcome.NO_TESTS, install=None)
        if not self._sandbox.available():
            return base.report(ExecutionOutcome.SANDBOX_UNAVAILABLE, install=None)

        try:
            image = self._images.ensure(profile.dependencies)
        except DependencyInstallError as exc:
            install = InstallReport(
                image="", requirements=[], from_cache=False, notes=list(exc.notes), log=exc.log
            )
            return base.report(ExecutionOutcome.INSTALL_FAILED, install=install, output=str(exc))
        install = InstallReport(
            image=image.tag,
            requirements=list(image.requirements),
            from_cache=image.from_cache,
            notes=list(image.notes),
            log=image.log,
        )

        job = PytestJob(
            test_files=generated, project_tests=tuple(project_tests), image=image.tag, env=env
        )
        try:
            result = self._sandbox.run(root, job)
        except SandboxUnavailableError as exc:
            return base.report(
                ExecutionOutcome.SANDBOX_UNAVAILABLE, install=install, output=str(exc)
            )
        return base.from_result(result, install)


class _Base:
    def __init__(self, project_tests: list[str], generated: list[str], env: dict[str, str]) -> None:
        self.project_tests = project_tests
        self.generated = generated
        self.env = env

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
        )

    def from_result(self, result: SandboxResult, install: InstallReport) -> TestRunReport:
        rootdir = PROJECT_PREFIX if self.project_tests else GENERATED_PREFIX
        tests = [_case(t, rootdir) for t in result.tests]
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
    file = posixpath.normpath(posixpath.join(rootdir, test.file)) if test.file else None
    generated = False
    if file and file.startswith(GENERATED_PREFIX):
        file, generated = "tests/" + file.removeprefix(GENERATED_PREFIX), True
    elif file and file.startswith(PROJECT_PREFIX):
        file = file.removeprefix(PROJECT_PREFIX)
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

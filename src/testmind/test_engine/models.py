from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from testmind.sandbox.models import Outcome


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExecutionOutcome(StrEnum):
    PASSED = "passed"  # every collected test passed / xfailed / skipped
    FAILED = "failed"  # at least one test failed
    ERROR = "error"  # collection error, internal error or usage error
    NO_TESTS = "no_tests"
    TIMED_OUT = "timed_out"
    INSTALL_FAILED = "install_failed"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"


PYTEST_EXIT_CODES = {
    0: "all tests passed",
    1: "some tests failed",
    2: "test execution was interrupted (usually a collection error)",
    3: "internal pytest error",
    4: "pytest usage error",
    5: "no tests were collected",
}


class TestCaseReport(_Model):
    __test__ = False

    nodeid: str = Field(description="file::name when the file is known, else classname::name")
    file: str | None
    line: int | None
    name: str
    outcome: Outcome
    duration_seconds: float
    message: str | None
    traceback: str | None
    generated: bool = Field(description="True for tests produced by AI test generation")


class TestTotals(_Model):
    __test__ = False

    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    xfailed: int = 0


class InstallReport(_Model):
    image: str
    requirements: list[str]
    from_cache: bool
    notes: list[str]
    log: str


class TestRunReport(_Model):
    __test__ = False

    outcome: ExecutionOutcome
    exit_code: int | None
    exit_meaning: str | None
    totals: TestTotals
    duration_seconds: float
    tests: list[TestCaseReport]
    output: str = Field(description="Tail of pytest's console output")
    install: InstallReport | None
    project_test_files: list[str]
    generated_test_files: list[str]
    environment: dict[str, str]

    def failures(self) -> list[TestCaseReport]:
        return [t for t in self.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}]

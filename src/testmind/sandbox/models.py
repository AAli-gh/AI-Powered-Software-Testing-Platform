from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    XFAILED = "xfailed"


class TestCaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    __test__ = False  # not a pytest test class

    name: str
    classname: str
    file: str | None = None
    line: int | None = None
    outcome: Outcome
    message: str | None = None
    details: str | None = Field(default=None, description="Truncated traceback / output")
    duration_seconds: float = 0.0


class SandboxResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    exit_code: int
    timed_out: bool
    duration_seconds: float
    tests: list[TestCaseResult]
    output: str = Field(description="Truncated combined stdout/stderr")
    coverage_json: str | None = Field(default=None, description="coverage.py JSON, if measured")
    coverage_error: str | None = Field(default=None, description="Why coverage is missing")

    def count(self, outcome: Outcome) -> int:
        return sum(1 for t in self.tests if t.outcome is outcome)


class SandboxUnavailableError(RuntimeError):
    """Docker (or the sandbox image) is not available where this code runs."""

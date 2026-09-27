"""Mutation testing report."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from testmind.test_engine.models import ExecutionOutcome, TestTotals


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MutantStatus(StrEnum):
    KILLED = "killed"  # a test failed (or the mutant broke import): the tests noticed
    SURVIVED = "survived"  # every test that runs the line still passed
    TIMEOUT = "timeout"  # the tests hung (usually an endless loop): counted as detected
    NO_COVERAGE = "no_coverage"  # no test executes the line: not run
    ERROR = "error"  # no verdict (the driver could not run it); excluded from the score


class MutantResult(_Model):
    id: str
    path: str
    function: str
    line: int
    column: int
    start: int = Field(description="Byte offset of the mutated code in the file")
    end: int
    operator: str
    original: str
    replacement: str
    status: MutantStatus
    tests: int = Field(description="Tests that executed the line and were run against it")
    killed_by: str | None
    duration_seconds: float | None
    detail: str | None


class Score(_Model):
    total: int
    killed: int
    survived: int
    timeout: int
    no_coverage: int
    errors: int
    score: float | None = Field(
        description="detected / (detected + survived + no coverage), detected = killed + timeout"
    )
    covered_score: float | None = Field(
        description="detected / (detected + survived): the quality of the tests that do run"
    )


class FileScore(Score):
    path: str


class FunctionScore(Score):
    path: str
    function: str


class MutationReport(_Model):
    outcome: ExecutionOutcome = Field(description="Outcome of the baseline run of the suite")
    baseline_totals: TestTotals
    excluded_tests: list[str] = Field(
        description="Tests already failing without any mutation (not used to judge mutants)"
    )
    generated: int = Field(description="Mutants generated before the limit")
    totals: Score
    files: list[FileScore]
    functions: list[FunctionScore]
    mutants: list[MutantResult]
    duration_seconds: float
    notes: list[str]

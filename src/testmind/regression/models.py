"""Regression report: what changed between two commits, which tests that affects, and what
the two test runs show."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from testmind.coverage_engine import CoverageDiff
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import ExecutionOutcome, TestTotals


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RegressionMode(StrEnum):
    FULL = "full"  # run everything at head; also checks that the selection missed nothing
    SELECTED = "selected"  # run only the selected tests at head


class Verdict(StrEnum):
    REGRESSIONS = "regressions"  # at least one test passed at base and fails at head
    CLEAN = "clean"
    INCONCLUSIVE = "inconclusive"  # a side could not be run


class Commit(_Model):
    sha: str
    ref: str
    subject: str
    author: str
    timestamp: int


class ChangedFile(_Model):
    path: str
    status: str = Field(description="added, modified or deleted")
    kind: str = Field(description="production, test, configuration or other")


class ChangedFunction(_Model):
    path: str
    qualname: str
    status: str = Field(description="added, modified or removed")
    base_lines: tuple[int, int] | None
    head_lines: tuple[int, int] | None
    head_coverage: float | None = Field(
        default=None, description="Coverage of the function at head, when head was measured"
    )


class Changes(_Model):
    files: list[ChangedFile]
    functions: list[ChangedFunction]
    module_level: list[str] = Field(description="Python files whose module-level code changed")
    notes: list[str]


class SelectedTarget(_Model):
    target: str = Field(description="A test file or a pytest nodeid")
    reasons: list[str]


class Selection(_Model):
    everything: bool = Field(description="All tests are affected (e.g. a dependency change)")
    targets: list[SelectedTarget]
    notes: list[str]


class RunSummary(_Model):
    commit: str
    outcome: ExecutionOutcome
    totals: TestTotals
    coverage_percent: float | None
    failure_groups: int | None
    selected_only: bool
    snapshot_notes: list[str]


class TestChangeKind(StrEnum):
    __test__ = False

    REGRESSED = "regressed"  # passed at base, fails at head
    FIXED = "fixed"  # failed at base, passes at head
    STILL_FAILING = "still_failing"
    ADDED = "added"  # only at head
    REMOVED = "removed"  # only at base
    UNCHANGED = "unchanged"
    NOT_RUN = "not_run"  # not selected at head (selected mode)


class TestChange(_Model):
    __test__ = False

    nodeid: str
    base: Outcome | None
    head: Outcome | None
    change: TestChangeKind
    selected: bool


class FailureGroupChange(_Model):
    id: str
    category: str
    exception_type: str | None
    crash: str | None = Field(description="path:line")
    tests: int


class FailureChanges(_Model):
    new: list[FailureGroupChange] = Field(description="Failure causes that appear at head")
    resolved: list[FailureGroupChange] = Field(description="Causes that are gone at head")
    persisting: list[FailureGroupChange]


class SelectionCheck(_Model):
    """Full mode only: did the selection include every test that regressed?"""

    regressed: int
    missed: list[str] = Field(description="Regressed tests the selection did not include")
    selected_tests: int
    total_tests: int


class RegressionReport(_Model):
    base: Commit
    head: Commit
    mode: RegressionMode
    verdict: Verdict
    changes: Changes
    selection: Selection
    base_run: RunSummary
    head_run: RunSummary
    tests: list[TestChange]
    failures: FailureChanges
    coverage: CoverageDiff | None
    selection_check: SelectionCheck | None
    notes: list[str]

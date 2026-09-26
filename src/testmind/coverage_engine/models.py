"""Coverage report model (stored as JSON with a test execution, returned by the API)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CoverageCounts(_Model):
    """Line and branch counts. Percentages follow coverage.py: 100 when there is nothing to
    cover, and `percent` combines lines and branches like coverage.py's total."""

    statements: int
    covered_lines: int
    branches: int
    covered_branches: int
    line_percent: float
    branch_percent: float
    percent: float


class CoverageTotals(CoverageCounts):
    files: int
    functions: int
    functions_never_executed: int


class FileCoverage(CoverageCounts):
    path: str
    missing_lines: list[int] = Field(description="Statement lines that were not executed")
    missing: list[str] = Field(description="Line ranges not executed, e.g. ['12-15', '20']")
    missing_branches_detail: list[str] = Field(
        description="Branches never taken, 'from->to' ('exit' when leaving the function)"
    )


class FunctionCoverage(CoverageCounts):
    path: str
    qualname: str
    line: int
    end_line: int
    missing: list[str]
    missing_branches_detail: list[str]
    complexity: int
    findings: list[str] = Field(description="Rule ids of static-analysis findings in the function")
    is_public: bool


class CoverageGap(_Model):
    """A function worth testing next: poorly covered and risky (deterministic ranking)."""

    path: str
    qualname: str
    line: int
    percent: float
    missing: list[str]
    missing_branches_detail: list[str]
    risk: float = Field(description="uncovered share x (complexity + 2 x findings); higher first")
    reasons: list[str]


class CoverageReport(_Model):
    tool: str = Field(description="What measured the coverage, e.g. 'coverage.py 7.16.1'")
    branch_coverage: bool
    threshold: float = Field(description="Functions below this percentage are reported as gaps")
    totals: CoverageTotals
    files: list[FileCoverage]
    functions: list[FunctionCoverage]
    gaps: list[CoverageGap]
    unmeasured_files: list[str] = Field(
        description="Production modules coverage.py reported nothing for (not in the totals)"
    )


class FileDelta(_Model):
    path: str
    base_percent: float | None
    head_percent: float | None
    delta: float | None
    newly_covered_lines: int


class FunctionDelta(_Model):
    path: str
    qualname: str
    base_percent: float | None
    head_percent: float | None
    delta: float | None


class CoverageDiff(_Model):
    base_percent: float
    head_percent: float
    delta: float
    base_totals: CoverageTotals
    head_totals: CoverageTotals
    newly_covered_lines: int
    newly_missed_lines: int
    files: list[FileDelta] = Field(description="Files whose coverage changed")
    functions: list[FunctionDelta] = Field(description="Functions whose coverage changed")

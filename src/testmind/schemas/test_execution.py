from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.coverage_engine import CoverageGap, CoverageReport, CoverageTotals
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.test_engine.models import ExecutionOutcome, TestRunReport, TestTotals


class TestExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    __test__ = False

    generation_id: uuid.UUID | None = Field(
        default=None, description="Also run the test modules of this (succeeded) generation"
    )
    api_probes: bool = Field(
        default=False,
        description="Also run TestMind's deterministic API probes against the application",
    )


class ExecutionSummary(BaseModel):
    exit_code: int | None
    exit_meaning: str | None
    totals: TestTotals
    duration_seconds: float
    image: str | None
    dependencies_from_cache: bool | None
    install_notes: list[str]
    project_test_files: list[str]
    generated_test_files: list[str]
    environment: dict[str, str]
    coverage_percent: float | None = Field(
        description="Total line + branch coverage of production code, if measured"
    )
    coverage_error: str | None = Field(description="Why coverage is missing, if it is")
    failure_groups: int | None = Field(
        description="Failure groups found by the deterministic analysis (see /failures)"
    )
    api_coverage_percent: float | None = Field(
        description="Share of the HTTP endpoints the project's tests call (see /api-coverage)"
    )


class TestExecutionRead(BaseModel):
    """An execution without the per-test list and logs (see /tests and /logs)."""

    __test__ = False

    id: uuid.UUID
    run_id: uuid.UUID
    generation_id: uuid.UUID | None
    api_probes: bool
    status: RunStatus
    outcome: ExecutionOutcome | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: ExecutionSummary | None

    @classmethod
    def from_row(cls, row: TestExecution) -> TestExecutionRead:
        report = TestRunReport.model_validate(row.report) if row.report else None
        summary = None
        if report is not None:
            summary = ExecutionSummary(
                exit_code=report.exit_code,
                exit_meaning=report.exit_meaning,
                totals=report.totals,
                duration_seconds=report.duration_seconds,
                image=report.install.image if report.install else None,
                dependencies_from_cache=report.install.from_cache if report.install else None,
                install_notes=report.install.notes if report.install else [],
                project_test_files=report.project_test_files,
                generated_test_files=report.generated_test_files,
                environment=report.environment,
                coverage_percent=row.coverage_percent,
                coverage_error=report.coverage_error,
                failure_groups=len(row.failures["groups"]) if row.failures else None,
                api_coverage_percent=row.api_coverage_percent,
            )
        return cls(
            id=row.id,
            run_id=row.run_id,
            generation_id=row.generation_id,
            api_probes=row.api_probes,
            status=row.status,
            outcome=row.outcome,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            summary=summary,
        )


class ExecutionLogs(BaseModel):
    output: str = Field(description="Tail of pytest's console output")
    install_log: str = Field(description="Tail of the dependency installation log")


class CoverageOverview(BaseModel):
    tool: str
    branch_coverage: bool
    threshold: float
    totals: CoverageTotals
    unmeasured_files: list[str]
    gap_count: int
    top_gaps: list[CoverageGap] = Field(description="The riskiest gaps (see /coverage/gaps)")

    @classmethod
    def from_report(cls, report: CoverageReport, *, top: int = 10) -> CoverageOverview:
        return cls(
            tool=report.tool,
            branch_coverage=report.branch_coverage,
            threshold=report.threshold,
            totals=report.totals,
            unmeasured_files=report.unmeasured_files,
            gap_count=len(report.gaps),
            top_gaps=report.gaps[:top],
        )

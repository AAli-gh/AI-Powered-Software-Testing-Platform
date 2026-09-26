from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.test_engine.models import ExecutionOutcome, TestRunReport, TestTotals


class TestExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    __test__ = False

    generation_id: uuid.UUID | None = Field(
        default=None, description="Also run the test modules of this (succeeded) generation"
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


class TestExecutionRead(BaseModel):
    """An execution without the per-test list and logs (see /tests and /logs)."""

    __test__ = False

    id: uuid.UUID
    run_id: uuid.UUID
    generation_id: uuid.UUID | None
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
            )
        return cls(
            id=row.id,
            run_id=row.run_id,
            generation_id=row.generation_id,
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

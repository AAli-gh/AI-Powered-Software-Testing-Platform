from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.db.models import Regression
from testmind.domain.enums import RunStatus
from testmind.regression.models import RegressionMode, RegressionReport, TestChangeKind, Verdict


class RegressionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = Field(
        min_length=1, max_length=200, description="Branch, tag, HEAD or sha, optionally ~N"
    )
    head: str = Field(default="HEAD", min_length=1, max_length=200)
    mode: RegressionMode = RegressionMode.FULL


class RegressionSummary(BaseModel):
    changed_files: int
    changed_functions: int
    selected_targets: int
    everything_selected: bool
    regressed: int
    fixed: int
    new_failure_groups: int
    missed_by_selection: int | None = Field(description="Full mode only")


class RegressionRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: RunStatus
    mode: RegressionMode
    base_ref: str
    head_ref: str
    base_commit: str
    head_commit: str
    verdict: Verdict | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: RegressionSummary | None

    @classmethod
    def from_row(cls, row: Regression) -> RegressionRead:
        summary = None
        if row.report is not None:
            report = RegressionReport.model_validate(row.report)
            count = dict.fromkeys(TestChangeKind, 0)
            for test in report.tests:
                count[test.change] += 1
            summary = RegressionSummary(
                changed_files=len(report.changes.files),
                changed_functions=len(report.changes.functions),
                selected_targets=len(report.selection.targets),
                everything_selected=report.selection.everything,
                regressed=count[TestChangeKind.REGRESSED],
                fixed=count[TestChangeKind.FIXED],
                new_failure_groups=len(report.failures.new),
                missed_by_selection=(
                    len(report.selection_check.missed) if report.selection_check else None
                ),
            )
        return cls(
            id=row.id,
            project_id=row.project_id,
            status=row.status,
            mode=row.mode,
            base_ref=row.base_ref,
            head_ref=row.head_ref,
            base_commit=row.base_commit,
            head_commit=row.head_commit,
            verdict=row.verdict,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            summary=summary,
        )

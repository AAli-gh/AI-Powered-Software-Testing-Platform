from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.domain.enums import RunStatus
from testmind.failure_engine import FailureReport
from testmind.failure_engine.models import FailureAnalysisResult


class FailureOverview(BaseModel):
    failed_tests: int
    analysed_tests: int
    unanalysed_tests: list[str]
    notes: list[str]
    groups: int
    by_category: dict[str, int]
    decided: int = Field(description="Groups whose blame the facts decide (no AI needed)")

    @classmethod
    def from_report(cls, report: FailureReport) -> FailureOverview:
        return cls(
            failed_tests=report.failed_tests,
            analysed_tests=report.analysed_tests,
            unanalysed_tests=report.unanalysed_tests,
            notes=report.notes,
            groups=len(report.groups),
            by_category=dict(Counter(g.category.value for g in report.groups)),
            decided=sum(1 for g in report.groups if g.blame is not None),
        )


class FailureAnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    status: RunStatus
    provider: str
    model: str | None
    prompt_version: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: FailureAnalysisResult | None

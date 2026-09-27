from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from testmind.db.models import MutationRun
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.models import MutationReport, Score
from testmind.mutation_engine.suggester import SuggestionResult
from testmind.services.mutation_service import MutationOptions
from testmind.test_engine.models import ExecutionOutcome


class MutationSummary(BaseModel):
    baseline_outcome: ExecutionOutcome
    generated: int
    totals: Score
    notes: list[str]


class MutationRunRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    status: RunStatus
    options: MutationOptions
    score: float | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: MutationSummary | None

    @classmethod
    def from_row(cls, row: MutationRun) -> MutationRunRead:
        summary = None
        if row.report is not None:
            report = MutationReport.model_validate(row.report)
            summary = MutationSummary(
                baseline_outcome=report.outcome,
                generated=report.generated,
                totals=report.totals,
                notes=report.notes,
            )
        return cls(
            id=row.id,
            run_id=row.run_id,
            status=row.status,
            options=MutationOptions.model_validate(row.options),
            score=row.score,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            summary=summary,
        )


class SuggestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mutation_run_id: uuid.UUID
    status: RunStatus
    provider: str
    model: str | None
    prompt_version: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: SuggestionResult | None

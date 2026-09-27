from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.api_engine.models import (
    ApiCoverageReport,
    ApiGap,
    ApiTotals,
    Framework,
    ProbeVerdict,
)
from testmind.api_engine.suggester import ApiSuggestionResult
from testmind.domain.enums import RunStatus


class ApiCoverageOverview(BaseModel):
    frameworks: list[Framework]
    totals: ApiTotals
    gap_count: int
    top_gaps: list[ApiGap] = Field(description="The first gaps (see /api-coverage/gaps)")
    unmatched_requests: int = Field(description="Requests no route answered (see /unmatched)")
    probes: dict[ProbeVerdict, int] = Field(description="Probe verdicts (see /probes)")
    notes: list[str]

    @classmethod
    def from_report(cls, report: ApiCoverageReport, *, top: int = 10) -> ApiCoverageOverview:
        return cls(
            frameworks=report.frameworks,
            totals=report.totals,
            gap_count=len(report.gaps),
            top_gaps=report.gaps[:top],
            unmatched_requests=len(report.unmatched),
            probes=dict(sorted(Counter(p.verdict for p in report.probes).items())),
            notes=report.notes,
        )


class ApiSuggestionRead(BaseModel):
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
    result: ApiSuggestionResult | None

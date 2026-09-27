from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from testmind.db.models import GitHubImport, GitHubPullAnalysis
from testmind.domain.enums import RunStatus
from testmind.github.models import CreatedIssue, IssueSuggestion, PullReport
from testmind.regression.models import TestChangeKind

REPOSITORY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$"


class WebhookResponse(BaseModel):
    status: str = Field(description="pong, queued, duplicate or ignored")
    id: uuid.UUID | None = Field(default=None, description="The pull request analysis")
    reason: str | None = None


class PullAnalysisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(pattern=REPOSITORY_PATTERN, examples=["acme/shop"])
    pull_number: int = Field(ge=1)
    installation_id: int = Field(ge=1, description="The GitHub App installation")


class PullSummary(BaseModel):
    title: str
    url: str
    regressed_tests: int
    new_findings: int
    untested_changes: int
    suggestions: list[IssueSuggestion]


class PullAnalysisRead(BaseModel):
    id: uuid.UUID
    repository: str
    pull_number: int
    installation_id: int
    head_sha: str | None
    base_sha: str | None
    status: RunStatus
    conclusion: str | None
    check_run_id: int | None
    comment_id: int | None
    issues: list[CreatedIssue]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: PullSummary | None

    @classmethod
    def from_row(cls, row: GitHubPullAnalysis) -> PullAnalysisRead:
        summary = None
        if row.report is not None:
            report = PullReport.model_validate(row.report)
            summary = PullSummary(
                title=report.title,
                url=report.url,
                regressed_tests=sum(
                    1 for t in report.regression.tests if t.change is TestChangeKind.REGRESSED
                ),
                new_findings=len(report.new_findings),
                untested_changes=len(report.untested_changes),
                suggestions=report.suggestions,
            )
        return cls(
            id=row.id,
            repository=row.repository,
            pull_number=row.pull_number,
            installation_id=row.installation_id,
            head_sha=row.head_sha,
            base_sha=row.base_sha,
            status=row.status,
            conclusion=row.conclusion,
            check_run_id=row.check_run_id,
            comment_id=row.comment_id,
            issues=[CreatedIssue.model_validate(i) for i in row.issues],
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            summary=summary,
        )


class IssuesCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[str] = Field(min_length=1, max_length=50, description="Suggestion ids")


class ImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(pattern=REPOSITORY_PATTERN, examples=["acme/shop"])
    ref: str = Field(
        default="HEAD", min_length=1, max_length=200, description="Branch, tag or commit sha"
    )
    installation_id: int = Field(ge=1)


class ImportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository: str
    ref: str
    installation_id: int
    status: RunStatus
    commit_sha: str | None
    project_id: uuid.UUID | None
    run_id: uuid.UUID | None
    skipped: list[str]
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_row(cls, row: GitHubImport) -> ImportRead:
        return cls.model_validate(row)

"""Aggregates for the dashboard: counts and trends computed from the stored jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from testmind.domain.enums import RunStatus, SourceType
from testmind.regression.models import RegressionMode, Verdict
from testmind.schemas.analysis_run import AnalysisRunRead
from testmind.schemas.project import ProjectRead
from testmind.test_engine.models import ExecutionOutcome


class StatusCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class JobCounts(BaseModel):
    kind: str = Field(description="The job table, e.g. test_executions")
    counts: StatusCounts


class TestTotalsPoint(BaseModel):
    __test__ = False

    total: int
    passed: int
    failed: int = Field(description="Failed tests plus errors")
    skipped: int


class ExecutionPoint(BaseModel):
    """One test execution, as a point of a trend."""

    id: uuid.UUID
    run_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    status: RunStatus
    outcome: ExecutionOutcome | None
    created_at: datetime
    finished_at: datetime | None
    tests: TestTotalsPoint | None = Field(description="None until the suite has run")
    coverage_percent: float | None
    api_coverage_percent: float | None


class MutationPoint(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None
    score: float | None
    mutants: int | None
    survived: int | None


class RegressionPoint(BaseModel):
    id: uuid.UUID
    status: RunStatus
    mode: RegressionMode
    base_ref: str
    head_ref: str
    verdict: Verdict | None
    created_at: datetime


class PullPoint(BaseModel):
    id: uuid.UUID
    repository: str
    pull_number: int
    status: RunStatus
    conclusion: str | None
    head_sha: str | None
    created_at: datetime


class ProjectHealth(BaseModel):
    """A project with its latest results, for the overview table."""

    id: uuid.UUID
    name: str
    source_type: SourceType
    remote_repository: str | None
    latest_run: AnalysisRunRead | None
    latest_execution: ExecutionPoint | None = Field(
        description="The latest execution that ran the suite (succeeded)"
    )
    latest_mutation_score: float | None
    active_jobs: int = Field(description="Queued or running analysis runs")


class PlatformOverview(BaseModel):
    projects: int
    jobs: list[JobCounts] = Field(description="Every job table, by status")
    active_jobs: int
    project_health: list[ProjectHealth] = Field(
        description="Projects with the most recent activity first (at most 50)"
    )
    recent_executions: list[ExecutionPoint] = Field(description="Newest first (at most 10)")
    recent_pull_analyses: list[PullPoint] = Field(description="Newest first (at most 10)")


class ProjectOverview(BaseModel):
    project: ProjectRead
    runs: StatusCounts
    latest_run: AnalysisRunRead | None
    latest_succeeded_run: AnalysisRunRead | None
    executions: list[ExecutionPoint] = Field(description="Oldest first: the last 50")
    mutation_runs: list[MutationPoint] = Field(description="Oldest first: the last 50")
    regressions: list[RegressionPoint] = Field(description="Newest first: the last 20")
    pull_analyses: list[PullPoint] = Field(
        description="Of the project's GitHub repository, newest first: the last 20"
    )

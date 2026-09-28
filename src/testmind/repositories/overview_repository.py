"""Read-only aggregate queries for the dashboard.

Large documents (reports) are never loaded here: only their totals are extracted in SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, String, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import (
    AnalysisRun,
    ApiTestSuggestion,
    FailureAnalysis,
    GitHubImport,
    GitHubPullAnalysis,
    MutantTestSuggestion,
    MutationRun,
    Project,
    Regression,
    TestExecution,
    TestGeneration,
)
from testmind.domain.enums import ACTIVE_STATUSES, RunStatus

JOB_MODELS: tuple[Any, ...] = (
    AnalysisRun,
    TestGeneration,
    TestExecution,
    FailureAnalysis,
    Regression,
    MutationRun,
    MutantTestSuggestion,
    ApiTestSuggestion,
    GitHubPullAnalysis,
    GitHubImport,
)


@dataclass(frozen=True, slots=True)
class JobStatistics:
    kind: str  # the job's table name
    status: RunStatus
    count: int
    oldest_created: datetime
    oldest_started: datetime | None  # None while every job with this status is queued


def _execution_columns() -> Select[Any]:
    return (
        select(
            TestExecution.id,
            TestExecution.run_id,
            AnalysisRun.project_id,
            Project.name.label("project_name"),
            TestExecution.status,
            TestExecution.outcome,
            TestExecution.created_at,
            TestExecution.finished_at,
            # The small totals object as one value: each JSON operator in SQL decompresses
            # the whole report again, so its fields are read in Python.
            TestExecution.report["totals"].label("totals"),
            TestExecution.coverage_percent,
            TestExecution.api_coverage_percent,
        )
        .join(AnalysisRun, AnalysisRun.id == TestExecution.run_id)
        .join(Project, Project.id == AnalysisRun.project_id)
    )


def _execution_rows(chosen: Select[Any]) -> Select[Any]:
    """The execution columns of the executions `chosen` selects by id.

    Choosing (filter, sort, limit) happens on narrow columns first, so the totals are read
    from the JSON reports of the chosen executions only. In a single query PostgreSQL would
    extract them from every candidate's report before sorting (docs/performance.md).
    """
    picked = chosen.subquery()
    return _execution_columns().join(picked, picked.c.id == TestExecution.id)


class OverviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -------------------------------------------------------------------------- counts
    async def count_projects(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Project))
        return result.scalar_one()

    async def status_counts(self, model: Any, *, project_id: uuid.UUID) -> dict[RunStatus, int]:
        query = (
            select(model.status, func.count())
            .where(model.project_id == project_id)
            .group_by(model.status)
        )
        rows = await self._session.execute(query)
        return {row[0]: row[1] for row in rows}

    async def job_statistics(self) -> list[JobStatistics]:
        """Per job kind and status: how many, and the oldest creation and start times.

        One round trip for every job table (UNION ALL), for the overview and for metrics.
        """
        parts = [
            select(
                literal(model.__tablename__).label("kind"),
                model.status.cast(String).label("status"),
                func.count().label("jobs"),
                func.min(model.created_at).label("oldest_created"),
                func.min(model.started_at).label("oldest_started"),
            ).group_by(model.status)
            for model in JOB_MODELS
        ]
        rows = await self._session.execute(union_all(*parts))
        return [
            JobStatistics(
                kind=row.kind,
                status=RunStatus(row.status),
                count=row.jobs,
                oldest_created=row.oldest_created,
                oldest_started=row.oldest_started,
            )
            for row in rows
        ]

    # --------------------------------------------------------------------------- trends
    async def executions(
        self, *, project_id: uuid.UUID | None = None, limit: int
    ) -> Sequence[Row[Any]]:
        """Newest first."""
        chosen = select(TestExecution.id)
        if project_id is not None:
            chosen = chosen.join(AnalysisRun, AnalysisRun.id == TestExecution.run_id).where(
                AnalysisRun.project_id == project_id
            )
        newest = (TestExecution.created_at.desc(), TestExecution.id)
        query = _execution_rows(chosen.order_by(*newest).limit(limit)).order_by(*newest)
        rows: Sequence[Row[Any]] = (await self._session.execute(query)).all()
        return rows

    async def mutation_runs(self, project_id: uuid.UUID, *, limit: int) -> Sequence[Row[Any]]:
        """Newest first. Like executions, the runs are chosen before their totals are read."""
        newest = (MutationRun.created_at.desc(), MutationRun.id)
        picked = (
            select(MutationRun.id)
            .join(AnalysisRun, AnalysisRun.id == MutationRun.run_id)
            .where(AnalysisRun.project_id == project_id)
            .order_by(*newest)
            .limit(limit)
            .subquery()
        )
        query = (
            select(
                MutationRun.id,
                MutationRun.run_id,
                MutationRun.status,
                MutationRun.created_at,
                MutationRun.finished_at,
                MutationRun.score,
                MutationRun.report["totals"].label("totals"),  # read once, as for executions
            )
            .join(picked, picked.c.id == MutationRun.id)
            .order_by(*newest)
        )
        return (await self._session.execute(query)).all()

    async def regressions(self, project_id: uuid.UUID, *, limit: int) -> Sequence[Regression]:
        result = await self._session.execute(
            select(Regression)
            .where(Regression.project_id == project_id)
            .order_by(Regression.created_at.desc(), Regression.id)
            .limit(limit)
        )
        return result.scalars().all()

    async def pull_analyses(
        self, *, repository: str | None = None, limit: int
    ) -> Sequence[GitHubPullAnalysis]:
        query = select(GitHubPullAnalysis)
        if repository is not None:
            query = query.where(GitHubPullAnalysis.repository == repository)
        result = await self._session.execute(
            query.order_by(GitHubPullAnalysis.created_at.desc(), GitHubPullAnalysis.id).limit(limit)
        )
        return result.scalars().all()

    async def latest_run(
        self, project_id: uuid.UUID, *, status: RunStatus | None = None
    ) -> AnalysisRun | None:
        """The project's latest run (with that status)."""
        query = select(AnalysisRun).where(AnalysisRun.project_id == project_id)
        if status is not None:
            query = query.where(AnalysisRun.status == status)
        result = await self._session.execute(
            query.order_by(AnalysisRun.created_at.desc(), AnalysisRun.id).limit(1)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ project health
    async def recent_projects(self, *, limit: int) -> Sequence[Project]:
        """Projects by their latest activity (latest run, else creation), newest first."""
        latest = (
            select(AnalysisRun.project_id, func.max(AnalysisRun.created_at).label("at"))
            .group_by(AnalysisRun.project_id)
            .subquery()
        )
        result = await self._session.execute(
            select(Project)
            .outerjoin(latest, latest.c.project_id == Project.id)
            .order_by(func.coalesce(latest.c.at, Project.created_at).desc(), Project.id)
            .limit(limit)
        )
        return result.scalars().all()

    async def latest_runs(self, project_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, AnalysisRun]:
        result = await self._session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.project_id.in_(project_ids))
            .order_by(AnalysisRun.project_id, AnalysisRun.created_at.desc(), AnalysisRun.id)
            .ext(distinct_on(AnalysisRun.project_id))
        )
        return {run.project_id: run for run in result.scalars()}

    async def latest_executions(
        self, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, Row[Any]]:
        """Each project's latest execution that ran its suite."""
        chosen = (
            select(TestExecution.id)
            .join(AnalysisRun, AnalysisRun.id == TestExecution.run_id)
            .where(
                AnalysisRun.project_id.in_(project_ids),
                TestExecution.status == RunStatus.SUCCEEDED,
            )
            .order_by(AnalysisRun.project_id, TestExecution.created_at.desc(), TestExecution.id)
            .ext(distinct_on(AnalysisRun.project_id))
        )
        query = _execution_rows(chosen)
        return {row.project_id: row for row in (await self._session.execute(query)).all()}

    async def latest_mutation_scores(
        self, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, float | None]:
        query = (
            select(AnalysisRun.project_id, MutationRun.score)
            .join(AnalysisRun, AnalysisRun.id == MutationRun.run_id)
            .where(
                AnalysisRun.project_id.in_(project_ids),
                MutationRun.status == RunStatus.SUCCEEDED,
            )
            .order_by(AnalysisRun.project_id, MutationRun.created_at.desc(), MutationRun.id)
            .ext(distinct_on(AnalysisRun.project_id))
        )
        return {row[0]: row[1] for row in await self._session.execute(query)}

    async def active_runs(self, project_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        query = (
            select(AnalysisRun.project_id, func.count())
            .where(
                AnalysisRun.project_id.in_(project_ids),
                AnalysisRun.status.in_(ACTIVE_STATUSES),
            )
            .group_by(AnalysisRun.project_id)
        )
        return {row[0]: row[1] for row in await self._session.execute(query)}

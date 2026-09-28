"""Read-only aggregate queries for the dashboard.

Large documents (reports) are never loaded here: only their totals are extracted in SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, Select, func, select
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


def _execution_columns() -> Select[Any]:
    totals = TestExecution.report["totals"]
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
            totals["total"].as_integer().label("total"),
            totals["passed"].as_integer().label("passed"),
            totals["failed"].as_integer().label("failed"),
            totals["errors"].as_integer().label("errors"),
            totals["skipped"].as_integer().label("skipped"),
            TestExecution.coverage_percent,
            TestExecution.api_coverage_percent,
        )
        .join(AnalysisRun, AnalysisRun.id == TestExecution.run_id)
        .join(Project, Project.id == AnalysisRun.project_id)
    )


class OverviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -------------------------------------------------------------------------- counts
    async def count_projects(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Project))
        return result.scalar_one()

    async def status_counts(
        self, model: Any, *, project_id: uuid.UUID | None = None
    ) -> dict[RunStatus, int]:
        query = select(model.status, func.count()).group_by(model.status)
        if project_id is not None:
            query = query.where(model.project_id == project_id)
        rows = await self._session.execute(query)
        return {row[0]: row[1] for row in rows}

    # --------------------------------------------------------------------------- trends
    async def executions(
        self, *, project_id: uuid.UUID | None = None, limit: int
    ) -> Sequence[Row[Any]]:
        """Newest first."""
        query = _execution_columns()
        if project_id is not None:
            query = query.where(AnalysisRun.project_id == project_id)
        query = query.order_by(TestExecution.created_at.desc(), TestExecution.id).limit(limit)
        rows: Sequence[Row[Any]] = (await self._session.execute(query)).all()
        return rows

    async def mutation_runs(self, project_id: uuid.UUID, *, limit: int) -> Sequence[Row[Any]]:
        """Newest first."""
        totals = MutationRun.report["totals"]
        query = (
            select(
                MutationRun.id,
                MutationRun.run_id,
                MutationRun.status,
                MutationRun.created_at,
                MutationRun.finished_at,
                MutationRun.score,
                totals["total"].as_integer().label("mutants"),
                totals["survived"].as_integer().label("survived"),
            )
            .join(AnalysisRun, AnalysisRun.id == MutationRun.run_id)
            .where(AnalysisRun.project_id == project_id)
            .order_by(MutationRun.created_at.desc(), MutationRun.id)
            .limit(limit)
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
        query = (
            _execution_columns()
            .where(
                AnalysisRun.project_id.in_(project_ids),
                TestExecution.status == RunStatus.SUCCEEDED,
            )
            .order_by(AnalysisRun.project_id, TestExecution.created_at.desc(), TestExecution.id)
            .ext(distinct_on(AnalysisRun.project_id))
        )
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

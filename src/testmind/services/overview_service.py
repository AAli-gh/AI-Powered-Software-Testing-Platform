"""Dashboard aggregates: platform-wide counts and a project's trends."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import NotFoundError
from testmind.db.models import AnalysisRun, GitHubPullAnalysis
from testmind.domain.enums import RunStatus
from testmind.repositories.overview_repository import JOB_MODELS, OverviewRepository
from testmind.repositories.project_repository import ProjectRepository
from testmind.schemas.analysis_run import AnalysisRunRead
from testmind.schemas.overview import (
    ExecutionPoint,
    JobCounts,
    MutationPoint,
    PlatformOverview,
    ProjectHealth,
    ProjectOverview,
    PullPoint,
    RegressionPoint,
    StatusCounts,
    TestTotalsPoint,
)
from testmind.schemas.project import ProjectRead

MAX_PROJECTS = 50
MAX_RECENT = 10
MAX_TREND = 50
MAX_LIST = 20


class OverviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._overview = OverviewRepository(session)
        self._projects = ProjectRepository(session)

    async def platform(self) -> PlatformOverview:
        jobs = [
            JobCounts(
                kind=model.__tablename__, counts=_counts(await self._overview.status_counts(model))
            )
            for model in JOB_MODELS
        ]
        projects = await self._overview.recent_projects(limit=MAX_PROJECTS)
        ids = [p.id for p in projects]
        runs = await self._overview.latest_runs(ids)
        executions = await self._overview.latest_executions(ids)
        scores = await self._overview.latest_mutation_scores(ids)
        active = await self._overview.active_runs(ids)
        health = [
            ProjectHealth(
                id=p.id,
                name=p.name,
                source_type=p.source_type,
                remote_repository=p.remote_repository,
                latest_run=_run(runs.get(p.id)),
                latest_execution=_execution(executions[p.id]) if p.id in executions else None,
                latest_mutation_score=scores.get(p.id),
                active_jobs=active.get(p.id, 0),
            )
            for p in projects
        ]
        return PlatformOverview(
            projects=await self._overview.count_projects(),
            jobs=jobs,
            active_jobs=sum(j.counts.queued + j.counts.running for j in jobs),
            project_health=health,
            recent_executions=[
                _execution(r) for r in await self._overview.executions(limit=MAX_RECENT)
            ],
            recent_pull_analyses=[
                _pull(p) for p in await self._overview.pull_analyses(limit=MAX_RECENT)
            ],
        )

    async def project(self, project_id: uuid.UUID) -> ProjectOverview:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"Project '{project_id}' not found")
        executions = await self._overview.executions(project_id=project_id, limit=MAX_TREND)
        mutations = await self._overview.mutation_runs(project_id, limit=MAX_TREND)
        pulls = (
            await self._overview.pull_analyses(repository=project.remote_repository, limit=MAX_LIST)
            if project.remote_repository
            else []
        )
        return ProjectOverview(
            project=ProjectRead.model_validate(project),
            runs=_counts(await self._overview.status_counts(AnalysisRun, project_id=project_id)),
            latest_run=_run(await self._overview.latest_run(project_id)),
            latest_succeeded_run=_run(
                await self._overview.latest_run(project_id, status=RunStatus.SUCCEEDED)
            ),
            executions=[_execution(r) for r in reversed(executions)],
            mutation_runs=[
                MutationPoint(
                    id=r.id,
                    run_id=r.run_id,
                    status=r.status,
                    created_at=r.created_at,
                    finished_at=r.finished_at,
                    score=r.score,
                    mutants=r.mutants,
                    survived=r.survived,
                )
                for r in reversed(mutations)
            ],
            regressions=[
                RegressionPoint(
                    id=r.id,
                    status=r.status,
                    mode=r.mode,
                    base_ref=r.base_ref,
                    head_ref=r.head_ref,
                    verdict=r.verdict,
                    created_at=r.created_at,
                )
                for r in await self._overview.regressions(project_id, limit=MAX_LIST)
            ],
            pull_analyses=[_pull(p) for p in pulls],
        )


def _counts(counts: dict[RunStatus, int]) -> StatusCounts:
    return StatusCounts(**{status.value: count for status, count in counts.items()})


def _run(run: AnalysisRun | None) -> AnalysisRunRead | None:
    return AnalysisRunRead.model_validate(run) if run is not None else None


def _execution(row: Row[Any]) -> ExecutionPoint:
    tests = None
    if row.total is not None:
        tests = TestTotalsPoint(
            total=row.total,
            passed=row.passed or 0,
            failed=(row.failed or 0) + (row.errors or 0),
            skipped=row.skipped or 0,
        )
    return ExecutionPoint(
        id=row.id,
        run_id=row.run_id,
        project_id=row.project_id,
        project_name=row.project_name,
        status=row.status,
        outcome=row.outcome,
        created_at=row.created_at,
        finished_at=row.finished_at,
        tests=tests,
        coverage_percent=row.coverage_percent,
        api_coverage_percent=row.api_coverage_percent,
    )


def _pull(row: GitHubPullAnalysis) -> PullPoint:
    return PullPoint(
        id=row.id,
        repository=row.repository,
        pull_number=row.pull_number,
        status=row.status,
        conclusion=row.conclusion,
        head_sha=row.head_sha,
        created_at=row.created_at,
    )

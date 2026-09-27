from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import GitHubImport, GitHubPullAnalysis, Project

JobT = TypeVar("JobT", GitHubPullAnalysis, GitHubImport)


class GitHubRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: JobT) -> JobT:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, model: type[JobT], job_id: uuid.UUID) -> JobT | None:
        return await self._session.get(model, job_id)

    async def lock(self, model: type[JobT], job_id: uuid.UUID) -> JobT | None:
        result = await self._session.execute(
            select(model)
            .where(model.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def by_delivery(self, delivery_id: str) -> GitHubPullAnalysis | None:
        result = await self._session.execute(
            select(GitHubPullAnalysis).where(GitHubPullAnalysis.delivery_id == delivery_id)
        )
        return result.scalar_one_or_none()

    async def list_pull_analyses(
        self, *, repository: str | None, pull_number: int | None, offset: int, limit: int
    ) -> tuple[Sequence[GitHubPullAnalysis], int]:
        conditions = []
        if repository is not None:
            conditions.append(GitHubPullAnalysis.repository == repository)
        if pull_number is not None:
            conditions.append(GitHubPullAnalysis.pull_number == pull_number)
        rows = await self._session.execute(
            select(GitHubPullAnalysis)
            .where(*conditions)
            .order_by(GitHubPullAnalysis.created_at.desc(), GitHubPullAnalysis.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(GitHubPullAnalysis).where(*conditions)
        )
        return rows.scalars().all(), total.scalar_one()

    async def previous_comment(
        self, repository: str, pull_number: int, excluding: uuid.UUID
    ) -> int | None:
        """TestMind's comment on the pull request, posted by an earlier analysis."""
        result = await self._session.execute(
            select(GitHubPullAnalysis.comment_id)
            .where(
                GitHubPullAnalysis.repository == repository,
                GitHubPullAnalysis.pull_number == pull_number,
                GitHubPullAnalysis.comment_id.is_not(None),
                GitHubPullAnalysis.id != excluding,
            )
            .order_by(GitHubPullAnalysis.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def project_for(self, repository: str) -> Project | None:
        result = await self._session.execute(
            select(Project).where(Project.remote_repository == repository)
        )
        return result.scalar_one_or_none()

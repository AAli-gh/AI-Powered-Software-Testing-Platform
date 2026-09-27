"""Requesting regression analyses (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import (
    DispatchError,
    InvalidRevisionError,
    NotFoundError,
    NotVersionedError,
)
from testmind.core.logging import get_logger
from testmind.db.models import Regression
from testmind.domain.enums import RunStatus
from testmind.regression.models import RegressionMode
from testmind.repositories.project_repository import ProjectRepository
from testmind.repositories.regression_repository import RegressionRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.vcs import GitError, GitRepository

logger = get_logger(__name__)


class RegressionService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: TaskDispatcher,
        repos_root: Path,
        *,
        imports_root: Path | None = None,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._regressions = RegressionRepository(session)
        self._projects = ProjectRepository(session)

    async def create(
        self, project_id: uuid.UUID, base: str, head: str, mode: RegressionMode
    ) -> Regression:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"Project '{project_id}' not found")
        root = resolve_local_source(
            self._repos_root, project.source_location, imports_root=self._imports_root
        )
        # Resolve now: the job compares these exact commits even if a branch moves later.
        base_commit, head_commit = _resolve(root, base, head)
        if base_commit == head_commit:
            raise InvalidRevisionError(f"'{base}' and '{head}' are the same commit")

        regression = await self._regressions.add(
            Regression(
                project_id=project_id,
                status=RunStatus.QUEUED,
                mode=mode,
                base_ref=base,
                head_ref=head,
                base_commit=base_commit,
                head_commit=head_commit,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_regression(regression.id)
        except Exception:
            logger.exception("regression_dispatch_failed", regression_id=str(regression.id))
            locked = await self._regressions.get_for_update(regression.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return regression

    async def get(self, regression_id: uuid.UUID) -> Regression:
        regression = await self._regressions.get(regression_id)
        if regression is None:
            raise NotFoundError(f"Regression '{regression_id}' not found")
        return regression

    async def list_for_project(
        self, project_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[Regression], int]:
        if await self._projects.get(project_id) is None:
            raise NotFoundError(f"Project '{project_id}' not found")
        return await self._regressions.list_for_project(project_id, offset=offset, limit=limit)


def _resolve(root: Path, base: str, head: str) -> tuple[str, str]:
    try:
        git = GitRepository(root)
    except GitError as exc:
        raise NotVersionedError(str(exc)) from None
    try:
        return git.resolve(base).sha, git.resolve(head).sha
    except GitError as exc:
        raise InvalidRevisionError(str(exc)) from None
    finally:
        git.close()

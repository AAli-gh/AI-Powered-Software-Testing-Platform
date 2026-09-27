"""Executes a queued regression analysis (background worker)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.regression.engine import RegressionEngine
from testmind.regression.models import RegressionReport
from testmind.repositories.regression_repository import RegressionRepository
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source
from testmind.vcs import GitError

logger = get_logger(__name__)


class RegressionExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repos_root: Path,
        engine: RegressionEngine,
        *,
        imports_root: Path | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root
        self._imports_root = imports_root
        self._engine = engine

    async def execute(self, regression_id: uuid.UUID) -> RunStatus | None:
        async with self._session_factory() as session:
            repository = RegressionRepository(session)
            regression = await repository.get_for_update(regression_id)
            if regression is None:
                logger.warning("regression_not_found", regression_id=str(regression_id))
                return None
            if regression.status is not RunStatus.QUEUED:
                return regression.status
            location = await repository.source_location(regression_id)
            # The commits resolved at request time, not the (possibly moved) refs.
            base, head, mode = regression.base_commit, regression.head_commit, regression.mode
            refs = (regression.base_ref, regression.head_ref)
            apply_transition(regression, RunStatus.RUNNING)
            await session.commit()

        try:
            root = resolve_local_source(
                self._repos_root, location or "", imports_root=self._imports_root
            )
            report = await asyncio.to_thread(self._engine.run, root, base, head, mode)
        except (AppError, GitError) as exc:
            return await self._finish(regression_id, RunStatus.FAILED, str(exc))
        except Exception:
            logger.exception("regression_crashed", regression_id=str(regression_id))
            await self._finish(regression_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        # Report the refs as requested (the engine saw only the resolved shas).
        report = report.model_copy(
            update={
                "base": report.base.model_copy(update={"ref": refs[0]}),
                "head": report.head.model_copy(update={"ref": refs[1]}),
            }
        )
        return await self._finish(regression_id, RunStatus.SUCCEEDED, None, report)

    async def _finish(
        self,
        regression_id: uuid.UUID,
        status: RunStatus,
        error_message: str | None,
        report: RegressionReport | None = None,
    ) -> RunStatus:
        async with self._session_factory() as session:
            regression = await RegressionRepository(session).get_for_update(regression_id)
            if regression is None:
                return status
            apply_transition(regression, status, error_message=error_message)
            if report is not None:
                regression.report = report.model_dump(mode="json")
                regression.verdict = report.verdict
            await session.commit()
        logger.info("regression_finished", regression_id=str(regression_id), status=status.value)
        return status

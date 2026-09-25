from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.run_repository import RunRepository
from testmind.services.run_service import apply_transition
from testmind.services.source_locator import resolve_local_source

logger = get_logger(__name__)

UNEXPECTED_ERROR_MESSAGE = "Unexpected internal error while executing the run"


class RunExecutor:
    """Executes a queued analysis run (called by the background worker).

    Each status change is committed in its own short transaction so the row lock is never held
    while the actual work is running.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], repos_root: Path) -> None:
        self._session_factory = session_factory
        self._repos_root = repos_root

    async def execute(self, run_id: uuid.UUID) -> RunStatus | None:
        """Run the job. Returns the final status, or None if the run does not exist.

        Safe to call more than once for the same run (the queue delivers at-least-once): only a
        run that is still `queued` is picked up; anything else is left untouched.
        """
        async with self._session_factory() as session:
            run = await RunRepository(session).get_for_update(run_id)
            if run is None:
                logger.warning("run_not_found", run_id=str(run_id))
                return None
            if run.status is not RunStatus.QUEUED:
                logger.info("run_skipped", run_id=str(run_id), status=run.status.value)
                return run.status
            source_location = run.project.source_location
            apply_transition(run, RunStatus.RUNNING)
            await session.commit()

        logger.info("run_started", run_id=str(run_id))
        try:
            self._perform(source_location)
        except AppError as exc:
            return await self._finish(run_id, RunStatus.FAILED, exc.message)
        except Exception:
            logger.exception("run_crashed", run_id=str(run_id))
            await self._finish(run_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
            raise
        return await self._finish(run_id, RunStatus.SUCCEEDED, None)

    def _perform(self, source_location: str) -> None:
        """The work done by a run.

        Phase 1 scope: verify the project's source directory is still present and inside the
        repositories root. Repository analysis (Phase 2) is added to this step.
        """
        resolve_local_source(self._repos_root, source_location)

    async def _finish(
        self, run_id: uuid.UUID, status: RunStatus, error_message: str | None
    ) -> RunStatus:
        async with self._session_factory() as session:
            run = await RunRepository(session).get_for_update(run_id)
            if run is None:  # project deleted while running
                logger.warning("run_vanished", run_id=str(run_id))
                return status
            apply_transition(run, status, error_message=error_message)
            await session.commit()
        logger.info("run_finished", run_id=str(run_id), status=status.value)
        return status

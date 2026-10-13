"""Starting and reading website scans (API side)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import DispatchError, InvalidTargetError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import WebScan
from testmind.domain.enums import RunStatus
from testmind.repositories.web_scan_repository import WebScanRepository
from testmind.schemas.web_scan import WebScanCreate
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition
from testmind.web_engine.network_policy import NetworkPolicy

logger = get_logger(__name__)


class WebScanService:
    def __init__(self, session: AsyncSession, dispatcher: TaskDispatcher) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._scans = WebScanRepository(session)

    async def create(self, request: WebScanCreate) -> WebScan:
        # Public scans never reach internal hosts; the policy is also the final gate in the
        # browser, but refusing here gives the caller a clear 422 instead of a failed job.
        policy = NetworkPolicy.for_target(request.target_url, allow_internal=False)
        refusal = policy.refusal(request.target_url)
        if refusal is not None:
            raise InvalidTargetError(f"The target URL cannot be scanned: {refusal}")
        scan = await self._scans.add(
            WebScan(
                target_url=request.target_url,
                status=RunStatus.QUEUED,
                max_pages=request.max_pages,
                max_depth=request.max_depth,
                interact=request.interact,
                security=request.security,
                allow_internal=False,
            )
        )
        await self._session.commit()
        try:
            await self._dispatcher.dispatch_web_scan(scan.id)
        except Exception:
            logger.exception("web_scan_dispatch_failed", scan_id=str(scan.id))
            locked = await self._scans.lock(scan.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return scan

    async def get(self, scan_id: uuid.UUID) -> WebScan:
        scan = await self._scans.get(scan_id)
        if scan is None:
            raise NotFoundError(f"Web scan '{scan_id}' not found")
        return scan

    async def page(self, *, limit: int, offset: int) -> tuple[Sequence[WebScan], int]:
        return await self._scans.page(limit=limit, offset=offset)

"""Website scans: crawl a site, exercise its controls, and report the issues found."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from testmind.api.deps import WebScanServiceDep
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.web_scan import WebScanCreate, WebScanRead

router = APIRouter(prefix="/api/v1/web-scans", tags=["Web scanning"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Scan not found"}
}
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        422: {"model": ErrorResponse, "description": "The target URL cannot be scanned"},
        503: {"model": ErrorResponse, "description": "The queue is unavailable"},
    },
)
async def create_web_scan(request: WebScanCreate, service: WebScanServiceDep) -> WebScanRead:
    """Queue a crawl of a public website. Internal and loopback hosts are refused."""
    return WebScanRead.model_validate(await service.create(request))


@router.get("")
async def list_web_scans(
    service: WebScanServiceDep, limit: Limit = 50, offset: Offset = 0
) -> Page[WebScanRead]:
    scans, total = await service.page(limit=limit, offset=offset)
    return Page[WebScanRead](
        items=[WebScanRead.model_validate(s) for s in scans],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{scan_id}", responses=NOT_FOUND)
async def get_web_scan(scan_id: uuid.UUID, service: WebScanServiceDep) -> WebScanRead:
    return WebScanRead.model_validate(await service.get(scan_id))

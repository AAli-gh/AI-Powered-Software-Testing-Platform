"""Authorized targets: prove you own a host so active scans may run against it."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status

from testmind.api.deps import AuthorizedTargetServiceDep
from testmind.schemas.authorized_target import AuthorizedTargetCreate, AuthorizedTargetRead
from testmind.schemas.common import ErrorResponse

router = APIRouter(prefix="/api/v1/authorized-targets", tags=["Authorized targets"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Target not found"}
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={422: {"model": ErrorResponse, "description": "Not a public host"}},
)
async def register_target(
    request: AuthorizedTargetCreate, service: AuthorizedTargetServiceDep
) -> AuthorizedTargetRead:
    """Register a host you own; a verification token is returned to publish on the site."""
    return AuthorizedTargetRead.model_validate(await service.register(request.host))


@router.get("")
async def list_targets(service: AuthorizedTargetServiceDep) -> list[AuthorizedTargetRead]:
    return [AuthorizedTargetRead.model_validate(t) for t in await service.all()]


@router.get("/{target_id}", responses=NOT_FOUND)
async def get_target(
    target_id: uuid.UUID, service: AuthorizedTargetServiceDep
) -> AuthorizedTargetRead:
    return AuthorizedTargetRead.model_validate(await service.get(target_id))


@router.post("/{target_id}/verify", responses=NOT_FOUND)
async def verify_target(
    target_id: uuid.UUID, service: AuthorizedTargetServiceDep
) -> AuthorizedTargetRead:
    """Fetch the well-known file and confirm the token. Safe to call repeatedly."""
    return AuthorizedTargetRead.model_validate(await service.verify(target_id))

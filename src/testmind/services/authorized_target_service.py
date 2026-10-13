"""Registering and verifying hosts the user owns, so active scans may run against them."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import InvalidTargetError, NotFoundError
from testmind.core.logging import get_logger
from testmind.db.models import AuthorizedTarget
from testmind.repositories.authorized_target_repository import AuthorizedTargetRepository
from testmind.web_engine import ownership
from testmind.web_engine.network_policy import NetworkPolicy

logger = get_logger(__name__)

VERIFY_FAILED = "The verification file was not found, or did not contain the token"


class TargetProber(Protocol):
    """Fetches the text at a URL, or None if it could not be read. The production prober is an
    httpx client (through the egress proxy); tests pass a fake."""

    def __call__(self, url: str) -> str | None: ...


class AuthorizedTargetService:
    def __init__(self, session: AsyncSession, prober: TargetProber) -> None:
        self._session = session
        self._prober = prober
        self._targets = AuthorizedTargetRepository(session)

    async def register(self, host: str) -> AuthorizedTarget:
        normalized = ownership.normalize_host(host)
        if not normalized or _is_internal(normalized):
            raise InvalidTargetError(f"{host!r} is not a public host that can be verified")
        existing = await self._targets.by_host(normalized)
        if existing is not None:
            return existing  # keep the same token, so a published file stays valid
        target = await self._targets.add(
            AuthorizedTarget(host=normalized, token=ownership.new_token(), verified=False)
        )
        await self._session.commit()
        return target

    async def verify(self, target_id: uuid.UUID) -> AuthorizedTarget:
        target = await self._require(target_id)
        policy = NetworkPolicy.for_target(f"https://{target.host}/")
        fetched: str | None = None
        for url in ownership.verification_urls(target.host):
            if policy.allows(url):
                fetched = self._prober(url)
                if ownership.token_matches(target.token, fetched):
                    target.verified = True
                    target.verified_at = datetime.now(UTC)
                    target.last_error = None
                    await self._session.commit()
                    logger.info("authorized_target_verified", host=target.host)
                    return target
        target.verified = False
        target.last_error = VERIFY_FAILED
        await self._session.commit()
        return target

    async def get(self, target_id: uuid.UUID) -> AuthorizedTarget:
        return await self._require(target_id)

    async def all(self) -> Sequence[AuthorizedTarget]:
        return await self._targets.all()

    async def is_authorized(self, host: str) -> bool:
        target = await self._targets.by_host(ownership.normalize_host(host))
        return target is not None and target.verified

    async def _require(self, target_id: uuid.UUID) -> AuthorizedTarget:
        target = await self._targets.get(target_id)
        if target is None:
            raise NotFoundError(f"Authorized target '{target_id}' not found")
        return target


def _is_internal(host: str) -> bool:
    return NetworkPolicy.for_target(f"https://{host}/").refusal(f"https://{host}/") is not None


MAX_VERIFICATION_BYTES = 8192
VERIFICATION_TIMEOUT_SECONDS = 10.0


def build_prober() -> TargetProber:
    """The production prober: a short-timeout httpx GET through the egress proxy."""
    import httpx  # noqa: PLC0415 - only needed in the worker/API process that verifies

    def fetch(url: str) -> str | None:
        try:
            response = httpx.get(url, timeout=VERIFICATION_TIMEOUT_SECONDS, follow_redirects=True)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:  # noqa: PLR2004
            return None
        return response.text[:MAX_VERIFICATION_BYTES]

    return fetch

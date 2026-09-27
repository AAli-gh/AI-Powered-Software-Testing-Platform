"""GitHub integration, API side: webhook deliveries, analysis and import requests, issues."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.config import Settings
from testmind.core.errors import (
    AppError,
    ConflictError,
    DispatchError,
    GitHubNotConfiguredError,
    GitHubRequestError,
    InvalidSignatureError,
    NotFoundError,
    PayloadTooLargeError,
    RepositoryNotAllowedError,
)
from testmind.core.logging import get_logger
from testmind.db.models import GitHubImport, GitHubPullAnalysis
from testmind.domain.enums import RunStatus
from testmind.github.client import (
    AppCredentials,
    GitHubClient,
    GitHubError,
    check_ref,
    check_repository,
)
from testmind.github.models import CreatedIssue, PullReport
from testmind.github.webhooks import (
    MAX_PAYLOAD_BYTES,
    InvalidPayloadError,
    PullEvent,
    pull_request_event,
    valid_signature,
)
from testmind.repositories.github_repository import GitHubRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, apply_transition

logger = get_logger(__name__)

JobT = TypeVar("JobT", GitHubPullAnalysis, GitHubImport)
ClientFactory = Callable[[], GitHubClient]


class InvalidWebhookPayloadError(AppError):
    code = "invalid_payload"


@dataclass(frozen=True, slots=True)
class WebhookResult:
    status: str  # pong, queued, duplicate, ignored
    id: uuid.UUID | None = None
    reason: str | None = None


def app_credentials(settings: Settings) -> AppCredentials:
    key = settings.github_private_key_pem()
    if settings.github_app_id is None or key is None:
        raise GitHubNotConfiguredError(
            "The GitHub App is not configured (TESTMIND_GITHUB_APP_ID and "
            "TESTMIND_GITHUB_PRIVATE_KEY or TESTMIND_GITHUB_PRIVATE_KEY_FILE)"
        )
    return AppCredentials(settings.github_app_id, key)


def client_factory(settings: Settings) -> ClientFactory:
    def create() -> GitHubClient:
        return GitHubClient(
            app_credentials(settings),
            api_url=settings.github_api_url,
            max_archive_bytes=settings.github_max_archive_mb * 1024 * 1024,
        )

    return create


class GitHubService:
    def __init__(
        self,
        session: AsyncSession,
        dispatcher: TaskDispatcher,
        settings: Settings,
        clients: ClientFactory | None = None,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher
        self._settings = settings
        self._clients = clients or client_factory(settings)
        self._jobs = GitHubRepository(session)

    # ----------------------------------------------------------------------- webhooks
    async def receive(
        self, event: str, delivery: str, body: bytes, signature: str | None
    ) -> WebhookResult:
        secret = self._settings.github_webhook_secret
        if secret is None:
            raise GitHubNotConfiguredError(
                "Webhooks are not configured (TESTMIND_GITHUB_WEBHOOK_SECRET)"
            )
        if len(body) > MAX_PAYLOAD_BYTES:
            raise PayloadTooLargeError(f"Webhook payloads are limited to {MAX_PAYLOAD_BYTES} bytes")
        if not valid_signature(secret.get_secret_value(), body, signature):
            raise InvalidSignatureError("The delivery is not signed with the webhook secret")
        if event == "ping":
            return WebhookResult("pong")
        if event != "pull_request":
            return WebhookResult("ignored", reason=f"event {event!r} is not handled")
        try:
            parsed = pull_request_event(body)
        except InvalidPayloadError as exc:
            raise InvalidWebhookPayloadError(str(exc)) from None
        if isinstance(parsed, str):
            return WebhookResult("ignored", reason=parsed)
        if not self._allowed(parsed.repository):
            return WebhookResult("ignored", reason="the repository's owner is not allowed")
        existing = await self._jobs.by_delivery(delivery)
        if existing is not None:
            return WebhookResult("duplicate", existing.id)
        job = await self._queue_pull(parsed, delivery)
        return WebhookResult("queued", job.id)

    # -------------------------------------------------------------- pull request analyses
    async def request_analysis(
        self, repository: str, pull_number: int, installation_id: int
    ) -> GitHubPullAnalysis:
        """Analyse the pull request's current head (without a webhook)."""
        app_credentials(self._settings)  # fail now, not in the worker
        self._check_allowed(repository)
        event = PullEvent(installation_id, repository, pull_number, head_sha="", base_sha="")
        return await self._queue_pull(event, None)

    async def get(self, analysis_id: uuid.UUID) -> GitHubPullAnalysis:
        job = await self._jobs.get(GitHubPullAnalysis, analysis_id)
        if job is None:
            raise NotFoundError(f"Pull request analysis '{analysis_id}' not found")
        return job

    async def report(self, analysis_id: uuid.UUID) -> PullReport:
        job = await self.get(analysis_id)
        if job.report is None:
            raise NotFoundError(
                f"Pull request analysis '{analysis_id}' has no report (status: {job.status.value})"
            )
        return PullReport.model_validate(job.report)

    async def list_analyses(
        self, *, repository: str | None, pull_number: int | None, offset: int, limit: int
    ) -> tuple[Sequence[GitHubPullAnalysis], int]:
        return await self._jobs.list_pull_analyses(
            repository=repository, pull_number=pull_number, offset=offset, limit=limit
        )

    async def create_issues(
        self, analysis_id: uuid.UUID, suggestion_ids: list[str]
    ) -> list[CreatedIssue]:
        """Create GitHub issues from the report's suggestions (each at most once)."""
        job = await self.get(analysis_id)
        report = await self.report(analysis_id)
        suggestions = {s.id: s for s in report.suggestions}
        unknown = [s for s in suggestion_ids if s not in suggestions]
        if unknown:
            raise NotFoundError(f"Unknown suggestion(s): {', '.join(unknown)}")
        done = {i["suggestion"]: CreatedIssue.model_validate(i) for i in job.issues}
        created: list[CreatedIssue] = []
        client = self._clients()
        try:
            for suggestion_id in dict.fromkeys(suggestion_ids):
                if suggestion_id in done:
                    created.append(done[suggestion_id])
                    continue
                suggestion = suggestions[suggestion_id]
                number, url = await client.create_issue(
                    job.installation_id, job.repository, title=suggestion.title,
                    body=suggestion.body, labels=suggestion.labels,
                )  # fmt: skip
                issue = CreatedIssue(suggestion=suggestion_id, number=number, url=url)
                done[suggestion_id] = issue
                created.append(issue)
                job.issues = [i.model_dump() for i in done.values()]
                await self._session.commit()  # recorded one by one: a retry never duplicates
        except GitHubError as exc:
            raise GitHubRequestError(str(exc)) from None
        finally:
            await client.close()
        return created

    # ------------------------------------------------------------ repositories as projects
    async def request_import(self, repository: str, ref: str, installation_id: int) -> GitHubImport:
        app_credentials(self._settings)
        if self._settings.github_imports_root is None:
            raise GitHubNotConfiguredError(
                "Imports are not configured (TESTMIND_GITHUB_IMPORTS_ROOT)"
            )
        try:
            check_repository(repository)
            check_ref(ref)
        except GitHubError as exc:
            raise InvalidWebhookPayloadError(str(exc)) from None
        self._check_allowed(repository)
        job = await self._jobs.add(
            GitHubImport(
                installation_id=installation_id,
                repository=repository,
                ref=ref,
                status=RunStatus.QUEUED,
                skipped=[],
            )
        )
        return await self._dispatch(job, self._dispatcher.dispatch_github_import)

    async def get_import(self, import_id: uuid.UUID) -> GitHubImport:
        job = await self._jobs.get(GitHubImport, import_id)
        if job is None:
            raise NotFoundError(f"GitHub import '{import_id}' not found")
        return job

    # --------------------------------------------------------------------------- helpers
    async def _queue_pull(self, event: PullEvent, delivery: str | None) -> GitHubPullAnalysis:
        job = GitHubPullAnalysis(
            delivery_id=delivery,
            installation_id=event.installation_id,
            repository=event.repository,
            pull_number=event.number,
            head_sha=event.head_sha or None,
            base_sha=event.base_sha or None,
            status=RunStatus.QUEUED,
            issues=[],
        )
        try:
            await self._jobs.add(job)
        except IntegrityError:  # the same delivery, concurrently
            await self._session.rollback()
            raise ConflictError(f"Delivery '{delivery}' is already being handled") from None
        return await self._dispatch(job, self._dispatcher.dispatch_pull_analysis)

    async def _dispatch(self, job: JobT, send: Callable[[uuid.UUID], Awaitable[None]]) -> JobT:
        await self._session.commit()
        try:
            await send(job.id)
        except Exception:
            logger.exception("github_dispatch_failed", job_id=str(job.id))
            locked = await self._jobs.lock(type(job), job.id)
            if locked is not None and locked.status is RunStatus.QUEUED:
                apply_transition(locked, RunStatus.FAILED, error_message=DISPATCH_FAILED_MESSAGE)
            await self._session.commit()
            raise DispatchError(DISPATCH_FAILED_MESSAGE) from None
        return job

    def _allowed(self, repository: str) -> bool:
        allowed = {o.lower() for o in self._settings.github_allowed_owners}
        return not allowed or repository.split("/", maxsplit=1)[0].lower() in allowed

    def _check_allowed(self, repository: str) -> None:
        try:
            check_repository(repository)
        except GitHubError as exc:
            raise InvalidWebhookPayloadError(str(exc)) from None
        if not self._allowed(repository):
            raise RepositoryNotAllowedError(
                f"Repositories of '{repository.split('/', maxsplit=1)[0]}' are not allowed "
                "(TESTMIND_GITHUB_ALLOWED_OWNERS)"
            )

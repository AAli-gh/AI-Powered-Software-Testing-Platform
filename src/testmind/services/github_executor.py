"""Executes queued pull request analyses and GitHub imports (background worker)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import AppError
from testmind.core.logging import get_logger
from testmind.db.models import GitHubImport, GitHubPullAnalysis, Project
from testmind.domain.enums import RunStatus, SourceType
from testmind.github.client import GitHubClient, GitHubError, PullRequest, check_repository
from testmind.github.models import PullReport
from testmind.github.pull_analysis import PullAnalyzer, render, title
from testmind.repositories.github_repository import GitHubRepository
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.run_service import RunService, apply_transition
from testmind.services.source_locator import GITHUB_PREFIX
from testmind.vcs import GitError
from testmind.vcs.archive import ArchiveError

logger = get_logger(__name__)

JobT = TypeVar("JobT", GitHubPullAnalysis, GitHubImport)
EXPECTED = (GitHubError, ArchiveError, GitError, AppError)
FAILED_TITLE = "TestMind could not analyse this pull request"


async def _finish(
    session_factory: async_sessionmaker[AsyncSession],
    model: type[JobT],
    job_id: uuid.UUID,
    status: RunStatus,
    *,
    error_message: str | None = None,
    fields: dict[str, Any] | None = None,
) -> RunStatus:
    async with session_factory() as session:
        job = await GitHubRepository(session).lock(model, job_id)
        if job is None:
            return status
        if status is not RunStatus.RUNNING:
            apply_transition(job, status, error_message=error_message)
        for name, value in (fields or {}).items():
            setattr(job, name, value)
        await session.commit()
    return status


async def _start(
    session_factory: async_sessionmaker[AsyncSession], model: type[JobT], job_id: uuid.UUID
) -> JobT | RunStatus | None:
    """The job, now running; its status if it was not queued; None if it does not exist."""
    async with session_factory() as session:
        job = await GitHubRepository(session).lock(model, job_id)
        if job is None:
            logger.warning("github_job_not_found", table=model.__tablename__, job_id=str(job_id))
            return None
        if job.status is not RunStatus.QUEUED:
            return job.status
        apply_transition(job, RunStatus.RUNNING)
        await session.commit()
        session.expunge(job)
        return job


class PullAnalysisExecutor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clients: Callable[[], GitHubClient],
        analyzer: Callable[[], PullAnalyzer],
        check_name: str = "TestMind",
        public_url: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clients = clients
        self._analyzer = analyzer
        self._check_name = check_name
        self._public_url = public_url.rstrip("/") if public_url else None

    async def execute(self, analysis_id: uuid.UUID) -> RunStatus | None:
        job = await _start(self._session_factory, GitHubPullAnalysis, analysis_id)
        if not isinstance(job, GitHubPullAnalysis):
            return job
        client: GitHubClient | None = None
        check_run: int | None = None
        async with contextlib.AsyncExitStack() as stack:
            try:
                client = self._clients()
                stack.push_async_callback(client.close)
                pull = await client.pull_request(
                    job.installation_id, job.repository, job.pull_number
                )
                # The commits of the delivery, even if the pull request moved on since.
                pull = dataclasses.replace(
                    pull,
                    head_sha=job.head_sha or pull.head_sha,
                    base_sha=job.base_sha or pull.base_sha,
                )
                check_run = await client.create_check_run(
                    job.installation_id, job.repository, pull.head_sha, self._check_name
                )
                await self._done(
                    analysis_id, RunStatus.RUNNING,
                    fields={"check_run_id": check_run, "head_sha": pull.head_sha,
                            "base_sha": pull.base_sha},
                )  # fmt: skip
                report = await self._analyse(client, job.installation_id, pull)
                body = render(report, analysis_url=self._url(analysis_id))
                await client.complete_check_run(
                    job.installation_id, job.repository, check_run,
                    conclusion=report.conclusion.value, title=title(report), summary=body,
                )  # fmt: skip
                async with self._session_factory() as session:
                    previous = await GitHubRepository(session).previous_comment(
                        job.repository, job.pull_number, analysis_id
                    )
                comment = await client.upsert_comment(
                    job.installation_id, job.repository, job.pull_number, body, previous
                )
            except EXPECTED as exc:
                message = exc.message if isinstance(exc, AppError) else str(exc)
                await self._report_failure(client, job, check_run, message)
                return await self._done(analysis_id, RunStatus.FAILED, message)
            except Exception:
                logger.exception("pull_analysis_crashed", analysis_id=str(analysis_id))
                await self._report_failure(client, job, check_run, UNEXPECTED_ERROR_MESSAGE)
                await self._done(analysis_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
                raise
        return await self._done(
            analysis_id, RunStatus.SUCCEEDED,
            fields={"report": report.model_dump(mode="json"),
                    "conclusion": report.conclusion.value, "comment_id": comment},
        )  # fmt: skip

    async def _analyse(
        self, client: GitHubClient, installation: int, pull: PullRequest
    ) -> PullReport:
        with tempfile.TemporaryDirectory(prefix="testmind-pull-") as scratch:
            snapshots = []
            for side, sha in (("base", pull.base_sha), ("head", pull.head_sha)):
                destination = Path(scratch) / side
                destination.mkdir()
                snapshots.append(
                    await client.download(installation, pull.repository, sha, destination)
                )
            return await asyncio.to_thread(self._analyzer().analyze, pull, *snapshots)

    async def _report_failure(
        self,
        client: GitHubClient | None,
        job: GitHubPullAnalysis,
        check_run: int | None,
        message: str,
    ) -> None:
        """Tell the pull request why there is no result (best effort)."""
        if client is None or check_run is None:
            return
        try:
            await client.complete_check_run(
                job.installation_id, job.repository, check_run,
                conclusion="neutral", title=FAILED_TITLE, summary=message,
            )  # fmt: skip
        except GitHubError:
            logger.warning("check_run_not_completed", job_id=str(job.id))

    def _url(self, analysis_id: uuid.UUID) -> str | None:
        if self._public_url is None:
            return None
        return f"{self._public_url}/api/v1/github/pull-analyses/{analysis_id}"

    async def _done(
        self,
        job_id: uuid.UUID,
        status: RunStatus,
        error: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        return await _finish(
            self._session_factory, GitHubPullAnalysis, job_id, status,
            error_message=error, fields=fields,
        )  # fmt: skip


class ImportExecutor:
    """Download a commit into the imports root and make it a project with an analysis run."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clients: Callable[[], GitHubClient],
        imports_root: Path | None,
        dispatcher: TaskDispatcher,
    ) -> None:
        self._session_factory = session_factory
        self._clients = clients
        self._imports_root = imports_root
        self._dispatcher = dispatcher

    async def execute(self, import_id: uuid.UUID) -> RunStatus | None:
        job = await _start(self._session_factory, GitHubImport, import_id)
        if not isinstance(job, GitHubImport):
            return job
        async with contextlib.AsyncExitStack() as stack:
            try:
                if self._imports_root is None:
                    raise GitHubError("Imports are not configured (TESTMIND_GITHUB_IMPORTS_ROOT)")
                client = self._clients()
                stack.push_async_callback(client.close)
                sha = await client.commit_sha(job.installation_id, job.repository, job.ref)
                skipped = await self._download(client, job, sha, self._imports_root)
                project_id, run_id = await self._project(job, sha)
            except EXPECTED as exc:
                message = exc.message if isinstance(exc, AppError) else str(exc)
                return await self._done(import_id, RunStatus.FAILED, message)
            except Exception:
                logger.exception("github_import_crashed", import_id=str(import_id))
                await self._done(import_id, RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
                raise
        return await self._done(
            import_id, RunStatus.SUCCEEDED,
            fields={"commit_sha": sha, "project_id": project_id, "run_id": run_id,
                    "skipped": skipped},
        )  # fmt: skip

    async def _download(
        self, client: GitHubClient, job: GitHubImport, sha: str, imports_root: Path
    ) -> list[str]:
        """The commit's files in <imports root>/<owner>/<repo>/<sha> (written once)."""
        target = imports_root / check_repository(job.repository) / sha
        partial = await asyncio.to_thread(_prepare, target)
        if partial is None:
            return []  # imported before
        try:
            snapshot = await client.download(job.installation_id, job.repository, sha, partial)
            await asyncio.to_thread(_publish, partial, target)
        finally:
            await asyncio.to_thread(shutil.rmtree, partial, ignore_errors=True)
        return list(snapshot.skipped)

    async def _project(self, job: GitHubImport, sha: str) -> tuple[uuid.UUID, uuid.UUID]:
        location = f"{GITHUB_PREFIX}{job.repository}/{sha}"
        async with self._session_factory() as session:
            project = await GitHubRepository(session).project_for(job.repository)
            if project is None:
                project = Project(
                    name=job.repository, source_type=SourceType.GITHUB, source_location=location,
                    remote_repository=job.repository,
                )  # fmt: skip
                session.add(project)
            project.source_location = location
            project.remote_ref = job.ref
            project.remote_commit = sha
            await session.commit()
            run = await RunService(session, self._dispatcher).create(project.id)
            return project.id, run.id

    async def _done(
        self,
        job_id: uuid.UUID,
        status: RunStatus,
        error: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> RunStatus:
        return await _finish(
            self._session_factory, GitHubImport, job_id, status, error_message=error, fields=fields
        )


def _prepare(target: Path) -> Path | None:
    """A fresh directory next to `target` to download into; None if `target` exists."""
    if target.is_dir():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))


def _publish(partial: Path, target: Path) -> None:
    """Complete snapshots only: an interrupted download is never used."""
    partial.chmod(0o755)  # readable by the sandbox's unprivileged user
    partial.rename(target)

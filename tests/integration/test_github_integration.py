"""The GitHub integration against the real database and the fake GitHub API (tests/github_fake.py:
JWTs verified, installation tokens checked, real tarballs). The regression engine and the code
analysis are real; only the sandbox is canned here. The real sandbox and Celery path is covered
by tests/sandbox/test_github_end_to_end.py."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import Settings
from testmind.core.errors import (
    ConflictError,
    DispatchError,
    GitHubNotConfiguredError,
    GitHubRequestError,
    InvalidSignatureError,
    NotFoundError,
    PayloadTooLargeError,
    RepositoryNotAllowedError,
)
from testmind.db.models import GitHubImport, GitHubPullAnalysis, Project
from testmind.domain.enums import RunStatus, SourceType
from testmind.github.client import GitHubClient
from testmind.github.pull_analysis import MARKER, PullAnalyzer
from testmind.github.webhooks import MAX_PAYLOAD_BYTES, PullEvent
from testmind.regression.engine import RegressionEngine
from testmind.sandbox.models import Outcome
from testmind.services.github_executor import (
    FAILED_TITLE,
    ImportExecutor,
    PullAnalysisExecutor,
    _finish,
)
from testmind.services.github_service import (
    GitHubService,
    InvalidWebhookPayloadError,
    client_factory,
)
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE
from testmind.services.source_locator import resolve_local_source
from testmind.test_engine.runner import TestSuiteRunner
from tests.conftest import GITHUB_REPOSITORY
from tests.conftest import GITHUB_WEBHOOK_SECRET as SECRET
from tests.doubles import FailingDispatcher, RecordingDispatcher
from tests.github_fake import FakeGitHub, pull_event, sign
from tests.pull_fixture import HEAD
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

JobT = TypeVar("JobT", GitHubPullAnalysis, GitHubImport)
REPOSITORY = GITHUB_REPOSITORY


def service(
    session: AsyncSession,
    settings: Settings,
    clients: Callable[[], GitHubClient] | None = None,
    dispatcher: RecordingDispatcher | FailingDispatcher | None = None,
) -> GitHubService:
    return GitHubService(session, dispatcher or RecordingDispatcher(), settings, clients)


def analysis_executor(
    session_factory: async_sessionmaker[AsyncSession],
    clients: Callable[[], GitHubClient],
    analyzer: Callable[[], PullAnalyzer] | None = None,
    public_url: str | None = "https://testmind.example/",
) -> PullAnalysisExecutor:
    canned = result(case("test_discount", Outcome.PASSED, file="tests/test_pricing.py"))
    runner = TestSuiteRunner(Sandbox(canned), Images())
    return PullAnalysisExecutor(
        session_factory,
        clients=clients,
        analyzer=analyzer or (lambda: PullAnalyzer(RegressionEngine(runner))),
        check_name="TestMind",
        public_url=public_url,
    )


async def stored(
    session_factory: async_sessionmaker[AsyncSession], model: type[JobT], job_id: uuid.UUID
) -> JobT:
    async with session_factory() as session:
        row = await session.get(model, job_id)
        assert row is not None
        return row


async def queue_pull(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    shas: tuple[str, str],
    delivery: str = "delivery-1",
) -> uuid.UUID:
    body = json.dumps(pull_event(REPOSITORY, 7, *shas)).encode()
    async with session_factory() as session:
        received = await service(session, settings).receive(
            "pull_request", delivery, body, sign(SECRET, body)
        )
    assert received.status == "queued"
    assert received.id is not None
    return received.id


# ------------------------------------------------------------------------------- webhooks
async def test_a_signed_pull_request_event_is_queued_once(
    session: AsyncSession, github_settings: Settings
) -> None:
    dispatcher = RecordingDispatcher()
    github_service = service(session, github_settings, dispatcher=dispatcher)
    body = json.dumps(pull_event(REPOSITORY, 7, "a" * 40, "b" * 40, installation=5)).encode()

    queued = await github_service.receive("pull_request", "d-1", body, sign(SECRET, body))
    again = await github_service.receive("pull_request", "d-1", body, sign(SECRET, body))

    assert (queued.status, again.status, again.id) == ("queued", "duplicate", queued.id)
    assert dispatcher.pull_analyses == [queued.id]
    assert queued.id is not None
    job = await github_service.get(queued.id)
    assert (job.repository, job.pull_number, job.installation_id) == (REPOSITORY, 7, 5)
    assert (job.base_sha, job.head_sha, job.status) == ("a" * 40, "b" * 40, RunStatus.QUEUED)


async def test_webhook_deliveries_that_are_refused_or_ignored(
    session: AsyncSession, github_settings: Settings
) -> None:
    github_service = service(session, github_settings)
    body = json.dumps(pull_event(REPOSITORY, 7, "a" * 40, "b" * 40)).encode()

    with pytest.raises(InvalidSignatureError):
        await github_service.receive("pull_request", "d", body, sign("wrong", body))
    with pytest.raises(InvalidSignatureError):
        await github_service.receive("pull_request", "d", body, None)
    big = b" " * (MAX_PAYLOAD_BYTES + 1)
    with pytest.raises(PayloadTooLargeError):
        await github_service.receive("pull_request", "d", big, sign(SECRET, big))
    with pytest.raises(InvalidWebhookPayloadError):
        await github_service.receive("pull_request", "d", b"[]", sign(SECRET, b"[]"))

    assert (await github_service.receive("ping", "d", b"{}", sign(SECRET, b"{}"))).status == "pong"
    ignored = await github_service.receive("push", "d", b"{}", sign(SECRET, b"{}"))
    assert (ignored.status, ignored.reason) == ("ignored", "event 'push' is not handled")
    closed = json.dumps(pull_event(REPOSITORY, 7, "a" * 40, "b" * 40, action="closed")).encode()
    result = await github_service.receive("pull_request", "d", closed, sign(SECRET, closed))
    assert result.status == "ignored"

    restricted = github_settings.model_copy(update={"github_allowed_owners": ["Other"]})
    result = await service(session, restricted).receive(
        "pull_request", "d", body, sign(SECRET, body)
    )
    assert (result.status, result.reason) == ("ignored", "the repository's owner is not allowed")
    allowed = github_settings.model_copy(update={"github_allowed_owners": ["ACME"]})
    result = await service(session, allowed).receive("pull_request", "d", body, sign(SECRET, body))
    assert result.status == "queued"

    unconfigured = github_settings.model_copy(update={"github_webhook_secret": None})
    with pytest.raises(GitHubNotConfiguredError):
        await service(session, unconfigured).receive("ping", "d", b"{}", sign(SECRET, b"{}"))


async def test_manual_requests_check_the_configuration_and_the_repository(
    session: AsyncSession, settings: Settings, github_settings: Settings
) -> None:
    with pytest.raises(GitHubNotConfiguredError):
        await service(session, settings).request_analysis(REPOSITORY, 7, 1)
    with pytest.raises(GitHubNotConfiguredError):
        await service(session, settings).request_import(REPOSITORY, "main", 1)
    no_imports = github_settings.model_copy(update={"github_imports_root": None})
    with pytest.raises(GitHubNotConfiguredError):
        await service(session, no_imports).request_import(REPOSITORY, "main", 1)
    with pytest.raises(InvalidWebhookPayloadError):
        await service(session, github_settings).request_analysis("acme/..", 7, 1)
    with pytest.raises(InvalidWebhookPayloadError):
        await service(session, github_settings).request_import(REPOSITORY, "a..b", 1)
    restricted = github_settings.model_copy(update={"github_allowed_owners": ["other"]})
    with pytest.raises(RepositoryNotAllowedError, match="'acme' are not allowed"):
        await service(session, restricted).request_analysis(REPOSITORY, 7, 1)
    with pytest.raises(RepositoryNotAllowedError):
        await service(session, restricted).request_import(REPOSITORY, "main", 1)

    dispatcher = RecordingDispatcher()
    github_service = service(session, github_settings, dispatcher=dispatcher)
    job = await github_service.request_analysis(REPOSITORY, 7, 1)
    imported = await github_service.request_import(REPOSITORY, "main", 1)
    assert (job.head_sha, job.base_sha, job.delivery_id) == (None, None, None)
    assert dispatcher.pull_analyses == [job.id]
    assert dispatcher.github_imports == [imported.id]
    assert (await github_service.get_import(imported.id)).ref == "main"
    with pytest.raises(NotFoundError):
        await github_service.get_import(uuid.uuid4())
    with pytest.raises(NotFoundError):
        await github_service.get(uuid.uuid4())
    with pytest.raises(NotFoundError, match="has no report"):
        await github_service.report(job.id)


async def test_a_job_that_cannot_be_queued_is_failed(
    session_factory: async_sessionmaker[AsyncSession], github_settings: Settings
) -> None:
    async with session_factory() as session:
        github_service = service(session, github_settings, dispatcher=FailingDispatcher())
        with pytest.raises(DispatchError):
            await github_service.request_analysis(REPOSITORY, 7, 1)
        with pytest.raises(DispatchError):
            await github_service.request_import(REPOSITORY, "main", 1)
        analyses, total = await github_service.list_analyses(
            repository=None, pull_number=None, offset=0, limit=10
        )
    assert total == 1
    assert analyses[0].status is RunStatus.FAILED
    async with session_factory() as session:
        imports = (await session.execute(GitHubImport.__table__.select())).all()
    assert [row.status for row in imports] == [RunStatus.FAILED]


# ----------------------------------------------------------------- pull request analysis
async def test_a_pull_request_is_analysed_and_reported_on_github(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    base, head = shop
    analysis_id = await queue_pull(session_factory, github_settings, shop)

    status = await analysis_executor(session_factory, github_clients).execute(analysis_id)

    assert status is RunStatus.SUCCEEDED
    job = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert (job.status, job.conclusion, job.error_message) == (RunStatus.SUCCEEDED, "failure", None)
    assert job.report is not None
    # The canned sandbox passes everything: the failure is the new critical finding (eval).
    assert [(f["rule_id"], f["path"]) for f in job.report["new_findings"]] == [
        ("TM010", "shop/admin.py")
    ]
    assert {s["kind"] for s in job.report["suggestions"]} >= {"finding"}

    check = github.check_runs[job.check_run_id or 0]
    assert (check["name"], check["head_sha"], check["status"]) == ("TestMind", head, "completed")
    assert check["conclusion"] == "failure"
    assert check["output"]["title"] == "1 new high or critical finding(s)"
    comment = github.comments[job.comment_id or 0]
    assert comment["number"] == 7
    assert comment["body"].startswith(MARKER)
    assert comment["body"] == check["output"]["summary"]
    assert f"https://testmind.example/api/v1/github/pull-analyses/{analysis_id}" in comment["body"]
    assert f"`{base[:7]}`" in comment["body"]
    # The token came from the App JWT; the archives through the codeload redirect.
    paths = [c.path for c in github.calls]
    assert paths[0] == "/app/installations/1/access_tokens"
    assert paths.count("/app/installations/1/access_tokens") == 1  # the token is cached
    assert f"/_codeload/acme/shop/{base}" in paths
    assert f"/_codeload/acme/shop/{head}" in paths

    # A new push: the same comment is updated, not a second one posted.
    second = await queue_pull(session_factory, github_settings, shop, delivery="delivery-2")
    executor = analysis_executor(session_factory, github_clients)
    assert await executor.execute(second) is RunStatus.SUCCEEDED
    again = await stored(session_factory, GitHubPullAnalysis, second)
    assert again.comment_id == job.comment_id
    assert len(github.comments) == 1
    assert len(github.check_runs) == 2

    # Someone deleted it: a new one is posted.
    github.comments.clear()
    third = await queue_pull(session_factory, github_settings, shop, delivery="delivery-3")
    executor = analysis_executor(session_factory, github_clients)
    assert await executor.execute(third) is RunStatus.SUCCEEDED
    posted = await stored(session_factory, GitHubPullAnalysis, third)
    assert posted.comment_id != job.comment_id
    assert list(github.comments) == [posted.comment_id]


async def test_a_manual_analysis_uses_the_pull_requests_current_head(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    async with session_factory() as session:
        job = await service(session, github_settings).request_analysis(REPOSITORY, 7, 1)
    executor = analysis_executor(session_factory, github_clients, public_url=None)
    assert await executor.execute(job.id) is RunStatus.SUCCEEDED
    row = await stored(session_factory, GitHubPullAnalysis, job.id)
    assert (row.base_sha, row.head_sha) == shop
    assert row.report is not None
    assert row.report["regression"]["head"]["ref"] == "feature"
    assert "/api/v1/github/pull-analyses/" not in github.comments[row.comment_id or 0]["body"]


async def test_issues_are_created_from_the_suggestions_once(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    analysis_id = await queue_pull(session_factory, github_settings, shop)
    await analysis_executor(session_factory, github_clients).execute(analysis_id)
    async with session_factory() as session:
        report = await service(session, github_settings, github_clients).report(analysis_id)
    ids = [s.id for s in report.suggestions]

    async with session_factory() as session:
        github_service = service(session, github_settings, github_clients)
        with pytest.raises(NotFoundError, match="Unknown suggestion"):
            await github_service.create_issues(analysis_id, [ids[0], "nope"])
        first = await github_service.create_issues(analysis_id, [ids[0], ids[0]])
        both = await github_service.create_issues(analysis_id, ids)

    assert [i.suggestion for i in first] == [ids[0]]
    assert [i.suggestion for i in both] == ids
    assert both[0] == first[0]  # not created twice
    assert len(github.issues) == len(ids)
    assert github.issues[0]["title"] == report.suggestions[0].title
    assert github.issues[0]["labels"] == ["testmind"]
    assert first[0].url == "https://github.test/acme/shop/issues/1"
    row = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert [i["suggestion"] for i in row.issues] == ids

    github.fail["POST /repos/acme/shop/issues"] = 403
    async with session_factory() as session:
        new = await queue_pull(session_factory, github_settings, shop, delivery="d-2")
        await analysis_executor(session_factory, github_clients).execute(new)
        with pytest.raises(GitHubRequestError, match="GitHub answered 403"):
            await service(session, github_settings, github_clients).create_issues(new, ids[:1])


async def test_a_pull_request_that_cannot_be_read_fails_the_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    github.repositories[REPOSITORY].pulls.clear()
    analysis_id = await queue_pull(session_factory, github_settings, shop)

    status = await analysis_executor(session_factory, github_clients).execute(analysis_id)

    assert status is RunStatus.FAILED
    job = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert job.error_message == "GitHub answered 404: Not Found"
    assert github.check_runs == {}  # nothing to report on: no check run was created
    # Not queued any more: executing again changes nothing.
    assert await analysis_executor(session_factory, github_clients).execute(analysis_id) is (
        RunStatus.FAILED
    )
    assert await analysis_executor(session_factory, github_clients).execute(uuid.uuid4()) is None


async def test_a_failed_download_completes_the_check_run_as_neutral(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    github.fail["GET /repos/acme/shop/tarball"] = 500
    analysis_id = await queue_pull(session_factory, github_settings, shop)

    assert await analysis_executor(session_factory, github_clients).execute(analysis_id) is (
        RunStatus.FAILED
    )

    job = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert job.error_message == "GitHub answered 500: failure 500"
    check = github.check_runs[job.check_run_id or 0]
    assert (check["status"], check["conclusion"]) == ("completed", "neutral")
    assert check["output"] == {"title": FAILED_TITLE, "summary": job.error_message}
    assert github.comments == {}


async def test_an_unexpected_error_is_reported_without_details(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    def broken() -> PullAnalyzer:
        raise RuntimeError("internal detail")

    analysis_id = await queue_pull(session_factory, github_settings, shop)
    # Completing the check run fails too: the job is still marked failed.
    github.fail["PATCH /repos/acme/shop/check-runs"] = 502

    with pytest.raises(RuntimeError):
        await analysis_executor(session_factory, github_clients, broken).execute(analysis_id)

    job = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert (job.status, job.error_message) == (RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)
    assert [c.path for c in github.calls if c.method == "PATCH"] == [
        f"/repos/acme/shop/check-runs/{next(iter(github.check_runs))}"
    ]


async def test_an_unconfigured_app_fails_the_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    github_settings: Settings,
    shop: tuple[str, str],
) -> None:
    analysis_id = await queue_pull(session_factory, github_settings, shop)
    executor = analysis_executor(session_factory, client_factory(settings))
    assert await executor.execute(analysis_id) is RunStatus.FAILED
    job = await stored(session_factory, GitHubPullAnalysis, analysis_id)
    assert job.error_message is not None
    assert job.error_message.startswith("The GitHub App is not configured")


# ------------------------------------------------------------------------ repository imports
async def queue_import(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings, ref: str
) -> uuid.UUID:
    async with session_factory() as session:
        job = await service(session, settings).request_import(REPOSITORY, ref, 1)
    return job.id


def import_executor(
    session_factory: async_sessionmaker[AsyncSession],
    clients: Callable[[], GitHubClient],
    imports_root: Path | None,
    dispatcher: RecordingDispatcher,
) -> ImportExecutor:
    return ImportExecutor(
        session_factory, clients=clients, imports_root=imports_root, dispatcher=dispatcher
    )


async def test_a_repository_is_imported_as_a_project(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    base, head = shop
    github.repositories[REPOSITORY].extras[head] = [("link", "symlink", "/etc/passwd")]
    imports_root = github_settings.github_imports_root
    assert imports_root is not None
    dispatcher = RecordingDispatcher()
    executor = import_executor(session_factory, github_clients, imports_root, dispatcher)

    import_id = await queue_import(session_factory, github_settings, "feature")
    assert await executor.execute(import_id) is RunStatus.SUCCEEDED

    job = await stored(session_factory, GitHubImport, import_id)
    assert (job.commit_sha, job.skipped) == (head, ["link: symbolic link"])
    assert job.run_id is not None
    assert dispatcher.dispatched == [job.run_id]
    async with session_factory() as session:
        project = await session.get(Project, job.project_id)
    assert project is not None
    assert (project.name, project.source_type) == (REPOSITORY, SourceType.GITHUB)
    assert project.source_location == f"github:{REPOSITORY}/{head}"
    assert (project.remote_repository, project.remote_ref, project.remote_commit) == (
        REPOSITORY,
        "feature",
        head,
    )
    source = resolve_local_source(
        github_settings.repos_root, project.source_location, imports_root=imports_root
    )
    assert source == imports_root / "acme" / "shop" / head
    assert (source / "shop" / "admin.py").read_text() == HEAD["shop/admin.py"]
    assert not (source / "link").exists()
    assert sorted(p.name for p in source.parent.iterdir()) == [head]  # no partial download

    # Importing another ref updates the same project; an imported commit is not downloaded again.
    again = await queue_import(session_factory, github_settings, "main")
    assert await executor.execute(again) is RunStatus.SUCCEEDED
    same = await queue_import(session_factory, github_settings, base)
    downloads = sum(1 for c in github.calls if "/tarball/" in c.path)
    assert await executor.execute(same) is RunStatus.SUCCEEDED
    assert sum(1 for c in github.calls if "/tarball/" in c.path) == downloads
    async with session_factory() as session:
        projects = (await session.execute(Project.__table__.select())).all()
    assert [(p.id, p.source_location, p.remote_ref) for p in projects] == [
        (job.project_id, f"github:{REPOSITORY}/{base}", base)
    ]
    assert len(dispatcher.dispatched) == 3


async def test_imports_that_fail(
    session_factory: async_sessionmaker[AsyncSession],
    github_settings: Settings,
    github: FakeGitHub,
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    imports_root = github_settings.github_imports_root
    dispatcher = RecordingDispatcher()

    missing = await queue_import(session_factory, github_settings, "no-such-branch")
    executor = import_executor(session_factory, github_clients, imports_root, dispatcher)
    assert await executor.execute(missing) is RunStatus.FAILED
    job = await stored(session_factory, GitHubImport, missing)
    assert job.error_message == "GitHub answered 422: No commit found for SHA: no-such-branch"

    unconfigured = await queue_import(session_factory, github_settings, "main")
    assert await import_executor(session_factory, github_clients, None, dispatcher).execute(
        unconfigured
    ) is (RunStatus.FAILED)
    job = await stored(session_factory, GitHubImport, unconfigured)
    assert job.error_message == "Imports are not configured (TESTMIND_GITHUB_IMPORTS_ROOT)"

    github.fail["GET /_codeload"] = 500
    broken = await queue_import(session_factory, github_settings, "main")
    assert await executor.execute(broken) is RunStatus.FAILED
    assert imports_root is not None
    assert list((imports_root / "acme" / "shop").iterdir()) == []  # the partial is removed
    assert dispatcher.dispatched == []
    assert await executor.execute(broken) is RunStatus.FAILED  # not queued any more
    assert await executor.execute(uuid.uuid4()) is None


async def test_an_unexpected_import_error_is_reported_without_details(
    session_factory: async_sessionmaker[AsyncSession], github_settings: Settings
) -> None:
    def broken() -> GitHubClient:
        raise RuntimeError("internal detail")

    import_id = await queue_import(session_factory, github_settings, "main")
    executor = import_executor(
        session_factory, broken, github_settings.github_imports_root, RecordingDispatcher()
    )
    with pytest.raises(RuntimeError):
        await executor.execute(import_id)
    job = await stored(session_factory, GitHubImport, import_id)
    assert (job.status, job.error_message) == (RunStatus.FAILED, UNEXPECTED_ERROR_MESSAGE)


async def test_a_job_that_disappeared_is_not_recreated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    missing = uuid.uuid4()
    assert await _finish(session_factory, GitHubImport, missing, RunStatus.FAILED) is (
        RunStatus.FAILED
    )
    async with session_factory() as session:
        assert await session.get(GitHubImport, missing) is None


# ------------------------------------------------------------------------------------ races
async def test_the_same_delivery_handled_concurrently_is_a_conflict(
    session: AsyncSession, github_settings: Settings
) -> None:
    """Two workers of the API received the same delivery: the unique delivery id decides."""
    github_service = service(session, github_settings)
    event = PullEvent(1, REPOSITORY, 7, head_sha="b" * 40, base_sha="a" * 40)
    await github_service._queue_pull(event, "delivery-x")
    with pytest.raises(ConflictError, match="delivery-x"):
        await github_service._queue_pull(event, "delivery-x")


class StartedThenFailingDispatcher(RecordingDispatcher):
    """The message reached a worker, which started the job, but sending reported an error."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__()
        self._session_factory = session_factory

    async def dispatch_pull_analysis(self, analysis_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            job = await session.get(GitHubPullAnalysis, analysis_id)
            assert job is not None
            job.status = RunStatus.RUNNING
            await session.commit()
        raise ConnectionError("acknowledgement lost")


async def test_a_job_already_started_is_not_failed_by_a_dispatch_error(
    session_factory: async_sessionmaker[AsyncSession], github_settings: Settings
) -> None:
    async with session_factory() as session:
        github_service = GitHubService(
            session, StartedThenFailingDispatcher(session_factory), github_settings
        )
        with pytest.raises(DispatchError):
            await github_service.request_analysis(REPOSITORY, 7, 1)
        analyses, _ = await github_service.list_analyses(
            repository=REPOSITORY, pull_number=7, offset=0, limit=1
        )
    assert analyses[0].status is RunStatus.RUNNING

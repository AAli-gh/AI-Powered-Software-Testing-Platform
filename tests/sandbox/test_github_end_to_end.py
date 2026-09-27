"""No doubles on TestMind's side: a signed webhook → Celery (Redis) → real worker → GitHub API
over HTTP → tarballs → Docker sandbox at both commits → check run and comment → DB. GitHub itself
is the local fake (tests/github_fake.py) served by uvicorn on a real port."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import uuid
from collections.abc import Iterator
from typing import TypeVar

import pytest
import uvicorn
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import Settings
from testmind.db.models import AnalysisRun, GitHubImport, GitHubPullAnalysis
from testmind.domain.enums import RunStatus
from testmind.github.models import Conclusion, PullReport
from testmind.github.pull_analysis import MARKER
from testmind.regression.models import TestChangeKind
from testmind.services.github_service import GitHubService
from testmind.workers.celery_app import create_celery_app
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.conftest import GITHUB_REPOSITORY, GITHUB_WEBHOOK_SECRET
from tests.github_fake import FakeGitHub, pull_event, sign
from tests.sandbox.conftest import SANDBOX_IMAGE

pytestmark = pytest.mark.integration

RowT = TypeVar("RowT", GitHubPullAnalysis, GitHubImport, AnalysisRun)


@pytest.fixture
def github_url(github: FakeGitHub) -> Iterator[str]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(github.app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        threading.Event().wait(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    listener.close()


@pytest.fixture
def worker_settings(github_settings: Settings, github_url: str) -> Settings:
    return github_settings.model_copy(
        update={
            "github_api_url": github_url,
            "sandbox_image": SANDBOX_IMAGE,
            "public_url": "https://testmind.example",
        }
    )


@pytest.fixture
def celery_app(worker_settings: Settings) -> Celery:
    app = create_celery_app(worker_settings)
    app.control.purge()
    return app


async def finished(
    session_factory: async_sessionmaker[AsyncSession], model: type[RowT], row_id: uuid.UUID
) -> RowT:
    async with asyncio.timeout(300):
        while True:
            async with session_factory() as session:
                row = await session.get(model, row_id)
                if row is not None and row.status.is_terminal:
                    return row
            await asyncio.sleep(0.5)


@pytest.mark.usefixtures("worker")
async def test_a_pull_request_through_the_whole_stack(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    worker_settings: Settings,
    github: FakeGitHub,
    shop: tuple[str, str],
) -> None:
    body = json.dumps(pull_event(GITHUB_REPOSITORY, 7, *shop, action="synchronize")).encode()
    async with session_factory() as session:
        received = await GitHubService(
            session, CeleryTaskDispatcher(celery_app), worker_settings
        ).receive("pull_request", "delivery-e2e", body, sign(GITHUB_WEBHOOK_SECRET, body))
    assert received.id is not None

    row = await finished(session_factory, GitHubPullAnalysis, received.id)

    assert row.status is RunStatus.SUCCEEDED, row.error_message
    report = PullReport.model_validate(row.report)
    assert report.conclusion is Conclusion.FAILURE
    regressed = sorted(
        t.nodeid for t in report.regression.tests if t.change is TestChangeKind.REGRESSED
    )
    assert regressed == ["tests/test_cart.py::test_total", "tests/test_pricing.py::test_discount"]
    assert report.regression.selection_check is not None
    assert report.regression.selection_check.missed == []
    assert [(f.rule_id, f.path) for f in report.new_findings] == [("TM010", "shop/admin.py")]
    assert sorted(f"{f.path}::{f.qualname}" for f in report.untested_changes) == [
        "shop/admin.py::run",
        "shop/pricing.py::rebate",
        "shop/unused.py::helper",
    ]
    assert [s.kind for s in report.suggestions] == [
        "regression", "regression", "finding", "untested_changes",
    ]  # fmt: skip

    check = github.check_runs[row.check_run_id or 0]
    assert (check["head_sha"], check["status"], check["conclusion"]) == (
        shop[1],
        "completed",
        "failure",
    )
    assert check["output"]["title"] == "2 regressed test(s), 1 new high or critical finding(s)"
    comment = github.comments[row.comment_id or 0]["body"]
    assert comment.startswith(MARKER)
    assert "- `tests/test_pricing.py::test_discount` passed at base, fails at head" in comment


@pytest.mark.usefixtures("worker")
async def test_an_imported_repository_is_analysed(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    worker_settings: Settings,
    shop: tuple[str, str],
) -> None:
    async with session_factory() as session:
        job = await GitHubService(
            session, CeleryTaskDispatcher(celery_app), worker_settings
        ).request_import(GITHUB_REPOSITORY, "feature", 1)

    imported = await finished(session_factory, GitHubImport, job.id)
    assert imported.status is RunStatus.SUCCEEDED, imported.error_message
    assert imported.commit_sha == shop[1]
    assert imported.run_id is not None

    run = await finished(session_factory, AnalysisRun, imported.run_id)
    assert run.status is RunStatus.SUCCEEDED, run.error_message

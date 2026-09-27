"""The GitHub endpoints over HTTP, against the real database and the fake GitHub API."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from testmind.api.app import create_app
from testmind.core.config import Settings
from testmind.github.client import GitHubClient
from testmind.github.pull_analysis import MARKER
from tests.conftest import GITHUB_WEBHOOK_SECRET
from tests.doubles import RecordingDispatcher
from tests.github_fake import FakeGitHub, pull_event, sign
from tests.integration.test_github_integration import REPOSITORY, analysis_executor

SHAS = ("a" * 40, "b" * 40)
BASE = "/api/v1/github"


@pytest.fixture
async def github_api(
    engine: AsyncEngine,
    github_settings: Settings,
    dispatcher: RecordingDispatcher,
    github_clients: Callable[[], GitHubClient],
) -> AsyncIterator[httpx.AsyncClient]:
    """The API with the GitHub App configured (`engine`: migrated, empty tables)."""
    app = create_app(github_settings, dispatcher=dispatcher, github_clients=github_clients)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def delivery(
    body: bytes, event: str = "pull_request", secret: str = GITHUB_WEBHOOK_SECRET
) -> dict[str, str]:
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": str(uuid.uuid4()),
        "X-Hub-Signature-256": sign(secret, body),
        "Content-Type": "application/json",
    }


async def test_the_webhook(github_api: httpx.AsyncClient, dispatcher: RecordingDispatcher) -> None:
    body = json.dumps(pull_event(REPOSITORY, 7, *SHAS)).encode()
    headers = delivery(body)

    queued = await github_api.post(f"{BASE}/webhook", content=body, headers=headers)
    again = await github_api.post(f"{BASE}/webhook", content=body, headers=headers)

    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert (again.status_code, again.json()["status"]) == (200, "duplicate")
    assert again.json()["id"] == queued.json()["id"]
    assert dispatcher.pull_analyses == [uuid.UUID(queued.json()["id"])]

    ping = await github_api.post(f"{BASE}/webhook", content=b"{}", headers=delivery(b"{}", "ping"))
    assert (ping.status_code, ping.json()) == (200, {"status": "pong", "id": None, "reason": None})

    forged = await github_api.post(
        f"{BASE}/webhook", content=body, headers=delivery(body, secret="guess")
    )
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "invalid_signature"
    unsigned = {k: v for k, v in headers.items() if k != "X-Hub-Signature-256"}
    assert (
        await github_api.post(f"{BASE}/webhook", content=body, headers=unsigned)
    ).status_code == 401
    missing = await github_api.post(f"{BASE}/webhook", content=body)
    assert missing.status_code == 422
    garbage = b"not json"
    invalid = await github_api.post(f"{BASE}/webhook", content=garbage, headers=delivery(garbage))
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (400, "invalid_payload")


async def test_the_webhook_without_a_secret(client: httpx.AsyncClient) -> None:
    reply = await client.post(f"{BASE}/webhook", content=b"{}", headers=delivery(b"{}", "ping"))
    assert reply.status_code == 503
    assert "TESTMIND_GITHUB_WEBHOOK_SECRET" in reply.json()["error"]["message"]


async def test_manual_requests_without_the_app(client: httpx.AsyncClient) -> None:
    analysis = {"repository": REPOSITORY, "pull_number": 7, "installation_id": 1}
    assert (await client.post(f"{BASE}/pull-analyses", json=analysis)).status_code == 503
    imported = {"repository": REPOSITORY, "installation_id": 1}
    assert (await client.post(f"{BASE}/imports", json=imported)).status_code == 503


async def test_analyses_reports_and_issues(
    github_api: httpx.AsyncClient,
    github: FakeGitHub,
    session_factory: async_sessionmaker[AsyncSession],
    github_clients: Callable[[], GitHubClient],
    shop: tuple[str, str],
) -> None:
    body = {"repository": REPOSITORY, "pull_number": 7, "installation_id": 1}
    created = await github_api.post(f"{BASE}/pull-analyses", json=body)
    assert created.status_code == 202
    analysis = created.json()
    assert (analysis["status"], analysis["summary"], analysis["head_sha"]) == ("queued", None, None)
    url = f"{BASE}/pull-analyses/{analysis['id']}"
    assert (await github_api.get(f"{url}/report")).status_code == 404

    await analysis_executor(session_factory, github_clients).execute(uuid.UUID(analysis["id"]))

    done = (await github_api.get(url)).json()
    assert (done["status"], done["conclusion"]) == ("succeeded", "failure")
    assert (done["base_sha"], done["head_sha"]) == shop
    assert done["summary"]["new_findings"] == 1
    assert done["summary"]["url"] == "https://github.test/acme/shop/pull/7"
    report = (await github_api.get(f"{url}/report")).json()
    assert report["new_findings"][0]["rule_id"] == "TM010"
    comment = await github_api.get(f"{url}/comment")
    assert comment.headers["content-type"].startswith("text/markdown")
    assert comment.text.startswith(MARKER)
    assert comment.text.split("\n")[1] == github.comments[done["comment_id"]]["body"].split("\n")[1]

    listed = (
        await github_api.get(f"{BASE}/pull-analyses", params={"repository": REPOSITORY})
    ).json()
    assert (listed["total"], [i["id"] for i in listed["items"]]) == (1, [analysis["id"]])
    other = await github_api.get(f"{BASE}/pull-analyses", params={"pull_number": 8})
    assert other.json()["total"] == 0
    bad = await github_api.get(f"{BASE}/pull-analyses", params={"repository": "no-slash"})
    assert bad.status_code == 422

    suggestion = done["summary"]["suggestions"][0]["id"]
    github.fail["POST /repos/acme/shop/issues"] = 410
    refused = await github_api.post(f"{url}/issues", json={"suggestions": [suggestion]})
    assert (refused.status_code, refused.json()["error"]["code"]) == (502, "github_error")
    assert "GitHub answered 410" in refused.json()["error"]["message"]
    del github.fail["POST /repos/acme/shop/issues"]

    issues = await github_api.post(f"{url}/issues", json={"suggestions": [suggestion]})
    assert issues.status_code == 201
    assert issues.json() == [
        {"suggestion": suggestion, "number": 1, "url": "https://github.test/acme/shop/issues/1"}
    ]
    assert (await github_api.get(url)).json()["issues"] == issues.json()
    unknown = await github_api.post(f"{url}/issues", json={"suggestions": ["nope"]})
    assert unknown.status_code == 404
    assert (await github_api.post(f"{url}/issues", json={"suggestions": []})).status_code == 422

    missing = f"{BASE}/pull-analyses/{uuid.uuid4()}"
    assert (await github_api.get(missing)).status_code == 404
    assert (await github_api.get(f"{missing}/comment")).status_code == 404


async def test_imports(github_api: httpx.AsyncClient, dispatcher: RecordingDispatcher) -> None:
    created = await github_api.post(
        f"{BASE}/imports", json={"repository": REPOSITORY, "installation_id": 3}
    )
    assert created.status_code == 202
    job = created.json()
    assert (job["ref"], job["status"], job["project_id"], job["skipped"]) == (
        "HEAD", "queued", None, [],
    )  # fmt: skip
    assert dispatcher.github_imports == [uuid.UUID(job["id"])]
    assert (await github_api.get(f"{BASE}/imports/{job['id']}")).json() == job
    assert (await github_api.get(f"{BASE}/imports/{uuid.uuid4()}")).status_code == 404
    bad_ref = {"repository": REPOSITORY, "installation_id": 3, "ref": "a..b"}
    assert (await github_api.post(f"{BASE}/imports", json=bad_ref)).status_code == 400
    extra = {"repository": REPOSITORY, "installation_id": 3, "token": "x"}
    assert (await github_api.post(f"{BASE}/imports", json=extra)).status_code == 422


async def test_owners_that_are_not_allowed(
    engine: AsyncEngine, github_settings: Settings, dispatcher: RecordingDispatcher
) -> None:
    settings = github_settings.model_copy(update={"github_allowed_owners": ["someone-else"]})
    app = create_app(settings, dispatcher=dispatcher)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            body = {"repository": REPOSITORY, "pull_number": 7, "installation_id": 1}
            reply = await client.post(f"{BASE}/pull-analyses", json=body)
    assert (reply.status_code, reply.json()["error"]["code"]) == (403, "repository_not_allowed")
    assert dispatcher.pull_analyses == []

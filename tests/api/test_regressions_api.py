import uuid
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.doubles import RecordingDispatcher
from tests.integration.test_regression_service import executor
from tests.regression_fixture import make_shop_repo

PROJECTS = "/api/v1/projects"


async def shop(client: httpx.AsyncClient, repos_root: Path) -> tuple[str, list[str]]:
    shas = make_shop_repo(repos_root / "shop")
    body = {"name": "shop", "source_location": "shop"}
    project: str = (await client.post(PROJECTS, json=body)).json()["id"]
    return project, shas


async def test_lifecycle(
    client: httpx.AsyncClient,
    dispatcher: RecordingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    project, shas = await shop(client, repos_root)
    created = await client.post(
        f"{PROJECTS}/{project}/regressions", json={"base": "v1", "mode": "selected"}
    )
    assert created.status_code == 202
    body = created.json()
    assert (body["status"], body["head_ref"], body["mode"]) == ("queued", "HEAD", "selected")
    assert (body["base_commit"], body["head_commit"]) == (shas[0], shas[1])
    assert dispatcher.regressions == [uuid.UUID(body["id"])]

    pending = await client.get(f"/api/v1/regressions/{body['id']}/report")
    assert pending.status_code == 404
    assert "has no report (status: queued)" in pending.json()["error"]["message"]

    await executor(session_factory, repos_root).execute(uuid.UUID(body["id"]))

    read = (await client.get(f"/api/v1/regressions/{body['id']}")).json()
    assert read["status"] == "succeeded"
    assert read["verdict"] == "clean"  # the canned sandbox answers the same on both sides
    assert read["summary"]["changed_functions"] == 3
    report = (await client.get(f"/api/v1/regressions/{body['id']}/report")).json()
    assert report["selection"]["targets"][0]["reasons"]
    tests = (await client.get(f"/api/v1/regressions/{body['id']}/tests")).json()
    assert tests["total"] == len(report["tests"])
    regressed = await client.get(
        f"/api/v1/regressions/{body['id']}/tests", params={"change": "regressed"}
    )
    assert regressed.json()["total"] == 0

    listed = (await client.get(f"{PROJECTS}/{project}/regressions")).json()
    assert [r["id"] for r in listed["items"]] == [body["id"]]


async def test_errors(client: httpx.AsyncClient, repos_root: Path) -> None:
    project, _ = await shop(client, repos_root)
    url = f"{PROJECTS}/{project}/regressions"
    bad = await client.post(url, json={"base": "nope"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid_revision")
    assert (await client.post(url, json={"base": "v1", "extra": 1})).status_code == 422
    assert (await client.post(url, json={})).status_code == 422

    plain = (
        await client.post(PROJECTS, json={"name": "plain", "source_location": "sample"})
    ).json()["id"]
    not_git = await client.post(f"{PROJECTS}/{plain}/regressions", json={"base": "HEAD~1"})
    assert (not_git.status_code, not_git.json()["error"]["code"]) == (409, "not_a_git_repository")

    unknown = uuid.uuid4()
    assert (
        await client.post(f"{PROJECTS}/{unknown}/regressions", json={"base": "a"})
    ).status_code == 404
    assert (await client.get(f"{PROJECTS}/{unknown}/regressions")).status_code == 404
    assert (await client.get(f"/api/v1/regressions/{unknown}")).status_code == 404

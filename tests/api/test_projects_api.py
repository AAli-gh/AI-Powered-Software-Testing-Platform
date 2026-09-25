import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECTS = "/api/v1/projects"


async def create(client: httpx.AsyncClient, **overrides: Any) -> httpx.Response:
    body = {"name": "demo", "source_location": "sample", **overrides}
    return await client.post(PROJECTS, json=body)


async def test_create_project(client: httpx.AsyncClient) -> None:
    response = await create(client)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "demo"
    assert body["source_type"] == "local"
    assert body["source_location"] == "sample"
    assert uuid.UUID(body["id"])
    assert response.headers["Location"] == f"{PROJECTS}/{body['id']}"


async def test_absolute_location_is_stored_relative(
    client: httpx.AsyncClient, repos_root: Path
) -> None:
    response = await create(client, source_location=str(repos_root / "sample"))
    assert response.json()["source_location"] == "sample"


async def test_name_is_trimmed(client: httpx.AsyncClient) -> None:
    assert (await create(client, name="  demo  ")).json()["name"] == "demo"


async def test_duplicate_name_is_409(client: httpx.AsyncClient) -> None:
    await create(client)
    response = await create(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("location", ["../../etc", "/etc", "missing", "sample/main.py"])
async def test_unusable_source_is_422(client: httpx.AsyncClient, location: str) -> None:
    response = await create(client, source_location=location)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_source"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"name": ""}, "name"),
        ({"name": "-starts-with-dash"}, "name"),
        ({"name": "x" * 201}, "name"),
        ({"source_type": "github"}, "source_type"),
        ({"source_location": ""}, "source_location"),
        ({"unexpected": True}, "unexpected"),
    ],
)
async def test_invalid_body_is_422_with_details(
    client: httpx.AsyncClient, overrides: dict[str, Any], field: str
) -> None:
    response = await create(client, **overrides)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(detail["loc"][-1] == field for detail in error["details"])
    assert all(set(detail) == {"loc", "msg", "type"} for detail in error["details"])


async def test_list_projects_paginates(client: httpx.AsyncClient) -> None:
    for index in range(3):
        await create(client, name=f"p{index}")

    page = (await client.get(PROJECTS, params={"limit": 2})).json()
    assert page["total"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert [item["name"] for item in page["items"]] == ["p2", "p1"]

    page = (await client.get(PROJECTS, params={"limit": 2, "offset": 2})).json()
    assert [item["name"] for item in page["items"]] == ["p0"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_list_rejects_bad_pagination(
    client: httpx.AsyncClient, params: dict[str, int]
) -> None:
    assert (await client.get(PROJECTS, params=params)).status_code == 422


async def test_get_project(client: httpx.AsyncClient) -> None:
    created = (await create(client)).json()
    response = await client.get(f"{PROJECTS}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_missing_project_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{PROJECTS}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_malformed_id_is_422(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{PROJECTS}/not-a-uuid")).status_code == 422


async def test_delete_project(client: httpx.AsyncClient) -> None:
    created = (await create(client)).json()

    response = await client.delete(f"{PROJECTS}/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert (await client.get(f"{PROJECTS}/{created['id']}")).status_code == 404


async def test_delete_with_active_run_is_409(client: httpx.AsyncClient) -> None:
    created = (await create(client)).json()
    await client.post(f"{PROJECTS}/{created['id']}/runs")

    response = await client.delete(f"{PROJECTS}/{created['id']}")

    assert response.status_code == 409

from collections.abc import AsyncIterator

import httpx
import pytest

from testmind.github.client import AppCredentials, GitHubClient
from tests.github_fake import APP_ID, FakeGitHub


@pytest.fixture
async def client(github: FakeGitHub, private_key: str) -> AsyncIterator[GitHubClient]:
    client = GitHubClient(
        AppCredentials(APP_ID, private_key),
        api_url="http://github.test",
        transport=httpx.ASGITransport(app=github.app),
    )
    yield client
    await client.close()

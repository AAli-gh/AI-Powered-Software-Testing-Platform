from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from testmind.api.app import create_app
from testmind.core.config import Settings
from tests.doubles import RecordingDispatcher


@pytest.fixture
def dispatcher() -> RecordingDispatcher:
    return RecordingDispatcher()


@pytest.fixture
async def app(
    engine: AsyncEngine, settings: Settings, dispatcher: RecordingDispatcher
) -> AsyncIterator[FastAPI]:
    # `engine` is requested so every API test starts from migrated, empty tables.
    application = create_app(settings, dispatcher=dispatcher)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

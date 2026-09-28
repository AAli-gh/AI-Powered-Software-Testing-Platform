from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from testmind import __version__
from testmind.api.errors import install_exception_handlers
from testmind.api.middleware import RequestContextMiddleware
from testmind.api.routes import (
    api_testing,
    code,
    executions,
    failures,
    generations,
    github,
    health,
    mutations,
    overview,
    projects,
    regressions,
    runs,
)
from testmind.core.config import Settings, get_settings
from testmind.core.logging import configure_logging
from testmind.db.session import create_engine, create_session_factory
from testmind.services.dispatcher import TaskDispatcher
from testmind.services.github_service import ClientFactory
from testmind.workers.celery_app import create_celery_app
from testmind.workers.dispatcher import CeleryTaskDispatcher

REDIS_TIMEOUT_SECONDS = 2.0


def create_app(
    settings: Settings | None = None,
    *,
    dispatcher: TaskDispatcher | None = None,
    github_clients: ClientFactory | None = None,
) -> FastAPI:
    """Application factory (`uvicorn --factory testmind.api.app:create_app`).

    `dispatcher` and `github_clients` may be injected by tests; by default runs are sent to
    Celery and GitHub is reached at TESTMIND_GITHUB_API_URL.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
        )
        app.state.settings = settings
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.dispatcher = dispatcher or CeleryTaskDispatcher(create_celery_app(settings))
        app.state.github_clients = github_clients
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(
        title="TestMind API",
        version=__version__,
        summary="AI-powered software testing platform",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(overview.router)
    app.include_router(runs.router)
    app.include_router(code.router)
    app.include_router(generations.router)
    app.include_router(executions.router)
    app.include_router(failures.router)
    app.include_router(regressions.router)
    app.include_router(mutations.router)
    app.include_router(github.router)
    app.include_router(api_testing.router)
    return app

"""FastAPI dependencies. Long-lived resources live on `app.state` (created in the lifespan)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.config import Settings
from testmind.services.generation_service import GenerationService
from testmind.services.project_service import ProjectService
from testmind.services.run_service import RunService


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_project_service(session: SessionDep, settings: SettingsDep) -> ProjectService:
    return ProjectService(session, settings.repos_root)


def get_run_service(request: Request, session: SessionDep) -> RunService:
    return RunService(session, request.app.state.dispatcher)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]
RunServiceDep = Annotated[RunService, Depends(get_run_service)]


def get_generation_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> GenerationService:
    return GenerationService(session, request.app.state.dispatcher, settings)


GenerationServiceDep = Annotated[GenerationService, Depends(get_generation_service)]

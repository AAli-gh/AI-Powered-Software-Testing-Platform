"""FastAPI dependencies. Long-lived resources live on `app.state` (created in the lifespan)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.config import Settings
from testmind.services.api_testing_service import ApiTestingService
from testmind.services.execution_service import ExecutionService
from testmind.services.failure_analysis_service import FailureAnalysisService
from testmind.services.generation_service import GenerationService
from testmind.services.github_service import GitHubService
from testmind.services.mutation_service import MutationService
from testmind.services.overview_service import OverviewService
from testmind.services.project_service import ProjectService
from testmind.services.regression_service import RegressionService
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


def get_execution_service(request: Request, session: SessionDep) -> ExecutionService:
    return ExecutionService(session, request.app.state.dispatcher)


ExecutionServiceDep = Annotated[ExecutionService, Depends(get_execution_service)]


def get_failure_analysis_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> FailureAnalysisService:
    return FailureAnalysisService(session, request.app.state.dispatcher, settings)


FailureAnalysisServiceDep = Annotated[FailureAnalysisService, Depends(get_failure_analysis_service)]


def get_regression_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> RegressionService:
    return RegressionService(
        session,
        request.app.state.dispatcher,
        settings.repos_root,
        imports_root=settings.github_imports_root,
    )


RegressionServiceDep = Annotated[RegressionService, Depends(get_regression_service)]


def get_mutation_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> MutationService:
    return MutationService(session, request.app.state.dispatcher, settings)


MutationServiceDep = Annotated[MutationService, Depends(get_mutation_service)]


def get_api_testing_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> ApiTestingService:
    return ApiTestingService(session, request.app.state.dispatcher, settings)


ApiTestingServiceDep = Annotated[ApiTestingService, Depends(get_api_testing_service)]


def get_github_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> GitHubService:
    return GitHubService(
        session, request.app.state.dispatcher, settings, request.app.state.github_clients
    )


GitHubServiceDep = Annotated[GitHubService, Depends(get_github_service)]


def get_overview_service(session: SessionDep) -> OverviewService:
    return OverviewService(session)


OverviewServiceDep = Annotated[OverviewService, Depends(get_overview_service)]

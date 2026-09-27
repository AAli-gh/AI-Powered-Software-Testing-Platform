"""GitHub App: the webhook, pull request analyses, issues from their suggestions, imports."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from testmind.api.deps import GitHubServiceDep
from testmind.github.models import CreatedIssue, PullReport
from testmind.github.pull_analysis import render
from testmind.schemas.common import ErrorResponse, Page
from testmind.schemas.github import (
    REPOSITORY_PATTERN,
    ImportCreate,
    ImportRead,
    IssuesCreate,
    PullAnalysisCreate,
    PullAnalysisRead,
    WebhookResponse,
)

router = APIRouter(prefix="/api/v1/github", tags=["GitHub"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"}
}
NOT_CONFIGURED: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "GitHub App not configured, or queue down"}
}


@router.post(
    "/webhook",
    responses={
        202: {"model": WebhookResponse, "description": "Pull request analysis queued"},
        400: {"model": ErrorResponse, "description": "Signed, but not a usable event"},
        401: {"model": ErrorResponse, "description": "Invalid signature"},
        413: {"model": ErrorResponse, "description": "Payload too large"},
        **NOT_CONFIGURED,
    },
)
async def webhook(
    request: Request,
    response: Response,
    service: GitHubServiceDep,
    *,
    x_github_event: Annotated[str, Header(max_length=100)],
    x_github_delivery: Annotated[str, Header(max_length=100)],
    x_hub_signature_256: Annotated[str | None, Header(max_length=200)] = None,
) -> WebhookResponse:
    """GitHub App webhook. Every delivery must be signed with the webhook secret
    (`X-Hub-Signature-256`); `pull_request` events (opened, synchronize, reopened, ready for
    review) queue an analysis, a re-delivered event is recognised by its delivery id."""
    body = await request.body()
    result = await service.receive(x_github_event, x_github_delivery, body, x_hub_signature_256)
    if result.status == "queued":
        response.status_code = status.HTTP_202_ACCEPTED
    return WebhookResponse(status=result.status, id=result.id, reason=result.reason)


@router.post(
    "/pull-analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        403: {"model": ErrorResponse, "description": "Owner not allowed"},
        **NOT_CONFIGURED,
    },
)
async def request_analysis(body: PullAnalysisCreate, service: GitHubServiceDep) -> PullAnalysisRead:
    """Analyse a pull request's current head without waiting for a webhook."""
    job = await service.request_analysis(body.repository, body.pull_number, body.installation_id)
    return PullAnalysisRead.from_row(job)


@router.get("/pull-analyses")
async def list_analyses(
    service: GitHubServiceDep,
    repository: Annotated[str | None, Query(pattern=REPOSITORY_PATTERN)] = None,
    pull_number: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[PullAnalysisRead]:
    items, total = await service.list_analyses(
        repository=repository, pull_number=pull_number, offset=offset, limit=limit
    )
    return Page[PullAnalysisRead](
        items=[PullAnalysisRead.from_row(i) for i in items], total=total, limit=limit,
        offset=offset,
    )  # fmt: skip


@router.get("/pull-analyses/{analysis_id}", responses=NOT_FOUND)
async def get_analysis(analysis_id: uuid.UUID, service: GitHubServiceDep) -> PullAnalysisRead:
    return PullAnalysisRead.from_row(await service.get(analysis_id))


@router.get("/pull-analyses/{analysis_id}/report", responses=NOT_FOUND)
async def get_report(analysis_id: uuid.UUID, service: GitHubServiceDep) -> PullReport:
    """Regression analysis, new findings, untested changes and issue suggestions."""
    return await service.report(analysis_id)


@router.get(
    "/pull-analyses/{analysis_id}/comment",
    response_class=PlainTextResponse,
    responses={**NOT_FOUND, 200: {"content": {"text/markdown": {}}}},
)
async def get_comment(analysis_id: uuid.UUID, service: GitHubServiceDep) -> PlainTextResponse:
    """The markdown posted on the pull request (and as the check run's summary)."""
    markdown = render(await service.report(analysis_id))
    return PlainTextResponse(markdown, media_type="text/markdown")


@router.post(
    "/pull-analyses/{analysis_id}/issues",
    status_code=status.HTTP_201_CREATED,
    responses={
        **NOT_FOUND,
        502: {"model": ErrorResponse, "description": "GitHub refused"},
        **NOT_CONFIGURED,
    },
)
async def create_issues(
    analysis_id: uuid.UUID, body: IssuesCreate, service: GitHubServiceDep
) -> list[CreatedIssue]:
    """Create GitHub issues from the chosen suggestions; a suggestion becomes an issue once."""
    return await service.create_issues(analysis_id, body.suggestions)


@router.post(
    "/imports",
    status_code=status.HTTP_202_ACCEPTED,
    responses={403: {"model": ErrorResponse, "description": "Owner not allowed"}, **NOT_CONFIGURED},
)
async def request_import(body: ImportCreate, service: GitHubServiceDep) -> ImportRead:
    """Import a repository at a ref as a project (and queue its analysis run)."""
    job = await service.request_import(body.repository, body.ref, body.installation_id)
    return ImportRead.from_row(job)


@router.get("/imports/{import_id}", responses=NOT_FOUND)
async def get_import(import_id: uuid.UUID, service: GitHubServiceDep) -> ImportRead:
    return ImportRead.from_row(await service.get_import(import_id))

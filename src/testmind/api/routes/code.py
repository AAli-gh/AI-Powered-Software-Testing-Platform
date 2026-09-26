"""Code analysis of a run: metrics, modules, functions, classes and findings."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from testmind.analyzers.code.models import (
    ClassInfo,
    CodeSummary,
    Finding,
    FindingCategory,
    FunctionInfo,
    ModuleInfo,
    Severity,
)
from testmind.analyzers.code.rules import RULES
from testmind.api.deps import RunServiceDep
from testmind.schemas.common import ErrorResponse, Page

router = APIRouter(prefix="/api/v1", tags=["code analysis"])

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Run not found, or it has no code analysis yet"}
}
SEVERITY_ORDER = {s: i for i, s in enumerate(Severity)}

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
PathPrefix = Annotated[
    str | None, Query(max_length=1024, description="Only entries whose path starts with this")
]


class RuleInfo(BaseModel):
    id: str
    category: FindingCategory
    title: str


@router.get("/code-rules")
async def list_rules() -> list[RuleInfo]:
    """The catalog of code-analysis rules that findings refer to."""
    return [
        RuleInfo(id=rule_id, category=r.category, title=r.title) for rule_id, r in RULES.items()
    ]


@router.get("/runs/{run_id}/code", responses=NOT_FOUND)
async def get_code_summary(run_id: uuid.UUID, service: RunServiceDep) -> CodeSummary:
    """Metrics (size, complexity distribution, findings by severity/category) and issues."""
    return (await service.get_code_analysis(run_id)).summary()


@router.get("/runs/{run_id}/code/modules", responses=NOT_FOUND)
async def list_modules(
    run_id: uuid.UUID,
    service: RunServiceDep,
    *,
    path_prefix: PathPrefix = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[ModuleInfo]:
    modules = (await service.get_code_analysis(run_id)).modules
    if path_prefix:
        modules = [m for m in modules if m.path.startswith(path_prefix)]
    return Page[ModuleInfo].slice(modules, limit=limit, offset=offset)


@router.get("/runs/{run_id}/code/functions", responses=NOT_FOUND)
async def list_functions(
    run_id: uuid.UUID,
    service: RunServiceDep,
    *,
    path_prefix: PathPrefix = None,
    min_complexity: Annotated[int, Query(ge=1)] = 1,
    public_only: bool = False,
    sort: Annotated[str, Query(pattern="^(path|complexity)$")] = "path",
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[FunctionInfo]:
    """Functions and methods. `sort=complexity` lists the most complex first."""
    functions = [
        f
        for f in (await service.get_code_analysis(run_id)).functions
        if f.complexity >= min_complexity
        and (not public_only or f.is_public)
        and (not path_prefix or f.path.startswith(path_prefix))
    ]
    if sort == "complexity":
        functions.sort(key=lambda f: (-f.complexity, f.path, f.line))
    return Page[FunctionInfo].slice(functions, limit=limit, offset=offset)


@router.get("/runs/{run_id}/code/classes", responses=NOT_FOUND)
async def list_classes(
    run_id: uuid.UUID,
    service: RunServiceDep,
    *,
    path_prefix: PathPrefix = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[ClassInfo]:
    classes = (await service.get_code_analysis(run_id)).classes
    if path_prefix:
        classes = [c for c in classes if c.path.startswith(path_prefix)]
    return Page[ClassInfo].slice(classes, limit=limit, offset=offset)


@router.get("/runs/{run_id}/code/findings", responses=NOT_FOUND)
async def list_findings(
    run_id: uuid.UUID,
    service: RunServiceDep,
    *,
    min_severity: Severity = Severity.LOW,
    category: FindingCategory | None = None,
    rule_id: Annotated[str | None, Query(pattern="^TM[0-9]{3}$")] = None,
    min_confidence: Annotated[float, Query(ge=0, le=1)] = 0.0,
    path_prefix: PathPrefix = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> Page[Finding]:
    """Findings, most severe first (then by path and line)."""
    threshold = SEVERITY_ORDER[min_severity]
    findings = [
        f
        for f in (await service.get_code_analysis(run_id)).findings
        if SEVERITY_ORDER[f.severity] >= threshold
        and (category is None or f.category is category)
        and (rule_id is None or f.rule_id == rule_id)
        and f.confidence >= min_confidence
        and (not path_prefix or f.path.startswith(path_prefix))
    ]
    findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.path, f.line, f.rule_id))
    return Page[Finding].slice(findings, limit=limit, offset=offset)

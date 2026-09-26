"""Result model of the code analysis (stored as JSON, returned by the API)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from testmind.analyzers.repository.models import Issue


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ParameterKind(StrEnum):
    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class Parameter(_Model):
    name: str
    kind: ParameterKind
    annotation: str | None = None
    default: str | None = Field(default=None, description="Source text of the default value")


class FunctionInfo(_Model):
    qualname: str = Field(description="e.g. 'OrderService.create' or 'outer.<locals>.inner'")
    name: str
    path: str
    module: str
    line: int
    end_line: int
    lines: int
    is_async: bool
    is_method: bool
    is_public: bool
    decorators: list[str]
    parameters: list[Parameter]
    returns: str | None
    docstring: str | None = Field(description="First line of the docstring")
    complexity: int = Field(description="Cyclomatic complexity (McCabe)")
    complexity_rank: str = Field(description="A (1-5) … F (41+)")
    raises: list[str] = Field(description="Exception names raised directly in the body")


class ClassInfo(_Model):
    qualname: str
    name: str
    path: str
    module: str
    line: int
    end_line: int
    bases: list[str]
    decorators: list[str]
    docstring: str | None
    methods: list[str] = Field(description="Method names, in definition order")
    is_public: bool


class ImportRef(_Model):
    module: str = Field(description="Absolute module name (relative imports resolved)")
    names: list[str] = Field(description="Imported names for `from … import`, else empty")
    line: int
    relative: bool


class ModuleInfo(_Model):
    path: str
    module: str
    lines: int
    docstring: str | None
    imports: list[ImportRef]
    functions: int
    classes: int


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingCategory(StrEnum):
    DANGEROUS_PATTERN = "dangerous_pattern"
    ERROR_HANDLING = "error_handling"
    POTENTIAL_BUG = "potential_bug"
    DEAD_CODE = "dead_code"
    COMPLEXITY = "complexity"
    MISSING_VALIDATION = "missing_validation"


class Finding(_Model):
    rule_id: str = Field(examples=["TM010"])
    category: FindingCategory
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    path: str
    line: int
    symbol: str | None = Field(description="Enclosing function/class qualname, if any")
    message: str
    evidence: str = Field(description="The offending source line")


class CodeMetrics(_Model):
    modules: int
    test_modules_skipped: int
    lines: int
    functions: int
    methods: int
    classes: int
    average_complexity: float
    max_complexity: int
    complexity_ranks: dict[str, int]
    findings_by_severity: dict[str, int]
    findings_by_category: dict[str, int]


class CodeSummary(_Model):
    metrics: CodeMetrics
    issues: list[Issue]


class CodeProfile(CodeSummary):
    modules: list[ModuleInfo]
    functions: list[FunctionInfo]
    classes: list[ClassInfo]
    findings: list[Finding]

    def summary(self) -> CodeSummary:
        return CodeSummary(metrics=self.metrics, issues=self.issues)

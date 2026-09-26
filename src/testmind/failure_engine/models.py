"""Failure analysis model: deterministic groups and, on request, AI hypotheses about them."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FailureCategory(StrEnum):
    ASSERTION_FAILURE = "assertion_failure"  # an assert in a test did not hold
    EXCEPTION_IN_CODE = "exception_in_code"  # raised in the repository's production code
    EXCEPTION_IN_LIBRARY = "exception_in_library"  # raised in a library the code called
    EXCEPTION_IN_TEST = "exception_in_test"  # raised by the test code itself
    FIXTURE_ERROR = "fixture_error"  # setup or teardown failed
    IMPORT_ERROR = "import_error"  # a module could not be imported
    SYNTAX_ERROR = "syntax_error"
    COLLECTION_ERROR = "collection_error"  # any other error while collecting tests
    # xfail(strict=True) passed. The value is an outcome, not a secret.
    UNEXPECTED_PASS = "unexpected_pass"  # noqa: S105  # nosec B105


class Blame(StrEnum):
    CODE = "code"  # the code under test is wrong
    TEST = "test"  # the test is wrong
    ENVIRONMENT = "environment"  # dependencies, configuration, the environment


class FrameLocation(StrEnum):
    PRODUCTION = "production"  # repository code that is not a test
    TEST = "test"  # the repository's tests (and conftest / test helpers)
    GENERATED = "generated"  # tests written by AI test generation
    EXTERNAL = "external"  # standard library, installed packages


class Frame(_Model):
    path: str = Field(description="Repository-relative, or absolute for external files")
    line: int
    function: str
    code: str
    location: FrameLocation


class FailedTest(_Model):
    nodeid: str
    file: str
    name: str | None = Field(description="None for a whole test module that failed to collect")
    line: int | None
    phase: str = Field(description="collect, setup, call or teardown")
    generated: bool


class Evidence(_Model):
    kind: str = Field(
        description="traceback, assertion, locals, chain, finding, dependency, repetition"
    )
    detail: str
    path: str | None = None
    line: int | None = None


class LinkedFunction(_Model):
    """The production function the failure was raised in (from the code analysis)."""

    path: str
    qualname: str
    line: int
    end_line: int
    complexity: int
    findings: list[str] = Field(description="Rule ids of findings in the function")
    coverage_percent: float | None = Field(description="In the same execution, if measured")


class FailureGroup(_Model):
    id: str = Field(description="Stable id: a hash of the grouping signature")
    category: FailureCategory
    exception_type: str | None
    message: str = Field(description="Exception message (of the first test in the group)")
    crash: Frame | None = Field(description="Innermost frame in the repository or its tests")
    raised_at: Frame | None = Field(description="Innermost frame overall (may be a library)")
    frames: list[Frame] = Field(description="Traceback of the first test, outermost first")
    locals: dict[str, str] = Field(description="Local variables at the crash frame (repr)")
    chain: list[str] = Field(description="Causes: 'Type: message', nearest first")
    tests: list[FailedTest]
    function: LinkedFunction | None
    evidence: list[Evidence]
    blame: Blame | None = Field(
        description="Set only when the facts decide it (e.g. a missing third-party module)"
    )
    blame_reason: str | None


class FailureReport(_Model):
    groups: list[FailureGroup]
    failed_tests: int
    analysed_tests: int
    unanalysed_tests: list[str] = Field(
        description="Failed tests without structured failure data (listed, not guessed)"
    )
    notes: list[str]


# ------------------------------------------------------------------------------ AI hypotheses
class EvidenceReference(_Model):
    path: str
    line: int
    explanation: str
    verified: bool = Field(
        description="The line was part of what the model was shown (file and line exist)"
    )


class AnalysisStatus(StrEnum):
    ANALYSED = "analysed"
    DETERMINISTIC = "deterministic"  # the facts decide; the LLM was not asked
    SKIPPED = "skipped"  # over the per-request group limit
    LLM_ERROR = "llm_error"  # the provider failed, refused or never returned valid output


class FailureHypothesis(_Model):
    group_id: str
    status: AnalysisStatus
    blame: Blame | None
    root_cause: str | None
    evidence: list[EvidenceReference]
    suggested_fix: str | None
    model_confidence: float | None = Field(description="As stated by the model")
    confidence: float | None = Field(
        description="After deterministic checks: lowered when evidence cannot be verified"
    )
    notes: list[str]


class FailureAnalysisResult(_Model):
    hypotheses: list[FailureHypothesis]
    model: str | None
    prompt_version: str
    input_tokens: int
    output_tokens: int

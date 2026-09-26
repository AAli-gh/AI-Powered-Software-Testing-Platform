from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TestKind(StrEnum):
    __test__ = False

    UNIT = "unit"
    EDGE_CASES = "edge_cases"
    NEGATIVE = "negative"


class GenerationOptions(_Model):
    kinds: list[TestKind] = Field(
        default_factory=lambda: [TestKind.UNIT, TestKind.EDGE_CASES, TestKind.NEGATIVE],
        min_length=1,
    )
    max_targets: int | None = Field(default=None, ge=1, le=200)
    path_prefix: str | None = Field(default=None, max_length=1024)
    coverage_execution_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Coverage-guided generation: target the functions this (succeeded) test execution "
            "left uncovered, and tell the model which lines and branches never ran"
        ),
    )


class FileStatus(StrEnum):
    PASSED = "passed"  # every test passed (xfail markers for suspected bugs allowed)
    FAILED = "failed"  # still failing after the last fix attempt
    REJECTED = "rejected"  # never produced acceptable code (syntax, unsafe, wrong target)
    ENVIRONMENT_ERROR = "environment_error"  # target module cannot be imported in the sandbox
    UNVERIFIED = "unverified"  # statically valid, but no sandbox was available to run it
    LLM_ERROR = "llm_error"  # the provider failed or refused


class SuspectedBug(_Model):
    test: str
    reason: str


class TestRunSummary(_Model):
    __test__ = False

    passed: int = 0
    failed: int = 0
    errors: int = 0
    xfailed: int = 0
    skipped: int = 0


class Attempt(_Model):
    iteration: int
    outcome: str
    feedback: str | None = None


class GeneratedTestFile(_Model):
    path: str = Field(description="Suggested location, e.g. tests/testmind/test_inventory_stock.py")
    module: str
    targets: list[str]
    status: FileStatus
    code: str | None
    test_count: int
    iterations: int
    run: TestRunSummary | None
    suspected_bugs: list[SuspectedBug]
    attempts: list[Attempt]


class GenerationResult(_Model):
    files: list[GeneratedTestFile]
    model: str | None
    prompt_version: str
    input_tokens: int
    output_tokens: int
    targets_selected: int
    targets_available: int

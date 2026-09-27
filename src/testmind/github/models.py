"""Result of a pull request analysis (stored as JSON, returned by the API, posted to GitHub)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from testmind.analyzers.code.models import Finding
from testmind.regression.models import ChangedFunction, RegressionReport


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Conclusion(StrEnum):
    """The check run's conclusion, as GitHub names it."""

    SUCCESS = "success"  # no regression, no new serious finding
    FAILURE = "failure"  # a test regressed, or a new high or critical finding
    NEUTRAL = "neutral"  # the tests could not run (no sandbox, dependencies, timeout)


class IssueSuggestion(_Model):
    id: str = Field(description="Stable id: the same problem gets the same id on every run")
    kind: str = Field(description="regression, finding or untested_changes")
    title: str
    body: str
    labels: list[str]


class CreatedIssue(_Model):
    suggestion: str
    number: int
    url: str


class PullReport(_Model):
    repository: str
    number: int
    title: str
    url: str
    conclusion: Conclusion
    regression: RegressionReport
    new_findings: list[Finding] = Field(
        description="Code-analysis findings in the changed files that are not in the base"
    )
    untested_changes: list[ChangedFunction] = Field(
        description="Added or modified functions that no test executes at head"
    )
    suggestions: list[IssueSuggestion]
    notes: list[str]

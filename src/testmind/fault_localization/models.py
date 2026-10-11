"""What fault localisation reports: ranked suspect functions and how sure the ranking is."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Confidence(StrEnum):
    HIGH = "high"  # the top suspect is in the traceback, or the only one with the top score
    MEDIUM = "medium"  # at most three functions share the top score
    LOW = "low"  # more share it: the spectrum does not tell them apart
    NONE = "none"  # no production function was executed by the failing tests


class Suspect(_Model):
    path: str
    qualname: str
    line: int
    rank: int = Field(
        description="1-based; tied suspects all get the last position of their tie (worst case)"
    )
    score: float = Field(description="Ochiai suspiciousness, 0..1")
    failed: int = Field(description="Failing tests that executed the function")
    passed: int = Field(description="Passing tests that executed the function")
    in_traceback: bool = Field(description="The function is a frame of the failure's traceback")


class Localization(_Model):
    suspects: list[Suspect]
    confidence: Confidence
    failing_tests: int = Field(description="Failing tests with coverage, the ranking's basis")
    passing_tests: int
    notes: list[str] = Field(default_factory=list)

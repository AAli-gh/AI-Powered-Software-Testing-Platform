"""Parse coverage.py's JSON report.

The report is produced inside the sandbox, next to untrusted code, so it is treated as untrusted
input: its size is capped by the sandbox, its shape is validated here, and file names are only
used as keys to match files TestMind already knows (they are never opened).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testmind.sandbox.paths import report_path, repository_path


class CoverageParseError(ValueError):
    """The coverage report is missing required data or is not coverage.py JSON."""


class _Meta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: str | None = None
    branch_coverage: bool = False


class _File(BaseModel):
    model_config = ConfigDict(extra="ignore")

    executed_lines: list[int]
    missing_lines: list[int]
    # Present only with branch coverage.
    executed_branches: list[tuple[int, int]] = Field(default_factory=list)
    missing_branches: list[tuple[int, int]] = Field(default_factory=list)
    # Only with dynamic contexts (--cov-context=test): line -> contexts that executed it.
    contexts: dict[str, list[str]] = Field(default_factory=dict)


class _Report(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meta: _Meta = Field(default_factory=_Meta)
    files: dict[str, _File]


@dataclass(frozen=True, slots=True)
class RawFile:
    executed: frozenset[int]
    missing: frozenset[int]
    executed_branches: frozenset[tuple[int, int]]
    missing_branches: frozenset[tuple[int, int]]
    # line -> test nodeids (as TestMind reports them) that executed it; empty without contexts
    contexts: Mapping[int, frozenset[str]] = field(default_factory=dict)

    @property
    def statements(self) -> frozenset[int]:
        return self.executed | self.missing


@dataclass(frozen=True, slots=True)
class RawCoverage:
    tool: str
    branch_coverage: bool
    files: dict[str, RawFile]  # repository-relative POSIX path -> data


def parse_coverage_json(text: str, *, rootdir: str) -> RawCoverage:
    """`rootdir` is the directory pytest ran in; coverage.py reports files under it relative
    to it and everything else as absolute paths. Files outside the repository are dropped."""
    try:
        report = _Report.model_validate_json(text)
    except ValidationError as exc:
        raise CoverageParseError(
            f"Not a coverage.py JSON report: {exc.error_count()} errors"
        ) from None

    files: dict[str, RawFile] = {}
    for name, data in report.files.items():
        path = repository_path(name, rootdir)
        if path is None:
            continue
        files[path] = RawFile(
            executed=frozenset(data.executed_lines),
            missing=frozenset(data.missing_lines),
            executed_branches=frozenset(data.executed_branches),
            missing_branches=frozenset(data.missing_branches),
            contexts=_contexts(data.contexts, rootdir),
        )
    version = report.meta.version
    return RawCoverage(
        tool=f"coverage.py {version}" if version else "coverage.py",
        branch_coverage=report.meta.branch_coverage,
        files=files,
    )


def _contexts(raw: dict[str, list[str]], rootdir: str) -> dict[int, frozenset[str]]:
    """pytest-cov names contexts `<nodeid>|<phase>` with the nodeid relative to pytest's
    rootdir. Contexts without a test (import time) or without a file part (tests outside the
    rootdir, i.e. generated tests next to a repository suite) are dropped."""
    contexts: dict[int, frozenset[str]] = {}
    for line, names in raw.items():
        tests = {test for name in names if (test := _nodeid(name, rootdir))}
        if tests and line.isdigit():
            contexts[int(line)] = frozenset(tests)
    return contexts


def _nodeid(context: str, rootdir: str) -> str | None:
    nodeid = context.split("|", 1)[0]
    file, separator, rest = nodeid.partition("::")
    if not file or not separator:
        return None
    located = report_path(file, rootdir)
    return f"{located[0]}::{rest}" if located else None

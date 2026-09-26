"""Turn raw coverage.py data into file, function and gap reports.

Everything here is deterministic. coverage.py decides which lines and branches ran; the code
analysis (Phase 3) supplies function boundaries, complexity and findings; the ranking of gaps is
a fixed formula. No LLM is involved.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from typing import Any

from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.coverage_engine.models import (
    CoverageCounts,
    CoverageGap,
    CoverageReport,
    CoverageTotals,
    FileCoverage,
    FunctionCoverage,
)
from testmind.coverage_engine.parser import RawCoverage, RawFile

DEFAULT_THRESHOLD = 80.0
FINDING_WEIGHT = 2
FULLY_COVERED = 100.0
EXCLUDED_FILES = frozenset({"conftest.py"})
TEST_DIRECTORIES = frozenset({"tests", "test"})


def build_coverage_report(
    raw: RawCoverage, code: CodeProfile, *, threshold: float = DEFAULT_THRESHOLD
) -> CoverageReport:
    """Production code = the non-test modules of the code analysis, without test support
    code (conftest.py, anything inside a `tests/` or `test/` directory)."""
    production = sorted(m.path for m in code.modules if is_production(m.path))
    functions_by_path: dict[str, list[FunctionInfo]] = defaultdict(list)
    for function in code.functions:
        functions_by_path[function.path].append(function)
    findings: dict[tuple[str, str | None], list[str]] = defaultdict(list)
    for finding in code.findings:
        findings[(finding.path, finding.symbol)].append(finding.rule_id)

    files: list[FileCoverage] = []
    functions: list[FunctionCoverage] = []
    unmeasured: list[str] = []
    for path in production:
        data = raw.files.get(path)
        if data is None:
            unmeasured.append(path)
            continue
        files.append(_file(path, data))
        siblings = functions_by_path.get(path, [])
        functions += [
            _function(f, data, siblings, sorted(findings.get((path, f.qualname), [])))
            for f in siblings
        ]

    gaps = sorted(
        (gap for f in functions if (gap := _gap(f, threshold)) is not None),
        key=lambda g: (-g.risk, g.percent, g.path, g.line),
    )
    return CoverageReport(
        tool=raw.tool,
        branch_coverage=raw.branch_coverage,
        threshold=threshold,
        totals=_totals(files, functions),
        files=files,
        functions=functions,
        gaps=gaps,
        unmeasured_files=unmeasured,
    )


def is_production(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts[-1] not in EXCLUDED_FILES and not TEST_DIRECTORIES & set(parts[:-1])


def own_lines(function: FunctionInfo, siblings: Iterable[FunctionInfo]) -> Callable[[int], bool]:
    """Whether a line is one of the function's own: its body, without the bodies of functions
    nested in it.

    The `def` line itself runs when the module (or enclosing scope) is executed, not when the
    function is called, so it belongs to the enclosing scope.
    """
    nested = [
        (other.line, other.end_line)
        for other in siblings
        if other is not function
        and function.line < other.line
        and other.end_line <= function.end_line
    ]

    def own(line: int) -> bool:
        return function.line < line <= function.end_line and not any(
            start < line <= end for start, end in nested
        )

    return own


# ---------------------------------------------------------------------------------- counting
def _counts(
    statements: frozenset[int],
    executed: frozenset[int],
    taken: frozenset[tuple[int, int]],
    missed: frozenset[tuple[int, int]],
) -> dict[str, Any]:
    covered = len(statements & executed)
    branches = len(taken) + len(missed)
    return CoverageCounts(
        statements=len(statements),
        covered_lines=covered,
        branches=branches,
        covered_branches=len(taken),
        line_percent=percent(covered, len(statements)),
        branch_percent=percent(len(taken), branches),
        percent=percent(covered + len(taken), len(statements) + branches),
    ).model_dump()


def percent(part: int, whole: int) -> float:
    """coverage.py's convention: nothing to cover counts as fully covered."""
    return round(FULLY_COVERED * part / whole, 2) if whole else FULLY_COVERED


def _file(path: str, data: RawFile) -> FileCoverage:
    statements = data.statements
    return FileCoverage(
        path=path,
        **_counts(statements, data.executed, data.executed_branches, data.missing_branches),
        missing_lines=sorted(data.missing),
        missing=line_ranges(data.missing, statements),
        missing_branches_detail=_arcs(data.missing_branches),
    )


def _function(
    function: FunctionInfo, data: RawFile, siblings: list[FunctionInfo], rule_ids: list[str]
) -> FunctionCoverage:
    own = own_lines(function, siblings)
    statements = frozenset(line for line in data.statements if own(line))
    taken = frozenset(arc for arc in data.executed_branches if own(arc[0]))
    missed = frozenset(arc for arc in data.missing_branches if own(arc[0]))
    return FunctionCoverage(
        path=function.path,
        qualname=function.qualname,
        line=function.line,
        end_line=function.end_line,
        **_counts(statements, data.executed, taken, missed),
        missing=line_ranges(statements - data.executed, statements),
        missing_branches_detail=_arcs(missed),
        complexity=function.complexity,
        findings=rule_ids,
        is_public=function.is_public,
    )


def _totals(files: list[FileCoverage], functions: list[FunctionCoverage]) -> CoverageTotals:
    statements = sum(f.statements for f in files)
    covered = sum(f.covered_lines for f in files)
    branches = sum(f.branches for f in files)
    taken = sum(f.covered_branches for f in files)
    return CoverageTotals(
        statements=statements,
        covered_lines=covered,
        branches=branches,
        covered_branches=taken,
        line_percent=percent(covered, statements),
        branch_percent=percent(taken, branches),
        percent=percent(covered + taken, statements + branches),
        files=len(files),
        functions=len(functions),
        functions_never_executed=sum(1 for f in functions if f.statements and f.covered_lines == 0),
    )


# ---------------------------------------------------------------------------------- gaps
def _gap(function: FunctionCoverage, threshold: float) -> CoverageGap | None:
    if function.statements == 0 or function.percent >= threshold:
        return None
    uncovered = 1 - function.percent / 100
    risk = round(uncovered * (function.complexity + FINDING_WEIGHT * len(function.findings)), 2)
    return CoverageGap(
        path=function.path,
        qualname=function.qualname,
        line=function.line,
        percent=function.percent,
        missing=function.missing,
        missing_branches_detail=function.missing_branches_detail,
        risk=risk,
        reasons=_reasons(function),
    )


def _reasons(function: FunctionCoverage) -> list[str]:
    missing = function.statements - function.covered_lines
    reasons = []
    if function.covered_lines == 0:
        reasons.append("never executed by the test suite")
    elif missing:
        reasons.append(f"{missing} of {function.statements} statements not executed")
    if function.branches > function.covered_branches:
        reasons.append(
            f"{function.branches - function.covered_branches} of {function.branches} "
            "branches never taken"
        )
    if function.complexity > 1:
        reasons.append(f"cyclomatic complexity {function.complexity}")
    if function.findings:
        reasons.append("static-analysis findings: " + ", ".join(function.findings))
    return reasons


# ---------------------------------------------------------------------------------- formatting
def line_ranges(missing: Iterable[int], statements: Iterable[int]) -> list[str]:
    """Group missing lines like coverage.py does: a range ends only at an executed statement,
    so blank lines and comments between two missing statements do not split it."""
    missing_set = set(missing)
    ranges: list[str] = []
    start: int | None = None
    end = 0
    for line in sorted(set(statements) | missing_set):
        if line in missing_set:
            if start is None:
                start = line
            end = line
        elif start is not None:
            ranges.append(_range(start, end))
            start = None
    if start is not None:
        ranges.append(_range(start, end))
    return ranges


def _range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _arcs(arcs: Iterable[tuple[int, int]]) -> list[str]:
    return [f"{a}->{b if b > 0 else 'exit'}" for a, b in sorted(arcs)]

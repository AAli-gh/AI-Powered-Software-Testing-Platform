"""Code coverage: measured by coverage.py in the sandbox, interpreted here (Phase 6)."""

from testmind.coverage_engine.compare import compare_coverage
from testmind.coverage_engine.models import (
    CoverageDiff,
    CoverageGap,
    CoverageReport,
    CoverageTotals,
    FileCoverage,
    FunctionCoverage,
)
from testmind.coverage_engine.parser import CoverageParseError, RawCoverage, parse_coverage_json
from testmind.coverage_engine.report import build_coverage_report

__all__ = [
    "CoverageDiff",
    "CoverageGap",
    "CoverageParseError",
    "CoverageReport",
    "CoverageTotals",
    "FileCoverage",
    "FunctionCoverage",
    "RawCoverage",
    "build_coverage_report",
    "compare_coverage",
    "parse_coverage_json",
]

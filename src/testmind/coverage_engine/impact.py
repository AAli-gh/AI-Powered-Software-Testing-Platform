"""Which tests execute which functions, from coverage.py's per-test contexts.

The regression engine uses this to select the tests affected by a change. Module-level code
(imports, class bodies, constants) runs while pytest collects, outside any test context, so
it has no test attribution; changes there are handled through the import graph instead.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.coverage_engine.parser import RawCoverage
from testmind.coverage_engine.report import is_production, own_lines


class TestImpact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    __test__ = False

    functions: dict[str, list[str]] = Field(
        description="'path::qualname' -> pytest nodeids of the tests that executed it"
    )
    tests: int = Field(description="Distinct tests seen in the contexts")

    @staticmethod
    def key(path: str, qualname: str) -> str:
        return f"{path}::{qualname}"


def build_test_impact(raw: RawCoverage, code: CodeProfile) -> TestImpact:
    by_path: dict[str, list[FunctionInfo]] = defaultdict(list)
    for function in code.functions:
        if is_production(function.path):
            by_path[function.path].append(function)

    impact: dict[str, list[str]] = {}
    seen: set[str] = set()
    for path, functions in by_path.items():
        data = raw.files.get(path)
        if data is None or not data.contexts:
            continue
        for function in functions:
            own = own_lines(function, functions)
            tests = {test for line, names in data.contexts.items() if own(line) for test in names}
            if tests:
                impact[TestImpact.key(path, function.qualname)] = sorted(tests)
                seen |= tests
    return TestImpact(functions=impact, tests=len(seen))

"""The program spectrum: which tests executed which production function.

Built from coverage.py's per-test contexts (`TestImpact`). Test ids are kept in the form
TestMind reports tests in, `file::name` (contexts name the class too, `file::Class::name`),
so they match the run's outcomes. Two tests of the same name in different classes of one file
share an id; they are counted once.

The compact JSON form stores each test id once and functions as lists of test indexes, so an
execution's spectrum stays small enough to keep with it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from testmind.analyzers.code.models import CodeProfile
from testmind.coverage_engine.impact import TestImpact
from testmind.regression.selection import short_nodeid


class SpectrumError(ValueError):
    """A stored spectrum is not in the expected form."""


@dataclass(frozen=True, slots=True)
class Spectrum:
    tests: tuple[str, ...]
    # 'path::qualname' -> (first line, indexes into `tests` of the tests that executed it)
    functions: Mapping[str, tuple[int, frozenset[int]]] = field(default_factory=dict)

    @classmethod
    def build(cls, impact: TestImpact, code: CodeProfile) -> Spectrum:
        lines = {TestImpact.key(f.path, f.qualname): f.line for f in code.functions}
        ids = sorted({short_nodeid(t) for tests in impact.functions.values() for t in tests})
        index = {test: i for i, test in enumerate(ids)}
        functions = {
            key: (lines.get(key, 0), frozenset(index[short_nodeid(t)] for t in tests))
            for key, tests in impact.functions.items()
        }
        return cls(tuple(ids), functions)

    def executed_by(self, key: str) -> frozenset[str]:
        entry = self.functions.get(key)
        return frozenset(self.tests[i] for i in entry[1]) if entry else frozenset()

    def to_json(self) -> dict[str, Any]:
        return {
            "tests": list(self.tests),
            "functions": {
                key: [line, sorted(indexes)] for key, (line, indexes) in self.functions.items()
            },
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Spectrum:
        try:
            tests = tuple(str(t) for t in data["tests"])
            functions = {
                str(key): (int(line), frozenset(int(i) for i in indexes))
                for key, (line, indexes) in data["functions"].items()
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise SpectrumError(f"Invalid spectrum: {exc}") from exc
        if any(not 0 <= i < len(tests) for _, indexes in functions.values() for i in indexes):
            raise SpectrumError("Invalid spectrum: a test index is out of range")
        return cls(tests, functions)

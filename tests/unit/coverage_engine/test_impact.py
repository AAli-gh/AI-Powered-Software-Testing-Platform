import json
from pathlib import Path

from testmind.analyzers.code import CodeAnalyzer
from testmind.coverage_engine import parse_coverage_json
from testmind.coverage_engine.impact import TestImpact, build_test_impact
from tests.unit.analyzers.helpers import make_repo

SOURCE = """\
def outer(x):
    def inner():
        return x
    return inner()


def untouched():
    return 0
"""


def report(contexts: dict[str, list[str]]) -> str:
    return json.dumps(
        {
            "meta": {"version": "7.16.1", "branch_coverage": True},
            "files": {
                "app/m.py": {
                    "executed_lines": [1, 2, 3, 4, 7],
                    "missing_lines": [8],
                    "contexts": contexts,
                },
                "tests/test_m.py": {
                    "executed_lines": [1],
                    "missing_lines": [],
                    "contexts": {"1": ["tests/test_m.py::test_a|run"]},
                },
            },
        }
    )


def test_contexts_are_normalised() -> None:
    raw = parse_coverage_json(
        report(
            {
                "1": [""],  # import time: no test
                "3": ["tests/test_m.py::test_a|run", "tests/test_m.py::TestB::test_b[1]|setup"],
                "4": ["::TestGenerated::test_g|run", "no-separator|run"],  # dropped
                "x": ["tests/test_m.py::test_a|run"],  # not a line number
            }
        ),
        rootdir="/workspace/src",
    )
    assert dict(raw.files["app/m.py"].contexts) == {
        3: frozenset({"tests/test_m.py::test_a", "tests/test_m.py::TestB::test_b[1]"})
    }


def test_generated_tests_are_mapped_under_tests() -> None:
    raw = parse_coverage_json(
        report({"3": ["../tests/testmind/test_g.py::test_g|run"]}), rootdir="/workspace/src"
    )
    assert raw.files["app/m.py"].contexts[3] == frozenset({"tests/testmind/test_g.py::test_g"})


def test_impact_by_function(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"app/m.py": SOURCE, "tests/test_m.py": "def test_a(): pass\n"})
    code = CodeAnalyzer().analyze(root, ["app/m.py", "tests/test_m.py"])
    raw = parse_coverage_json(
        report(
            {
                "3": ["tests/test_m.py::test_inner|run"],  # inside inner
                "4": ["tests/test_m.py::test_outer|run"],  # outer's own line
            }
        ),
        rootdir="/workspace/src",
    )
    impact = build_test_impact(raw, code)
    assert impact.functions == {
        TestImpact.key("app/m.py", "outer"): ["tests/test_m.py::test_outer"],
        TestImpact.key("app/m.py", "outer.<locals>.inner"): ["tests/test_m.py::test_inner"],
    }
    assert impact.tests == 2
    assert build_test_impact(parse_coverage_json(report({}), rootdir="/workspace/src"), code) == (
        TestImpact(functions={}, tests=0)
    )

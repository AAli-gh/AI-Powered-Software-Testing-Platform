"""Coverage in the real sandbox, cross-checked against coverage.py's own numbers.

TestMind computes function coverage itself (from the Phase 3 function boundaries) so that it
does not depend on the coverage.py version installed by a project. These tests prove that the
result is identical to coverage.py's own file totals and function regions.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from testmind.coverage_engine import CoverageReport, build_coverage_report, parse_coverage_json
from testmind.sandbox.docker_sandbox import DockerSandbox, PytestJob
from testmind.sandbox.runner_image import RunnerImageBuilder
from tests.sandbox.test_suite_runner import EXAMPLES, analyse
from tests.unit.analyzers.helpers import make_repo

TRICKY = """\
import functools


def deco(f):
    @functools.wraps(f)
    def wrapper(*a, **k):
        return f(*a, **k)

    return wrapper


@deco
def decorated(x):
    if x > 0:
        return "pos"
    return "neg"


def outer(n):
    def inner(m):
        return m * 2

    class Local:
        def method(self):
            return 1

    total = 0
    for i in range(n):
        total += inner(i)
    return total


class Shape:
    def __init__(self, sides):
        self.sides = sides

    @property
    def name(self):
        return {3: "triangle", 4: "square"}.get(self.sides, "polygon")

    def describe(
        self,
        prefix="",
    ):
        try:
            return prefix + self.name
        except TypeError:
            return "?"


async def fetch(x):
    return x


def never(y):
    while y:
        y -= 1
    return y
"""

TRICKY_TESTS = """\
from shapes import Shape, decorated, outer


def test_decorated():
    assert decorated(1) == "pos"


def test_outer():
    assert outer(3) == 6


def test_shape():
    assert Shape(3).describe("a ") == "a triangle"
"""


def coverage_of(sandbox: DockerSandbox, root: Path) -> tuple[dict[str, Any], CoverageReport]:
    repository, code = analyse(root)
    image = RunnerImageBuilder(sandbox.image).ensure(repository.dependencies).tag
    env = (
        {"DJANGO_SETTINGS_MODULE": repository.django_settings_module}
        if repository.django_settings_module
        else {}
    )
    job = PytestJob(project_tests=tuple(repository.test_files), image=image, env=env, coverage=True)
    result = sandbox.run(root, job)
    assert result.coverage_json is not None, result.coverage_error
    report = build_coverage_report(
        parse_coverage_json(result.coverage_json, rootdir="/workspace/src/"), code
    )
    return json.loads(result.coverage_json)["files"], report


def assert_agrees_with_coverage_py(
    raw: dict[str, Any], report: CoverageReport, *, differs: frozenset[str] = frozenset()
) -> int:
    """Every file and function: same statement, line and branch counts as coverage.py
    (functions in `differs` are checked separately by the caller)."""
    for file in report.files:
        summary = raw[file.path]["summary"]
        assert (file.statements, file.covered_lines, file.branches, file.covered_branches) == (
            summary["num_statements"],
            summary["covered_lines"],
            summary["num_branches"],
            summary["covered_branches"],
        ), file.path
        assert file.percent == pytest.approx(summary["percent_covered"], abs=0.01)
    for function in report.functions:
        if function.qualname in differs:
            continue
        # coverage.py names nested scopes "outer.inner" where Python says "outer.<locals>.inner".
        name = function.qualname.replace(".<locals>.", ".")
        region = raw[function.path]["functions"][name]["summary"]
        assert (
            function.statements,
            function.covered_lines,
            function.branches,
            function.covered_branches,
        ) == (
            region["num_statements"],
            region["covered_lines"],
            region["num_branches"],
            region["covered_branches"],
        ), function.qualname
    return len(report.functions) - len(differs)


@pytest.mark.parametrize(
    "example", ["discount_service", "fastapi_todo", "flask_notes", "django_blog", "buggy_inventory"]
)
def test_examples_agree_with_coverage_py(sandbox: DockerSandbox, example: str) -> None:
    raw, report = coverage_of(sandbox, EXAMPLES / example)
    assert assert_agrees_with_coverage_py(raw, report) > 0


def test_tricky_scopes_agree_with_coverage_py(sandbox: DockerSandbox, tmp_path: Path) -> None:
    # tmp_path itself is private (0700); the container user can read a subdirectory.
    root = make_repo(tmp_path / "repo", {"shapes.py": TRICKY, "tests/test_shapes.py": TRICKY_TESTS})
    raw, report = coverage_of(sandbox, root)
    assert assert_agrees_with_coverage_py(raw, report, differs=frozenset({"deco"})) == 10

    # The one intended difference: the decorator line of a nested function (line 5,
    # `@functools.wraps(f)`) runs when `deco` is called, so TestMind counts it in `deco`.
    # coverage.py's function regions start at the `def` line and leave it to the module.
    functions = {f.qualname: f for f in report.functions}
    assert (functions["deco"].statements, functions["deco"].covered_lines) == (3, 3)
    assert raw["shapes.py"]["functions"]["deco"]["executed_lines"] == [6, 9]
    assert 5 in raw["shapes.py"]["functions"][""]["executed_lines"]

    assert functions["outer.<locals>.inner"].percent == 100.0
    assert functions["outer.<locals>.Local.method"].covered_lines == 0
    assert functions["never"].missing == ["56-58"]
    assert functions["never"].missing_branches_detail == ["56->57", "56->58"]
    assert functions["Shape.describe"].missing == ["47-48"]
    gaps = [g.qualname for g in report.gaps]
    assert gaps[0] == "never"  # complexity 2, nothing ran
    assert "outer.<locals>.inner" not in gaps

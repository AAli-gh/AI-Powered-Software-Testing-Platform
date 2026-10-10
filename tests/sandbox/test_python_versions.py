"""Every supported Python version, for real: a project pinned to it runs on its image, and
TestMind's in-sandbox parts (pytest plugins, coverage, the mutation driver) work there.

The images come from `make sandbox-images`. A version whose image is missing is skipped
locally; with TESTMIND_REQUIRE_PYTHON_IMAGES=1 (CI) it fails instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testmind.mutation_engine.engine import MutationEngine
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.models import Outcome
from testmind.sandbox.python_versions import SANDBOX_PYTHONS, image_for
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner
from tests.sandbox.conftest import SANDBOX_IMAGE
from tests.sandbox.test_suite_runner import analyse
from tests.unit.analyzers.helpers import make_repo

OPS = "def div(a, b):\n    return a / b\n\n\ndef add(a, b):\n    return a + b\n"


def project(root: Path, version: str) -> Path:
    expected = tuple(int(part) for part in version.split("."))
    return make_repo(
        root,
        {
            ".python-version": f"{version}\n",
            "calc/__init__.py": "",
            "calc/ops.py": OPS,
            "tests/test_ops.py": (
                "import sys\nfrom calc.ops import add, div\n\n"
                "def test_runs_on_the_pinned_python():\n"
                f"    assert sys.version_info[:2] == {expected}\n\n"
                "def test_add():\n    assert add(2, 3) == 5\n\n"
                "def test_div_by_zero():\n    assert div(1, 0) == 0\n"
            ),
        },
    )


@pytest.mark.parametrize("version", list(SANDBOX_PYTHONS))
def test_a_project_runs_on_its_python(sandbox: DockerSandbox, tmp_path: Path, version: str) -> None:
    image = image_for(SANDBOX_IMAGE, version)
    if not sandbox.available(image):
        if os.environ.get("TESTMIND_REQUIRE_PYTHON_IMAGES") == "1":
            pytest.fail(f"{image} is not built (make sandbox-images)")
        pytest.skip(f"{image} is not built (make sandbox-images)")
    root = project(tmp_path / "repo", version)
    repository, code = analyse(root)
    assert repository.python.pinned == version
    runner = TestSuiteRunner(
        sandbox, RunnerImageBuilder(SANDBOX_IMAGE, python_versions=tuple(SANDBOX_PYTHONS))
    )

    result = runner.run(root, repository, code=code)

    report = result.report
    assert report.install is not None
    assert (report.install.image, report.install.notes) == (
        image,
        [f"Python {version}: pinned to {version}"],
    )
    assert report.outcome is ExecutionOutcome.FAILED
    assert {t.name: t.outcome for t in report.tests} == {
        "test_runs_on_the_pinned_python": Outcome.PASSED,  # really that interpreter
        "test_add": Outcome.PASSED,
        "test_div_by_zero": Outcome.FAILED,
    }
    # coverage.py of that image, read by TestMind's own coverage engine
    assert result.coverage is not None
    assert result.coverage.totals.percent == 100.0
    # the failure-capture plugin
    assert result.failures is not None
    (group,) = result.failures.groups
    assert (group.exception_type, group.function.qualname if group.function else None) == (
        "ZeroDivisionError", "div",
    )  # fmt: skip
    # the mutation driver, on the same image
    mutation = MutationEngine(
        runner, sandbox, max_mutants=20, mutant_timeout_cap=30, job_timeout_cap=300
    ).run(root, repository, code)
    assert mutation.totals.killed >= 2
    assert mutation.totals.errors == 0

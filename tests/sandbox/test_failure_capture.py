"""Failure capture in the real sandbox (the testmind_failures pytest plugin)."""

import json
import re
from pathlib import Path
from typing import Any

from testmind.failure_engine import FailureCategory
from testmind.sandbox.docker_sandbox import DockerSandbox, PytestJob
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner
from tests.sandbox.test_suite_runner import EXAMPLES, analyse
from tests.unit.analyzers.helpers import make_repo
from tests.unit.failure_engine.test_analyzer import CAPTURE
from tests.unit.failure_engine.test_explainer import GENERATED_SOURCE

EXAMPLE = EXAMPLES / "failing_suite"
GENERATED = {"testmind/test_gen.py": GENERATED_SOURCE}
ADDRESS = re.compile(r"0x[0-9a-f]+")


def normalised(document: dict[str, Any]) -> dict[str, Any]:
    """Object reprs contain memory addresses; everything else must be identical."""
    stable: dict[str, Any] = json.loads(ADDRESS.sub("0x…", json.dumps(document, sort_keys=True)))
    return stable


def test_the_golden_capture_matches_a_live_run(sandbox: DockerSandbox) -> None:
    repository, _ = analyse(EXAMPLE)
    job = PytestJob(
        test_files=GENERATED,
        project_tests=tuple(repository.test_files),
        capture_failures=True,
        continue_on_collection_errors=True,
    )
    result = sandbox.run(EXAMPLE, job)
    assert result.failures_json is not None
    assert normalised(json.loads(result.failures_json)) == normalised(
        json.loads(CAPTURE.read_text())
    )


def test_the_runner_analyses_failures(sandbox: DockerSandbox) -> None:
    repository, code = analyse(EXAMPLE)
    runner = TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image))
    suite = runner.run(EXAMPLE, repository, GENERATED, code=code)

    # Collection errors no longer hide the other tests: all 14 failures ran and were analysed.
    assert suite.report.outcome is ExecutionOutcome.ERROR
    assert suite.report.totals.total == 14
    assert suite.failures is not None
    assert (suite.failures.failed_tests, suite.failures.analysed_tests) == (14, 14)
    assert sorted(g.category for g in suite.failures.groups) == sorted(
        [
            FailureCategory.EXCEPTION_IN_CODE, FailureCategory.EXCEPTION_IN_CODE,
            FailureCategory.ASSERTION_FAILURE, FailureCategory.ASSERTION_FAILURE,
            FailureCategory.EXCEPTION_IN_LIBRARY, FailureCategory.EXCEPTION_IN_TEST,
            FailureCategory.FIXTURE_ERROR, FailureCategory.UNEXPECTED_PASS,
            FailureCategory.IMPORT_ERROR, FailureCategory.SYNTAX_ERROR,
        ]
    )  # fmt: skip
    assert suite.coverage is not None  # coverage still measured next to the failures


def test_hostile_reprs_do_not_break_the_capture(sandbox: DockerSandbox, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "repo",
        {
            "tests/test_x.py": (
                "class Evil:\n"
                "    def __repr__(self):\n"
                "        raise RuntimeError('no')\n\n"
                "def test_x():\n"
                "    evil = Evil()\n"
                "    big = 'y' * 10000\n"
                "    assert evil is None\n"
            )
        },
    )
    result = sandbox.run(repo, PytestJob(project_tests=("tests/test_x.py",), capture_failures=True))
    assert result.failures_json is not None
    [failure] = json.loads(result.failures_json)["failures"]
    assert failure["locals"]["evil"] == "<repr failed: RuntimeError>"
    assert len(failure["locals"]["big"]) == 203  # 200 characters + "..."


def test_capture_is_off_unless_requested(sandbox: DockerSandbox) -> None:
    result = sandbox.run(EXAMPLE, PytestJob(project_tests=("tests/test_stock.py",)))
    assert result.failures_json is None

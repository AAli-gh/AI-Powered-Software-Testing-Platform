"""The mutation engine's planning, scoring and error handling. The two sandbox boundaries are
doubles here (a canned test run and a recording mutation sandbox); the real ones are covered
by tests/sandbox/test_mutation_engine.py and test_mutation_driver.py."""

import json
from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.mutation_engine.engine import MutationEngine
from testmind.mutation_engine.models import MutantStatus
from testmind.sandbox.docker_sandbox import MutationJob, MutationResult
from testmind.sandbox.models import Outcome, SandboxUnavailableError
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner
from tests.unit.analyzers.helpers import analyze
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

APP = "def double(x):\n    return x * 2\n\n\ndef untested(y):\n    return y + 1\n"
COVERAGE = json.dumps(
    {
        "meta": {"version": "7.16.1", "branch_coverage": True},
        "files": {
            "app.py": {
                "executed_lines": [1, 2, 5],
                "missing_lines": [6],
                "contexts": {
                    "2": [
                        "tests/test_app.py::test_double|run",
                        "tests/test_app.py::test_broken|run",
                    ]
                },
            }
        },
    }
)


class MutationSandbox:
    def __init__(self, answer: MutationResult | Exception | None = None) -> None:
        self.answer = answer
        self.jobs: list[MutationJob] = []

    def run_mutants(self, repo_root: Path, job: MutationJob) -> MutationResult:
        self.jobs.append(job)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is not None:
            return self.answer
        results = [
            {"id": m["id"], "status": "killed", "duration": 0.1, "killed_by": "t"}
            for m in job.mutants
        ]
        return MutationResult(0, False, 1.0, json.dumps({"version": 1, "results": results}), "")


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, RepositoryProfile, CodeProfile]:
    files = {
        "app.py": APP,
        "gone.py": "def gone(z):\n    return z - 1\n",
        "tests/test_app.py": "def test_double(): pass\n",
        "tests/helpers.py": "def helper(a):\n    return a + 1\n",
    }
    repository = analyze(tmp_path, files)
    code = CodeAnalyzer().analyze(tmp_path, ["app.py", "gone.py", "tests/helpers.py"])
    (tmp_path / "gone.py").unlink()  # removed after analysis: skipped, not an error
    return tmp_path, repository, code


def engine(
    mutation: MutationSandbox,
    *,
    coverage: str | None = COVERAGE,
    available: bool = True,
    **options: int,
) -> MutationEngine:
    baseline = result(
        case("test_double", Outcome.PASSED, file="tests/test_app.py"),
        case("test_broken", Outcome.FAILED, file="tests/test_app.py"),
        exit_code=1,
        coverage_json=coverage,
    )
    runner = TestSuiteRunner(Sandbox(baseline, available=available), Images())
    settings = {"max_mutants": 50, "mutant_timeout_cap": 30, "job_timeout_cap": 600, **options}
    return MutationEngine(runner, mutation, **settings)  # type: ignore[arg-type]


def test_plan_and_scores(project: tuple[Path, RepositoryProfile, CodeProfile]) -> None:
    root, repository, code = project
    sandbox = MutationSandbox()
    report = engine(sandbox).run(root, repository, code)

    [job] = sandbox.jobs
    # Only mutants on executed lines are sent, each with the tests that ran the line; the
    # test that already failed at baseline is excluded.
    assert [m["original"] for m in job.mutants] == ["return x * 2", "x * 2", "2"]
    assert all(m["tests"] == ["tests/test_app.py::test_double"] for m in job.mutants)
    assert all(m["timeout"] == 5.0 for m in job.mutants)
    assert job.image == "testmind-runner:abc"
    assert job.timeout_seconds == 3 * 5 + 60
    assert report.excluded_tests == ["tests/test_app.py::test_broken"]
    statuses = [(m.function, m.status) for m in report.mutants]
    assert statuses.count(("double", MutantStatus.KILLED)) == 3
    assert statuses.count(("untested", MutantStatus.NO_COVERAGE)) == 3
    assert (report.totals.score, report.totals.covered_score) == (50.0, 100.0)
    assert report.notes == ["1 test(s) already fail without mutation and were excluded"]


def test_limit(project: tuple[Path, RepositoryProfile, CodeProfile]) -> None:
    root, repository, code = project
    report = engine(MutationSandbox(), max_mutants=2).run(root, repository, code)
    assert (report.generated, report.totals.total) == (6, 2)
    assert report.notes[0] == "6 mutants generated; the 2 riskiest were run"


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        (SandboxUnavailableError("gone"), "Mutants were not run: gone"),
        (MutationResult(-1, True, 9.0, None, "tail"), "The mutation run exceeded 75 s"),
        (MutationResult(1, False, 1.0, None, "driver crashed"), "wrote no results: driver crashed"),
        (MutationResult(0, False, 1.0, '{"results": 3}', ""), "results are invalid"),
    ],
)
def test_driver_problems_become_errors_with_a_note(
    project: tuple[Path, RepositoryProfile, CodeProfile],
    answer: MutationResult | Exception,
    note: str,
) -> None:
    root, repository, code = project
    report = engine(MutationSandbox(answer)).run(root, repository, code)
    assert any(note in n for n in report.notes)
    assert report.totals.errors == 3
    assert report.totals.score == 0.0  # only the untested mutants were judged


def test_nothing_to_judge_without_a_baseline(
    project: tuple[Path, RepositoryProfile, CodeProfile],
) -> None:
    root, repository, code = project
    unavailable = engine(MutationSandbox(), available=False).run(root, repository, code)
    assert unavailable.outcome is ExecutionOutcome.SANDBOX_UNAVAILABLE
    assert unavailable.notes == ["The baseline run did not run tests (sandbox_unavailable)"]
    assert unavailable.mutants == []
    assert unavailable.totals.score is None

    unmeasured = engine(MutationSandbox(), coverage=None).run(root, repository, code)
    assert unmeasured.notes == [
        "No per-test coverage from the baseline run: mutants cannot be targeted "
        "(coverage.py wrote no report)"
    ]

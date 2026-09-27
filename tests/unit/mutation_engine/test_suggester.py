"""The suggester's loop and checks (scripted model, canned sandbox answers). The real sandbox
is used in tests/sandbox/test_mutant_suggestions.py."""

import json
from pathlib import Path

import pytest

from testmind.ai_engine.llm import LLMError
from testmind.mutation_engine.models import MutantResult, MutantStatus, MutationReport, Score
from testmind.mutation_engine.suggester import (
    MutantTestSuggester,
    SuggestionResult,
    SuggestionStatus,
)
from testmind.sandbox.docker_sandbox import MutationJob, MutationResult
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError, TestCaseResult
from testmind.test_engine.models import ExecutionOutcome, TestTotals
from tests.doubles import ScriptedLLM
from tests.unit.analyzers.helpers import make_repo

SOURCE = "def over(weight):\n    return weight > 10\n"
TEST = "```python\nfrom app import over\n\ndef test_ten():\n    assert over(10) is False\n```"


def survivor(ident: str = "m1") -> MutantResult:
    start = SOURCE.index("weight > 10")
    return MutantResult(
        id=ident, path="app.py", function="over", line=2, column=12, start=start,
        end=start + len("weight > 10"), operator="comparison", original="weight > 10",
        replacement="(weight >= 10)", status=MutantStatus.SURVIVED, tests=1, killed_by=None,
        duration_seconds=0.1, detail=None,
    )  # fmt: skip


def report(*mutants: MutantResult) -> MutationReport:
    score = Score(
        total=0,
        killed=0,
        survived=0,
        timeout=0,
        no_coverage=0,
        errors=0,
        score=None,
        covered_score=None,
    )
    return MutationReport(
        outcome=ExecutionOutcome.PASSED, baseline_totals=TestTotals(), excluded_tests=[],
        generated=len(mutants), totals=score, files=[], functions=[], mutants=list(mutants),
        duration_seconds=0, notes=[],
    )  # fmt: skip


def run(outcome: Outcome, exit_code: int = 0) -> SandboxResult:
    tests = [TestCaseResult(name="test_ten", classname="c", outcome=outcome, message="boom")]
    return SandboxResult(
        exit_code=exit_code, timed_out=False, duration_seconds=0.1, tests=tests, output=""
    )


def driver(status: str | None) -> MutationResult:
    body = None if status is None else json.dumps({"results": [{"id": "m1", "status": status}]})
    return MutationResult(0, False, 1.0, body, "")


class Sandbox:
    def __init__(self, originals: list[SandboxResult], kills: list[MutationResult]) -> None:
        self.originals = originals
        self.kills = kills
        self.jobs: list[MutationJob] = []
        self.pytest_files: list[dict[str, str]] = []

    def run_pytest(
        self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
    ) -> SandboxResult:
        self.pytest_files.append(test_files)
        return self.originals.pop(0)

    def run_mutants(self, repo_root: Path, job: MutationJob) -> MutationResult:
        self.jobs.append(job)
        return self.kills.pop(0)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return make_repo(tmp_path, {"app.py": SOURCE})


def suggest(
    root: Path, llm: ScriptedLLM, sandbox: Sandbox, *mutants: MutantResult
) -> SuggestionResult:
    suggester = MutantTestSuggester(llm, sandbox, image="runner:1", max_tokens=100, max_mutants=5)
    return suggester.suggest(root, report(*(mutants or (survivor(),))))


def test_accepted_only_after_both_sandbox_checks(root: Path) -> None:
    llm = ScriptedLLM([TEST])
    sandbox = Sandbox([run(Outcome.PASSED)], [driver("killed")])
    result = suggest(root, llm, sandbox)

    [suggestion] = result.suggestions
    assert (suggestion.status, result.accepted) == (SuggestionStatus.ACCEPTED, 1)
    assert suggestion.test_path == "tests/testmind/test_kill_m1.py"
    assert [a.outcome for a in suggestion.attempts] == ["accepted"]
    [job] = sandbox.jobs
    [mutant] = job.mutants
    assert (mutant["original"], mutant["replacement"], mutant["tests"]) == (
        "weight > 10", "(weight >= 10)", ["tests/testmind/test_kill_m1.py"],
    )  # fmt: skip
    assert job.files == {"tests/testmind/test_kill_m1.py": suggestion.code}
    assert sandbox.pytest_files == [{"testmind/test_kill_m1.py": suggestion.code}]
    prompt = llm.requests[0].prompt
    assert "Changed to:       (weight >= 10)" in prompt
    assert ">    2 |     return weight > 10" in prompt


def test_feedback_then_acceptance(root: Path) -> None:
    llm = ScriptedLLM([TEST, TEST])
    sandbox = Sandbox([run(Outcome.FAILED, 1), run(Outcome.PASSED)], [driver("killed")])
    [suggestion] = suggest(root, llm, sandbox).suggestions
    assert [a.outcome for a in suggestion.attempts] == ["fails_on_original", "accepted"]
    assert "does not pass on the ORIGINAL code" in llm.requests[1].prompt
    assert "test_ten: boom" in llm.requests[1].prompt


def test_a_test_that_does_not_kill_is_rejected(root: Path) -> None:
    llm = ScriptedLLM([TEST, TEST])
    sandbox = Sandbox([run(Outcome.PASSED)] * 2, [driver("survived"), driver("survived")])
    [suggestion] = suggest(root, llm, sandbox).suggestions
    assert suggestion.status is SuggestionStatus.REJECTED
    assert [a.outcome for a in suggestion.attempts] == ["mutant_survives", "mutant_survives"]
    assert "ALSO passes when `weight > 10` is changed to `(weight >= 10)`" in llm.requests[1].prompt
    assert suggestion.code is not None  # the last candidate is kept for inspection


@pytest.mark.parametrize(
    ("replies", "originals", "kills", "status", "outcomes"),
    [
        (
            ["no code here", "```python\ndef test_x(:\n```"],
            [],
            [],
            SuggestionStatus.REJECTED,
            ["rejected", "rejected"],
        ),
        ([LLMError("down")], [], [], SuggestionStatus.LLM_ERROR, ["llm_error"]),
        (
            [TEST, TEST],
            [run(Outcome.PASSED)] * 2,
            [driver(None), driver("error")],
            SuggestionStatus.REJECTED,
            ["sandbox_error", "sandbox_error"],
        ),
        (
            [TEST, TEST],
            [run(Outcome.PASSED)] * 2,
            [
                MutationResult(0, False, 1.0, "{}", ""),
                MutationResult(0, False, 1.0, '{"results": []}', ""),
            ],
            SuggestionStatus.REJECTED,
            ["sandbox_error", "sandbox_error"],
        ),
        (
            [TEST, TEST],
            [run(Outcome.PASSED)] * 2,
            [driver("timeout")],
            SuggestionStatus.ACCEPTED,
            ["accepted"],
        ),
    ],
)
def test_outcomes(
    root: Path,
    *,
    replies: list[str | Exception],
    originals: list[SandboxResult],
    kills: list[MutationResult],
    status: SuggestionStatus,
    outcomes: list[str],
) -> None:
    [suggestion] = suggest(root, ScriptedLLM(replies), Sandbox(originals, kills)).suggestions
    assert suggestion.status is status
    assert [a.outcome for a in suggestion.attempts] == outcomes


def test_sandbox_unavailable(root: Path) -> None:
    class Gone(Sandbox):
        def run_pytest(
            self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
        ) -> SandboxResult:
            raise SandboxUnavailableError("no docker")

    [suggestion] = suggest(root, ScriptedLLM([TEST]), Gone([], [])).suggestions
    assert (suggestion.status, suggestion.attempts[0].feedback) == (
        SuggestionStatus.SANDBOX_ERROR, "no docker",
    )  # fmt: skip


def test_only_survivors_up_to_the_limit(root: Path) -> None:
    killed = survivor("k").model_copy(update={"status": MutantStatus.KILLED})
    llm = ScriptedLLM([TEST] * 5)
    sandbox = Sandbox([run(Outcome.PASSED)] * 5, [driver("killed")] * 5)
    suggester = MutantTestSuggester(llm, sandbox, image=None, max_tokens=100, max_mutants=2)
    result = suggester.suggest(root, report(killed, survivor("a"), survivor("b"), survivor("c")))
    assert [s.mutant_id for s in result.suggestions] == ["a", "b"]
    assert (result.input_tokens, result.output_tokens, result.model) == (200, 100, "scripted-model")

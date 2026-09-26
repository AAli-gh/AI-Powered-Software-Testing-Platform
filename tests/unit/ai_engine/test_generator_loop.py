"""Branches of the generator loop that are awkward to reach with the real sandbox.
The sandbox double here returns canned results; the real one is covered in tests/sandbox/."""

from pathlib import Path

import pytest

from testmind.ai_engine.llm import LLMError, LLMRefusalError
from testmind.ai_engine.test_generator.generator import TestGenerator
from testmind.ai_engine.test_generator.models import (
    FileStatus,
    GenerationOptions,
    GenerationResult,
    TestKind,
)
from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError, TestCaseResult
from tests.doubles import ScriptedLLM
from tests.unit.analyzers.helpers import make_repo

VALID = "```python\nfrom pkg.mod import f\n\ndef test_f():\n    assert f() == 1\n```"
INVALID = "```python\ndef test_f(:\n```"


class CannedSandbox:
    def __init__(self, results: list[SandboxResult | Exception], *, available: bool = True) -> None:
        self._results = results
        self._available = available
        self.calls: list[dict[str, str]] = []
        self.images: list[str | None] = []

    def available(self) -> bool:
        return self._available

    def run_pytest(
        self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
    ) -> SandboxResult:
        self.calls.append(test_files)
        self.images.append(image)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def result(
    *outcomes: Outcome, exit_code: int = 0, timed_out: bool = False, output: str = ""
) -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=0.1,
        tests=[
            TestCaseResult(name=f"test_{i}", classname="c", outcome=o, message="boom")
            for i, o in enumerate(outcomes)
        ],
        output=output,
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, CodeProfile]:
    root = make_repo(tmp_path, {"pkg/__init__.py": "", "pkg/mod.py": "def f():\n    return 1\n"})
    return root, CodeAnalyzer().analyze(root, ["pkg/__init__.py", "pkg/mod.py"])


def run(
    repo: tuple[Path, CodeProfile], llm: ScriptedLLM, sandbox: CannedSandbox | None
) -> GenerationResult:
    root, profile = repo
    generator = TestGenerator(llm, sandbox, max_iterations=3, max_tokens=4000)
    return generator.generate(root, profile, GenerationOptions(), max_targets=10)


def test_without_sandbox_code_is_unverified(repo: tuple[Path, CodeProfile]) -> None:
    [file] = run(repo, ScriptedLLM([VALID]), None).files
    assert file.status is FileStatus.UNVERIFIED
    assert file.code is not None
    assert file.run is None


def test_sandbox_not_available(repo: tuple[Path, CodeProfile]) -> None:
    [file] = run(repo, ScriptedLLM([VALID]), CannedSandbox([], available=False)).files
    assert file.status is FileStatus.UNVERIFIED


def test_sandbox_disappearing_mid_run(repo: tuple[Path, CodeProfile]) -> None:
    sandbox = CannedSandbox([SandboxUnavailableError("gone")])
    [file] = run(repo, ScriptedLLM([VALID]), sandbox).files
    assert file.status is FileStatus.UNVERIFIED
    assert file.attempts[0].feedback == "gone"


def test_rejected_code_is_sent_back_then_accepted(repo: tuple[Path, CodeProfile]) -> None:
    llm = ScriptedLLM(["Sorry, no code.", INVALID, VALID])
    sandbox = CannedSandbox([result(Outcome.PASSED)])
    [file] = run(repo, llm, sandbox).files
    assert file.status is FileStatus.PASSED
    assert [a.outcome for a in file.attempts] == ["rejected", "rejected", "passed"]
    assert "did not contain a Python code block" in llm.requests[1].prompt
    assert "SyntaxError" in llm.requests[2].prompt
    assert list(sandbox.calls[0]) == ["testmind/test_pkg_mod.py"]


def test_never_valid_is_rejected_without_code(repo: tuple[Path, CodeProfile]) -> None:
    [file] = run(repo, ScriptedLLM([INVALID, INVALID, INVALID]), CannedSandbox([])).files
    assert file.status is FileStatus.REJECTED
    assert file.code is None
    assert file.test_count == 0


def test_failure_then_worse_rewrite_is_reported_as_failed(repo: tuple[Path, CodeProfile]) -> None:
    sandbox = CannedSandbox([result(Outcome.FAILED, exit_code=1)])
    [file] = run(repo, ScriptedLLM([VALID, INVALID, INVALID]), sandbox).files
    assert file.status is FileStatus.FAILED
    assert file.code is not None
    assert file.run is not None
    assert file.run.failed == 1


@pytest.mark.parametrize("error", [LLMError("api down"), LLMRefusalError("declined")])
def test_llm_errors(repo: tuple[Path, CodeProfile], error: LLMError) -> None:
    [file] = run(repo, ScriptedLLM([error]), CannedSandbox([])).files
    assert file.status is FileStatus.LLM_ERROR
    assert file.attempts[0].feedback == str(error)


@pytest.mark.parametrize(
    ("sandbox_result", "expected_feedback"),
    [
        (result(timed_out=True, exit_code=-1), "timed out"),
        (result(exit_code=2, output="INTERNALERROR boom"), "collected no tests"),
        (result(Outcome.ERROR, exit_code=1), "test_0: error: boom"),
    ],
)
def test_failure_reports(
    repo: tuple[Path, CodeProfile], sandbox_result: SandboxResult, expected_feedback: str
) -> None:
    llm = ScriptedLLM([VALID, VALID])
    sandbox = CannedSandbox([sandbox_result, result(Outcome.PASSED)])
    [file] = run(repo, llm, sandbox).files
    assert file.status is FileStatus.PASSED
    assert expected_feedback in (file.attempts[0].feedback or "")
    assert expected_feedback in llm.requests[1].prompt


def test_import_error_without_quotes(repo: tuple[Path, CodeProfile]) -> None:
    sandbox = CannedSandbox(
        [result(exit_code=2, output="ImportError: cannot import name x from y")]
    )
    [file] = run(repo, ScriptedLLM([VALID]), sandbox).files
    assert file.status is FileStatus.ENVIRONMENT_ERROR


def test_requested_kinds_and_findings_reach_the_prompt(tmp_path: Path) -> None:
    source = (
        "def div(a, b: int = 1) -> float:\n    '''Divide.'''\n"
        "    if a is None:\n        raise ValueError\n    return a / b\n"
    )
    root = make_repo(tmp_path, {"calc.py": source})
    profile = CodeAnalyzer().analyze(root, ["calc.py"])
    llm = ScriptedLLM([VALID.replace("pkg.mod", "calc")])
    TestGenerator(llm, None, max_iterations=1, max_tokens=10).generate(
        root, profile, GenerationOptions(kinds=[TestKind.NEGATIVE]), max_targets=5
    )
    prompt = llm.requests[0].prompt
    assert "- negative:" in prompt
    assert "- unit:" not in prompt
    assert "TM040" in prompt  # b is a divisor that is never checked
    assert (
        '- div(a, b: int = 1) -> float  [complexity 2; raises ValueError; doc: "Divide."] (line 1)'
        in prompt
    )
    assert llm.requests[0].max_tokens == 10
    assert "pytest" in llm.requests[0].system


def test_no_targets(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"m.py": "def _hidden(): pass\n"})
    profile = CodeAnalyzer().analyze(root, ["m.py"])
    generated = TestGenerator(ScriptedLLM([]), None, max_iterations=1, max_tokens=10).generate(
        root, profile, GenerationOptions(), max_targets=5
    )
    assert generated.files == []
    assert (generated.targets_selected, generated.targets_available) == (0, 0)
    assert generated.model is None

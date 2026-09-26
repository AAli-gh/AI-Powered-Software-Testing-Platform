"""The real generator loop: static checks, the real Docker sandbox and the fix loop.
Only the LLM provider is scripted (no API key in CI)."""

from pathlib import Path

import pytest

from testmind.ai_engine.test_generator.generator import TestGenerator
from testmind.ai_engine.test_generator.models import FileStatus, GenerationOptions
from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.sandbox.docker_sandbox import DockerSandbox
from tests.doubles import ScriptedLLM
from tests.unit.analyzers.helpers import make_repo

SOURCE = "def average(total, count):\n    return total / count\n"

WRONG_EXPECTATION = """```python
from stats.core import average

def test_average():
    assert average(10, 4) == 2
```"""

FIXED_WITH_SUSPECTED_BUG = """```python
import pytest
from stats.core import average

def test_average():
    assert average(10, 4) == 2.5

@pytest.mark.xfail(reason="possible bug: zero count raises ZeroDivisionError", strict=True)
def test_average_of_nothing_is_zero():
    assert average(0, 0) == 0
```"""


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, CodeProfile]:
    root = make_repo(tmp_path / "repo", {"stats/__init__.py": "", "stats/core.py": SOURCE})
    return root, CodeAnalyzer().analyze(root, ["stats/__init__.py", "stats/core.py"])


def generator(
    llm: ScriptedLLM, sandbox: DockerSandbox | None, iterations: int = 3
) -> TestGenerator:
    return TestGenerator(llm, sandbox, max_iterations=iterations, max_tokens=4000)


def test_failure_is_fed_back_and_fixed(
    sandbox: DockerSandbox, repo: tuple[Path, CodeProfile]
) -> None:
    root, profile = repo
    llm = ScriptedLLM([WRONG_EXPECTATION, FIXED_WITH_SUSPECTED_BUG])

    result = generator(llm, sandbox).generate(root, profile, GenerationOptions(), max_targets=10)

    [file] = result.files
    assert file.status is FileStatus.PASSED
    assert file.path == "tests/testmind/test_stats_core.py"
    assert file.targets == ["average"]
    assert [a.outcome for a in file.attempts] == ["failed", "passed"]
    assert file.run is not None
    assert (file.run.passed, file.run.xfailed) == (1, 1)
    assert [b.test for b in file.suspected_bugs] == ["test_average_of_nothing_is_zero"]
    assert file.test_count == 2
    # the concrete pytest failure was sent back to the model
    assert "assert 2.5 == 2" in llm.requests[1].prompt
    assert "stats.core" in llm.requests[0].prompt
    assert SOURCE.strip() in llm.requests[0].prompt
    assert (result.input_tokens, result.output_tokens, result.model) == (200, 100, "scripted-model")


def test_gives_up_after_max_iterations(
    sandbox: DockerSandbox, repo: tuple[Path, CodeProfile]
) -> None:
    root, profile = repo
    llm = ScriptedLLM([WRONG_EXPECTATION, WRONG_EXPECTATION])
    [file] = (
        generator(llm, sandbox, iterations=2)
        .generate(root, profile, GenerationOptions(), max_targets=10)
        .files
    )
    assert file.status is FileStatus.FAILED
    assert file.iterations == 2
    assert file.suspected_bugs == []


def test_missing_dependency_is_an_environment_error(sandbox: DockerSandbox, tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "repo", {"web.py": "import requests\n\ndef fetch(url):\n    return url\n"}
    )
    profile = CodeAnalyzer().analyze(root, ["web.py"])
    reply = (
        "```python\nfrom web import fetch\n\ndef test_fetch():\n    assert fetch('x') == 'x'\n```"
    )
    llm = ScriptedLLM([reply])

    [file] = (
        generator(llm, sandbox).generate(root, profile, GenerationOptions(), max_targets=10).files
    )

    assert file.status is FileStatus.ENVIRONMENT_ERROR
    assert len(llm.requests) == 1  # not retried: the LLM cannot install packages
    assert "requests" in (file.attempts[0].feedback or "")

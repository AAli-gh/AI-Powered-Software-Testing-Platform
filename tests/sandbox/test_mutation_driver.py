"""The mutation driver (docker/testmind_mutate.py) in the real sandbox."""

import json
from pathlib import Path

import pytest

from testmind.sandbox.docker_sandbox import DockerSandbox, MutationJob
from testmind.sandbox.models import SandboxUnavailableError
from tests.unit.analyzers.helpers import make_repo

SOURCE = "def count(n):\n    i = 0\n    while i < n:\n        i += 1\n    return i\n"
TESTS = "from app import count\n\n\ndef test_count():\n    assert count(3) == 3\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo", {"app.py": SOURCE, "tests/test_app.py": TESTS})


def mutant(ident: str, original: str, replacement: str, **extra: object) -> dict[str, object]:
    start = SOURCE.encode().index(original.encode())
    return {
        "id": ident, "path": "app.py", "start": start, "end": start + len(original.encode()),
        "original": original, "replacement": replacement,
        "tests": ["tests/test_app.py::test_count"], "timeout": 10, **extra,
    }  # fmt: skip


def run(
    sandbox: DockerSandbox, repo: Path, *mutants: dict[str, object], timeout: int = 120
) -> dict[str, dict[str, object]]:
    job = MutationJob(image=sandbox.image, mutants=list(mutants), timeout_seconds=timeout)
    result = sandbox.run_mutants(repo, job)
    assert result.results_json is not None, result.output
    return {r["id"]: r for r in json.loads(result.results_json)["results"]}


def test_statuses(sandbox: DockerSandbox, repo: Path) -> None:
    results = run(
        sandbox,
        repo,
        mutant("killed", "return i", "return None"),
        mutant("survived", "i = 0", "i = (0)"),
        mutant("endless", "i += 1", "i += 0", timeout=3),
        {**mutant("mismatch", "return i", "return None"), "original": "return j"},
        mutant("import", "def count", "1 / 0\ndef count"),
    )
    assert results["killed"]["status"] == "killed"
    assert results["killed"]["killed_by"] == "tests/test_app.py::test_count"
    assert results["survived"]["status"] == "survived"
    assert results["endless"]["status"] == "timeout"  # the endless loop was killed
    assert results["mismatch"]["status"] == "error"
    assert results["mismatch"]["detail"] == "the source does not match the mutant"
    # A mutant that breaks import is detected: the nodeid is not found (pytest exit code 4)
    # because its module failed to collect.
    assert (results["import"]["status"], results["import"]["exit_code"]) == ("killed", 4)
    assert results["import"]["killed_by"] == "tests/test_app.py"


def test_the_repository_is_never_modified(sandbox: DockerSandbox, repo: Path) -> None:
    run(sandbox, repo, mutant("killed", "return i", "return None"))
    assert (repo / "app.py").read_text() == SOURCE


def test_job_timeout_stops_everything(sandbox: DockerSandbox, repo: Path) -> None:
    job = MutationJob(
        image=sandbox.image,
        mutants=[mutant("endless", "i += 1", "i += 0", timeout=60)],
        timeout_seconds=5,
    )
    result = sandbox.run_mutants(repo, job)
    assert result.timed_out is True
    assert result.results_json is None


def test_environment_is_passed_and_validated(sandbox: DockerSandbox, repo: Path) -> None:
    (repo / "tests" / "test_env.py").write_text(
        "import os\n\n\ndef test_env():\n"
        "    assert os.environ['DJANGO_SETTINGS_MODULE'] == 'site.settings'\n"
    )
    env_mutant = {
        **mutant("m", "return i", "return None"),
        "tests": ["tests/test_env.py::test_env"],
    }
    job = MutationJob(
        image=sandbox.image, mutants=[env_mutant], timeout_seconds=60,
        env={"DJANGO_SETTINGS_MODULE": "site.settings"},
    )  # fmt: skip
    result = sandbox.run_mutants(repo, job)
    assert result.results_json is not None
    # The environment reached the tests: the unrelated test passes, so the mutant survives.
    assert json.loads(result.results_json)["results"][0]["status"] == "survived"

    with pytest.raises(ValueError, match="Invalid sandbox environment variable"):
        sandbox.run_mutants(
            repo, MutationJob(image=sandbox.image, mutants=[], timeout_seconds=5, env={"bad": "x"})
        )
    with pytest.raises(ValueError, match="Invalid test file path"):
        sandbox.run_mutants(
            repo,
            MutationJob(image=sandbox.image, mutants=[], timeout_seconds=5, files={"../x.py": ""}),
        )


def test_missing_image(sandbox: DockerSandbox, repo: Path) -> None:
    with pytest.raises(SandboxUnavailableError):
        sandbox.run_mutants(
            repo, MutationJob(image="testmind-missing:none", mutants=[], timeout_seconds=5)
        )

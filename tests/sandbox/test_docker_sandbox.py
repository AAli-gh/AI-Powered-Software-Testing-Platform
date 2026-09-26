import json
from pathlib import Path

import pytest

from testmind.sandbox.docker_sandbox import DockerSandbox, PytestJob
from testmind.sandbox.models import Outcome, SandboxUnavailableError
from tests.unit.analyzers.helpers import make_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path / "repo",
        {"calc/__init__.py": "", "calc/ops.py": "def div(a, b):\n    return a / b\n"},
    )


def outcomes(result) -> dict[str, Outcome]:  # type: ignore[no-untyped-def]
    return {t.name: t.outcome for t in result.tests}


def test_runs_tests_against_the_repository(sandbox: DockerSandbox, repo: Path) -> None:
    tests = {
        "test_ops.py": (
            "import pytest\nfrom calc.ops import div\n\n"
            "def test_ok():\n    assert div(6, 3) == 2\n\n"
            "def test_fails():\n    assert div(1, 0) == 0\n\n"
            "@pytest.mark.xfail(reason='possible bug: zero', strict=True)\n"
            "def test_xfail():\n    assert div(1, 0) == 0\n"
        )
    }
    result = sandbox.run_pytest(repo, tests)
    assert outcomes(result) == {
        "test_ok": Outcome.PASSED,
        "test_fails": Outcome.FAILED,
        "test_xfail": Outcome.XFAILED,
    }
    assert result.exit_code == 1
    assert result.timed_out is False
    failed = next(t for t in result.tests if t.name == "test_fails")
    assert failed.message is not None
    assert "ZeroDivisionError" in failed.message


def test_isolation(sandbox: DockerSandbox, repo: Path) -> None:
    tests = {
        "test_isolation.py": (
            "import os, socket, pytest\nimport calc.ops\n\n"
            "def test_no_network():\n"
            "    with pytest.raises(OSError):\n"
            "        socket.create_connection(('1.1.1.1', 53), timeout=2)\n\n"
            "def test_repository_is_read_only():\n"
            "    with pytest.raises(OSError):\n"
            "        open('/workspace/src/calc/pwned.py', 'w')\n\n"
            "def test_root_filesystem_is_read_only():\n"
            "    with pytest.raises(OSError):\n"
            "        open('/etc/pwned', 'w')\n\n"
            "def test_not_root():\n"
            "    assert os.getuid() == 65534\n\n"
            "def test_no_host_environment():\n"
            "    assert 'TESTMIND_DATABASE_URL' not in os.environ\n"
            "    assert 'ANTHROPIC_API_KEY' not in os.environ\n"
        )
    }
    result = sandbox.run_pytest(repo, tests)
    assert set(outcomes(result).values()) == {Outcome.PASSED}, result.output
    assert not (repo / "calc" / "pwned.py").exists()


def test_timeout_kills_the_container(repo: Path, sandbox: DockerSandbox) -> None:
    fast = DockerSandbox(sandbox._image, timeout_seconds=5)
    tests = {"test_hang.py": "import calc.ops, time\n\ndef test_hang():\n    time.sleep(60)\n"}
    result = fast.run(repo, PytestJob(test_files=tests, coverage=True))
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.duration_seconds < 30
    assert result.coverage_json is None
    assert result.coverage_error == (
        "coverage.py wrote no report (the run timed out or pytest crashed)"
    )


def test_coverage_is_only_measured_on_request(sandbox: DockerSandbox, repo: Path) -> None:
    # A project whose own coverage settings would fail the run and hide its code.
    (repo / "pyproject.toml").write_text(
        '[tool.coverage.run]\nomit = ["calc/*"]\n\n[tool.coverage.report]\nfail_under = 100\n'
    )
    tests = {
        "test_ok.py": "from calc.ops import div\n\ndef test_ok():\n    assert div(4, 2) == 2\n"
    }
    plain = sandbox.run_pytest(repo, tests)
    assert (plain.coverage_json, plain.coverage_error) == (None, None)

    measured = sandbox.run(repo, PytestJob(test_files=tests, coverage=True))
    assert measured.exit_code == 0, measured.output  # fail_under is not applied
    assert measured.coverage_json is not None
    files = json.loads(measured.coverage_json)["files"]
    assert "/workspace/src/calc/ops.py" in files  # omit is not applied
    assert not any(name.startswith("/workspace/tests") for name in files)


def test_collection_error_is_reported(sandbox: DockerSandbox, repo: Path) -> None:
    result = sandbox.run_pytest(
        repo, {"test_bad.py": "import not_installed_pkg\n\ndef test_a(): pass\n"}
    )
    assert result.exit_code != 0
    assert "not_installed_pkg" in result.output + "".join(t.message or "" for t in result.tests)


@pytest.mark.parametrize("path", ["../escape.py", "/abs/test.py", "notes.txt"])
def test_invalid_test_paths_are_rejected(sandbox: DockerSandbox, repo: Path, path: str) -> None:
    with pytest.raises(ValueError, match="Invalid test file path"):
        sandbox.run_pytest(repo, {path: "def test_a(): pass\n"})


def test_unavailable_image(repo: Path) -> None:
    box = DockerSandbox("testmind-sandbox:does-not-exist", timeout_seconds=5)
    assert box.available() is False
    with pytest.raises(SandboxUnavailableError):
        box.run_pytest(repo, {"test_a.py": "def test_a(): pass\n"})


def test_missing_docker_binary(repo: Path) -> None:
    assert (
        DockerSandbox("x", timeout_seconds=5, docker_binary="no-such-docker").available() is False
    )


def test_broken_docker_binary(tmp_path: Path) -> None:
    fake = tmp_path / "docker"
    fake.write_text("this is not an executable format")
    fake.chmod(0o755)
    assert DockerSandbox("x", timeout_seconds=5, docker_binary=str(fake)).available() is False

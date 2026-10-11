"""The command line: a BugsInPy-shaped dataset, a real local repository, and a stand-in for
TestMind's API (whose client is tested on its own)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from benchmarks.bugsinpy import __main__ as cli
from benchmarks.bugsinpy.client import SuiteRun

TARGET = "test_calc.py::test_div"


def write_bug(projects: Path, number: int, fixed: str, patch: str, run_test: str) -> None:
    directory = projects / "calc" / "bugs" / str(number)
    directory.mkdir(parents=True)
    (directory / "bug.info").write_text(
        f'python_version="3.8.3"\nbuggy_commit_id="unused"\nfixed_commit_id="{fixed}"\n'
    )
    (directory / "run_test.sh").write_text(run_test)
    (directory / "bug_patch.txt").write_text(patch)
    (directory / "requirements.txt").write_text("six==1.15.0\n")


class FakeTestMind:
    """Fails the bug's test on the buggy tree only, blaming `div`."""

    runs: ClassVar[list[str]] = []

    def __init__(self, url: str, token: str | None) -> None:
        assert (url, token) == ("http://testmind", "secret-token")

    def run_suite(self, name: str) -> SuiteRun:
        self.runs.append(name)
        buggy = name.endswith("-buggy")
        group = {
            "category": "assertion_failure",
            "tests": [{"nodeid": TARGET}],
            "function": {"path": "calc.py", "qualname": "div"},
            "frames": [{"path": "calc.py", "function": "div", "location": "production"}],
        }
        return SuiteRun(
            name, "succeeded", "failed" if buggy else "passed", None, 1.5,
            totals={"total": 1, "passed": int(not buggy), "failed": int(buggy)},
            notes=["Python 3.8: pinned to 3.8"], python="3.8",
            failed=[TARGET] if buggy else [], groups=[group] if buggy else [],
        )  # fmt: skip


class AllWheels:
    def __init__(self, cache: Path) -> None:
        assert cache.name == "wheels.json"

    def has_wheel(self, name: str, version: str, python: str) -> bool:
        return True


def test_run_then_report(
    tmp_path: Path,
    upstream: tuple[Path, str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, fixed, patch = upstream
    projects = tmp_path / "BugsInPy" / "projects"
    (projects / "calc").mkdir(parents=True)
    (projects / "calc" / "project.info").write_text(f'github_url="{repo.as_uri()}/"\n')
    write_bug(projects, 1, fixed, patch, f"pytest {TARGET}\n")
    write_bug(projects, 2, fixed, patch, "python -m unittest -q tests.test_missing.T.test\n")
    write_bug(projects, 3, "0" * 40, patch, f"pytest {TARGET}\n")  # no such commit
    write_bug(projects, 4, fixed, patch, f"pytest {TARGET}\n")  # beyond --max-per-project
    monkeypatch.setattr(cli, "TestMind", FakeTestMind)
    monkeypatch.setattr(cli, "WheelIndex", AllWheels)
    monkeypatch.setenv("TESTMIND_API_TOKEN", "secret-token")
    results = tmp_path / "results.jsonl"
    workspace = tmp_path / "work"
    args = [
        "run", "--dataset", str(tmp_path / "BugsInPy"), "--workspace", str(workspace),
        "--cache", str(tmp_path / "cache"), "--projects", "calc", "--max-per-project", "3",
        "--results", str(results), "--url", "http://testmind", "--clean",
        "--environment", "recorded",
    ]  # fmt: skip

    assert cli.main(args) == 0
    assert cli.main(args) == 0  # resumed: nothing left to do

    rows = {row["bug"]: row for row in map(json.loads, results.read_text().splitlines())}
    assert {bug: row["status"] for bug, row in rows.items()} == {
        "calc-1": "reproduced",
        "calc-2": "target_not_found",
        "calc-3": "preparation_error",
    }
    assert (rows["calc-1"]["blamed"], rows["calc-1"]["function_hit"]) == ("calc.py::div", True)
    assert rows["calc-1"]["truth_functions"] == ["calc.py::div"]
    assert FakeTestMind.runs == ["calc-1-buggy", "calc-1-fixed"]
    assert not list(workspace.iterdir())  # --clean removed every tree

    capsys.readouterr()
    assert cli.main(["report", "--results", str(results)]) == 0
    printed = capsys.readouterr().out
    assert '"function_at_1": 100.0' in printed
    assert "| calc-1 | 3.8 | reproduced | `calc.py::div` | `calc.py::div` | yes | 1 |" in printed

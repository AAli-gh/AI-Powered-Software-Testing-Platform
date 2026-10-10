"""A real local git repository standing in for a bug's upstream project."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=cwd, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return completed.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str, str]:
    """A repository whose second commit fixes a bug and adds its test."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "calc.py").write_text("def div(a, b):\n    return a // b\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "buggy")
    (repo / "calc.py").write_text("def div(a, b):\n    return a / b\n")
    (repo / "test_calc.py").write_text(
        "from calc import div\n\ndef test_div():\n    assert div(1, 2) == 0.5\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fixed")
    patch = git(repo, "diff", "HEAD~1", "HEAD", "--", "calc.py") + "\n"
    return repo, git(repo, "rev-parse", "HEAD"), patch

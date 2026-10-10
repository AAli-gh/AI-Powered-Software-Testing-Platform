"""Building a bug's two trees from a real git repository (a local one, not GitHub)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.bugsinpy.dataset import Bug
from benchmarks.bugsinpy.prepare import PreparationError, fetch, prepare, recorded_environment


def bug(repo: Path, fixed: str, patch: str) -> Bug:
    return Bug("calc", 1, repo.as_uri(), "unused", fixed, "3.8", ("test_calc.py::test_div",), patch)


def test_both_trees_have_the_fixed_tests_and_only_buggy_has_the_bug(
    tmp_path: Path, upstream: tuple[Path, str, str]
) -> None:
    repo, fixed_commit, patch = upstream
    buggy, fixed = prepare(bug(repo, fixed_commit, patch), tmp_path / "cache", tmp_path / "work")
    assert (buggy.name, fixed.name) == ("calc-1-buggy", "calc-1-fixed")
    assert "a // b" in (buggy / "calc.py").read_text()
    assert "a / b" in (fixed / "calc.py").read_text()
    assert (buggy / "test_calc.py").read_text() == (fixed / "test_calc.py").read_text()
    assert not (buggy / ".git").exists()
    assert (buggy / "calc.py").stat().st_mode & 0o004  # readable by TestMind's user
    # A second preparation reuses the cached commit and rebuilds the trees.
    (buggy / "stale.txt").write_text("x")
    again, _ = prepare(bug(repo, fixed_commit, patch), tmp_path / "cache", tmp_path / "work")
    assert not (again / "stale.txt").exists()


def test_a_patch_that_does_not_apply_is_a_preparation_error(
    tmp_path: Path, upstream: tuple[Path, str, str]
) -> None:
    repo, fixed_commit, patch = upstream
    wrong = patch.replace("a / b", "a * b")
    with pytest.raises(PreparationError, match="patch"):
        prepare(bug(repo, fixed_commit, wrong), tmp_path / "cache", tmp_path / "work")


def test_an_unknown_commit_is_a_preparation_error(
    tmp_path: Path, upstream: tuple[Path, str, str]
) -> None:
    repo, _, patch = upstream
    with pytest.raises(PreparationError, match="git fetch"):
        fetch(bug(repo, "0" * 40, patch), tmp_path / "cache")


def test_the_recorded_environment_is_declared_in_both_trees(
    tmp_path: Path, upstream: tuple[Path, str, str]
) -> None:
    repo, fixed_commit, patch = upstream
    frozen = "# pip freeze\nFlask==1.1.2\n-e git+https://example.com/x@1#egg=x\nidna==2.9\n\n"
    recorded = replace(bug(repo, fixed_commit, patch), python_version="3.6.9", requirements=frozen)
    trees = prepare(recorded, tmp_path / "cache", tmp_path / "work", recorded=True)
    for tree in trees:
        assert (tree / "requirements-bugsinpy.txt").read_text() == "Flask==1.1.2\nidna==2.9\n"
        assert (tree / ".python-version").read_text() == "3.6\n"
    assert recorded_environment(replace(recorded, python_version="", requirements="")) == {}
    plain, _ = prepare(recorded, tmp_path / "cache", tmp_path / "work")
    assert not (plain / ".python-version").exists()

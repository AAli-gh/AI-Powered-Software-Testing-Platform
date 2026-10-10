"""The two trees of a bug: `fixed` (the fixing commit) and `buggy` (the same tree with the
source fix reversed, so the bug's tests are present in both).

Commits are fetched with git into a cache (one repository per project), shallowly and only the
commits needed. The trees are plain directories, as TestMind receives any local project.

With `recorded=True` both trees also get the environment BugsInPy reproduced the bug in, as a
project with a lock file would declare it: `requirements-bugsinpy.txt` (its pinned packages,
without editable installs and VCS URLs, which TestMind never installs) and `.python-version`
(its Python, major.minor).
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - fixed argument lists, never a shell
import tarfile
import tempfile
from pathlib import Path

from benchmarks.bugsinpy.dataset import Bug

GIT_TIMEOUT = 300


class PreparationError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path, stdin: str | None = None) -> str:
    completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
        args, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        check=False,
    )  # fmt: skip
    if completed.returncode != 0:
        raise PreparationError(f"{' '.join(args[:3])}: {completed.stderr.strip()[-500:]}")
    return completed.stdout


def fetch(bug: Bug, cache: Path) -> Path:
    """The project's cached repository, with the bug's fixing commit present."""
    repository = cache / bug.project
    if not (repository / ".git").exists():
        repository.mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "-q"], repository)
        _run(["git", "remote", "add", "origin", bug.repository], repository)
    try:
        _run(["git", "cat-file", "-e", f"{bug.fixed_commit}^{{commit}}"], repository)
    except PreparationError:
        _run(["git", "fetch", "-q", "--depth", "1", "origin", bug.fixed_commit], repository)
    return repository


def export(repository: Path, commit: str, target: Path) -> None:
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "tree.tar"
        _run(["git", "archive", "--format=tar", "-o", str(archive), commit], repository)
        with tarfile.open(archive) as tar:
            tar.extractall(target, filter="data")


RECORDED_REQUIREMENTS = "requirements-bugsinpy.txt"
PINNED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[\w,.-]+\])?==[\w.+!-]+$")


def recorded_environment(bug: Bug) -> dict[str, str]:
    """The files that declare the bug's recorded environment (see the module docstring)."""
    files: dict[str, str] = {}
    pins = [line.strip() for line in bug.requirements.splitlines() if PINNED.match(line.strip())]
    if pins:
        files[RECORDED_REQUIREMENTS] = "\n".join(pins) + "\n"
    version = re.match(r"(\d+\.\d+)", bug.python_version)
    if version:
        files[".python-version"] = version.group(1) + "\n"
    return files


def prepare(bug: Bug, cache: Path, workspace: Path, *, recorded: bool = False) -> tuple[Path, Path]:
    """`workspace/<bug>-fixed` and `workspace/<bug>-buggy`, rebuilt from scratch."""
    repository = fetch(bug, cache)
    fixed = workspace / f"{bug.name}-fixed"
    buggy = workspace / f"{bug.name}-buggy"
    for tree in (fixed, buggy):
        shutil.rmtree(tree, ignore_errors=True)
    export(repository, bug.fixed_commit, fixed)
    shutil.copytree(fixed, buggy, symlinks=True)
    _run(["patch", "-R", "-p1", "--batch", "--no-backup-if-mismatch", "-s"], buggy, bug.patch)
    if recorded:
        for name, content in recorded_environment(bug).items():
            for tree in (fixed, buggy):
                (tree / name).write_text(content)
    for tree in (fixed, buggy):  # TestMind reads the tree as another user
        for path in [tree, *tree.rglob("*")]:
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o444 | (0o111 if path.is_dir() else 0))
    return buggy, fixed

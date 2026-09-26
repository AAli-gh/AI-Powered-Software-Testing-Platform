"""Safe traversal of a repository.

- Symlinks are never followed (a link could point outside the repository) and are not listed.
- Well-known vendored / generated / VCS directories are skipped.
- The root `.gitignore` is honoured (Git's own matching rules, via pathspec.GitIgnoreSpec).
- Traversal stops at `max_files` and never goes deeper than `max_depth`.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import pathspec

from testmind.analyzers.repository.limits import AnalysisLimits

SKIPPED_DIRECTORIES = frozenset(
    {
        ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "__pycache__",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".nox", ".eggs", "build", "dist",
        "site-packages", ".idea", ".vscode", "htmlcov", ".cache",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class WalkedFile:
    path: Path
    relative: str
    size_bytes: int


@dataclass(slots=True)
class WalkResult:
    files: list[WalkedFile] = field(default_factory=list)
    truncated: bool = False


def _load_gitignore(root: Path) -> pathspec.GitIgnoreSpec | None:
    gitignore = root / ".gitignore"
    try:
        if gitignore.is_symlink() or not gitignore.is_file():
            return None
        lines = gitignore.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines)


def walk_repository(root: Path, limits: AnalysisLimits) -> WalkResult:
    result = WalkResult()
    ignore = _load_gitignore(root)
    for walked in _walk(root, root, ignore, depth=0, limits=limits):
        if len(result.files) >= limits.max_files:
            result.truncated = True
            break
        result.files.append(walked)
    result.files.sort(key=lambda f: f.relative)
    return result


def _is_ignored(ignore: pathspec.GitIgnoreSpec | None, relative: str, *, is_dir: bool) -> bool:
    if ignore is None:
        return False
    return ignore.match_file(relative + "/" if is_dir else relative)


def _walk(
    root: Path,
    directory: Path,
    ignore: pathspec.GitIgnoreSpec | None,
    *,
    depth: int,
    limits: AnalysisLimits,
) -> Iterator[WalkedFile]:
    if depth > limits.max_depth:
        return
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError:
        return
    for entry in entries:
        relative = PurePosixPath(Path(entry.path).relative_to(root)).as_posix()
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode):
            continue
        if stat.S_ISDIR(info.st_mode):
            if entry.name in SKIPPED_DIRECTORIES or entry.name.endswith(".egg-info"):
                continue
            if _is_ignored(ignore, relative, is_dir=True):
                continue
            yield from _walk(root, Path(entry.path), ignore, depth=depth + 1, limits=limits)
        elif stat.S_ISREG(info.st_mode):
            if not _is_ignored(ignore, relative, is_dir=False):
                yield WalkedFile(Path(entry.path), relative, info.st_size)


def read_text(file: WalkedFile, limits: AnalysisLimits) -> str | None:
    """Return the file's text, or None if it is too large, binary or unreadable."""
    if file.size_bytes > limits.max_read_bytes:
        return None
    try:
        # O_NOFOLLOW: the file could have been swapped for a symlink after traversal.
        fd = os.open(file.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(limits.max_read_bytes + 1)
    except OSError:
        return None
    if len(data) > limits.max_read_bytes or b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def read_relative(root: Path, relative: str, limits: AnalysisLimits) -> str | None:
    """Read a file identified by its path relative to `root` (symlinks refused)."""
    path = root / relative
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return read_text(WalkedFile(path, relative, info.st_size), limits)

"""Read-only access to a repository's git history, without running git.

Repositories are untrusted, and the git command line can run commands from a repository's own
configuration: `core.fsmonitor`, `diff.external`, textconv drivers, hooks. TestMind therefore
reads git objects with dulwich, a pure-Python implementation, and never starts a git process.
Only objects are read. Nothing in the repository's configuration is executed, and nothing is
written.

Layouts that would redirect reads outside the repository are refused, not followed: a `.git`
file (`gitdir:` of a worktree or submodule), `objects/info/alternates` and `commondir`.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from dulwich import objectspec
from dulwich.diff_tree import tree_changes
from dulwich.errors import NotGitRepository
from dulwich.object_store import iter_tree_contents, tree_lookup_path
from dulwich.objects import Blob, Commit
from dulwich.repo import Repo

from testmind.analyzers.repository.limits import AnalysisLimits

REVISION = re.compile(r"^(?P<base>[A-Za-z0-9._/@-]{1,200})(?:~(?P<back>\d{1,4}))?$")
MAX_ANCESTRY = 1000


class GitError(Exception):
    """The repository or a revision cannot be used."""


class FileStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class CommitInfo:
    sha: str
    ref: str  # as requested, e.g. "main~1"
    subject: str
    author: str
    timestamp: int


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    status: FileStatus


@dataclass(frozen=True, slots=True)
class Snapshot:
    root: Path
    files: int
    skipped: tuple[str, ...]  # "path: reason"


class GitRepository:
    def __init__(self, root: Path, limits: AnalysisLimits | None = None) -> None:
        self._root = root
        self._limits = limits or AnalysisLimits()
        dot_git = root / ".git"
        if dot_git.is_symlink() or not dot_git.is_dir():
            raise GitError(
                "Not a git repository (a .git directory is required; worktrees and submodules "
                "are not supported)"
            )
        for redirect in ("objects/info/alternates", "commondir"):
            if (dot_git / redirect).exists():
                raise GitError(f"Unsupported repository layout: .git/{redirect} is present")
        try:
            self._repo = Repo(str(root))
        except NotGitRepository:
            raise GitError("Not a git repository") from None

    def close(self) -> None:
        self._repo.close()

    # ------------------------------------------------------------------------------ revisions
    def resolve(self, revision: str) -> CommitInfo:
        """`<branch | tag | HEAD | full or abbreviated sha>[~N]` (N first parents back)."""
        match = REVISION.match(revision)
        if match is None or ".." in revision:
            raise GitError(f"Invalid revision '{revision}'")
        try:
            commit = objectspec.parse_commit(self._repo, match["base"].encode())
        except (KeyError, ValueError, TypeError, LookupError):
            raise GitError(f"Unknown revision '{revision}'") from None
        for _ in range(min(int(match["back"] or 0), MAX_ANCESTRY)):
            if not commit.parents:
                raise GitError(f"'{revision}' goes back past the first commit")
            commit = self._commit(commit.parents[0])
        subject = commit.message.decode("utf-8", "replace").strip().splitlines()
        return CommitInfo(
            sha=commit.id.decode(),
            ref=revision,
            subject=subject[0][:200] if subject else "",
            author=commit.author.decode("utf-8", "replace")[:200],
            timestamp=commit.commit_time,
        )

    # ------------------------------------------------------------------------------ contents
    def changes(self, base: str, head: str) -> list[FileChange]:
        """Files that differ between two commits (renames appear as delete + add)."""
        changes: list[FileChange] = []
        for change in tree_changes(
            self._repo.object_store, self._commit(base).tree, self._commit(head).tree
        ):
            old = _path(change.old.path if change.old else None)
            new = _path(change.new.path if change.new else None)
            if new is None:  # tree_changes always has a side: no new path means a deletion
                changes.append(FileChange(old or "", FileStatus.DELETED))
            else:
                changes.append(FileChange(new, FileStatus.MODIFIED if old else FileStatus.ADDED))
        return sorted(changes, key=lambda c: c.path)

    def read(self, sha: str, path: str) -> str | None:
        """Text of a regular file at a commit; None if absent, not a file, or too large."""
        store = self._repo.object_store
        try:
            mode, blob_id = tree_lookup_path(
                store.__getitem__, self._commit(sha).tree, path.encode()
            )
        except KeyError:
            return None
        if not stat.S_ISREG(mode):
            return None
        blob = self._repo[blob_id]
        if not isinstance(blob, Blob) or blob.raw_length() > self._limits.max_read_bytes:
            return None
        return blob.as_raw_string().decode("utf-8", "replace")

    def materialize(self, sha: str, destination: Path) -> Snapshot:
        """Write the commit's regular files into `destination` (which must be empty).

        Symbolic links and submodules are skipped, and so are unsafe paths and files over the
        read limit. Files are written world-readable (0644, directories 0755) because the
        sandbox runs as an unprivileged user.
        """
        written = 0
        skipped: list[str] = []
        for entry in iter_tree_contents(self._repo.object_store, self._commit(sha).tree):
            path = entry.path.decode("utf-8", "replace")
            if not safe_path(path):
                skipped.append(f"{path}: unsafe path")
                continue
            if not stat.S_ISREG(entry.mode):
                kind = "symbolic link" if stat.S_ISLNK(entry.mode) else "submodule"
                skipped.append(f"{path}: {kind}")
                continue
            if written >= self._limits.max_files:
                skipped.append(f"{path}: file limit reached")
                continue
            blob = self._repo[entry.sha]
            if not isinstance(blob, Blob) or blob.raw_length() > self._limits.max_read_bytes:
                skipped.append(f"{path}: too large")
                continue
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.as_raw_string())
            target.chmod(0o644)
            written += 1
        for directory, _, _ in os.walk(destination):
            Path(directory).chmod(0o755)
        return Snapshot(root=destination, files=written, skipped=tuple(skipped))

    def _commit(self, sha: str | bytes) -> Commit:
        commit = self._repo[sha.encode() if isinstance(sha, str) else sha]
        if not isinstance(commit, Commit):
            raise GitError(f"'{sha!r}' is not a commit")
        return commit


def _path(raw: bytes | None) -> str | None:
    return raw.decode("utf-8", "replace") if raw else None


def safe_path(path: str) -> bool:
    """A relative path without `..`, `.`, `.git`, backslashes or NUL bytes."""
    parts = PurePosixPath(path).parts
    return (
        bool(parts)
        and not PurePosixPath(path).is_absolute()
        and not any(p in {"..", ".", ".git"} or "\\" in p or "\0" in p for p in parts)
    )

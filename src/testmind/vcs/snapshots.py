"""Two commits already written to directories (e.g. downloaded from GitHub), used like a git
repository by the regression engine: the same `changes` / `read` / `materialize` contract."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.vcs.git import FileChange, FileStatus, GitError, Snapshot


@dataclass(frozen=True, slots=True)
class SnapshotSource:
    """`snapshots`: commit sha → the directory holding that commit's files."""

    snapshots: dict[str, Snapshot]
    limits: AnalysisLimits = field(default_factory=AnalysisLimits)

    def changes(self, base: str, head: str) -> list[FileChange]:
        before, after = _files(self._root(base)), _files(self._root(head))
        changes = [FileChange(p, FileStatus.DELETED) for p in before.keys() - after.keys()]
        changes += [FileChange(p, FileStatus.ADDED) for p in after.keys() - before.keys()]
        changes += [
            FileChange(p, FileStatus.MODIFIED)
            for p in before.keys() & after.keys()
            if not _same(before[p], after[p])
        ]
        return sorted(changes, key=lambda c: c.path)

    def read(self, sha: str, path: str) -> str | None:
        """Text of a file at a commit; None if absent or too large (as for git)."""
        target = self._root(sha) / path
        if not target.is_file() or target.is_symlink():
            return None
        if target.stat().st_size > self.limits.max_read_bytes:
            return None
        return target.read_bytes().decode("utf-8", "replace")

    def materialize(self, sha: str, destination: Path) -> Snapshot:
        """A copy of the commit's files in `destination` (which must be empty)."""
        snapshot = self.snapshots.get(sha)
        if snapshot is None:
            raise GitError(f"No snapshot of commit {sha[:12]}")
        shutil.copytree(snapshot.root, destination, dirs_exist_ok=True, symlinks=True)
        return Snapshot(root=destination, files=snapshot.files, skipped=snapshot.skipped)

    def _root(self, sha: str) -> Path:
        if sha not in self.snapshots:
            raise GitError(f"No snapshot of commit {sha[:12]}")
        return self.snapshots[sha].root


def _files(root: Path) -> dict[str, Path]:
    found = {}
    for directory, _, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            if path.is_file() and not path.is_symlink():
                found[path.relative_to(root).as_posix()] = path
    return found


def _same(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    return first.read_bytes() == second.read_bytes()

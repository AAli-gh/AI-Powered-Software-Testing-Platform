"""Extract a repository archive (GitHub's commit tarball) with the same rules as git snapshots.

The archive is untrusted. Nothing in it is interpreted: only regular files and directories are
written, below `destination`, world-readable. Skipped, and reported:
- symbolic and hard links, devices and FIFOs;
- unsafe paths (absolute, `..`, `.git`, backslashes, NUL bytes);
- files over the read limit, and anything beyond the file limit.

GitHub puts every entry under one top-level directory (`owner-repo-sha/`), which is
removed. No archive member is ever extracted by `tarfile` itself.
"""

from __future__ import annotations

import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.vcs.git import Snapshot, safe_path

CHUNK = 64 * 1024


class ArchiveError(Exception):
    """The file is not a readable tar archive."""


def extract_archive(
    archive: Path, destination: Path, limits: AnalysisLimits | None = None
) -> Snapshot:
    """Write the archive's regular files into `destination` (which must be empty)."""
    limits = limits or AnalysisLimits()
    written = 0
    skipped: list[str] = []
    try:
        with tarfile.open(archive, mode="r:*") as tar:
            for member in tar:
                path = _without_top_directory(member.name)
                if path is None:
                    continue  # the top-level directory itself, or a pax header
                if not safe_path(path):
                    skipped.append(f"{path}: unsafe path")
                elif member.isdir():
                    continue  # directories are created for the files in them
                elif not member.isreg():
                    skipped.append(f"{path}: {_kind(member)}")
                elif written >= limits.max_files:
                    skipped.append(f"{path}: file limit reached")
                elif member.size > limits.max_read_bytes:
                    skipped.append(f"{path}: too large")
                else:
                    try:
                        _write(tar, member, destination / path)
                    except (FileExistsError, NotADirectoryError):
                        skipped.append(f"{path}: duplicate or conflicting entry")
                        continue
                    written += 1
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ArchiveError(f"The repository archive cannot be read: {exc}") from None
    for directory, _, _ in os.walk(destination):
        Path(directory).chmod(0o755)
    return Snapshot(root=destination, files=written, skipped=tuple(skipped))


def _without_top_directory(name: str) -> str | None:
    if PurePosixPath(name).is_absolute():
        return name  # reported as an unsafe path, never written
    parts = PurePosixPath(name).parts
    if len(parts) < 2 or name.startswith("pax_global_header"):  # noqa: PLR2004 - top/rest
        return None
    return str(PurePosixPath(*parts[1:]))


def _kind(member: tarfile.TarInfo) -> str:
    if member.issym():
        return "symbolic link"
    if member.islnk():
        return "hard link"
    return "not a regular file"


def _write(tar: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    source = tar.extractfile(member)
    if source is None:  # pragma: no cover - isreg() members always have data
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with source, target.open("xb") as out:  # "x": never follow or overwrite an existing path
        shutil.copyfileobj(source, out, CHUNK)
    target.chmod(0o644)

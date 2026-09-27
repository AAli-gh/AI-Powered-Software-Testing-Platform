"""Resolve a project's source directory safely.

Every local project must live inside `repos_root`. The check is done on the fully resolved path
(symlinks followed), so `../`, absolute paths and symlinks pointing outside the root are all
rejected. What gets stored is the path *relative to the root*, which keeps rows valid no matter
where the root is mounted (host vs. container).
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from testmind.core.errors import InvalidSourceError

GITHUB_PREFIX = "github:"


def resolve_local_source(
    repos_root: Path, location: str, *, imports_root: Path | None = None
) -> Path:
    """Return the absolute, resolved directory for `location`, or raise InvalidSourceError.

    `github:<owner>/<repo>/<sha>` locations (imported repositories) resolve below
    `imports_root` with the same checks; everything else below `repos_root`."""
    if not location or "\x00" in location:
        raise InvalidSourceError("Source location is empty or contains invalid characters")
    if location.startswith(GITHUB_PREFIX):
        if imports_root is None:
            raise InvalidSourceError(
                "Imported GitHub repositories are not available here "
                "(TESTMIND_GITHUB_IMPORTS_ROOT is not set)"
            )
        return resolve_local_source(imports_root, location.removeprefix(GITHUB_PREFIX))

    root = repos_root.resolve()
    candidate = Path(location)
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise InvalidSourceError(f"Source location '{location}' does not exist") from None
    except (OSError, RuntimeError) as exc:  # permission errors, symlink loops
        raise InvalidSourceError(f"Source location '{location}' is not accessible") from exc

    if resolved == root or not resolved.is_relative_to(root):
        raise InvalidSourceError(
            f"Source location '{location}' must be a directory inside the repositories root"
        )
    if not resolved.is_dir():
        raise InvalidSourceError(f"Source location '{location}' is not a directory")
    return resolved


def to_stored_location(repos_root: Path, resolved: Path) -> str:
    """The canonical value persisted in `projects.source_location`."""
    return PurePosixPath(resolved.relative_to(repos_root.resolve())).as_posix()

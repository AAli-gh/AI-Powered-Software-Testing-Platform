"""Where things live inside the sandbox container, and how to map them back.

The repository is mounted at /workspace/src and generated test modules at /workspace/tests.
TestMind reports generated modules under their suggested location, `tests/<path>`.
"""

from __future__ import annotations

import posixpath

PROJECT_PREFIX = "/workspace/src/"
GENERATED_PREFIX = "/workspace/tests/"
GENERATED_REPORT_PREFIX = "tests/"


def repository_path(path: str, rootdir: str) -> str | None:
    """Repository-relative path of a file under /workspace/src, else None.

    `path` may be absolute or relative to `rootdir` (the directory pytest ran in).
    """
    absolute = posixpath.normpath(posixpath.join(rootdir, path))
    if not absolute.startswith(PROJECT_PREFIX):
        return None
    return absolute.removeprefix(PROJECT_PREFIX)


def report_path(path: str, rootdir: str) -> tuple[str, bool] | None:
    """(path as TestMind reports it, generated?) for a file of the repository or of the
    generated tests; None for anything else (the standard library, site-packages, …)."""
    absolute = posixpath.normpath(posixpath.join(rootdir, path))
    if absolute.startswith(GENERATED_PREFIX):
        return GENERATED_REPORT_PREFIX + absolute.removeprefix(GENERATED_PREFIX), True
    if absolute.startswith(PROJECT_PREFIX):
        return absolute.removeprefix(PROJECT_PREFIX), False
    return None

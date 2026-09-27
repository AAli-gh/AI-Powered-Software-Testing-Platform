from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Where a project's code comes from."""

    LOCAL = "local"  # a directory inside TESTMIND_REPOS_ROOT
    GITHUB = "github"  # a commit imported from GitHub into TESTMIND_GITHUB_IMPORTS_ROOT


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})
ACTIVE_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING})

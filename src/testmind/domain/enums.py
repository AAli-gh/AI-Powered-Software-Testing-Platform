from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Where a project's code comes from.

    Only local directories are supported in Phase 1. GitHub sources arrive with the GitHub
    integration (Phase 11) together with a migration that extends the column constraint.
    """

    LOCAL = "local"


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

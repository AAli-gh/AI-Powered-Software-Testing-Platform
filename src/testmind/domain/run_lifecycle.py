"""The analysis-run state machine.

    queued ──► running ──► succeeded
      │           └──────► failed
      ├──► cancelled
      └──► failed        (the job could not be dispatched to the queue)

Terminal states never change again. Keeping the rules here — and nowhere else — means the API
and the worker cannot disagree about what is allowed.
"""

from __future__ import annotations

from collections.abc import Mapping

from testmind.core.errors import InvalidStateTransitionError
from testmind.domain.enums import RunStatus

ALLOWED_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: RunStatus, target: RunStatus) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransitionError(
            f"Run cannot move from '{current.value}' to '{target.value}'"
        )

import itertools

import pytest

from testmind.core.errors import InvalidStateTransitionError
from testmind.domain.enums import ACTIVE_STATUSES, TERMINAL_STATUSES, RunStatus
from testmind.domain.run_lifecycle import ALLOWED_TRANSITIONS, can_transition, ensure_transition

EXPECTED_ALLOWED = {
    (RunStatus.QUEUED, RunStatus.RUNNING),
    (RunStatus.QUEUED, RunStatus.CANCELLED),
    (RunStatus.QUEUED, RunStatus.FAILED),
    (RunStatus.RUNNING, RunStatus.SUCCEEDED),
    (RunStatus.RUNNING, RunStatus.FAILED),
}


@pytest.mark.parametrize(("current", "target"), list(itertools.product(RunStatus, RunStatus)))
def test_transition_table_is_exactly_as_specified(current: RunStatus, target: RunStatus) -> None:
    assert can_transition(current, target) is ((current, target) in EXPECTED_ALLOWED)


def test_every_status_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(RunStatus)


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
def test_terminal_states_are_final(status: RunStatus) -> None:
    assert status.is_terminal
    assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_active_and_terminal_partition_all_statuses() -> None:
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert set(RunStatus) == ACTIVE_STATUSES | TERMINAL_STATUSES


def test_ensure_transition_raises_with_readable_message() -> None:
    with pytest.raises(InvalidStateTransitionError, match="from 'succeeded' to 'running'"):
        ensure_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)


def test_ensure_transition_allows_valid_change() -> None:
    ensure_transition(RunStatus.QUEUED, RunStatus.RUNNING)

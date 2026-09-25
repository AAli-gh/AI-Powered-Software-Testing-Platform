import uuid

import pytest

from testmind.core.errors import InvalidStateTransitionError
from testmind.db.models import AnalysisRun
from testmind.domain.enums import RunStatus
from testmind.services.run_service import apply_transition


def new_run(status: RunStatus = RunStatus.QUEUED) -> AnalysisRun:
    return AnalysisRun(project_id=uuid.uuid4(), status=status)


def test_running_sets_started_at_only() -> None:
    run = new_run()
    apply_transition(run, RunStatus.RUNNING)
    assert run.status is RunStatus.RUNNING
    assert run.started_at is not None
    assert run.started_at.tzinfo is not None
    assert run.finished_at is None


@pytest.mark.parametrize("target", [RunStatus.SUCCEEDED, RunStatus.FAILED])
def test_terminal_sets_finished_at(target: RunStatus) -> None:
    run = new_run()
    apply_transition(run, RunStatus.RUNNING)
    apply_transition(run, target)
    assert run.finished_at is not None
    assert run.started_at is not None
    assert run.finished_at >= run.started_at


def test_error_message_is_recorded() -> None:
    run = new_run()
    apply_transition(run, RunStatus.FAILED, error_message="boom")
    assert run.error_message == "boom"


def test_invalid_transition_leaves_run_untouched() -> None:
    run = new_run(RunStatus.SUCCEEDED)
    with pytest.raises(InvalidStateTransitionError):
        apply_transition(run, RunStatus.RUNNING)
    assert run.status is RunStatus.SUCCEEDED
    assert run.started_at is None

import pytest

from vermay.main_agent.lifecycle import (
    InvalidLocalTaskTransitionError,
    accepts_remote_proxy_snapshot,
    lifecycle_event_type_for_status,
    validate_local_task_transition,
)
from vermay.main_agent.models import TaskStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.CREATED, TaskStatus.QUEUED),
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.INPUT_REQUIRED),
        (TaskStatus.INPUT_REQUIRED, TaskStatus.QUEUED),
        (TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED),
        (TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED),
        (TaskStatus.RUNNING, TaskStatus.COMPLETED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
    ],
)
def test_local_task_lifecycle_accepts_supported_transitions(current, target):
    assert validate_local_task_transition(current, target) is True


def test_local_task_lifecycle_treats_equal_status_as_no_change():
    assert validate_local_task_transition(TaskStatus.RUNNING, TaskStatus.RUNNING) is False


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.CREATED, TaskStatus.COMPLETED),
        (TaskStatus.QUEUED, TaskStatus.INPUT_REQUIRED),
        (TaskStatus.INPUT_REQUIRED, TaskStatus.RUNNING),
        (TaskStatus.CANCEL_REQUESTED, TaskStatus.COMPLETED),
        (TaskStatus.COMPLETED, TaskStatus.RUNNING),
        (TaskStatus.CANCELED, TaskStatus.QUEUED),
        (TaskStatus.FAILED, TaskStatus.QUEUED),
    ],
)
def test_local_task_lifecycle_rejects_unsupported_transitions(current, target):
    with pytest.raises(
        InvalidLocalTaskTransitionError,
        match=f"{current.value} -> {target.value}",
    ):
        validate_local_task_transition(current, target)


def test_remote_proxy_snapshot_accepts_refresh_and_forward_terminal_state():
    assert accepts_remote_proxy_snapshot(TaskStatus.RUNNING, TaskStatus.RUNNING) is True
    assert accepts_remote_proxy_snapshot(TaskStatus.RUNNING, TaskStatus.COMPLETED) is True


def test_remote_proxy_snapshot_rejects_backward_and_post_terminal_updates():
    assert accepts_remote_proxy_snapshot(TaskStatus.RUNNING, TaskStatus.QUEUED) is False
    assert accepts_remote_proxy_snapshot(TaskStatus.COMPLETED, TaskStatus.FAILED) is False


@pytest.mark.parametrize(
    ("status", "event_type"),
    [
        (TaskStatus.QUEUED, "task_queued"),
        (TaskStatus.RUNNING, "task_started"),
        (TaskStatus.INPUT_REQUIRED, "task_interrupted"),
        (TaskStatus.AUTH_REQUIRED, "task_interrupted"),
        (TaskStatus.CANCEL_REQUESTED, "task_cancel_requested"),
        (TaskStatus.COMPLETED, "task_completed"),
        (TaskStatus.CANCELED, "task_cancelled"),
        (TaskStatus.FAILED, "task_failed"),
    ],
)
def test_lifecycle_status_maps_to_event_type(status, event_type):
    assert lifecycle_event_type_for_status(status) == event_type


def test_created_status_cannot_be_used_as_transition_event_target():
    with pytest.raises(InvalidLocalTaskTransitionError, match="created"):
        lifecycle_event_type_for_status(TaskStatus.CREATED)

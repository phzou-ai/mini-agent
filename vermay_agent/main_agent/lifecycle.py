from __future__ import annotations

from collections.abc import Mapping

from .models import TaskStatus


class InvalidLocalTaskTransitionError(ValueError):
    """Raised when a locally owned Agent Process would violate its state machine."""


LOCAL_TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }
    ),
    # A restart can leave a cancellation request without an active worker to
    # reach its next safe boundary. That is an explicit retryable failure, not
    # a completed cancellation.
    TaskStatus.CANCEL_REQUESTED: frozenset({TaskStatus.CANCELED, TaskStatus.FAILED}),
    TaskStatus.INPUT_REQUIRED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.AUTH_REQUIRED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


# A remote proxy has no local worker and, today, no supported continuation
# forwarding. The first observed terminal state is therefore final locally.
REMOTE_PROXY_SNAPSHOT_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
            TaskStatus.COMPLETED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.INPUT_REQUIRED: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.CANCELED, TaskStatus.FAILED}
    ),
    TaskStatus.AUTH_REQUIRED: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.CANCELED, TaskStatus.FAILED}
    ),
    TaskStatus.CANCEL_REQUESTED: frozenset({TaskStatus.CANCELED, TaskStatus.FAILED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
    TaskStatus.FAILED: frozenset(),
}


_TRANSITION_EVENT_TYPES: Mapping[TaskStatus, str] = {
    TaskStatus.QUEUED: "task_queued",
    TaskStatus.RUNNING: "task_started",
    TaskStatus.INPUT_REQUIRED: "task_interrupted",
    TaskStatus.AUTH_REQUIRED: "task_interrupted",
    TaskStatus.CANCEL_REQUESTED: "task_cancel_requested",
    TaskStatus.COMPLETED: "task_completed",
    TaskStatus.CANCELED: "task_cancelled",
    TaskStatus.FAILED: "task_failed",
}


def validate_local_task_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Validate a local-process status change and return whether it is real."""

    if current == target:
        return False
    allowed = LOCAL_TASK_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidLocalTaskTransitionError(
            f"invalid local task transition: {current.value} -> {target.value}"
        )
    return True


def accepts_remote_proxy_snapshot(current: TaskStatus, target: TaskStatus) -> bool:
    """Return whether a child-agent snapshot can advance its local proxy.

    Equal snapshots are allowed for diagnostic refresh and a late final
    artifact. A stale snapshot can never move a proxy backwards or out of its
    first observed terminal state.
    """

    if current == target:
        return True
    return target in REMOTE_PROXY_SNAPSHOT_TRANSITIONS.get(current, frozenset())


def lifecycle_event_type_for_status(status: TaskStatus) -> str:
    try:
        return _TRANSITION_EVENT_TYPES[status]
    except KeyError as exc:
        raise InvalidLocalTaskTransitionError(
            f"local task status cannot be a transition target: {status.value}"
        ) from exc

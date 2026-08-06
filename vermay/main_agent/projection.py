from __future__ import annotations

from enum import Enum
from typing import Any

from vermay.a2a_metadata import thread_metadata

from .models import ArtifactRecord, TaskEventRecord, TaskRecord, TaskStatus, normalize_task_status


class A2ATaskState(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    AUTH_REQUIRED = "auth-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


_STATUS_TO_A2A = {
    TaskStatus.CREATED: A2ATaskState.SUBMITTED,
    TaskStatus.QUEUED: A2ATaskState.SUBMITTED,
    TaskStatus.RUNNING: A2ATaskState.WORKING,
    TaskStatus.CANCEL_REQUESTED: A2ATaskState.WORKING,
    TaskStatus.INPUT_REQUIRED: A2ATaskState.INPUT_REQUIRED,
    TaskStatus.AUTH_REQUIRED: A2ATaskState.AUTH_REQUIRED,
    TaskStatus.COMPLETED: A2ATaskState.COMPLETED,
    TaskStatus.CANCELED: A2ATaskState.CANCELED,
    TaskStatus.FAILED: A2ATaskState.FAILED,
}


def task_status_to_a2a_state(status: object) -> A2ATaskState:
    return _STATUS_TO_A2A[normalize_task_status(status)]


def task_to_a2a_payload(
    task: TaskRecord,
    *,
    input_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "state": task_status_to_a2a_state(task.status).value,
        "timestamp": task.updated_at,
    }
    if input_request is not None:
        status["message"] = _input_request_message(
            task_id=task.task_id,
            context_id=task.context_id,
            input_request=input_request,
            message_id=f"input-{task.task_id}",
        )
    metadata: dict[str, Any] = {
        "localContextId": task.context_id,
        "localTaskId": task.task_id,
        **thread_metadata(task.runtime_thread_id, include_runtime_alias=True),
        "inputMessageId": task.input_message_id,
        "outputMessageId": task.output_message_id,
        "localStatus": task.status.value,
        "localAttempt": task.attempt,
        **({"inputRequest": input_request} if input_request is not None else {}),
    }
    _add_task_failure_metadata(
        metadata,
        error_code=task.error_code,
        error_message=task.error_message,
        retryable=task.error_retryable,
    )
    return {
        "kind": "task",
        "id": task.task_id,
        "contextId": task.context_id,
        "status": status,
        "metadata": metadata,
    }


def task_event_to_a2a_status_update(event: TaskEventRecord, *, task: TaskRecord) -> dict[str, Any] | None:
    if event.status is None:
        return None
    input_request = event.payload.get("input_request")
    status: dict[str, Any] = {
        "state": task_status_to_a2a_state(event.status).value,
        "timestamp": event.created_at,
    }
    if isinstance(input_request, dict):
        status["message"] = _input_request_message(
            task_id=task.task_id,
            context_id=task.context_id,
            input_request=input_request,
            message_id=f"input-event-{event.event_id}",
        )
    metadata: dict[str, Any] = {
        "localEventId": event.event_id,
        "localEventType": event.type,
        "localEventCreatedAt": event.created_at,
        **thread_metadata(task.runtime_thread_id, include_runtime_alias=True),
        "localStatus": event.status.value,
        **({"inputRequest": input_request} if isinstance(input_request, dict) else {}),
    }
    if event.status is TaskStatus.FAILED:
        _add_task_failure_metadata(
            metadata,
            error_code=event.payload.get("error_code") or task.error_code,
            error_message=event.payload.get("error_message") or task.error_message,
            retryable=event.payload.get("retryable", task.error_retryable),
        )

    return {
        "kind": "status-update",
        "taskId": event.task_id,
        "contextId": task.context_id,
        "status": status,
        "final": task_status_to_a2a_state(event.status) in {
            A2ATaskState.COMPLETED,
            A2ATaskState.CANCELED,
            A2ATaskState.FAILED,
        },
        "metadata": metadata,
    }


def _add_task_failure_metadata(
    metadata: dict[str, Any],
    *,
    error_code: object,
    error_message: object,
    retryable: object | None = None,
) -> None:
    """Add the public failure projection without exposing runtime diagnostics."""

    if not isinstance(error_code, str) or not error_code:
        return
    metadata["localErrorCode"] = error_code
    metadata["localErrorMessage"] = (
        error_message
        if isinstance(error_message, str) and error_message
        else "Agent execution failed."
    )
    if isinstance(retryable, bool):
        metadata["localErrorRetryable"] = retryable


def _input_request_message(
    *,
    task_id: str,
    context_id: str,
    input_request: dict[str, Any],
    message_id: str,
) -> dict[str, Any]:
    prompt = str(input_request.get("prompt") or input_request.get("message") or "Additional input is required.")
    return {
        "kind": "message",
        "role": "agent",
        "messageId": message_id,
        "taskId": task_id,
        "contextId": context_id,
        "parts": [{"kind": "text", "text": prompt}],
        "metadata": {"inputRequest": input_request},
    }


def task_event_to_a2a_artifact_update(
    event: TaskEventRecord,
    *,
    task: TaskRecord,
    artifact: ArtifactRecord | None,
) -> dict[str, Any] | None:
    if event.type not in {"task_artifact_created", "task_artifact_updated"}:
        return None
    if artifact is None:
        return None
    return {
        "kind": "artifact-update",
        "taskId": event.task_id,
        "contextId": task.context_id,
        "artifact": {
            "artifactId": str(artifact.metadata.get("kind") or artifact.artifact_id),
            "parts": artifact.parts,
            "metadata": artifact.metadata,
        },
        "append": False,
        "lastChunk": True,
        "metadata": {
            "localEventId": event.event_id,
            "localEventType": event.type,
            "localEventCreatedAt": event.created_at,
            "localArtifactId": artifact.artifact_id,
            **thread_metadata(task.runtime_thread_id, include_runtime_alias=True),
        },
    }

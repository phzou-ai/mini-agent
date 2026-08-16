"""Persistence adapter for accepted local and remote Task execution outcomes.

This module owns no routing, scheduling, or model execution. ``MainAgentCore``
remains the lifecycle owner and invokes this recorder only after an application
command has accepted an execution outcome.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from vermay.errors import error_info_from_exception

from .lifecycle import accepts_remote_proxy_snapshot
from .models import MessageRole, RouteDecisionKind, TaskRecord, TaskStatus, is_terminal_task_status
from .remote_agent import RemoteAgentProtocolError, RemoteAgentTaskSnapshot
from .store import MainAgentStore
from .task_result_projection import (
    continuation_input_request,
    local_process_status,
    task_result_error_payload,
    task_result_execution_metadata,
    task_result_lifecycle_payload,
    task_result_observations,
)
from .task_runner import LocalTaskRunResult


logger = logging.getLogger(__name__)


class TaskOutcomeRecorder:
    """Apply already-accepted execution outcomes to durable Task state."""

    def __init__(self, store: MainAgentStore) -> None:
        self.store = store

    def record_local_result(
        self,
        task_id: str,
        result: LocalTaskRunResult,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        task = self._require_task(task_id)
        if is_terminal_task_status(task.status):
            return task
        if task.status == TaskStatus.CANCEL_REQUESTED:
            return self.record_cancellation(task_id)

        result_status = local_process_status(result)
        input_request = (
            continuation_input_request(result)
            if result_status in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}
            else None
        )
        if result_status in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED} and input_request is None:
            return self.record_failure(
                task_id,
                ValueError("interrupted task result must include a supported input_request.kind"),
            )

        if result_status == TaskStatus.COMPLETED:
            return self._record_completed_result(task_id, result, metadata=metadata)
        if result_status == TaskStatus.RUNNING:
            return task
        if result_status in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
            return self._record_interrupted_result(
                task_id,
                result,
                result_status=result_status,
                input_request=input_request,
            )
        return self._record_failed_result(task_id, result, result_status=result_status)

    def record_failure(self, task_id: str, error: Exception) -> TaskRecord:
        info = error_info_from_exception(error)
        error_code = info.code.value
        error_message = info.public_message
        failure_payload = {
            "error_code": error_code,
            "error_message": error_message,
            "retryable": info.retryable,
            "execution": {
                "stop_reason": "environment_failure",
                "stop_detail": {"error_code": error_code},
                "residual_risks": [
                    {
                        "category": "environment_failure",
                        "summary": error_message,
                        "retryable": info.retryable,
                    }
                ],
            },
        }
        logger.exception("Local task %s failed: %s", task_id, info.message)
        with self.store.transaction():
            task = self._require_task(task_id)
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self.record_cancellation(task_id)
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload=failure_payload,
                error_code=error_code,
                error_message=error_message,
                error_retryable=info.retryable,
            )
            self._close_unresolved_execution(
                task_id,
                error_code=error_code,
                error_message=error_message,
                retryable=info.retryable,
                prepared_reason="task failed before prepared tool execution",
            )
            return failed

    def record_runtime_recovery_failure(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> TaskRecord:
        payload = {
            "error_code": error_code,
            "error_message": error_message,
            "retryable": True,
        }
        logger.warning("Local task %s was not recovered: %s", task_id, error_code)
        with self.store.transaction():
            task = self._require_task(task_id)
            if is_terminal_task_status(task.status):
                return task
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload=payload,
                error_code=error_code,
                error_message=error_message,
                error_retryable=True,
            )
            self._close_unresolved_execution(
                task_id,
                error_code=error_code,
                error_message=error_message,
                retryable=True,
                prepared_reason="runtime recovery ended before prepared tool execution",
            )
            return failed

    def record_cancellation(self, task_id: str) -> TaskRecord:
        with self.store.transaction():
            task = self._require_task(task_id)
            if task.status == TaskStatus.CANCELED or is_terminal_task_status(task.status):
                return task
            task = self.store.transition_local_task(
                task_id,
                TaskStatus.CANCELED,
                payload={
                    "execution": {
                        "stop_reason": "canceled",
                        "stop_detail": {"source": "control_plane_cancellation"},
                    }
                },
            )
            self._close_unresolved_execution(
                task_id,
                error_code="task_canceled_during_tool_execution",
                error_message=(
                    "Task cancellation reached a boundary while a side-effecting tool outcome "
                    "was unresolved."
                ),
                retryable=False,
                prepared_reason="task canceled before prepared tool execution",
            )
            return task

    def record_remote_snapshot(
        self,
        task_id: str,
        snapshot: RemoteAgentTaskSnapshot,
    ) -> TaskRecord:
        """Persist an accepted child-agent snapshot without regressing its proxy."""

        with self.store.transaction():
            task = self._require_task(task_id)
            delegation = self.store.get_delegated_task_by_local_task_id(task_id)
            if delegation is None:
                raise ValueError(f"remote proxy task is missing delegation: {task_id}")
            _validate_remote_proxy_snapshot(delegation, snapshot)

            next_status = remote_task_status(snapshot.status, fallback=task.status)
            if not accepts_remote_proxy_snapshot(task.status, next_status):
                return task

            delegation_metadata = {
                **delegation.metadata,
                "remoteTaskId": snapshot.task_id,
                "remoteContextId": snapshot.context_id,
                "remoteStatus": snapshot.status,
            }
            if snapshot.raw:
                delegation_metadata["lastRemoteSnapshot"] = snapshot.raw
            self.store.update_delegated_task_status(
                delegation.delegation_id,
                status=snapshot.status or next_status.value,
                metadata=delegation_metadata,
            )

            if next_status == TaskStatus.COMPLETED:
                task = self._materialize_remote_final_answer(
                    task,
                    delegation=delegation,
                    snapshot=snapshot,
                )
            if next_status == task.status:
                return task

            updated = self.store.update_task_status(task.task_id, next_status)
            self.store.append_task_event(
                task_id=task.task_id,
                type="remote_task_status_synced",
                status=next_status,
                payload={
                    "remote_agent_id": delegation.remote_agent_id,
                    "remote_task_id": snapshot.task_id,
                    "remote_context_id": snapshot.context_id,
                    "remote_status": snapshot.status,
                },
            )
            return updated

    def _record_completed_result(
        self,
        task_id: str,
        result: LocalTaskRunResult,
        *,
        metadata: dict[str, Any] | None,
    ) -> TaskRecord:
        with self.store.transaction():
            task = self._require_task(task_id)
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self.record_cancellation(task_id)
            observation_artifact_id = self._persist_observations(task, result)
            assistant_message = self.store.append_message(
                message_id=_new_id("msg"),
                context_id=task.context_id,
                role=MessageRole.AGENT,
                parts=result.parts,
                task_id=task_id,
                metadata={
                    **(metadata or {}),
                    "routeKind": RouteDecisionKind.LOCAL_TASK.value,
                },
            )
            artifact = self.store.upsert_artifact(
                artifact_id=f"{task_id}:final_answer",
                task_id=task_id,
                context_id=task.context_id,
                parts=result.artifact_parts or result.parts,
                metadata={
                    "kind": "final_answer",
                    "outputMessageId": assistant_message.message_id,
                    **task_result_execution_metadata(
                        result,
                        observation_artifact_id=observation_artifact_id,
                    ),
                },
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_artifact_created",
                status=None,
                payload={"artifact_id": artifact.artifact_id, "kind": "final_answer"},
            )
            task = self.store.set_task_output_message(task_id, assistant_message.message_id)
            task = self.store.transition_local_task(
                task_id,
                TaskStatus.COMPLETED,
                payload=task_result_lifecycle_payload(
                    result,
                    observation_artifact_id=observation_artifact_id,
                ),
            )
            self.store.clear_pending_continuation(task_id)
            self.store.clear_queued_task_execution(task_id)
            return task

    def _record_interrupted_result(
        self,
        task_id: str,
        result: LocalTaskRunResult,
        *,
        result_status: TaskStatus,
        input_request: dict[str, Any] | None,
    ) -> TaskRecord:
        with self.store.transaction():
            task = self._require_task(task_id)
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self.record_cancellation(task_id)
            observation_artifact_id = self._persist_observations(task, result)
            if input_request is not None:
                self.store.set_pending_continuation(
                    task_id,
                    kind=str(input_request["kind"]),
                    input_request=input_request,
                )
            active = self.store.transition_local_task(
                task_id=task_id,
                target_status=result_status,
                payload=task_result_error_payload(
                    result,
                    observation_artifact_id=observation_artifact_id,
                ),
            )
            if input_request is not None and input_request["kind"] == "user_input_required":
                self.store.append_message(
                    message_id=(
                        f"msg-input-request-{task_id}-{active.lifecycle_revision}"
                    ),
                    context_id=task.context_id,
                    role=MessageRole.AGENT,
                    parts=[
                        {
                            "kind": "text",
                            "text": _input_request_prompt(input_request),
                        }
                    ],
                    task_id=task_id,
                    metadata={
                        "routeKind": RouteDecisionKind.LOCAL_TASK.value,
                        "messageKind": "task_input_request",
                        "inputRequest": input_request,
                        "inputRequestRevision": active.lifecycle_revision,
                    },
                )
            self.store.clear_queued_task_execution(task_id)
            return active

    def _record_failed_result(
        self,
        task_id: str,
        result: LocalTaskRunResult,
        *,
        result_status: TaskStatus,
    ) -> TaskRecord:
        with self.store.transaction():
            task = self._require_task(task_id)
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self.record_cancellation(task_id)
            observation_artifact_id = self._persist_observations(task, result)
            error_code = result.error_code or "task_not_completed"
            error_message = (
                result.error_message
                or f"local task ended with unsupported status: {result_status.value}"
            )
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload={
                    "error_code": error_code,
                    "error_message": error_message,
                    "retryable": result.error_retryable,
                    **task_result_lifecycle_payload(
                        result,
                        observation_artifact_id=observation_artifact_id,
                    ),
                },
                error_code=error_code,
                error_message=error_message,
                error_retryable=result.error_retryable,
            )
            self._close_unresolved_execution(
                task_id,
                error_code="task_ended_before_tool_outcome",
                error_message=(
                    "Task ended before a side-effecting tool outcome was durably recorded."
                ),
                retryable=True,
                prepared_reason="task failed before prepared tool execution",
            )
            return failed

    def _persist_observations(
        self,
        task: TaskRecord,
        result: LocalTaskRunResult,
    ) -> str | None:
        observations = task_result_observations(result)
        if not observations:
            return None
        artifact_id = f"{task.task_id}:tool_observations"
        existing = self.store.get_artifact(artifact_id)
        artifact = self.store.upsert_artifact(
            artifact_id=artifact_id,
            task_id=task.task_id,
            context_id=task.context_id,
            parts=[{"kind": "data", "data": {"observations": observations}}],
            metadata={
                "kind": "tool_observations",
                "observationCount": len(observations),
                **task_result_execution_metadata(result),
            },
        )
        self.store.append_task_event(
            task_id=task.task_id,
            type="task_artifact_updated" if existing is not None else "task_artifact_created",
            status=None,
            payload={"artifact_id": artifact.artifact_id, "kind": "tool_observations"},
        )
        return artifact.artifact_id

    def _materialize_remote_final_answer(
        self,
        task: TaskRecord,
        *,
        delegation: Any,
        snapshot: RemoteAgentTaskSnapshot,
    ) -> TaskRecord:
        parts, remote_artifact_id = _remote_final_artifact(snapshot.artifacts)
        if not parts:
            return task

        artifact_id = f"{task.task_id}:remote_final_answer"
        if self.store.get_artifact(artifact_id) is not None:
            return self.store.get_task(task.task_id) or task

        assistant_message = self.store.append_message(
            message_id=_new_id("msg"),
            context_id=task.context_id,
            role=MessageRole.AGENT,
            parts=parts,
            task_id=task.task_id,
            metadata={
                "routeKind": RouteDecisionKind.REMOTE_AGENT.value,
                "remoteAgentId": delegation.remote_agent_id,
                "remoteTaskId": snapshot.task_id,
                "remoteContextId": snapshot.context_id,
                "remoteArtifactId": remote_artifact_id,
                "delegationId": delegation.delegation_id,
            },
        )
        artifact = self.store.upsert_artifact(
            artifact_id=artifact_id,
            task_id=task.task_id,
            context_id=task.context_id,
            parts=parts,
            metadata={
                "kind": "final_answer",
                "source": "remote_agent",
                "outputMessageId": assistant_message.message_id,
                "remoteAgentId": delegation.remote_agent_id,
                "remoteTaskId": snapshot.task_id,
                "remoteArtifactId": remote_artifact_id,
            },
        )
        self.store.append_task_event(
            task_id=task.task_id,
            type="task_artifact_created",
            status=None,
            payload={
                "artifact_id": artifact.artifact_id,
                "kind": "final_answer",
                "source": "remote_agent",
                "remote_agent_id": delegation.remote_agent_id,
                "remote_task_id": snapshot.task_id,
                "remote_artifact_id": remote_artifact_id,
            },
        )
        return self.store.set_task_output_message(task.task_id, assistant_message.message_id)

    def _close_unresolved_execution(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
        prepared_reason: str,
    ) -> None:
        self.store.mark_running_tool_invocations_uncertain(
            task_id,
            error_code=error_code,
            error_message=error_message,
            retryable=retryable,
        )
        self.store.cancel_prepared_tool_invocations(task_id, reason=prepared_reason)
        self.store.clear_pending_continuation(task_id)
        self.store.clear_queued_task_execution(task_id)

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        return task


def remote_task_status(
    status: str | None,
    *,
    fallback: TaskStatus = TaskStatus.FAILED,
) -> TaskStatus:
    if status in {"submitted", "TASK_STATE_SUBMITTED", "created", "queued"}:
        return TaskStatus.QUEUED
    if status in {"working", "TASK_STATE_WORKING", "running"}:
        return TaskStatus.RUNNING
    if status in {"completed", "TASK_STATE_COMPLETED"}:
        return TaskStatus.COMPLETED
    if status in {"canceled", "cancelled", "TASK_STATE_CANCELED"}:
        return TaskStatus.CANCELED
    if status in {"failed", "rejected", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}:
        return TaskStatus.FAILED
    if status in {"input-required", "TASK_STATE_INPUT_REQUIRED"}:
        return TaskStatus.INPUT_REQUIRED
    if status in {"auth-required", "TASK_STATE_AUTH_REQUIRED"}:
        return TaskStatus.AUTH_REQUIRED
    return fallback


def _validate_remote_proxy_snapshot(delegation: Any, snapshot: RemoteAgentTaskSnapshot) -> None:
    expected_task_id = delegation.remote_task_id
    if not expected_task_id:
        raise RemoteAgentProtocolError(
            f"delegated task has no persisted remote task id: {delegation.delegation_id}"
        )
    if snapshot.task_id != expected_task_id:
        raise RemoteAgentProtocolError(
            "remote agent snapshot task id does not match the delegated task "
            f"({snapshot.task_id!r} != {expected_task_id!r})"
        )
    expected_context_id = delegation.remote_context_id
    if (
        expected_context_id is not None
        and snapshot.context_id is not None
        and snapshot.context_id != expected_context_id
    ):
        raise RemoteAgentProtocolError(
            "remote agent snapshot context id does not match the delegated context "
            f"({snapshot.context_id!r} != {expected_context_id!r})"
        )


def _remote_final_artifact(
    artifacts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw_parts = artifact.get("parts")
        if not isinstance(raw_parts, list):
            continue
        parts = [_normalize_remote_part(part) for part in raw_parts]
        parts = [part for part in parts if part is not None]
        if parts:
            return parts, _optional_str(artifact.get("artifactId") or artifact.get("id"))
    return [], None


def _normalize_remote_part(part: object) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        return None
    if isinstance(part.get("text"), str):
        return {"kind": "text", "text": part["text"]}
    if isinstance(part.get("kind"), str):
        return dict(part)
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _input_request_prompt(input_request: dict[str, Any]) -> str:
    prompt = input_request.get("prompt") or input_request.get("message")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return "Please provide the information required to continue."


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"

"""Pure projections from a local runtime result to main-agent persistence data."""

from __future__ import annotations

from typing import Any

from .models import TaskStatus
from .task_runner import LocalTaskRunResult


def local_process_status(result: LocalTaskRunResult) -> TaskStatus:
    if result.status not in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
        return result.status
    input_request = continuation_input_request(result)
    if input_request is None:
        return result.status
    if input_request["kind"] == "approval_required":
        return TaskStatus.AUTH_REQUIRED
    return TaskStatus.INPUT_REQUIRED


def continuation_input_request(result: LocalTaskRunResult) -> dict[str, Any] | None:
    input_request = result.input_request
    if not isinstance(input_request, dict):
        return None
    kind = input_request.get("kind")
    if kind not in {"approval_required", "user_input_required"}:
        return None
    return dict(input_request)


def task_result_error_payload(
    result: LocalTaskRunResult,
    *,
    observation_artifact_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if result.error_code:
        payload["error_code"] = result.error_code
    if result.error_message:
        payload["error_message"] = result.error_message
    if result.input_request:
        payload["input_request"] = result.input_request
    payload.update(
        task_result_lifecycle_payload(
            result,
            observation_artifact_id=observation_artifact_id,
        )
    )
    return payload


def task_result_lifecycle_payload(
    result: LocalTaskRunResult,
    *,
    observation_artifact_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    execution = task_result_execution(result)
    if execution:
        payload["execution"] = execution
    if observation_artifact_id is not None:
        payload["observation_artifact_id"] = observation_artifact_id
    return payload


def task_result_execution_metadata(
    result: LocalTaskRunResult,
    *,
    observation_artifact_id: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    execution = task_result_execution(result)
    if execution:
        metadata["execution"] = execution
    if observation_artifact_id is not None:
        metadata["observationArtifactId"] = observation_artifact_id
    return metadata


def task_result_execution(result: LocalTaskRunResult) -> dict[str, Any]:
    execution = getattr(result, "execution", None)
    return dict(execution) if isinstance(execution, dict) else {}


def task_result_observations(result: LocalTaskRunResult) -> list[dict[str, Any]]:
    observations = getattr(result, "observations", None)
    if not isinstance(observations, list):
        return []
    return [
        dict(observation)
        for observation in observations
        if isinstance(observation, dict)
    ]

"""Pure SQLite value normalization and record-mapping helpers.

`MainAgentStore` owns queries and transactions. This module deliberately owns
only conversion between SQLite rows and the durable main-agent record models.
"""

from __future__ import annotations

import json
from typing import Any

from .models import (
    ArtifactRecord,
    ContextRecord,
    DelegatedTaskRecord,
    MessageIngressOutcomeKind,
    MessageIngressRecord,
    MessageIngressState,
    MessageRecord,
    MessageRole,
    PendingContinuationRecord,
    QueuedTaskExecutionKind,
    QueuedTaskExecutionRecord,
    RegisteredAgentRecord,
    RouteDecisionKind,
    RouteDecisionRecord,
    TaskEventRecord,
    TaskRecord,
    ToolInvocationApprovalStatus,
    ToolInvocationRecord,
    ToolInvocationStatus,
    normalize_task_status,
)


def _context_from_row(row: Any) -> ContextRecord:
    return ContextRecord(
        context_id=str(row["context_id"]),
        title=row["title"],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _message_from_row(row: Any) -> MessageRecord:
    return MessageRecord(
        message_id=str(row["message_id"]),
        context_id=str(row["context_id"]),
        context_sequence=int(row["context_sequence"]),
        role=MessageRole(str(row["role"])),
        parts=_loads(row["parts"]) or [],
        task_id=row["task_id"],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
    )


def _message_ingress_from_row(row: Any) -> MessageIngressRecord:
    return MessageIngressRecord(
        message_id=str(row["message_id"]),
        context_id=str(row["context_id"]),
        request_fingerprint=str(row["request_fingerprint"]),
        state=MessageIngressState(str(row["state"])),
        route_decision_id=row["route_decision_id"],
        outcome_kind=(
            MessageIngressOutcomeKind(str(row["outcome_kind"]))
            if row["outcome_kind"] is not None
            else None
        ),
        outcome_id=row["outcome_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_http_status=(
            int(row["error_http_status"])
            if row["error_http_status"] is not None
            else None
        ),
        error_retryable=bool(row["error_retryable"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _route_decision_from_row(row: Any) -> RouteDecisionRecord:
    return RouteDecisionRecord(
        decision_id=str(row["decision_id"]),
        context_id=str(row["context_id"]),
        message_id=str(row["message_id"]),
        kind=RouteDecisionKind(str(row["kind"])),
        target_agent_id=row["target_agent_id"],
        reason=str(row["reason"]),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
    )


def _task_from_row(row: Any) -> TaskRecord:
    return TaskRecord(
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        status=normalize_task_status(row["status"]),
        input_message_id=str(row["input_message_id"]),
        input_context_sequence=int(row["input_context_sequence"]),
        output_message_id=row["output_message_id"],
        runtime_thread_id=str(row["runtime_thread_id"]),
        assigned_agent_id=row["assigned_agent_id"],
        retry_of_task_id=row["retry_of_task_id"],
        attempt=int(row["attempt"]),
        model=_loads(row["model"]) if row["model"] is not None else None,
        max_loops=int(row["max_loops"]) if row["max_loops"] is not None else None,
        mcp=_loads(row["mcp"]) if row["mcp"] is not None else None,
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_retryable=bool(row["error_retryable"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _task_event_from_row(row: Any) -> TaskEventRecord:
    return TaskEventRecord(
        event_id=int(row["event_id"]),
        task_id=str(row["task_id"]),
        type=str(row["type"]),
        status=normalize_task_status(row["status"]) if row["status"] is not None else None,
        payload=_loads(row["payload"]) or {},
        created_at=str(row["created_at"]),
    )


def _pending_continuation_from_row(row: Any) -> PendingContinuationRecord:
    return PendingContinuationRecord(
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        input_request=_loads(row["input_request"]) or {},
        created_at=str(row["created_at"]),
    )


def _queued_task_execution_from_row(row: Any) -> QueuedTaskExecutionRecord:
    payload = _loads(row["payload"])
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError(f"queued execution payload must be an object: {row['task_id']}")
    return QueuedTaskExecutionRecord(
        task_id=str(row["task_id"]),
        kind=QueuedTaskExecutionKind(str(row["kind"])),
        runtime_thread_id=str(row["runtime_thread_id"]),
        payload=payload,
        created_at=str(row["created_at"]),
    )


def _artifact_from_row(row: Any) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        parts=_loads(row["parts"]) or [],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _tool_invocation_from_row(row: Any) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=str(row["invocation_id"]),
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        runtime_thread_id=str(row["runtime_thread_id"]),
        loop_index=int(row["loop_index"]),
        tool_call_id=str(row["tool_call_id"]),
        tool_name=str(row["tool_name"]),
        normalized_arguments=_loads(row["normalized_arguments"]) or {},
        arguments_digest=str(row["arguments_digest"]),
        capability=_loads(row["capability"]) or {},
        side_effect_level=str(row["side_effect_level"]),
        idempotency_key=row["idempotency_key"],
        approval_required=bool(row["approval_required"]),
        approval_status=ToolInvocationApprovalStatus(str(row["approval_status"])),
        approval_reason=row["approval_reason"],
        status=ToolInvocationStatus(str(row["status"])),
        result_artifact_id=row["result_artifact_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_retryable=bool(row["error_retryable"]),
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=(
            str(row["completed_at"]) if row["completed_at"] is not None else None
        ),
        updated_at=str(row["updated_at"]),
    )


def _registered_agent_from_row(row: Any) -> RegisteredAgentRecord:
    return RegisteredAgentRecord(
        agent_id=str(row["agent_id"]),
        name=str(row["name"]),
        card_url=str(row["card_url"]),
        card_json=_loads(row["card_json"]) or {},
        enabled=bool(row["enabled"]),
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _delegated_task_from_row(row: Any) -> DelegatedTaskRecord:
    return DelegatedTaskRecord(
        delegation_id=str(row["delegation_id"]),
        context_id=str(row["context_id"]),
        input_message_id=str(row["input_message_id"]),
        route_decision_id=str(row["route_decision_id"]),
        remote_agent_id=str(row["remote_agent_id"]),
        local_task_id=row["local_task_id"],
        remote_task_id=row["remote_task_id"],
        remote_context_id=row["remote_context_id"],
        remote_message_id=row["remote_message_id"],
        result_kind=str(row["result_kind"]),
        status=str(row["status"]),
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))

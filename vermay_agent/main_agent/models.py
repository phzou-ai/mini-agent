from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class RouteDecisionKind(str, Enum):
    LOCAL_MESSAGE = "local_message"
    LOCAL_TASK = "local_task"
    REMOTE_AGENT = "remote_agent"


class MessageIngressState(str, Enum):
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    FAILED = "failed"


class MessageIngressOutcomeKind(str, Enum):
    MESSAGE = "message"
    TASK = "task"
    DELEGATION = "delegation"


class TaskStatus(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    INPUT_REQUIRED = "input_required"
    AUTH_REQUIRED = "auth_required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


class QueuedTaskExecutionKind(str, Enum):
    """Durable command types for a local process waiting on the worker."""

    INITIAL = "initial"
    APPROVAL = "approval"
    USER_INPUT = "user_input"


class ToolInvocationStatus(str, Enum):
    """Durable outcome of one side-effecting tool attempt.

    This deliberately is not a Task or A2A status. It describes only the
    external-effect boundary within one local Agent Process.
    """

    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    CANCELED = "canceled"


class ToolInvocationApprovalStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MainAgentRequest:
    context_id: str | None
    message_id: str | None
    role: MessageRole
    parts: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalMessageResult:
    kind: RouteDecisionKind
    context_id: str
    message_id: str
    input_message_id: str
    route_decision_id: str
    parts: list[dict[str, Any]]


@dataclass(frozen=True)
class LocalMessageDelta:
    kind: RouteDecisionKind
    context_id: str
    message_id: str
    input_message_id: str
    route_decision_id: str
    text: str
    sequence: int


@dataclass(frozen=True)
class LocalTaskResult:
    kind: RouteDecisionKind
    context_id: str
    task_id: str
    input_message_id: str
    route_decision_id: str


@dataclass(frozen=True)
class RemoteAgentResult:
    kind: RouteDecisionKind
    context_id: str
    input_message_id: str
    target_agent_id: str
    route_decision_id: str
    delegation_id: str
    message_id: str | None = None
    task_id: str | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)


MainAgentResult = LocalMessageResult | LocalTaskResult | RemoteAgentResult
MainAgentStreamResult = LocalMessageDelta | MainAgentResult


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.CANCELED,
        TaskStatus.FAILED,
    }
)


@dataclass(frozen=True)
class ContextRecord:
    context_id: str
    title: str | None
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    context_id: str
    role: MessageRole
    parts: list[dict[str, Any]]
    task_id: str | None
    metadata: dict[str, Any]
    created_at: str
    context_sequence: int = 0


@dataclass(frozen=True)
class MessageIngressRecord:
    message_id: str
    context_id: str
    request_fingerprint: str
    state: MessageIngressState
    route_decision_id: str | None
    outcome_kind: MessageIngressOutcomeKind | None
    outcome_id: str | None
    error_code: str | None
    error_message: str | None
    error_http_status: int | None
    error_retryable: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RouteDecisionRecord:
    decision_id: str
    context_id: str
    message_id: str
    kind: RouteDecisionKind
    target_agent_id: str | None
    reason: str
    confidence: float | None
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    context_id: str
    status: TaskStatus
    input_message_id: str
    input_context_sequence: int
    output_message_id: str | None
    runtime_thread_id: str
    assigned_agent_id: str | None
    retry_of_task_id: str | None
    attempt: int
    model: dict[str, Any] | None
    max_loops: int | None
    mcp: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PendingContinuationRecord:
    task_id: str
    kind: str
    input_request: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class QueuedTaskExecutionRecord:
    """One durable, not-yet-claimed execution slice for a local Task."""

    task_id: str
    kind: QueuedTaskExecutionKind
    runtime_thread_id: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class TaskEventRecord:
    event_id: int
    task_id: str
    type: str
    status: TaskStatus | None
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    task_id: str
    context_id: str
    parts: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ToolInvocationRecord:
    invocation_id: str
    task_id: str
    context_id: str
    runtime_thread_id: str
    loop_index: int
    tool_call_id: str
    tool_name: str
    normalized_arguments: dict[str, Any]
    arguments_digest: str
    capability: dict[str, Any]
    side_effect_level: str
    idempotency_key: str | None
    approval_required: bool
    approval_status: ToolInvocationApprovalStatus
    approval_reason: str | None
    status: ToolInvocationStatus
    result_artifact_id: str | None
    error_code: str | None
    error_message: str | None
    error_retryable: bool
    created_at: str
    started_at: str | None
    completed_at: str | None
    updated_at: str


@dataclass(frozen=True)
class RegisteredAgentRecord:
    agent_id: str
    name: str
    card_url: str
    card_json: dict[str, Any]
    enabled: bool
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DelegatedTaskRecord:
    delegation_id: str
    context_id: str
    input_message_id: str
    route_decision_id: str
    remote_agent_id: str
    local_task_id: str | None
    remote_task_id: str | None
    remote_context_id: str | None
    remote_message_id: str | None
    result_kind: str
    status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DeleteContextResult:
    context_id: str
    deleted_messages: int
    deleted_tasks: int
    deleted_task_events: int
    deleted_artifacts: int
    deleted_route_decisions: int


def normalize_task_status(value: object) -> TaskStatus:
    if isinstance(value, TaskStatus):
        return value
    return TaskStatus(str(value))


def is_terminal_task_status(value: object) -> bool:
    return normalize_task_status(value) in TERMINAL_TASK_STATUSES

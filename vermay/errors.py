from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from .mcp.transport import MCPTransportError


class AgentErrorCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    INVALID_SESSION_STATE = "invalid_session_state"
    SESSION_NOT_FOUND = "session_not_found"
    TASK_NOT_FOUND = "task_not_found"
    ARTIFACT_NOT_FOUND = "artifact_not_found"
    MODEL_ERROR = "model_error"
    MODEL_PROTOCOL_ERROR = "model_protocol_error"
    TOOL_ERROR = "tool_error"
    MCP_ERROR = "mcp_error"
    CHECKPOINT_ERROR = "checkpoint_error"
    PERMISSION_ERROR = "permission_error"
    MESSAGE_IN_PROGRESS = "message_in_progress"
    MESSAGE_INGRESS_STALE = "message_ingress_stale"
    MESSAGE_STREAM_ABORTED = "message_stream_aborted"
    TASK_EVENT_PROJECTION_ERROR = "task_event_projection_error"
    RESOURCE_CONFLICT = "resource_conflict"
    RUNTIME_ERROR = "runtime_error"


@dataclass(frozen=True)
class AgentErrorInfo:
    code: AgentErrorCode
    message: str
    http_status: int
    public_message: str
    retryable: bool = False


class AgentError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: AgentErrorCode = AgentErrorCode.RUNTIME_ERROR,
        http_status: int = 500,
        public_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.public_message = public_message or message


class InvalidRequestError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=AgentErrorCode.INVALID_REQUEST, http_status=400)


class InvalidSessionStateError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=AgentErrorCode.INVALID_SESSION_STATE, http_status=409)


class MessageIngressInProgressError(AgentError):
    """A duplicate Message arrived before its original ingress resolved."""

    def __init__(self, message_id: str) -> None:
        super().__init__(
            f"message execution is still in progress: {message_id}",
            code=AgentErrorCode.MESSAGE_IN_PROGRESS,
            http_status=409,
            public_message="Message execution is still in progress.",
        )
        self.retryable = True


class MessageIngressStaleError(AgentError):
    """A previous process accepted a Message but did not finish it."""

    def __init__(self) -> None:
        super().__init__(
            "message execution did not finish before the runtime restarted",
            code=AgentErrorCode.MESSAGE_INGRESS_STALE,
            http_status=503,
            public_message="The previous message did not finish. Send a new message to retry.",
        )
        self.retryable = True


class MessageStreamAbortedError(AgentError):
    """A client disconnected before a direct-message stream reached its result."""

    def __init__(self) -> None:
        super().__init__(
            "direct message stream ended before execution completed",
            code=AgentErrorCode.MESSAGE_STREAM_ABORTED,
            http_status=503,
            public_message="The response stream ended before it completed. Send a new message to retry.",
        )
        self.retryable = True


class ResourceConflictError(AgentError):
    """A destructive management operation conflicts with durable work."""

    def __init__(self, message: str, *, public_message: str) -> None:
        super().__init__(
            message,
            code=AgentErrorCode.RESOURCE_CONFLICT,
            http_status=409,
            public_message=public_message,
        )


class ContextDeletionConflictError(ResourceConflictError):
    def __init__(self, context_id: str, *, reason: str) -> None:
        super().__init__(
            f"cannot delete context {context_id}: {reason}",
            public_message="Context has work that must finish or be canceled before deletion.",
        )


class RegisteredAgentDeletionConflictError(ResourceConflictError):
    def __init__(self, agent_id: str, *, reason: str) -> None:
        super().__init__(
            f"cannot delete registered agent {agent_id}: {reason}",
            public_message="Registered agent has delegation history or active work and cannot be deleted.",
        )


class PersistedMessageIngressError(AgentError):
    """Replays the public error stored for a previously failed Message ingress."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        http_status: int | None,
        retryable: bool,
    ) -> None:
        try:
            error_code = AgentErrorCode(code)
        except ValueError:
            error_code = AgentErrorCode.RUNTIME_ERROR
        super().__init__(
            message,
            code=error_code,
            http_status=http_status or 500,
            public_message=message,
        )
        self.retryable = retryable


class SessionNotFoundError(AgentError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"unknown session: {session_id}",
            code=AgentErrorCode.SESSION_NOT_FOUND,
            http_status=404,
            public_message="session not found",
        )


class TaskNotFoundError(AgentError):
    def __init__(self, task_id: str) -> None:
        super().__init__(
            f"unknown task: {task_id}",
            code=AgentErrorCode.TASK_NOT_FOUND,
            http_status=404,
            public_message="task not found",
        )


class TaskEventProjectionError(AgentError):
    """A durable Task event cannot be represented by the A2A stream."""

    def __init__(self, *, task_id: str, event_id: int, event_type: str) -> None:
        super().__init__(
            f"cannot project task event {event_id} ({event_type}) for task {task_id}",
            code=AgentErrorCode.TASK_EVENT_PROJECTION_ERROR,
            http_status=500,
            public_message="A persisted Task event could not be projected to A2A.",
        )
        self.task_id = task_id
        self.event_id = event_id
        self.event_type = event_type


class ArtifactNotFoundError(AgentError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            f"unknown artifact: {artifact_id}",
            code=AgentErrorCode.ARTIFACT_NOT_FOUND,
            http_status=404,
            public_message="artifact not found",
        )


class ModelError(AgentError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        code: AgentErrorCode = AgentErrorCode.MODEL_ERROR,
        public_message: str = "Model request failed.",
    ) -> None:
        super().__init__(
            message,
            code=code,
            http_status=502,
            public_message=public_message,
        )
        self.retryable = retryable


class ModelProviderError(ModelError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, retryable=retryable)
        self.provider = provider
        self.status_code = status_code


class ModelProtocolError(ModelError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        reason: str = "invalid_model_output",
    ) -> None:
        super().__init__(
            message,
            retryable=False,
            code=AgentErrorCode.MODEL_PROTOCOL_ERROR,
            public_message="The selected model returned an invalid task action.",
        )
        self.provider = provider
        self.reason = reason


def error_info_from_exception(exc: Exception) -> AgentErrorInfo:
    if isinstance(exc, AgentError):
        return AgentErrorInfo(
            code=exc.code,
            message=_safe_message(str(exc), fallback=exc.code.value),
            http_status=exc.http_status,
            public_message=exc.public_message,
            retryable=bool(getattr(exc, "retryable", False)),
        )
    if isinstance(exc, MCPTransportError):
        message = _safe_message(str(exc), fallback="MCP transport error")
        return AgentErrorInfo(
            code=AgentErrorCode.MCP_ERROR,
            message=message,
            http_status=400,
            public_message="MCP server request failed.",
        )
    if isinstance(exc, FileNotFoundError):
        message = _safe_message(str(exc), fallback="file not found")
        return AgentErrorInfo(
            code=AgentErrorCode.INVALID_REQUEST,
            message=message,
            http_status=400,
            public_message=message,
        )
    if isinstance(exc, json.JSONDecodeError):
        message = _safe_message(str(exc), fallback="invalid JSON")
        return AgentErrorInfo(
            code=AgentErrorCode.INVALID_REQUEST,
            message=message,
            http_status=400,
            public_message=message,
        )
    if isinstance(exc, ValueError):
        message = _safe_message(str(exc), fallback="invalid request")
        return AgentErrorInfo(
            code=AgentErrorCode.INVALID_REQUEST,
            message=message,
            http_status=400,
            public_message=message,
        )
    if isinstance(exc, KeyError):
        message = _safe_message(str(exc), fallback="session not found").strip("'\"")
        return AgentErrorInfo(
            code=AgentErrorCode.SESSION_NOT_FOUND,
            message=message,
            http_status=404,
            public_message="session not found",
        )

    message = _safe_message(str(exc), fallback=exc.__class__.__name__)
    return AgentErrorInfo(
        code=AgentErrorCode.RUNTIME_ERROR,
        message=message,
        http_status=500,
        public_message="Agent execution failed.",
    )


def public_error_payload(error: AgentErrorInfo) -> dict[str, str | bool]:
    return {
        "code": error.code.value,
        "message": error.public_message,
        "retryable": error.retryable,
    }


def _safe_message(value: str, *, fallback: str) -> str:
    message = value.strip()
    return message or fallback

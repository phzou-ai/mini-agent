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
    TOOL_ERROR = "tool_error"
    MCP_ERROR = "mcp_error"
    CHECKPOINT_ERROR = "checkpoint_error"
    PERMISSION_ERROR = "permission_error"
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


class ArtifactNotFoundError(AgentError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            f"unknown artifact: {artifact_id}",
            code=AgentErrorCode.ARTIFACT_NOT_FOUND,
            http_status=404,
            public_message="artifact not found",
        )


class ModelError(AgentError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(
            message,
            code=AgentErrorCode.MODEL_ERROR,
            http_status=502,
            public_message="Model request failed.",
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
    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message, retryable=False)
        self.provider = provider


class ToolError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code=AgentErrorCode.TOOL_ERROR, http_status=500, public_message="tool error")


class CheckpointError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code=AgentErrorCode.CHECKPOINT_ERROR,
            http_status=500,
            public_message="checkpoint error",
        )


class PermissionBoundaryError(AgentError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code=AgentErrorCode.PERMISSION_ERROR,
            http_status=403,
            public_message="permission error",
        )


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

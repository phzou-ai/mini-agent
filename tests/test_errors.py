from __future__ import annotations

import json

from vermay_agent.errors import (
    AgentError,
    AgentErrorCode,
    ArtifactNotFoundError,
    InvalidRequestError,
    InvalidSessionStateError,
    ModelProtocolError,
    ModelProviderError,
    SessionNotFoundError,
    TaskNotFoundError,
    error_info_from_exception,
)
from vermay_agent.mcp.transport import MCPTransportError


def test_error_info_uses_typed_agent_error_metadata():
    error = error_info_from_exception(InvalidSessionStateError("not resumable"))

    assert error.code == AgentErrorCode.INVALID_SESSION_STATE
    assert error.http_status == 409
    assert error.message == "not resumable"
    assert error.public_message == "not resumable"


def test_error_info_maps_session_not_found_to_safe_public_message():
    error = error_info_from_exception(SessionNotFoundError("session-1"))

    assert error.code == AgentErrorCode.SESSION_NOT_FOUND
    assert error.http_status == 404
    assert error.message == "unknown session: session-1"
    assert error.public_message == "session not found"


def test_error_info_maps_task_not_found_to_safe_public_message():
    error = error_info_from_exception(TaskNotFoundError("task-1"))

    assert error.code == AgentErrorCode.TASK_NOT_FOUND
    assert error.http_status == 404
    assert error.message == "unknown task: task-1"
    assert error.public_message == "task not found"


def test_error_info_maps_artifact_not_found_to_safe_public_message():
    error = error_info_from_exception(ArtifactNotFoundError("task-1:artifact-1"))

    assert error.code == AgentErrorCode.ARTIFACT_NOT_FOUND
    assert error.http_status == 404
    assert error.message == "unknown artifact: task-1:artifact-1"
    assert error.public_message == "artifact not found"


def test_error_info_maps_common_request_errors():
    assert error_info_from_exception(ValueError("bad config")).code == AgentErrorCode.INVALID_REQUEST
    assert error_info_from_exception(FileNotFoundError("missing")).code == AgentErrorCode.INVALID_REQUEST
    assert error_info_from_exception(InvalidRequestError("bad input")).http_status == 400


def test_error_info_maps_mcp_transport_errors():
    error = error_info_from_exception(MCPTransportError("MCP server failed"))

    assert error.code == AgentErrorCode.MCP_ERROR
    assert error.http_status == 400
    assert error.public_message == "MCP server request failed."


def test_error_info_maps_json_decode_errors():
    try:
        json.loads("{")
    except json.JSONDecodeError as exc:
        error = error_info_from_exception(exc)

    assert error.code == AgentErrorCode.INVALID_REQUEST
    assert error.http_status == 400


def test_error_info_masks_generic_runtime_public_message():
    error = error_info_from_exception(RuntimeError("secret detail"))

    assert error.code == AgentErrorCode.RUNTIME_ERROR
    assert error.http_status == 500
    assert error.message == "secret detail"
    assert error.public_message == "Agent execution failed."
    assert error.retryable is False


def test_custom_agent_error_can_mask_public_message():
    error = error_info_from_exception(
        AgentError(
            "internal model detail",
            code=AgentErrorCode.MODEL_ERROR,
            http_status=502,
            public_message="model error",
        )
    )

    assert error.code == AgentErrorCode.MODEL_ERROR
    assert error.http_status == 502
    assert error.message == "internal model detail"
    assert error.public_message == "model error"


def test_model_errors_preserve_type_and_retryability():
    provider_error = ModelProviderError(
        "provider unavailable",
        provider="openai_compatible",
        retryable=True,
        status_code=503,
    )
    protocol_error = ModelProtocolError("invalid response", provider="ollama")

    assert error_info_from_exception(provider_error).code == AgentErrorCode.MODEL_ERROR
    assert error_info_from_exception(provider_error).public_message == "Model request failed."
    assert error_info_from_exception(provider_error).retryable is True
    assert provider_error.retryable is True
    assert provider_error.status_code == 503
    assert error_info_from_exception(protocol_error).code == AgentErrorCode.MODEL_PROTOCOL_ERROR
    assert (
        error_info_from_exception(protocol_error).public_message
        == "The selected model returned an invalid task action."
    )
    assert protocol_error.retryable is False

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from vermay.errors import AgentErrorCode, error_info_from_exception


class RpcRequest:
    def __init__(self, *, payload: dict[str, Any] | None = None, error: JSONResponse | None = None) -> None:
        self.payload = payload
        self.error = error


async def parse_rpc_request(request: Request) -> RpcRequest:
    body = await request.body()
    if not body.strip():
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                None,
                code=-32600,
                message="JSON-RPC request body is required.",
            )
        )
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                None,
                code=-32700,
                message="JSON parse error.",
                local_code="parse_error",
            )
        )
    if isinstance(payload, list):
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                None,
                code=-32600,
                message="JSON-RPC batch requests are not supported yet.",
                local_code="batch_not_supported",
            )
        )
    if not isinstance(payload, dict):
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                None,
                code=-32600,
                message="JSON-RPC request must be an object.",
            )
        )

    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                request_id,
                code=-32600,
                message="JSON-RPC request jsonrpc must be '2.0'.",
            )
        )
    method = payload.get("method")
    if not isinstance(method, str) or not method:
        return RpcRequest(
            error=jsonrpc_protocol_error_response(
                request_id,
                code=-32600,
                message="JSON-RPC request method must be a string.",
            )
        )
    return RpcRequest(payload=payload)


def jsonrpc_error_response(request_id: Any, exc: Exception) -> JSONResponse:
    error_payload = jsonrpc_error_payload(request_id, exc)
    error = error_info_from_exception(exc)
    return JSONResponse(status_code=error.http_status, content=error_payload)


def jsonrpc_success_payload(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("jsonrpc") == "2.0" and ("result" in payload or "error" in payload):
        return {**payload, "id": request_id}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": payload,
    }


def jsonrpc_error_payload(request_id: Any, exc: Exception) -> dict[str, Any]:
    error = error_info_from_exception(exc)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": _jsonrpc_error_code(error.code),
            "message": error.public_message,
            "data": jsonrpc_error_data(error.code.value, retryable=error.retryable),
        },
    }


def jsonrpc_protocol_error_response(
    request_id: Any,
    *,
    code: int,
    message: str,
    local_code: str = "invalid_request",
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
                "data": jsonrpc_error_data(local_code),
            },
        },
    )


def jsonrpc_error_data(local_code: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "localCode": local_code,
        "retryable": retryable,
        "errorInfo": {
            "reason": local_code,
            "domain": "vermay",
            "metadata": {
                "localCode": local_code,
            },
        },
    }


def rpc_params(payload: dict[str, Any]) -> dict[str, Any]:
    params = payload.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValueError("JSON-RPC params must be an object.")
    return params


def rpc_task_id(params: dict[str, Any]) -> str:
    task_id = params.get("id", params.get("taskId"))
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("JSON-RPC params.id must be a non-empty string.")
    return task_id


def rpc_after_event_id(params: dict[str, Any]) -> int:
    after_event_id = params.get("afterEventId", 0)
    if not isinstance(after_event_id, int) or after_event_id < 0:
        raise ValueError("JSON-RPC params.afterEventId must be a non-negative integer.")
    return after_event_id


def is_jsonrpc_request(payload: dict[str, Any]) -> bool:
    return payload.get("jsonrpc") == "2.0" or payload.get("method") is not None


def _jsonrpc_error_code(code: AgentErrorCode) -> int:
    if code == AgentErrorCode.INVALID_REQUEST:
        return -32602
    if code in {AgentErrorCode.SESSION_NOT_FOUND, AgentErrorCode.TASK_NOT_FOUND, AgentErrorCode.ARTIFACT_NOT_FOUND}:
        return -32004
    if code == AgentErrorCode.INVALID_SESSION_STATE:
        return -32009
    if code == AgentErrorCode.PERMISSION_ERROR:
        return -32003
    return -32000

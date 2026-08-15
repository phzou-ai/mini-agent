from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from vermay.a2a_protocol import (
    MESSAGE_SEND_METHOD,
    TASK_CANCEL_METHOD,
    TASK_GET_METHOD,
)

from .models import MainAgentRequest, RegisteredAgentRecord


@dataclass(frozen=True)
class RemoteAgentSendResult:
    kind: str
    context_id: str | None = None
    message_id: str | None = None
    task_id: str | None = None
    status: str | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteAgentTaskSnapshot:
    task_id: str
    context_id: str | None = None
    status: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class RemoteAgentProtocolError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class RemoteAgentClient(Protocol):
    def send_message(
        self,
        *,
        agent: RegisteredAgentRecord,
        request: MainAgentRequest,
        context_id: str,
        message_id: str,
    ) -> RemoteAgentSendResult:
        """Forward a message to a registered child A2A agent."""

    def get_task(self, *, agent: RegisteredAgentRecord, task_id: str) -> RemoteAgentTaskSnapshot:
        """Fetch a remote task snapshot from a registered child A2A agent."""

    def cancel_task(
        self,
        *,
        agent: RegisteredAgentRecord,
        task_id: str,
        reason: str | None = None,
    ) -> RemoteAgentTaskSnapshot:
        """Request remote task cancellation from a registered child A2A agent."""


class DirectA2ARemoteAgentClient:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def send_message(
        self,
        *,
        agent: RegisteredAgentRecord,
        request: MainAgentRequest,
        context_id: str,
        message_id: str,
    ) -> RemoteAgentSendResult:
        payload = {
            "jsonrpc": "2.0",
            "id": f"delegate-{message_id}",
            "method": MESSAGE_SEND_METHOD,
            "params": {
                "message": {
                    "kind": "message",
                    "role": request.role.value,
                    "messageId": message_id,
                    "contextId": context_id,
                    "parts": request.parts,
                },
                "metadata": _forward_metadata(request.metadata, context_id=context_id),
            },
        }
        body, result = self._post_jsonrpc(agent, payload)
        return _remote_result_from_payload(result, raw=body)

    def get_task(self, *, agent: RegisteredAgentRecord, task_id: str) -> RemoteAgentTaskSnapshot:
        payload = {
            "jsonrpc": "2.0",
            "id": f"get-remote-task-{task_id}",
            "method": TASK_GET_METHOD,
            "params": {"id": task_id},
        }
        body, result = self._post_jsonrpc(agent, payload)
        return _remote_task_snapshot_from_payload(result, raw=body)

    def cancel_task(
        self,
        *,
        agent: RegisteredAgentRecord,
        task_id: str,
        reason: str | None = None,
    ) -> RemoteAgentTaskSnapshot:
        payload = {
            "jsonrpc": "2.0",
            "id": f"cancel-remote-task-{task_id}",
            "method": TASK_CANCEL_METHOD,
            "params": {
                "id": task_id,
            },
        }
        if reason:
            payload["params"]["reason"] = reason
        body, result = self._post_jsonrpc(agent, payload)
        return _remote_task_snapshot_from_payload(result, raw=body)

    def _post_jsonrpc(
        self,
        agent: RegisteredAgentRecord,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        http_request = Request(
            _rpc_url(agent),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urlopen(http_request, timeout=self.timeout_seconds) as response:
            body = _read_json_object(response, label="remote agent response")
        return body, _jsonrpc_result(body, expected_id=payload["id"])


def fetch_agent_card(card_url: str, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    with urlopen(
        Request(card_url, headers={"Accept": "application/json"}, method="GET"),
        timeout=timeout_seconds,
    ) as response:
        return _read_json_object(response, label="agent card response")


def _rpc_url(agent: RegisteredAgentRecord) -> str:
    card = agent.card_json
    supported_interfaces = card.get("supportedInterfaces")
    if isinstance(supported_interfaces, list):
        endpoint = _interface_url(supported_interfaces, binding_key="protocolBinding")
        if endpoint is not None:
            return _validated_http_url(endpoint, label="agent card JSON-RPC interface URL")

    preferred_transport = card.get("preferredTransport")
    card_url = card.get("url")
    if isinstance(card_url, str) and (
        preferred_transport is None or _is_jsonrpc_binding(preferred_transport)
    ):
        return _validated_http_url(card_url, label="agent card JSON-RPC URL")

    additional_interfaces = card.get("additionalInterfaces")
    if isinstance(additional_interfaces, list):
        endpoint = _interface_url(additional_interfaces, binding_key="transport")
        if endpoint is not None:
            return _validated_http_url(endpoint, label="agent card JSON-RPC interface URL")

    declares_non_jsonrpc = (
        isinstance(supported_interfaces, list)
        or preferred_transport is not None
        or isinstance(additional_interfaces, list)
    )
    if declares_non_jsonrpc:
        raise ValueError(f"registered agent does not declare a JSON-RPC interface: {agent.agent_id}")

    return _legacy_rpc_url(agent.card_url)


def _legacy_rpc_url(card_url: str) -> str:
    return _validated_http_url(
        _root_url(card_url).rstrip("/") + "/rpc",
        label="registered agent card URL",
    )


def _root_url(card_url: str) -> str:
    parsed = urlsplit(card_url)
    path = parsed.path
    marker = "/.well-known/agent-card.json"
    if path.endswith(marker):
        path = path[: -len(marker)]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _interface_url(interfaces: list[object], *, binding_key: str) -> str | None:
    for interface in interfaces:
        if not isinstance(interface, dict) or not _is_jsonrpc_binding(interface.get(binding_key)):
            continue
        url = interface.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("agent card JSON-RPC interface must define a non-empty URL")
        return url.strip()
    return None


def _is_jsonrpc_binding(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.replace("-", "").replace("_", "").strip().lower() == "jsonrpc"


def _validated_http_url(value: str, *, label: str) -> str:
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    return normalized


def _read_json_object(response, *, label: str) -> dict[str, Any]:
    try:
        body = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteAgentProtocolError(f"{label} is not valid JSON") from exc
    if not isinstance(body, dict):
        raise RemoteAgentProtocolError(f"{label} must be a JSON object")
    return body


def _jsonrpc_result(body: dict[str, Any], *, expected_id: object) -> dict[str, Any]:
    if body.get("jsonrpc") != "2.0":
        raise RemoteAgentProtocolError("remote agent response must use JSON-RPC 2.0")
    if body.get("id") != expected_id:
        raise RemoteAgentProtocolError("remote agent response id does not match request id")

    error = body.get("error")
    if error is not None:
        if not isinstance(error, dict):
            raise RemoteAgentProtocolError("remote agent JSON-RPC error must be an object")
        code = error.get("code")
        message = error.get("message")
        normalized_code = code if isinstance(code, int) and not isinstance(code, bool) else None
        normalized_message = message.strip() if isinstance(message, str) and message.strip() else "Remote agent error"
        suffix = f" ({normalized_code})" if normalized_code is not None else ""
        raise RemoteAgentProtocolError(
            f"{normalized_message}{suffix}",
            code=normalized_code,
            data=error.get("data"),
        )

    result = body.get("result")
    if not isinstance(result, dict):
        raise RemoteAgentProtocolError("remote agent response missing JSON-RPC result")
    return result


def _remote_result_from_payload(result: dict[str, Any], *, raw: dict[str, Any]) -> RemoteAgentSendResult:
    kind = str(result.get("kind") or "")
    if kind == "message":
        return RemoteAgentSendResult(
            kind="message",
            context_id=_optional_str(result.get("contextId")),
            message_id=_optional_str(result.get("messageId")),
            parts=list(result.get("parts") or []),
            raw=raw,
        )
    if kind == "task":
        status = result.get("status") if isinstance(result.get("status"), dict) else {}
        task_id = _required_nonempty_string(result.get("id"), label="remote agent task result id")
        return RemoteAgentSendResult(
            kind="task",
            context_id=_optional_str(result.get("contextId")),
            task_id=task_id,
            status=_optional_str(status.get("state")),
            raw=raw,
        )
    raise ValueError(f"unsupported remote agent result kind: {kind}")


def _remote_task_snapshot_from_payload(
    result: dict[str, Any],
    *,
    raw: dict[str, Any],
) -> RemoteAgentTaskSnapshot:
    task = result.get("task") if isinstance(result.get("task"), dict) else result
    if not isinstance(task, dict):
        raise ValueError("remote agent task response missing task object")
    status = task.get("status") if isinstance(task.get("status"), dict) else {}
    artifacts = task.get("artifacts") if isinstance(task.get("artifacts"), list) else []
    return RemoteAgentTaskSnapshot(
        task_id=_required_nonempty_string(task.get("id"), label="remote agent task snapshot id"),
        context_id=_optional_str(task.get("contextId")),
        status=_optional_str(status.get("state")),
        artifacts=list(artifacts),
        raw=raw,
    )


def _forward_metadata(metadata: dict[str, object], *, context_id: str) -> dict[str, Any]:
    forwarded: dict[str, Any] = {
        "delegatedBy": "vermay-main-agent",
        "sourceContextId": context_id,
    }
    execution_mode = metadata.get("executionMode")
    if isinstance(execution_mode, str) and execution_mode:
        forwarded["executionMode"] = execution_mode
    return forwarded


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_nonempty_string(value: object, *, label: str) -> str:
    normalized = _optional_str(value)
    if normalized is None or not normalized.strip():
        raise RemoteAgentProtocolError(f"{label} must be a non-empty string")
    return normalized.strip()

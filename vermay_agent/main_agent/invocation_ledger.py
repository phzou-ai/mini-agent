from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import ToolMessage

from vermay_agent.langgraph_runtime.invocations import (
    ToolInvocationExecution,
    ToolInvocationRecorder,
    ToolInvocationReference,
)
from vermay_agent.tool_metadata import ToolMetadata, metadata_from_legacy

from .models import ToolInvocationStatus
from .store import MainAgentStore


_SENSITIVE_ARGUMENT_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)


class MainAgentToolInvocationLedger(ToolInvocationRecorder):
    """Durable side-effect boundary for local Agent Process Tasks.

    The LangGraph runtime calls this adapter before and after ``ToolNode``
    executes a non-read-only tool. The adapter does not schedule tasks or make
    routing decisions; it merely ensures that an external effect has a durable
    identity, approval binding, outcome, and result artifact.
    """

    def __init__(self, store: MainAgentStore) -> None:
        self.store = store

    def prepare(
        self,
        *,
        runtime_thread_id: str | None,
        loop_index: int,
        tool_call: dict[str, Any],
        tool_metadata: dict[str, Any] | None,
        approval_required: bool,
    ) -> ToolInvocationReference | None:
        if not runtime_thread_id:
            return None

        task = self.store.get_task_by_runtime_thread_id(runtime_thread_id)
        if task is None or task.assigned_agent_id is not None:
            return None

        metadata = metadata_from_legacy(tool_metadata)
        if metadata.read_only:
            return None

        tool_name = str(tool_call.get("name") or "")
        if not tool_name:
            return None
        normalized_arguments = _normalized_arguments(tool_call.get("args"))
        arguments_digest = _arguments_digest(tool_call.get("args"))
        tool_call_id = str(tool_call.get("id") or _tool_call_fallback_id(loop_index, tool_name, arguments_digest))
        invocation_id = _invocation_id(
            task_id=task.task_id,
            runtime_thread_id=runtime_thread_id,
            loop_index=loop_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest,
        )

        existing = self.store.get_tool_invocation(invocation_id)
        if existing is None:
            prior = self.store.find_latest_tool_invocation_for_effect(
                task_id=task.task_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest,
            )
            if prior is not None and prior.status in {
                ToolInvocationStatus.RUNNING,
                ToolInvocationStatus.SUCCEEDED,
                ToolInvocationStatus.UNCERTAIN,
            }:
                return _blocked_reference(prior)

        record = self.store.create_or_get_tool_invocation(
            invocation_id=invocation_id,
            task_id=task.task_id,
            context_id=task.context_id,
            runtime_thread_id=runtime_thread_id,
            loop_index=loop_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            normalized_arguments=normalized_arguments,
            arguments_digest=arguments_digest,
            capability=_capability_payload(metadata),
            side_effect_level=metadata.side_effect_level.value,
            idempotency_key=None,
            approval_required=approval_required,
        )
        if record.status in {
            ToolInvocationStatus.RUNNING,
            ToolInvocationStatus.SUCCEEDED,
            ToolInvocationStatus.UNCERTAIN,
        }:
            return _blocked_reference(record)
        return ToolInvocationReference(
            invocation_id=record.invocation_id,
            arguments_digest=record.arguments_digest,
            status=record.status.value,
        )

    def begin_execution(self, invocation_id: str) -> ToolInvocationExecution:
        record = self.store.begin_tool_invocation(invocation_id)
        if record.status == ToolInvocationStatus.RUNNING:
            return ToolInvocationExecution(invocation_id=invocation_id, execute=True)
        if record.status == ToolInvocationStatus.PREPARED and record.approval_required:
            return ToolInvocationExecution(
                invocation_id=invocation_id,
                execute=False,
                message="Tool invocation is still waiting for its bound approval.",
            )
        return ToolInvocationExecution(
            invocation_id=invocation_id,
            execute=False,
            message=f"Tool invocation is already {record.status.value}; it will not be replayed automatically.",
        )

    def finish_execution(self, invocation_id: str, *, response: object) -> None:
        failure = _failed_tool_response(response)
        if failure is not None:
            error_code, error_message = failure
            self.mark_execution_uncertain(
                invocation_id,
                error_code=error_code,
                error_message=error_message,
            )
            return

        self.store.complete_tool_invocation_success(
            invocation_id=invocation_id,
            artifact_parts=[{"kind": "text", "text": _tool_response_text(response)}],
            artifact_metadata={
                "kind": "tool_invocation_result",
                "invocationId": invocation_id,
                "toolResultStatus": getattr(response, "status", "success"),
            },
        )

    def mark_execution_uncertain(
        self,
        invocation_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        self.store.mark_tool_invocation_uncertain(
            invocation_id,
            error_code=error_code,
            error_message=error_message,
        )

    def cancel(self, invocation_id: str, *, reason: str) -> None:
        self.store.cancel_tool_invocation(invocation_id, reason=reason)


def _blocked_reference(record) -> ToolInvocationReference:
    return ToolInvocationReference(
        invocation_id=record.invocation_id,
        arguments_digest=record.arguments_digest,
        status=record.status.value,
        execution_blocked=True,
        blocked_reason=(
            f"A matching side-effect invocation is already {record.status.value}; "
            "the runtime will not repeat it automatically."
        ),
    )


def _capability_payload(metadata: ToolMetadata) -> dict[str, Any]:
    return {
        "source": metadata.source.value,
        "category": metadata.category.value,
        "executionScope": metadata.execution_scope.value,
        "readOnly": metadata.read_only,
        "sideEffectLevel": metadata.side_effect_level.value,
        "destructive": metadata.destructive,
        "credentialSensitive": metadata.credential_sensitive,
        # No current built-in tool exposes an external idempotency key. Keep
        # this fact explicit instead of implying replay is safe.
        "idempotencySupported": False,
    }


def _normalized_arguments(value: Any) -> dict[str, Any]:
    arguments = value if isinstance(value, Mapping) else {"value": value}
    return _redact_value(arguments)


def _arguments_digest(value: Any) -> str:
    canonical = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tool_call_fallback_id(loop_index: int, tool_name: str, arguments_digest: str) -> str:
    return f"{loop_index}:{tool_name}:{arguments_digest[:16]}"


def _invocation_id(
    *,
    task_id: str,
    runtime_thread_id: str,
    loop_index: int,
    tool_call_id: str,
    tool_name: str,
    arguments_digest: str,
) -> str:
    identity = "|".join(
        (task_id, runtime_thread_id, str(loop_index), tool_call_id, tool_name, arguments_digest)
    )
    return f"inv-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.casefold() in _SENSITIVE_ARGUMENT_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(item) for item in value]
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)


def _tool_response_text(response: object) -> str:
    if isinstance(response, ToolMessage):
        content = response.content
    else:
        content = response
    if isinstance(content, str):
        return content
    return json.dumps(_json_safe(content), ensure_ascii=False, sort_keys=True)


def _failed_tool_response(response: object) -> tuple[str, str] | None:
    """Detect a structured execution failure even when ToolNode marks success.

    ``ToolNode`` transports normal tool return values as successful messages.
    Capability adapters use ``{\"ok\": false}`` for a completed local call
    whose external effect did not succeed or whose outcome is uncertain. A
    started non-read-only invocation must never be committed as successful in
    that case.
    """

    if isinstance(response, ToolMessage) and getattr(response, "status", "success") == "error":
        return "tool_execution_result_uncertain", _tool_response_text(response)

    content = response.content if isinstance(response, ToolMessage) else response
    structured = _structured_tool_response(content)
    if not isinstance(structured, Mapping) or structured.get("ok") is not False:
        return None

    error = structured.get("error")
    candidates = (
        structured.get("error_code"),
        structured.get("errorCode"),
        structured.get("code"),
        error.get("code") if isinstance(error, Mapping) else None,
    )
    error_code = next(
        (str(value) for value in candidates if isinstance(value, str) and value),
        "tool_execution_result_uncertain",
    )
    details = (
        structured.get("error_message"),
        structured.get("message"),
        structured.get("stderr"),
        error.get("message") if isinstance(error, Mapping) else None,
    )
    error_message = next(
        (str(value) for value in details if isinstance(value, str) and value),
        _tool_response_text(response),
    )
    return error_code, error_message


def _structured_tool_response(content: object) -> Any:
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None

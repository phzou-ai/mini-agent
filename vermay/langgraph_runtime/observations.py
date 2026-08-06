from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage

from vermay.result_summary import observation_summary


MAX_OBSERVATION_DATA_CHARS = 16_000
MAX_OBSERVATION_SUMMARY_CHARS = 2_000


def normalize_tool_observation(
    message: ToolMessage,
    *,
    loop_index: int,
    artifact_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Turn one ToolNode result into a bounded, typed observation fact.

    Error classification reads only structured tool fields and the ToolMessage
    status.  It does not infer retryability or category from arbitrary model or
    tool text.
    """

    content = _content_text(message.content)
    structured_data = _structured_data(message.content)
    status = str(getattr(message, "status", "success") or "success")
    ok = status != "error" and not (
        isinstance(structured_data, dict) and structured_data.get("ok") is False
    )
    error_category, retryable = _error_fields(structured_data, ok=ok)
    return {
        "loop_index": loop_index,
        "tool_call_id": str(message.tool_call_id or ""),
        "tool_name": str(message.name or "tool"),
        "ok": ok,
        "summary": _truncate(observation_summary(structured_data, content), MAX_OBSERVATION_SUMMARY_CHARS),
        "structured_data": _bounded_data(structured_data),
        "error_category": error_category,
        "retryable": retryable,
        "changed_resources": _changed_resources(structured_data),
        "artifact_refs": _artifact_refs(structured_data, artifact_refs),
    }


def observation_artifact_refs_for_tool_message(state: dict[str, Any], message: ToolMessage) -> list[str]:
    """Return known durable result references without coupling the graph to SQLite.

    R1 prepares non-read-only invocations before ToolNode executes them. Their
    result artifact IDs are deterministic, so the graph can cite the eventual
    artifact without reading main-agent storage.
    """

    permission = state.get("permission")
    if not isinstance(permission, dict):
        return []
    decisions = permission.get("decisions")
    if not isinstance(decisions, list):
        return []
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        tool_call = decision.get("tool_call")
        if not isinstance(tool_call, dict):
            continue
        if str(tool_call.get("id") or "") != str(message.tool_call_id or ""):
            continue
        invocation_id = decision.get("invocation_id")
        if isinstance(invocation_id, str) and invocation_id:
            return [f"{invocation_id}:result"]
    return []


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(_json_safe(content), ensure_ascii=False, sort_keys=True)


def _structured_data(content: Any) -> Any:
    if isinstance(content, dict | list):
        return _json_safe(content)
    if not isinstance(content, str):
        return None
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return None
    return _json_safe(decoded) if isinstance(decoded, dict | list) else None


def _error_fields(data: Any, *, ok: bool) -> tuple[str | None, bool]:
    if ok:
        return None, False
    if isinstance(data, dict):
        error = data.get("error")
        candidates = (
            data.get("error_category"),
            data.get("errorCategory"),
            data.get("error_code"),
            data.get("errorCode"),
            data.get("code"),
            error.get("category") if isinstance(error, dict) else None,
            error.get("code") if isinstance(error, dict) else None,
        )
        category = next((str(value) for value in candidates if isinstance(value, str) and value), None)
        retryable = data.get("retryable")
        if not isinstance(retryable, bool) and isinstance(error, dict):
            retryable = error.get("retryable")
        return category or "tool_execution_error", bool(retryable)
    return "tool_execution_error", False


def _changed_resources(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("changed_resources", data.get("changedResources"))
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            normalized.append(_json_safe(item))
        elif isinstance(item, str) and item:
            normalized.append({"id": item})
    return normalized


def _artifact_refs(data: Any, explicit_refs: list[str] | None) -> list[str]:
    values = list(explicit_refs or [])
    if isinstance(data, dict):
        raw = data.get("artifact_refs", data.get("artifactRefs"))
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if isinstance(item, str) and item)
        for key in ("artifact_id", "artifactId"):
            value = data.get(key)
            if isinstance(value, str) and value:
                values.append(value)
    return list(dict.fromkeys(values))


def _bounded_data(data: Any) -> Any:
    if data is None:
        return None
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if len(serialized) <= MAX_OBSERVATION_DATA_CHARS:
        return data
    return {
        "truncated": True,
        "preview": serialized[:MAX_OBSERVATION_DATA_CHARS],
        "original_characters": len(serialized),
    }


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated>"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return str(value)

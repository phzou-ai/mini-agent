from __future__ import annotations

import json
from typing import Any, Literal, cast

from vermay_agent.errors import ModelProtocolError
from vermay_agent.types import ModelResponse, ToolCall


ToolCallingMode = Literal["native", "prompt_json", "none"]

_KNOWN_TOOL_CALLING_MODES = frozenset({"native", "prompt_json", "none"})


def resolve_tool_calling_mode(
    value: object | None,
    *,
    provider: str,
    default: ToolCallingMode,
    supported: frozenset[ToolCallingMode],
) -> ToolCallingMode:
    """Validate one explicit provider tool-calling strategy.

    A provider adapter must never silently retry a request with another
    strategy. The configured mode describes the request shape expected by that
    model endpoint, while the adapter still normalizes its response into the
    project-owned ``ModelResponse`` and ``ToolCall`` values.
    """

    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{provider} option 'tool_calling' must be a string")

    normalized = value.strip().lower()
    if normalized not in _KNOWN_TOOL_CALLING_MODES:
        supported_values = ", ".join(sorted(_KNOWN_TOOL_CALLING_MODES))
        raise ValueError(f"{provider} option 'tool_calling' must be one of: {supported_values}")
    if normalized not in supported:
        supported_values = ", ".join(sorted(supported))
        raise ValueError(f"{provider} does not support tool_calling='{normalized}'; supported: {supported_values}")
    return cast(ToolCallingMode, normalized)


def to_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Translate the project tool schema to the shared native-function shape."""

    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def parse_function_tool_calls(
    raw_tool_calls: object,
    *,
    provider: str,
    provider_label: str,
) -> list[ToolCall]:
    """Normalize OpenAI- and Ollama-style native function calls safely.

    Both currently supported native transports place a list of call objects in
    ``message.tool_calls`` and use ``function.name`` plus
    ``function.arguments``. Arguments may arrive as an object (Ollama) or a
    JSON string (OpenAI-compatible endpoints). No call is executed here.
    """

    if not isinstance(raw_tool_calls, list):
        raise _tool_call_error(
            provider,
            provider_label,
            "message.tool_calls must be an array",
        )

    parsed: list[ToolCall] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            raise _tool_call_error(
                provider,
                provider_label,
                f"tool_calls[{index}] must be an object",
            )

        function = raw_tool_call.get("function")
        if not isinstance(function, dict):
            raise _tool_call_error(
                provider,
                provider_label,
                f"tool_calls[{index}].function must be an object",
            )

        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise _tool_call_error(
                provider,
                provider_label,
                f"tool_calls[{index}].function.name is required",
            )

        arguments = _parse_function_arguments(
            function.get("arguments"),
            provider=provider,
            provider_label=provider_label,
            index=index,
        )
        tool_call_id = raw_tool_call.get("id")
        parsed.append(
            ToolCall(
                name=name,
                arguments=arguments,
                id=tool_call_id if isinstance(tool_call_id, str) else None,
            )
        )
    return parsed


def model_response_from_tool_calls(tool_calls: list[ToolCall]) -> ModelResponse:
    names = ", ".join(tool_call.name for tool_call in tool_calls)
    return ModelResponse(
        content=f"Calling tools: {names}." if len(tool_calls) > 1 else f"Calling tool {names}.",
        tool_calls=tool_calls,
    )


def _parse_function_arguments(
    raw_arguments: object,
    *,
    provider: str,
    provider_label: str,
    index: int,
) -> dict[str, Any]:
    if raw_arguments is None or raw_arguments == "":
        return {}
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError as exc:
        raise _tool_call_error(
            provider,
            provider_label,
            f"tool_calls[{index}].function.arguments must be a JSON object",
        ) from exc
    if not isinstance(arguments, dict):
        raise _tool_call_error(
            provider,
            provider_label,
            f"tool_calls[{index}].function.arguments must be a JSON object",
        )
    return arguments


def _tool_call_error(provider: str, provider_label: str, detail: str) -> ModelProtocolError:
    return ModelProtocolError(
        f"Invalid {provider_label} response: {detail}",
        provider=provider,
        reason="invalid_tool_calls",
    )

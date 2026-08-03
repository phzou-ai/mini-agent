from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.types import Message, ModelResponse, ToolCall

from .json_decision import parse_json_decision


PROVIDER = "openai_compatible"


class OpenAICompatibleModelClient:
    """OpenAI chat-completions compatible HTTP client."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or (os.environ.get(api_key_env) if api_key_env else None)
        self.timeout_seconds = timeout_seconds

    def invoke(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        payload = {
            "model": self.model,
            "messages": [_to_openai_message(message) for message in messages],
            "temperature": 0,
        }
        if tools:
            payload["tools"] = [_to_openai_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._effective_timeout_seconds(timeout_seconds),
            ) as response:
                response_bytes = response.read()
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(
                self._format_http_error(exc),
                provider=PROVIDER,
                status_code=exc.code,
                retryable=_retryable_http_status(exc.code),
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                f"OpenAI-compatible request failed: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                f"OpenAI-compatible request timed out: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc

        try:
            raw = response_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModelProtocolError(
                "Invalid OpenAI-compatible response: response body is not UTF-8",
                provider=PROVIDER,
            ) from exc

        try:
            body = json.loads(raw)
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError) as exc:
            raise _protocol_error(exc, raw) from exc
        if not isinstance(message, dict):
            raise _protocol_error(TypeError("choices[0].message must be an object"), raw)

        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            parsed_tool_calls = _parse_tool_calls(tool_calls)
            if parsed_tool_calls:
                names = ", ".join(tool_call.name for tool_call in parsed_tool_calls)
                return ModelResponse(
                    content=f"Calling tools: {names}." if len(parsed_tool_calls) > 1 else f"Calling tool {names}.",
                    tool_calls=parsed_tool_calls,
                )

        content = message.get("content")
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string when no tool calls are returned"), raw)
        parsed = _parse_json_action(content)
        if parsed is not None:
            return parsed
        return ModelResponse(content=str(content))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _effective_timeout_seconds(self, override: float | None) -> float:
        if override is None:
            return float(self.timeout_seconds)
        if override <= 0:
            raise ModelProviderError(
                "OpenAI-compatible request did not start because its task deadline had elapsed.",
                provider=PROVIDER,
                retryable=True,
            )
        return min(float(self.timeout_seconds), override)

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        detail = f": {body[:1000]}" if body else ""
        return f"OpenAI-compatible request failed: HTTP {exc.code} {exc.reason}{detail}"


def _to_openai_message(message: Message) -> dict:
    if message.role == "tool":
        payload = {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id or message.name or "unknown_tool_call",
        }
        return payload

    payload: dict = {"role": message.role, "content": message.content}
    if message.role == "assistant" and message.tool_calls:
        payload["tool_calls"] = [_to_openai_tool_call(tool_call) for tool_call in message.tool_calls]
        if payload["content"] == "":
            payload["content"] = None
    return payload


def _to_openai_tool_call(tool_call: dict) -> dict:
    arguments = tool_call.get("args") or tool_call.get("arguments") or {}
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": str(tool_call.get("id") or "unknown_tool_call"),
        "type": "function",
        "function": {
            "name": str(tool_call.get("name") or ""),
            "arguments": arguments,
        },
    }


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or "",
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _parse_tool_calls(raw_tool_calls: object) -> list[ToolCall]:
    if not isinstance(raw_tool_calls, list):
        raise ModelProtocolError(
            "Invalid OpenAI-compatible response: message.tool_calls must be an array",
            provider=PROVIDER,
        )

    parsed: list[ToolCall] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            raise ModelProtocolError(
                f"Invalid OpenAI-compatible response: tool_calls[{index}] must be an object",
                provider=PROVIDER,
            )
        function = raw_tool_call.get("function") or {}
        if not isinstance(function, dict):
            raise ModelProtocolError(
                f"Invalid OpenAI-compatible response: tool_calls[{index}].function must be an object",
                provider=PROVIDER,
            )
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise ModelProtocolError(
                f"Invalid OpenAI-compatible response: tool_calls[{index}].function.name is required",
                provider=PROVIDER,
            )
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
        except (TypeError, ValueError) as exc:
            raise ModelProtocolError(
                f"Invalid OpenAI-compatible response: tool_calls[{index}].function.arguments must be a JSON object",
                provider=PROVIDER,
            ) from exc
        if not isinstance(arguments, dict):
            raise ModelProtocolError(
                f"Invalid OpenAI-compatible response: tool_calls[{index}].function.arguments must be a JSON object",
                provider=PROVIDER,
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


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _protocol_error(exc: Exception, raw: str) -> ModelProtocolError:
    return ModelProtocolError(
        f"Invalid OpenAI-compatible response: {exc}; raw={raw[:1000]}",
        provider=PROVIDER,
    )


def _parse_json_action(content: str) -> ModelResponse | None:
    decision = parse_json_decision(content)
    if decision is None:
        return None
    if decision.get("action") == "tool_call":
        name = decision.get("name")
        arguments = decision.get("arguments", {})
        if isinstance(name, str) and isinstance(arguments, dict):
            return ModelResponse(
                content=f"Calling tool {name}.",
                tool_calls=[ToolCall(name=name, arguments=arguments)],
            )
    if decision.get("action") == "final" or "content" in decision:
        content_value = decision.get("content", "")
        if not isinstance(content_value, str):
            content_value = json.dumps(content_value, ensure_ascii=False, indent=2)
        return ModelResponse(content=content_value)
    return None

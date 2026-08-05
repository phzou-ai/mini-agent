from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.types import Message, ModelResponse

from .tool_calling import (
    ToolCallingMode,
    model_response_from_tool_calls,
    parse_function_tool_calls,
    resolve_tool_calling_mode,
    to_function_tool,
)


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
        tool_calling: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or (os.environ.get(api_key_env) if api_key_env else None)
        self.timeout_seconds = timeout_seconds
        self.tool_calling: ToolCallingMode = resolve_tool_calling_mode(
            tool_calling,
            provider=PROVIDER,
            default="native",
            supported=frozenset({"native", "none"}),
        )

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
        if tools and self.tool_calling == "native":
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
            if self.tool_calling != "native" or not tools:
                raise ModelProtocolError(
                    "Invalid OpenAI-compatible response: native tool calls were returned when tools were unavailable.",
                    provider=PROVIDER,
                    reason="unexpected_tool_calls",
                )
            parsed_tool_calls = parse_function_tool_calls(
                tool_calls,
                provider=PROVIDER,
                provider_label="OpenAI-compatible",
            )
            if parsed_tool_calls:
                return model_response_from_tool_calls(parsed_tool_calls)

        content = message.get("content")
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string when no tool calls are returned"), raw)
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
    return to_function_tool(tool)


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _protocol_error(exc: Exception, raw: str) -> ModelProtocolError:
    return ModelProtocolError(
        f"Invalid OpenAI-compatible response: {exc}; raw={raw[:1000]}",
        provider=PROVIDER,
    )

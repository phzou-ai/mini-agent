from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Iterator

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.types import Message, ModelResponse, ToolCall

from .json_decision import parse_json_decision, strip_reasoning_markup
from .tool_calling import (
    ToolCallingMode,
    model_response_from_tool_calls,
    parse_function_tool_calls,
    resolve_tool_calling_mode,
    to_function_tool,
)


PROVIDER = "ollama"


class OllamaModelClient:
    """Ollama chat adapter with explicit native and prompt-JSON tool modes."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        tool_calling: str | None = None,
    ) -> None:
        self.model = model or "deepseek-v4-flash:cloud"
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else 120
        self.tool_calling: ToolCallingMode = resolve_tool_calling_mode(
            tool_calling,
            provider=PROVIDER,
            default="prompt_json",
            supported=frozenset({"native", "prompt_json", "none"}),
        )

    def invoke(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        use_native_tools = self.tool_calling == "native" and bool(tools)
        payload = self._invoke_payload(
            messages=messages,
            tools=tools,
            use_native_tools=use_native_tools,
        )

        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
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
                f"Ollama request failed: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                f"Ollama request timed out: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc

        try:
            raw = response_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ModelProtocolError(
                "Invalid Ollama response: response body is not UTF-8",
                provider=PROVIDER,
            ) from exc

        try:
            body = json.loads(raw)
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise _protocol_error(exc) from exc

        if not isinstance(body, dict):
            raise _protocol_error(TypeError("response body must be an object"))
        provider_error = body.get("error")
        if isinstance(provider_error, str) and provider_error:
            raise ModelProviderError(
                f"Ollama request failed: {provider_error}",
                provider=PROVIDER,
                retryable=True,
            )

        if use_native_tools:
            return self._parse_native_response(body)
        if self.tool_calling != "prompt_json":
            return self._parse_plain_response(body)

        try:
            content = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise _protocol_error(exc) from exc
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string"))
        return self._parse_content(
            content,
            allow_unstructured_final=_has_tool_observation(messages),
        )

    def _invoke_payload(
        self,
        *,
        messages: list[Message],
        tools: list[dict],
        use_native_tools: bool,
    ) -> dict[str, Any]:
        if use_native_tools:
            return {
                "model": self.model,
                "messages": self._to_native_ollama_messages(messages),
                "tools": [to_function_tool(tool) for tool in tools],
                "stream": False,
                "options": {"temperature": 0},
            }

        # ``none`` makes tool use unavailable to this invocation. Native mode
        # also uses a normal direct-answer request when no tools are exposed.
        # Neither path may prompt the model with the legacy action protocol.
        if self.tool_calling != "prompt_json":
            return {
                "model": self.model,
                "messages": self._to_plain_ollama_messages(messages),
                "stream": False,
                "options": {"temperature": 0},
            }

        return {
            "model": self.model,
            "messages": self._to_prompt_json_ollama_messages(messages, tools),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

    def _parse_native_response(self, body: dict[str, Any]) -> ModelResponse:
        message = body.get("message")
        if not isinstance(message, dict):
            raise _protocol_error(TypeError("message must be an object"))

        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls is not None:
            tool_calls = parse_function_tool_calls(
                raw_tool_calls,
                provider=PROVIDER,
                provider_label="Ollama",
            )
            if tool_calls:
                return model_response_from_tool_calls(tool_calls)

        content = message.get("content")
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string when no tool calls are returned"))
        return ModelResponse(content=content)

    def _parse_plain_response(self, body: dict[str, Any]) -> ModelResponse:
        """Parse a response from an invocation where tools were not exposed."""

        message = body.get("message")
        if not isinstance(message, dict):
            raise _protocol_error(TypeError("message must be an object"))

        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls is not None:
            tool_calls = parse_function_tool_calls(
                raw_tool_calls,
                provider=PROVIDER,
                provider_label="Ollama",
            )
            if tool_calls:
                raise ModelProtocolError(
                    "Invalid Ollama response: native tool calls were returned when tools were unavailable.",
                    provider=PROVIDER,
                    reason="unexpected_tool_calls",
                )

        content = message.get("content")
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string when tools are unavailable"))
        return ModelResponse(content=content)

    def stream_text(self, messages: list[Message], tools: list[dict]) -> Iterator[str]:
        if tools:
            yield self.invoke(messages, tools).content
            return

        payload = {
            "model": self.model,
            "messages": self._to_plain_ollama_messages(messages),
            "stream": True,
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        body = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise _protocol_error(exc) from exc
                    if not isinstance(body, dict):
                        raise _protocol_error(TypeError("stream event must be an object"))
                    error = body.get("error")
                    if isinstance(error, str) and error:
                        raise ModelProviderError(
                            f"Ollama request failed: {error}",
                            provider=PROVIDER,
                            retryable=True,
                        )
                    message = body.get("message", {})
                    if not isinstance(message, dict):
                        raise _protocol_error(TypeError("stream message must be an object"))
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise _protocol_error(TypeError("stream message.content must be a string"))
                    if content:
                        yield content
                    if body.get("done") is True:
                        break
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(
                self._format_http_error(exc),
                provider=PROVIDER,
                status_code=exc.code,
                retryable=_retryable_http_status(exc.code),
            ) from exc
        except urllib.error.URLError as exc:
            raise ModelProviderError(
                f"Ollama request failed: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ModelProviderError(
                f"Ollama request timed out: {exc}",
                provider=PROVIDER,
                retryable=True,
            ) from exc

    def _format_http_error(self, exc: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""

        if body:
            try:
                payload = json.loads(body)
                error = payload.get("error")
                if isinstance(error, str):
                    detail = f": {error}"
                else:
                    detail = f": {body[:1000]}"
            except json.JSONDecodeError:
                detail = f": {body[:1000]}"

        return f"Ollama request failed: HTTP {exc.code} {exc.reason}{detail}"

    def _effective_timeout_seconds(self, override: float | None) -> float:
        if override is None:
            return float(self.timeout_seconds)
        if override <= 0:
            raise ModelProviderError(
                "Ollama request did not start because its task deadline had elapsed.",
                provider=PROVIDER,
                retryable=True,
            )
        return min(float(self.timeout_seconds), override)

    def _parse_content(
        self,
        content: str,
        *,
        allow_unstructured_final: bool = False,
    ) -> ModelResponse:
        decision = parse_json_decision(content)
        if decision is None:
            fallback = _post_tool_final_content(content) if allow_unstructured_final else None
            if fallback is not None:
                return ModelResponse(content=fallback)
            raise ModelProtocolError(
                "Invalid Ollama agent action: expected a JSON object with an action field.",
                provider=PROVIDER,
                reason="missing_action",
            )

        if allow_unstructured_final:
            fallback = _legacy_final_content(decision)
            if fallback is not None:
                return ModelResponse(content=fallback)

        return self._parse_decision(decision)

    def _to_ollama_messages(self, messages: list[Message], tools: list[dict]) -> list[dict[str, str]]:
        """Compatibility alias for tests and explicit ``prompt_json`` mode."""

        return self._to_prompt_json_ollama_messages(messages, tools)

    def _to_prompt_json_ollama_messages(self, messages: list[Message], tools: list[dict]) -> list[dict[str, str]]:
        protocol = {
            "role": "system",
            "content": (
                "Return only JSON. Choose one action.\n"
                "Final answer: {\"action\":\"final\",\"content\":\"...\"}\n"
                "Tool call: {\"action\":\"tool_call\",\"name\":\"tool_name\",\"arguments\":{...}}\n"
                "Do not emit reasoning, <think> tags, markdown, or prose outside the JSON object.\n"
                "Never describe a pending tool call in a final action. If a tool is needed, return "
                "action=tool_call; use action=final only after the required tool observations exist.\n"
                "Only call tools listed below. Dangerous tools may require approval.\n"
                "Use request_user_input only for missing tool arguments, never for permission. "
                "Call dangerous tools directly and let the runtime request approval.\n"
                "If a tool message indicates an error or failed execution, either choose a corrected tool call "
                "or return a final answer explaining the failure. Do not repeat the same failing call.\n"
                f"Available tools:\n{json.dumps(tools, ensure_ascii=False, indent=2)}"
            ),
        }

        converted = [protocol]
        for message in messages:
            if message.role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool observation from {message.name}:\n{message.content}\n\n"
                            "This tool has already been executed. Return a final answer unless a different "
                            "tool is strictly required."
                        ),
                    }
                )
                continue

            converted.append({"role": message.role, "content": message.content})
        return converted

    def _to_native_ollama_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert project messages to Ollama's native tool conversation form."""

        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                # Ollama associates results by name and the ordered tool-call
                # list rather than OpenAI's ``tool_call_id``.
                tool_result: dict[str, Any] = {"role": "tool", "content": message.content}
                if message.name:
                    tool_result["tool_name"] = message.name
                converted.append(tool_result)
                continue

            payload: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.role == "assistant" and message.tool_calls:
                payload["tool_calls"] = [
                    _to_ollama_tool_call(tool_call, index=index)
                    for index, tool_call in enumerate(message.tool_calls)
                ]
            converted.append(payload)
        return converted

    def _to_plain_ollama_messages(self, messages: list[Message]) -> list[dict[str, str]]:
        converted = [
            {
                "role": "system",
                "content": "Answer the user directly in plain text. Do not wrap the answer in JSON.",
            }
        ]
        for message in messages:
            if message.role == "tool":
                continue
            converted.append({"role": message.role, "content": message.content})
        return converted

    def _parse_decision(self, decision: dict) -> ModelResponse:
        action = decision.get("action")
        if decision == {}:
            raise ModelProtocolError(
                "Invalid Ollama agent action: action is required.",
                provider=PROVIDER,
                reason="missing_action",
            )

        if action == "tool_call":
            name = decision.get("name")
            arguments = decision.get("arguments", {})
            if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                raise ModelProtocolError(
                    "Invalid Ollama tool_call action: name and object arguments are required.",
                    provider=PROVIDER,
                    reason="invalid_tool_call",
                )
            return ModelResponse(
                content=f"Calling tool {name}.",
                tool_calls=[ToolCall(name=name, arguments=arguments)],
            )

        if action == "final":
            content = decision.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            return ModelResponse(content=content)

        raise ModelProtocolError(
            f"Invalid Ollama agent action: unsupported action {action!r}.",
            provider=PROVIDER,
            reason="unsupported_action",
        )


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _has_tool_observation(messages: list[Message]) -> bool:
    return any(message.role == "tool" for message in messages)


def _legacy_final_content(decision: dict) -> str | None:
    if decision.get("action") is not None:
        return None
    for field in ("content", "answer", "final_answer"):
        value = decision.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _post_tool_final_content(content: str) -> str | None:
    """Accept a plain final answer only after a real tool observation.

    The first Task turn must still use the action protocol so the runtime never
    infers or executes a tool from prose. After a tool result, however, some
    models occasionally return their final answer as ordinary text despite the
    JSON instruction. That text is safe to treat as a final answer because it
    cannot trigger a tool; LangGraph separately rejects text that claims an
    unexecuted registered tool call.
    """

    if _has_unbalanced_think_tags(content):
        return None
    final = strip_reasoning_markup(content).strip()
    if not final or final.startswith("{"):
        return None
    return final


def _has_unbalanced_think_tags(content: str) -> bool:
    return len(re.findall(r"<think(?:\s[^>]*)?>", content, flags=re.IGNORECASE)) != len(
        re.findall(r"</think\s*>", content, flags=re.IGNORECASE)
    )


def _protocol_error(exc: Exception) -> ModelProtocolError:
    return ModelProtocolError(
        f"Invalid Ollama response: {type(exc).__name__}",
        provider=PROVIDER,
        reason="invalid_response_envelope",
    )


def _to_ollama_tool_call(tool_call: dict[str, Any], *, index: int) -> dict[str, Any]:
    arguments = tool_call.get("args", tool_call.get("arguments", {}))
    if not isinstance(arguments, dict):
        arguments = {}
    return {
        "type": "function",
        "function": {
            "index": index,
            "name": str(tool_call.get("name") or ""),
            "arguments": arguments,
        }
    }

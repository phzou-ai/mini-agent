from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Iterator

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.types import Message, ModelResponse, ToolCall

from .json_decision import parse_json_decision, strip_reasoning_markup


PROVIDER = "ollama"


class OllamaModelClient:
    """Ollama chat adapter using a small JSON protocol for tool calls."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.model = model or "deepseek-v4-flash:cloud"
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else 120

    def invoke(
        self,
        messages: list[Message],
        tools: list[dict],
        *,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        ollama_messages = self._to_ollama_messages(messages, tools)
        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }

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

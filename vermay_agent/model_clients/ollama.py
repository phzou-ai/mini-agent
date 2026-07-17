from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.types import Message, ModelResponse, ToolCall

from .json_decision import parse_json_decision


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

    def invoke(self, messages: list[Message], tools: list[dict]) -> ModelResponse:
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
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
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
            content = body["message"]["content"]
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise _protocol_error(exc, raw) from exc
        if not isinstance(content, str):
            raise _protocol_error(TypeError("message.content must be a string"), raw)

        return self._parse_content(content)

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
                        raise _protocol_error(exc, line) from exc
                    if not isinstance(body, dict):
                        raise _protocol_error(TypeError("stream event must be an object"), line)
                    error = body.get("error")
                    if isinstance(error, str) and error:
                        raise ModelProviderError(
                            f"Ollama request failed: {error}",
                            provider=PROVIDER,
                            retryable=True,
                        )
                    message = body.get("message", {})
                    if not isinstance(message, dict):
                        raise _protocol_error(TypeError("stream message must be an object"), line)
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise _protocol_error(TypeError("stream message.content must be a string"), line)
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

    def _parse_content(self, content: str) -> ModelResponse:
        decision = parse_json_decision(content)
        if decision is None:
            return ModelResponse(content=content)

        return self._parse_decision(decision)

    def _to_ollama_messages(self, messages: list[Message], tools: list[dict]) -> list[dict[str, str]]:
        protocol = {
            "role": "system",
            "content": (
                "Return only JSON. Choose one action.\n"
                "Final answer: {\"action\":\"final\",\"content\":\"...\"}\n"
                "Tool call: {\"action\":\"tool_call\",\"name\":\"tool_name\",\"arguments\":{...}}\n"
                "Only call tools listed below. Dangerous tools may require approval.\n"
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
            return ModelResponse(content="Model returned empty JSON instead of an agent action.")

        if action == "tool_call":
            name = decision.get("name")
            arguments = decision.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return ModelResponse(content=f"Invalid tool_call decision: {decision}")
            return ModelResponse(
                content=f"Calling tool {name}.",
                tool_calls=[ToolCall(name=name, arguments=arguments)],
            )

        if action == "final":
            content = decision.get("content")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            return ModelResponse(content=content)

        if "content" in decision:
            content = decision["content"]
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)
            return ModelResponse(content=content)

        if "message" in decision or "status" in decision:
            return ModelResponse(content=json.dumps(decision, ensure_ascii=False))

        return ModelResponse(content=f"Invalid model action: {decision}")


def _retryable_http_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _protocol_error(exc: Exception, raw: str) -> ModelProtocolError:
    return ModelProtocolError(
        f"Invalid Ollama response: {exc}; raw={raw[:1000]}",
        provider=PROVIDER,
    )

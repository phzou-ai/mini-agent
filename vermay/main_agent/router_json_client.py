from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping

from vermay.langgraph_runtime import ModelProviderConfig


def build_router_json_client(config: ModelProviderConfig) -> RouterJsonHttpClient:
    return RouterJsonHttpClient(provider=config.provider, options=config.options)


@dataclass(frozen=True)
class RouterJsonHttpClient:
    provider: str
    options: Mapping[str, object]

    def invoke_json(self, *, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "ollama":
            return self._invoke_ollama(system_prompt=system_prompt, user_prompt=user_prompt)
        if self.provider == "openai_compatible":
            return self._invoke_openai_compatible(system_prompt=system_prompt, user_prompt=user_prompt)
        return f"Unsupported router model provider: {self.provider}"

    def _invoke_ollama(self, *, system_prompt: str, user_prompt: str) -> str:
        base_url = _optional_str(self.options, "base_url") or "http://127.0.0.1:11434"
        payload = {
            "model": _optional_str(self.options, "model") or "deepseek-v4-flash:cloud",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_timeout_seconds(self.options)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return _format_http_error("Ollama", exc)
        except urllib.error.URLError as exc:
            return f"Ollama router request failed: {exc}"
        except json.JSONDecodeError as exc:
            return f"Invalid Ollama router response: {exc.msg}"

        content = body.get("message", {}).get("content")
        return content if isinstance(content, str) else f"Invalid Ollama router response payload: {body}"

    def _invoke_openai_compatible(self, *, system_prompt: str, user_prompt: str) -> str:
        base_url = _required_str(self.options, "base_url", provider="openai_compatible")
        payload = {
            "model": _required_str(self.options, "model", provider="openai_compatible"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
        }
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=_openai_headers(self.options),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_timeout_seconds(self.options)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return _format_http_error("OpenAI-compatible", exc)
        except urllib.error.URLError as exc:
            return f"OpenAI-compatible router request failed: {exc}"
        except json.JSONDecodeError as exc:
            return f"Invalid OpenAI-compatible router response: {exc.msg}"

        try:
            content = body["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError):
            content = None
        return content if isinstance(content, str) else f"Invalid OpenAI-compatible router response payload: {body}"


def _openai_headers(options: Mapping[str, object]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = _optional_str(options, "api_key")
    api_key_env = _optional_str(options, "api_key_env")
    if api_key is None and api_key_env:
        api_key = os.environ.get(api_key_env)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _format_http_error(provider: str, exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    detail = f": {body[:1000]}" if body else ""
    return f"{provider} router request failed: HTTP {exc.code} {exc.reason}{detail}"


def _optional_str(options: Mapping[str, object], key: str) -> str | None:
    value = options.get(key)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _required_str(options: Mapping[str, object], key: str, *, provider: str) -> str:
    value = _optional_str(options, key)
    if not value:
        raise ValueError(f"{provider} router option '{key}' is required")
    return value


def _timeout_seconds(options: Mapping[str, object]) -> int:
    value = options.get("timeout_seconds")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value)
    return 120

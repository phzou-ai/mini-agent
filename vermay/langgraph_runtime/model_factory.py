from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from vermay.model_clients import OllamaModelClient, OpenAICompatibleModelClient

from .model_adapters import OllamaModelAdapter, OpenAICompatibleModelAdapter
from .nodes import GraphModelClient


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str = "ollama"
    options: Mapping[str, object] = field(default_factory=dict)


def build_graph_model_client(config: ModelProviderConfig) -> GraphModelClient:
    if config.provider == "ollama":
        _validate_ollama_options(config.options)
        return OllamaModelAdapter(
            client=OllamaModelClient(
                model=_optional_str(config.options, "model"),
                base_url=_optional_str(config.options, "base_url"),
                timeout_seconds=_optional_int(config.options, "timeout_seconds"),
                tool_calling=_optional_str(config.options, "tool_calling"),
            )
        )
    if config.provider == "openai_compatible":
        _validate_openai_compatible_options(config.options)
        timeout = _optional_int(config.options, "timeout_seconds", provider="openai_compatible") or 120
        return OpenAICompatibleModelAdapter(
            client=OpenAICompatibleModelClient(
                model=_required_str(config.options, "model", provider="openai_compatible"),
                base_url=_required_str(config.options, "base_url", provider="openai_compatible"),
                api_key=_optional_str(config.options, "api_key", provider="openai_compatible"),
                api_key_env=_optional_str(config.options, "api_key_env", provider="openai_compatible"),
                timeout_seconds=timeout,
                tool_calling=_optional_str(config.options, "tool_calling", provider="openai_compatible"),
            )
        )

    raise ValueError(f"unsupported model provider: {config.provider}")


def _optional_str(options: Mapping[str, object], key: str, *, provider: str = "ollama") -> str | None:
    value = options.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{provider} option '{key}' must be a string")
    return value


def _required_str(options: Mapping[str, object], key: str, *, provider: str) -> str:
    value = _optional_str(options, key, provider=provider)
    if value is None or not value:
        raise ValueError(f"{provider} option '{key}' is required")
    return value


def _optional_int(options: Mapping[str, object], key: str, *, provider: str = "ollama") -> int | None:
    value = options.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{provider} option '{key}' must be a positive integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and value.strip().isdecimal():
        normalized = int(value)
    else:
        raise ValueError(f"{provider} option '{key}' must be a positive integer")
    if normalized <= 0:
        raise ValueError(f"{provider} option '{key}' must be a positive integer")
    return normalized


def _validate_ollama_options(options: Mapping[str, object]) -> None:
    allowed = {"model", "base_url", "timeout_seconds", "tool_calling"}
    unknown = sorted(set(options) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unsupported ollama model option(s): {joined}")


def _validate_openai_compatible_options(options: Mapping[str, object]) -> None:
    allowed = {"model", "base_url", "api_key", "api_key_env", "timeout_seconds", "tool_calling"}
    unknown = sorted(set(options) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unsupported openai_compatible model option(s): {joined}")

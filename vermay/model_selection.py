from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from vermay.langgraph_runtime import ModelProviderConfig


@dataclass(frozen=True)
class NamedModelSelection:
    name: str
    config: ModelProviderConfig


def resolve_model_selection(
    *,
    config_path: Path,
    model_name: str | None = None,
) -> ModelProviderConfig:
    return resolve_named_model_selection(config_path=config_path, model_name=model_name).config


def resolve_named_model_selection(
    *,
    config_path: Path,
    model_name: str | None = None,
) -> NamedModelSelection:
    body = _load_models_config(config_path)
    resolved_name = _normalize_model_name(model_name, label="model override") or _primary_model(body)
    return NamedModelSelection(name=resolved_name, config=_model_provider_config(body, resolved_name))


def resolve_router_model_selection(
    *,
    config_path: Path,
    model_name: str | None = None,
) -> ModelProviderConfig:
    return resolve_named_router_model_selection(config_path=config_path, model_name=model_name).config


def resolve_named_router_model_selection(
    *,
    config_path: Path,
    model_name: str | None = None,
) -> NamedModelSelection:
    body = _load_models_config(config_path)
    resolved_name = _resolve_router_model_name(body, model_name=model_name)
    return NamedModelSelection(name=resolved_name, config=_model_provider_config(body, resolved_name))


def _load_models_config(config_path: Path) -> Mapping[str, object]:
    if not config_path.exists():
        raise ValueError(f"model config does not exist: {config_path}")
    body = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise ValueError("model config must be an object")
    return body


def _primary_model(body: Mapping[str, object]) -> str:
    primary_model = body.get("primary_model")
    if not isinstance(primary_model, str):
        raise ValueError("model config must define primary_model")
    return primary_model


def _router_model(body: Mapping[str, object]) -> str | None:
    if "router_model" not in body:
        return None
    router_model = body.get("router_model")
    if not isinstance(router_model, str) or not router_model.strip():
        raise ValueError("model config router_model must be a non-empty string")
    return router_model.strip()


def _resolve_router_model_name(body: Mapping[str, object], *, model_name: str | None = None) -> str:
    normalized = _normalize_model_name(model_name, label="router model override")
    if normalized is not None:
        return normalized
    return _router_model(body) or _primary_model(body)


def _normalize_model_name(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must be non-empty")
    return normalized


def _model_provider_config(body: Mapping[str, object], model_name: str) -> ModelProviderConfig:
    models = body.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError("model config must define non-empty models")
    raw_model = models.get(model_name)
    if not isinstance(raw_model, dict):
        raise ValueError(f"model is not defined: {model_name}")
    provider = raw_model.get("provider")
    options = raw_model.get("options") or {}
    if not isinstance(provider, str):
        raise ValueError(f"model '{model_name}' must define provider")
    if not isinstance(options, dict):
        raise ValueError(f"model '{model_name}' options must be an object")
    return ModelProviderConfig(provider=provider, options=options)

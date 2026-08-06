from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode


@dataclass(frozen=True)
class MCPPromptSelectionConfig:
    server: str
    name: str
    arguments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPResourceSelectionConfig:
    server: str
    uri: str


@dataclass(frozen=True)
class MCPSelectionConfig:
    servers: tuple[str, ...] = field(default_factory=tuple)
    prompts: tuple[MCPPromptSelectionConfig, ...] = field(default_factory=tuple)
    resources: tuple[MCPResourceSelectionConfig, ...] = field(default_factory=tuple)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "MCPSelectionConfig | None":
        if payload is None:
            return None
        servers = _normalize_names(_list_value(payload.get("servers") or [], label="MCP servers"), label="MCP server")
        prompts = tuple(_prompt_selection(item) for item in _list_value(payload.get("prompts") or [], label="MCP prompts"))
        resources = tuple(
            _resource_selection(item) for item in _list_value(payload.get("resources") or [], label="MCP resources")
        )
        selection = cls(servers=servers, prompts=prompts, resources=resources)
        selection.validate()
        return selection

    def validate(self) -> None:
        server_set = set(self.servers)
        for prompt in self.prompts:
            if prompt.server not in server_set:
                raise ValueError(f"MCP prompt references unselected server: {prompt.server}")
        for resource in self.resources:
            if resource.server not in server_set:
                raise ValueError(f"MCP resource references unselected server: {resource.server}")

    def to_runtime_prompts(self) -> tuple[str, ...]:
        return tuple(_runtime_prompt_value(prompt) for prompt in self.prompts)

    def to_runtime_resources(self) -> tuple[str, ...]:
        return tuple(f"{resource.server}:{resource.uri}" for resource in self.resources)

    def to_payload(self) -> dict[str, Any]:
        return {
            "servers": list(self.servers),
            "prompts": [_prompt_payload(item) for item in self.prompts],
            "resources": [{"server": item.server, "uri": item.uri} for item in self.resources],
        }


def _normalize_names(values: list[Any], *, label: str) -> tuple[str, ...]:
    result = []
    seen = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            raise ValueError(f"{label} name cannot be empty")
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _list_value(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _prompt_selection(value: Any) -> MCPPromptSelectionConfig:
    if not isinstance(value, dict):
        raise ValueError("MCP prompt selection must be an object")
    server = str(value.get("server") or "").strip()
    name = str(value.get("name") or "").strip()
    arguments = _prompt_arguments(value.get("arguments") or {})
    if not server:
        raise ValueError("MCP prompt server cannot be empty")
    if not name:
        raise ValueError("MCP prompt name cannot be empty")
    return MCPPromptSelectionConfig(server=server, name=name, arguments=arguments)


def _resource_selection(value: Any) -> MCPResourceSelectionConfig:
    if not isinstance(value, dict):
        raise ValueError("MCP resource selection must be an object")
    server = str(value.get("server") or "").strip()
    uri = str(value.get("uri") or "").strip()
    if not server:
        raise ValueError("MCP resource server cannot be empty")
    if not uri:
        raise ValueError("MCP resource URI cannot be empty")
    return MCPResourceSelectionConfig(server=server, uri=uri)


def _prompt_arguments(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("MCP prompt arguments must be an object")
    arguments: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip()
        if not key:
            raise ValueError("MCP prompt argument key cannot be empty")
        if raw_value is None:
            arguments[key] = ""
        elif isinstance(raw_value, str | int | float | bool):
            arguments[key] = str(raw_value)
        else:
            raise ValueError("MCP prompt argument values must be scalar")
    return arguments


def _runtime_prompt_value(prompt: MCPPromptSelectionConfig) -> str:
    value = f"{prompt.server}:{prompt.name}"
    if prompt.arguments:
        value += "?" + urlencode(prompt.arguments)
    return value


def _prompt_payload(prompt: MCPPromptSelectionConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {"server": prompt.server, "name": prompt.name}
    if prompt.arguments:
        payload["arguments"] = dict(prompt.arguments)
    return payload

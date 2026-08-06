from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    read_only: bool = False
    read_only_tools: set[str] = field(default_factory=set)
    tool_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_metadata: dict[str, Any] = field(default_factory=dict)
    tool_exposure: str = "read_only"


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: MCPServerConfig


@dataclass(frozen=True)
class MCPResourceDefinition:
    server: MCPServerConfig
    uri: str
    name: str
    title: str | None = None
    description: str = ""
    mime_type: str | None = None
    size: int | None = None
    is_template: bool = False


@dataclass(frozen=True)
class MCPPromptDefinition:
    server: MCPServerConfig
    name: str
    title: str | None = None
    description: str = ""
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class MCPToolReport:
    server: str
    original_name: str
    model_facing_name: str
    description: str
    read_only: bool
    exposed_by_policy: bool
    requires_approval: bool

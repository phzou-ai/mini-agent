from __future__ import annotations

import re
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from ..tool_metadata import (
    ApprovalPolicy,
    ExecutionScope,
    ToolCategory,
    ToolSource,
    metadata_from_legacy,
)
from .models import MCPServerConfig, MCPToolDefinition, MCPToolReport


MCPToolCaller = Callable[[MCPServerConfig, str, dict[str, Any]], Any]


def tool_definition_to_structured_tool(definition: MCPToolDefinition, caller: MCPToolCaller) -> StructuredTool:
    tool_name = model_facing_tool_name(definition.server.name, definition.name)
    read_only = is_read_only(definition.server, definition.name)
    dangerous = not read_only

    def call_tool(**kwargs):
        return caller(definition.server, definition.name, kwargs)

    metadata_values = {
        "source": ToolSource.MCP,
        "category": ToolCategory.MCP,
        "execution_scope": ExecutionScope.MCP,
    }
    metadata_values.update(definition.server.tool_metadata)
    override = definition.server.tool_overrides.get(definition.name)
    if isinstance(override, dict):
        metadata_values.update(override)
    metadata_values["dangerous"] = dangerous
    metadata_values["read_only"] = read_only
    metadata_values.setdefault(
        "approval_policy",
        ApprovalPolicy.AUTO if read_only else ApprovalPolicy.APPROVAL_REQUIRED,
    )

    metadata = metadata_from_legacy(metadata_values).to_metadata()
    metadata.update(
        {
            "mcp_server": definition.server.name,
            "mcp_tool": definition.name,
            "mcp_model_facing_name": tool_name,
            "mcp_read_only": read_only,
        }
    )

    return StructuredTool(
        name=tool_name,
        description=definition.description or f"MCP tool {definition.name} from {definition.server.name}",
        args_schema=definition.input_schema or {"type": "object", "properties": {}},
        func=call_tool,
        metadata=metadata,
    )


def tool_definition_to_report(definition: MCPToolDefinition) -> MCPToolReport:
    read_only = is_read_only(definition.server, definition.name)
    return MCPToolReport(
        server=definition.server.name,
        original_name=definition.name,
        model_facing_name=model_facing_tool_name(definition.server.name, definition.name),
        description=definition.description,
        read_only=read_only,
        exposed_by_policy=is_exposed_by_policy(definition.server, definition.name),
        requires_approval=not read_only,
    )


def is_read_only(server: MCPServerConfig, tool_name: str) -> bool:
    if server.read_only:
        return True
    override = server.tool_overrides.get(tool_name)
    if isinstance(override, dict) and override.get("read_only") is True:
        return True
    return tool_name in server.read_only_tools


def is_exposed_by_policy(server: MCPServerConfig, tool_name: str) -> bool:
    if server.tool_exposure == "none":
        return False
    if server.tool_exposure == "all":
        return True
    if server.tool_exposure == "read_only":
        return is_read_only(server, tool_name)
    if server.tool_exposure == "allowlist":
        return tool_name in server.tool_overrides
    raise ValueError(f"unsupported MCP tool_exposure: {server.tool_exposure}")


def model_facing_tool_name(server_name: str, tool_name: str) -> str:
    return f"mcp__{canonical_name(server_name, 'server')}__{canonical_name(tool_name, 'tool')}"


def canonical_name(value: str, label: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", value.lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        raise ValueError(f"MCP {label} name is empty after canonicalization: {value!r}")
    return name

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Iterable

from langchain_core.tools import StructuredTool

from .config import TOOL_EXPOSURE_POLICIES, load_mcp_server_configs
from .models import (
    MCPPromptDefinition,
    MCPResourceDefinition,
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolReport,
)
from .tool_adapter import (
    is_exposed_by_policy,
    model_facing_tool_name,
    tool_definition_to_report,
    tool_definition_to_structured_tool,
)
from .transport import (
    call_stdio_tool,
    discover_stdio_prompts,
    discover_stdio_resources,
    discover_stdio_tools,
    get_stdio_prompt,
    read_stdio_resource,
)


class MCPClientManager:
    def __init__(
        self,
        config_path: Path,
        *,
        selected_servers: Iterable[str] | None = None,
        discovery: Callable[[MCPServerConfig], list[MCPToolDefinition]] | None = None,
        resource_discovery: Callable[[MCPServerConfig], list[MCPResourceDefinition]] | None = None,
        prompt_discovery: Callable[[MCPServerConfig], list[MCPPromptDefinition]] | None = None,
        resource_reader: Callable[[MCPServerConfig, str], str] | None = None,
        prompt_getter: Callable[[MCPServerConfig, str, dict[str, str] | None], str] | None = None,
        caller: Callable[[MCPServerConfig, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config_path = config_path
        self.selected_servers = tuple(selected_servers) if selected_servers is not None else None
        self.discovery = discovery
        self.resource_discovery = resource_discovery
        self.prompt_discovery = prompt_discovery
        self.resource_reader = resource_reader
        self.prompt_getter = prompt_getter
        self.caller = caller

    def load_tools(self) -> list[StructuredTool]:
        tools = []
        seen_model_names: set[str] = set()
        for server in self._selected_server_configs():
            definitions = self._discover(server)
            for definition in definitions:
                if not is_exposed_by_policy(server, definition.name):
                    continue
                tool = tool_definition_to_structured_tool(definition, self._call_tool)
                if tool.name in seen_model_names:
                    raise ValueError(f"MCP tool name collision after canonicalization: {tool.name}")
                seen_model_names.add(tool.name)
                tools.append(tool)
        return tools

    def list_tool_reports(self, server_name: str | None = None) -> list[MCPToolReport]:
        reports: list[MCPToolReport] = []
        for server in self._server_configs_for_discovery(server_name):
            for definition in self._discover(server):
                reports.append(tool_definition_to_report(definition))
        return reports

    def list_servers(self) -> list[MCPServerConfig]:
        return load_mcp_server_configs(self.config_path)

    def list_resources(self, server_name: str | None = None) -> list[MCPResourceDefinition]:
        resources: list[MCPResourceDefinition] = []
        for server in self._server_configs_for_discovery(server_name):
            resources.extend(self._discover_resources(server))
        return resources

    def list_prompts(self, server_name: str | None = None) -> list[MCPPromptDefinition]:
        prompts: list[MCPPromptDefinition] = []
        for server in self._server_configs_for_discovery(server_name):
            prompts.extend(self._discover_prompts(server))
        return prompts

    def read_resource(self, server_name: str, uri: str) -> str:
        server = self._server_configs_for_discovery(server_name)[0]
        if self.resource_reader is not None:
            return self.resource_reader(server, uri)
        return asyncio.run(read_stdio_resource(server, uri))

    def get_prompt(self, server_name: str, prompt_name: str, arguments: dict[str, str] | None = None) -> str:
        server = self._server_configs_for_discovery(server_name)[0]
        if self.prompt_getter is not None:
            return self.prompt_getter(server, prompt_name, arguments)
        return asyncio.run(get_stdio_prompt(server, prompt_name, arguments))

    def _selected_server_configs(self) -> list[MCPServerConfig]:
        if self.selected_servers is None:
            return load_mcp_server_configs(self.config_path)
        configs = load_mcp_server_configs(self.config_path)
        by_name = {config.name: config for config in configs}
        selected = _dedupe(self.selected_servers)
        unknown = [name for name in selected if name not in by_name]
        if unknown:
            raise ValueError(f"unknown selected MCP server(s): {', '.join(unknown)}")
        return [by_name[name] for name in selected]

    def _server_configs_for_discovery(self, server_name: str | None) -> list[MCPServerConfig]:
        configs = load_mcp_server_configs(self.config_path)
        if server_name is None:
            return configs
        for server in configs:
            if server.name == server_name:
                return [server]
        raise ValueError(f"unknown MCP server: {server_name}")

    def _discover(self, server: MCPServerConfig) -> list[MCPToolDefinition]:
        if self.discovery is not None:
            return self.discovery(server)
        return asyncio.run(discover_stdio_tools(server))

    def _discover_resources(self, server: MCPServerConfig) -> list[MCPResourceDefinition]:
        if self.resource_discovery is not None:
            return self.resource_discovery(server)
        return asyncio.run(discover_stdio_resources(server))

    def _discover_prompts(self, server: MCPServerConfig) -> list[MCPPromptDefinition]:
        if self.prompt_discovery is not None:
            return self.prompt_discovery(server)
        return asyncio.run(discover_stdio_prompts(server))

    def _call_tool(self, server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self.caller is not None:
            return self.caller(server, tool_name, arguments)
        return asyncio.run(call_stdio_tool(server, tool_name, arguments))


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


# Compatibility aliases for older imports.
MCPToolLoader = MCPClientManager
_model_facing_tool_name = model_facing_tool_name

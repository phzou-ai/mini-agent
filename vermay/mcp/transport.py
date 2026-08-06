from __future__ import annotations

import asyncio
from typing import Any

from .models import MCPPromptDefinition, MCPResourceDefinition, MCPServerConfig, MCPToolDefinition


async def discover_stdio_tools(server: MCPServerConfig) -> list[MCPToolDefinition]:
    return await _with_transport_handling(server, "tools/list", _discover_stdio_tools(server))


async def _discover_stdio_tools(server: MCPServerConfig) -> list[MCPToolDefinition]:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            definitions = []
            for tool in result.tools:
                definitions.append(
                    MCPToolDefinition(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                        server=server,
                    )
                )
            return definitions


async def discover_stdio_resources(server: MCPServerConfig) -> list[MCPResourceDefinition]:
    return await _with_transport_handling(server, "resources/list", _discover_stdio_resources(server))


async def _discover_stdio_resources(server: MCPServerConfig) -> list[MCPResourceDefinition]:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_resources()
            resources = []
            for resource in result.resources:
                resources.append(
                    MCPResourceDefinition(
                        server=server,
                        uri=str(getattr(resource, "uri", "")),
                        name=str(getattr(resource, "name", "")),
                        title=getattr(resource, "title", None),
                        description=getattr(resource, "description", None) or "",
                        mime_type=getattr(resource, "mimeType", None),
                        size=getattr(resource, "size", None),
                        is_template=False,
                    )
                )
            try:
                template_result = await session.list_resource_templates()
            except Exception:
                template_result = None
            if template_result is not None:
                for template in template_result.resourceTemplates:
                    resources.append(
                        MCPResourceDefinition(
                            server=server,
                            uri=str(getattr(template, "uriTemplate", "")),
                            name=str(getattr(template, "name", "")),
                            title=getattr(template, "title", None),
                            description=getattr(template, "description", None) or "",
                            mime_type=getattr(template, "mimeType", None),
                            size=None,
                            is_template=True,
                        )
                    )
            return resources


async def discover_stdio_prompts(server: MCPServerConfig) -> list[MCPPromptDefinition]:
    return await _with_transport_handling(server, "prompts/list", _discover_stdio_prompts(server))


async def _discover_stdio_prompts(server: MCPServerConfig) -> list[MCPPromptDefinition]:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_prompts()
            prompts = []
            for prompt in result.prompts:
                prompts.append(
                    MCPPromptDefinition(
                        server=server,
                        name=str(getattr(prompt, "name", "")),
                        title=getattr(prompt, "title", None),
                        description=getattr(prompt, "description", None) or "",
                        arguments=serialize_prompt_arguments(getattr(prompt, "arguments", None)),
                    )
                )
            return prompts


async def call_stdio_tool(server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> Any:
    return await _with_transport_handling(server, f"tools/call {tool_name}", _call_stdio_tool(server, tool_name, arguments))


async def _call_stdio_tool(server: MCPServerConfig, tool_name: str, arguments: dict[str, Any]) -> Any:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return serialize_mcp_result(result)


async def read_stdio_resource(server: MCPServerConfig, uri: str) -> str:
    return await _with_transport_handling(server, f"resources/read {uri}", _read_stdio_resource(server, uri))


async def _read_stdio_resource(server: MCPServerConfig, uri: str) -> str:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(uri)
            return serialize_resource_result(result)


async def get_stdio_prompt(
    server: MCPServerConfig, prompt_name: str, arguments: dict[str, str] | None = None
) -> str:
    return await _with_transport_handling(server, f"prompts/get {prompt_name}", _get_stdio_prompt(server, prompt_name, arguments))


async def _get_stdio_prompt(
    server: MCPServerConfig, prompt_name: str, arguments: dict[str, str] | None = None
) -> str:
    if not server.command:
        raise MCPTransportError(f"MCP server '{server.name}' requires command")
    ClientSession, StdioServerParameters, stdio_client = _import_mcp_stdio()

    params = StdioServerParameters(command=server.command, args=server.args, env=server.env or None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt(prompt_name, arguments)
            return serialize_prompt_result(result)


def serialize_mcp_result(result: Any) -> Any:
    content = getattr(result, "content", None)
    if isinstance(content, list):
        serialized = []
        for item in content:
            text = getattr(item, "text", None)
            if text is not None:
                serialized.append(str(text))
            else:
                serialized.append(str(item))
        return "\n".join(serialized)
    return str(result)


def serialize_resource_result(result: Any) -> str:
    contents = getattr(result, "contents", None)
    if not isinstance(contents, list):
        return str(result)

    serialized = []
    for item in contents:
        text = getattr(item, "text", None)
        if text is not None:
            serialized.append(str(text))
            continue
        uri = getattr(item, "uri", "")
        mime_type = getattr(item, "mimeType", "")
        serialized.append(f"[binary MCP resource omitted: uri={uri} mime_type={mime_type}]")
    return "\n".join(serialized)


def serialize_prompt_result(result: Any) -> str:
    sections = []
    description = getattr(result, "description", None)
    if description:
        sections.append(f"description: {description}")

    messages = getattr(result, "messages", None)
    if isinstance(messages, list):
        for message in messages:
            role = str(getattr(message, "role", "unknown"))
            content = getattr(message, "content", None)
            text = getattr(content, "text", None)
            if text is not None:
                sections.append(f"{role}:\n{text}")
            else:
                content_type = getattr(content, "type", type(content).__name__)
                sections.append(f"{role}:\n[non-text MCP prompt content omitted: type={content_type}]")
    return "\n\n".join(sections) if sections else str(result)


def serialize_prompt_arguments(arguments: Any) -> list[dict[str, Any]]:
    if not isinstance(arguments, list):
        return []
    serialized = []
    for argument in arguments:
        serialized.append(
            {
                "name": str(getattr(argument, "name", "")),
                "description": getattr(argument, "description", None) or "",
                "required": getattr(argument, "required", None),
            }
        )
    return serialized


def _import_mcp_stdio():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise MCPTransportError("MCP SDK is not installed. Install the 'mcp' Python package.") from exc
    return ClientSession, StdioServerParameters, stdio_client


class MCPTransportError(RuntimeError):
    pass


class MCPTransportTimeout(MCPTransportError):
    pass


async def _with_transport_handling(server: MCPServerConfig, operation: str, coro: Any) -> Any:
    try:
        return await asyncio.wait_for(coro, timeout=server.timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise MCPTransportTimeout(
            f"MCP server '{server.name}' {operation} timed out after {server.timeout_seconds:g}s"
        ) from exc
    except MCPTransportError:
        raise
    except Exception as exc:
        raise MCPTransportError(f"MCP server '{server.name}' {operation} failed: {exc}") from exc

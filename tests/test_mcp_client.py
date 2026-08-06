from __future__ import annotations

import json

import pytest

from vermay.mcp.client import (
    MCPPromptDefinition,
    MCPResourceDefinition,
    MCPToolDefinition,
    MCPToolLoader,
    load_mcp_server_configs,
)


def test_mcp_config_parser_reads_stdio_servers(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                        "timeout_seconds": 12.5,
                        "read_only_tools": ["search"],
                        "metadata": {
                            "category": "kubernetes",
                            "execution_scope": "remote",
                            "redaction_required": True,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    servers = load_mcp_server_configs(config)

    assert servers[0].name == "docs"
    assert servers[0].command == "python"
    assert servers[0].timeout_seconds == 12.5
    assert servers[0].read_only_tools == {"search"}
    assert servers[0].tool_metadata == {
        "category": "kubernetes",
        "execution_scope": "remote",
        "redaction_required": True,
    }


def test_mcp_config_parser_rejects_invalid_timeout(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps({"servers": {"docs": {"transport": "stdio", "command": "python", "timeout_seconds": 0}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="timeout_seconds must be a positive number"):
        load_mcp_server_configs(config)


def test_mcp_config_parser_rejects_non_object_metadata(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps({"servers": {"docs": {"transport": "stdio", "command": "python", "metadata": []}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="metadata must be an object"):
        load_mcp_server_configs(config)


def test_mcp_tools_are_approval_required_by_default(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "tool_exposure": "all",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search",
                description="Search docs.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
                server=server,
            )
        ]

    tools = MCPToolLoader(config, discovery=discover, caller=lambda server, name, args: {"ok": True}).load_tools()

    assert tools[0].name == "mcp__docs__search"
    assert tools[0].metadata["dangerous"] is True
    assert tools[0].metadata["read_only"] is False
    assert tools[0].metadata["approval_policy"] == "approval_required"
    assert tools[0].metadata["source"] == "mcp"
    assert tools[0].metadata["mcp_server"] == "docs"
    assert tools[0].metadata["mcp_tool"] == "search"


def test_mcp_read_only_config_bypasses_approval(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "tools": {"search": {"read_only": True}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            )
        ]

    tools = MCPToolLoader(config, discovery=discover, caller=lambda server, name, args: "ok").load_tools()

    assert tools[0].metadata["dangerous"] is False
    assert tools[0].metadata["read_only"] is True
    assert tools[0].metadata["approval_policy"] == "auto"


def test_mcp_tool_inherits_server_metadata_defaults(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "k8s": {
                        "transport": "stdio",
                        "command": "server",
                        "read_only": True,
                        "metadata": {
                            "category": "kubernetes",
                            "execution_scope": "remote",
                            "credential_sensitive": True,
                            "redaction_required": True,
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="kubectl_get",
                description="Read cluster.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            )
        ]

    tools = MCPToolLoader(config, discovery=discover, caller=lambda server, name, args: "ok").load_tools()

    assert tools[0].metadata["category"] == "kubernetes"
    assert tools[0].metadata["execution_scope"] == "remote"
    assert tools[0].metadata["credential_sensitive"] is True
    assert tools[0].metadata["redaction_required"] is True
    assert tools[0].metadata["read_only"] is True
    assert tools[0].metadata["approval_policy"] == "auto"


def test_mcp_tool_override_metadata_takes_precedence(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "read_only": True,
                        "metadata": {
                            "category": "mcp",
                            "execution_scope": "mcp",
                            "redaction_required": True,
                        },
                        "tools": {
                            "search": {
                                "category": "filesystem",
                                "execution_scope": "local",
                                "redaction_required": False,
                            }
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            )
        ]

    tools = MCPToolLoader(config, discovery=discover, caller=lambda server, name, args: "ok").load_tools()

    assert tools[0].metadata["category"] == "filesystem"
    assert tools[0].metadata["execution_scope"] == "local"
    assert tools[0].metadata["redaction_required"] is False
    assert tools[0].metadata["read_only"] is True


def test_mcp_runtime_selection_can_load_no_servers(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "read_only": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    tools = MCPToolLoader(config, selected_servers=[], discovery=lambda server: []).load_tools()

    assert tools == []


def test_mcp_runtime_selection_rejects_unknown_server(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": {"docs": {"transport": "stdio", "command": "server"}}}), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown selected MCP server"):
        MCPToolLoader(config, selected_servers=["missing"], discovery=lambda server: []).load_tools()


def test_mcp_default_read_only_exposure_skips_dangerous_tools(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": {"docs": {"transport": "stdio", "command": "server"}}}), encoding="utf-8")

    def discover(server):
        return [
            MCPToolDefinition(
                name="write",
                description="Write docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            )
        ]

    tools = MCPToolLoader(config, selected_servers=["docs"], discovery=discover).load_tools()

    assert tools == []


def test_mcp_allowlist_uses_original_tool_names(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "tool_exposure": "allowlist",
                        "tools": {"search.docs": {"read_only": True}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search.docs",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
            MCPToolDefinition(
                name="other",
                description="Other tool.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
        ]

    tools = MCPToolLoader(config, selected_servers=["docs"], discovery=discover).load_tools()

    assert [tool.name for tool in tools] == ["mcp__docs__search_docs"]
    assert tools[0].metadata["dangerous"] is False


def test_mcp_tool_name_collision_after_canonicalization_fails(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "tool_exposure": "all",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search-docs",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
            MCPToolDefinition(
                name="search/docs",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
        ]

    with pytest.raises(ValueError, match="collision"):
        MCPToolLoader(config, selected_servers=["docs"], discovery=discover).load_tools()


def test_mcp_tool_reports_include_policy_fields(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "Docs Server": {
                        "transport": "stdio",
                        "command": "server",
                        "tools": {"search": {"read_only": True}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    def discover(server):
        return [
            MCPToolDefinition(
                name="search",
                description="Search docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
            MCPToolDefinition(
                name="write",
                description="Write docs.",
                input_schema={"type": "object", "properties": {}},
                server=server,
            ),
        ]

    reports = MCPToolLoader(config, discovery=discover).list_tool_reports(server_name="Docs Server")

    assert reports[0].original_name == "search"
    assert reports[0].model_facing_name == "mcp__docs_server__search"
    assert reports[0].read_only is True
    assert reports[0].exposed_by_policy is True
    assert reports[0].requires_approval is False
    assert reports[1].original_name == "write"
    assert reports[1].exposed_by_policy is False
    assert reports[1].requires_approval is True


def test_mcp_list_resources_uses_discovery_without_registering_tools(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": {"docs": {"transport": "stdio", "command": "server"}}}), encoding="utf-8")

    def discover_resources(server):
        return [
            MCPResourceDefinition(
                server=server,
                uri="docs://guide",
                name="guide",
                title="Guide",
                description="Documentation guide.",
                mime_type="text/markdown",
                size=100,
            )
        ]

    resources = MCPToolLoader(config, resource_discovery=discover_resources).list_resources(server_name="docs")

    assert resources[0].server.name == "docs"
    assert resources[0].uri == "docs://guide"
    assert resources[0].title == "Guide"
    assert resources[0].mime_type == "text/markdown"


def test_mcp_list_prompts_uses_discovery_without_registering_tools(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text(json.dumps({"servers": {"docs": {"transport": "stdio", "command": "server"}}}), encoding="utf-8")

    def discover_prompts(server):
        return [
            MCPPromptDefinition(
                server=server,
                name="debug",
                title="Debug",
                description="Debug prompt.",
                arguments=[{"name": "service", "required": True}],
            )
        ]

    prompts = MCPToolLoader(config, prompt_discovery=discover_prompts).list_prompts(server_name="docs")

    assert prompts[0].server.name == "docs"
    assert prompts[0].name == "debug"
    assert prompts[0].arguments == [{"name": "service", "required": True}]

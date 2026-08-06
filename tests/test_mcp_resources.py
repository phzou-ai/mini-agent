from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import SystemMessage

from vermay.mcp.client import MCPServerConfig, MCPToolLoader
from vermay.mcp.resources import MCPResourceProvider, resolve_mcp_resource_selections
from vermay.runtime_context import RuntimeContextProvider


class FakeMCPClientManager:
    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        self.values = values
        self.calls: list[tuple[str, str]] = []

    def read_resource(self, server: str, uri: str) -> str:
        self.calls.append((server, uri))
        return self.values[(server, uri)]


def test_resolve_mcp_resource_selection_requires_selected_server():
    with pytest.raises(ValueError, match="requires at least one --mcp-server"):
        resolve_mcp_resource_selections((), ("docs://guide",))


def test_resolve_mcp_resource_selection_requires_qualified_uri_with_multiple_servers():
    with pytest.raises(ValueError, match="server:uri"):
        resolve_mcp_resource_selections(("docs", "k8s"), ("docs://guide",))


def test_resolve_mcp_resource_selection_uses_selected_server_for_unqualified_uri():
    selections = resolve_mcp_resource_selections(("docs",), ("docs://guide", "docs:docs://other"))

    assert [(item.server, item.uri) for item in selections] == [
        ("docs", "docs://guide"),
        ("docs", "docs://other"),
    ]


def test_resolve_mcp_resource_selection_rejects_unselected_qualified_server():
    with pytest.raises(ValueError, match="unselected MCP server"):
        resolve_mcp_resource_selections(("docs", "k8s"), ("other:docs://guide",))


def test_runtime_context_injects_mcp_resource_context():
    manager = FakeMCPClientManager({("docs", "docs://guide"): "Guide content"})
    provider = MCPResourceProvider(
        config_path=Path("unused.json"),
        selected_servers=("docs",),
        selected_resources=("docs://guide",),
        client_manager=manager,
    )

    messages = RuntimeContextProvider(mcp_resources=provider).context_messages("read guide")

    assert len(messages) == 1
    assert isinstance(messages[0], SystemMessage)
    assert "External MCP resources:" in str(messages[0].content)
    assert "server: docs" in str(messages[0].content)
    assert "resource: docs://guide" in str(messages[0].content)
    assert "Guide content" in str(messages[0].content)
    assert manager.calls == [("docs", "docs://guide")]


def test_mcp_resource_provider_truncates_and_skips_by_total_budget():
    manager = FakeMCPClientManager(
        {
            ("docs", "docs://one"): "abcdef",
            ("docs", "docs://two"): "uvwxyz",
            ("docs", "docs://three"): "123456",
        }
    )
    provider = MCPResourceProvider(
        config_path=Path("unused.json"),
        selected_servers=("docs",),
        selected_resources=("docs://one", "docs://two", "docs://three"),
        client_manager=manager,
        max_resource_chars=6,
        max_total_resource_chars=8,
    )

    content = provider.context_text()

    assert content is not None
    assert "abcdef" in content
    assert "uv" in content
    assert "123456" not in content
    assert manager.calls == [("docs", "docs://one"), ("docs", "docs://two")]


def test_mcp_client_read_resource_uses_resource_reader(tmp_path):
    config = tmp_path / "mcp_servers.json"
    config.write_text('{"servers":{"docs":{"transport":"stdio","command":"server"}}}', encoding="utf-8")

    def read(server: MCPServerConfig, uri: str) -> str:
        assert server.name == "docs"
        assert uri == "docs://guide"
        return "Guide content"

    text = MCPToolLoader(config, resource_reader=read).read_resource("docs", "docs://guide")

    assert text == "Guide content"

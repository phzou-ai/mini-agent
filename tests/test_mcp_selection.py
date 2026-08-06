from __future__ import annotations

import pytest

from vermay.mcp.selection import MCPSelectionConfig


def test_mcp_selection_normalizes_payload():
    selection = MCPSelectionConfig.from_payload(
        {
            "servers": ["k8s", "k8s"],
            "prompts": [{"server": "k8s", "name": "debug", "arguments": {"service": "api", "namespace": "default"}}],
            "resources": [{"server": "k8s", "uri": "k8s://cluster/services"}],
        }
    )

    assert selection is not None
    assert selection.servers == ("k8s",)
    assert selection.to_runtime_prompts() == ("k8s:debug?service=api&namespace=default",)
    assert selection.to_runtime_resources() == ("k8s:k8s://cluster/services",)
    assert selection.to_payload() == {
        "servers": ["k8s"],
        "prompts": [{"server": "k8s", "name": "debug", "arguments": {"service": "api", "namespace": "default"}}],
        "resources": [{"server": "k8s", "uri": "k8s://cluster/services"}],
    }


def test_mcp_selection_rejects_non_list_servers():
    with pytest.raises(ValueError, match="MCP servers must be a list"):
        MCPSelectionConfig.from_payload({"servers": "k8s"})


def test_mcp_selection_rejects_prompt_for_unselected_server():
    with pytest.raises(ValueError, match="unselected server"):
        MCPSelectionConfig.from_payload(
            {
                "servers": ["docs"],
                "prompts": [{"server": "k8s", "name": "debug"}],
            }
        )


def test_mcp_selection_keeps_legacy_prompt_payload_without_arguments():
    selection = MCPSelectionConfig.from_payload(
        {
            "servers": ["k8s"],
            "prompts": [{"server": "k8s", "name": "debug"}],
        }
    )

    assert selection is not None
    assert selection.to_runtime_prompts() == ("k8s:debug",)
    assert selection.to_payload()["prompts"] == [{"server": "k8s", "name": "debug"}]


def test_mcp_selection_rejects_non_object_prompt_arguments():
    with pytest.raises(ValueError, match="arguments must be an object"):
        MCPSelectionConfig.from_payload(
            {
                "servers": ["k8s"],
                "prompts": [{"server": "k8s", "name": "debug", "arguments": "topic=health"}],
            }
        )


def test_mcp_selection_rejects_complex_prompt_argument_values():
    with pytest.raises(ValueError, match="argument values must be scalar"):
        MCPSelectionConfig.from_payload(
            {
                "servers": ["k8s"],
                "prompts": [{"server": "k8s", "name": "debug", "arguments": {"filters": ["pods"]}}],
            }
        )

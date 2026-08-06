from __future__ import annotations

from pathlib import Path

from vermay.mcp.client import MCPClientManager


def test_tracked_k8s_mcp_config_discovers_example_server_capabilities():
    manager = MCPClientManager(Path("config/mcp_servers.json"))

    reports = manager.list_tool_reports("k8s")
    tools = manager.load_tools()
    resources = manager.list_resources("k8s")
    prompts = manager.list_prompts("k8s")

    assert {report.original_name for report in reports} == {"kubectl_get", "kubectl_describe", "cluster_events"}
    assert all(report.read_only is True for report in reports)
    assert all(report.requires_approval is False for report in reports)
    assert {tool.metadata["category"] for tool in tools} == {"kubernetes"}
    assert {tool.metadata["execution_scope"] for tool in tools} == {"remote"}
    assert all(tool.metadata["credential_sensitive"] is True for tool in tools)
    assert all(tool.metadata["redaction_required"] is True for tool in tools)
    assert {resource.uri for resource in resources} == {
        "k8s://cluster/nodes",
        "k8s://cluster/services",
        "k8s://namespace/{namespace}/pods",
    }
    assert {resource.uri for resource in resources if resource.is_template} == {"k8s://namespace/{namespace}/pods"}
    assert {prompt.name for prompt in prompts} == {"k8s-readonly-debug", "k8s-service-health-check"}

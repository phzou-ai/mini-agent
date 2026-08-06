from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

from vermay.tools.devops import remote_kubernetes


def load_server_module():
    path = Path(__file__).resolve().parents[1] / "examples" / "mcp_servers" / "k8s" / "server.py"
    spec = importlib.util.spec_from_file_location("k8s_mcp_example_server", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_k8s_mcp_server_lists_read_only_capabilities():
    server = load_server_module()

    async def inspect():
        tools = await server.mcp.list_tools()
        resources = await server.mcp.list_resources()
        templates = await server.mcp.list_resource_templates()
        prompts = await server.mcp.list_prompts()
        return tools, resources, templates, prompts

    tools, resources, templates, prompts = asyncio.run(inspect())

    assert {tool.name for tool in tools} == {"kubectl_get", "kubectl_describe", "cluster_events"}
    assert {str(resource.uri) for resource in resources} == {"k8s://cluster/nodes", "k8s://cluster/services"}
    assert {template.uriTemplate for template in templates} == {"k8s://namespace/{namespace}/pods"}
    assert {prompt.name for prompt in prompts} == {"k8s-readonly-debug", "k8s-service-health-check"}


def test_k8s_mcp_tools_reuse_read_only_remote_backend(monkeypatch):
    server = load_server_module()
    calls = []

    class FakeSshClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def run(self, command):
            calls.append(command)
            return {"ok": True, "stdout": "ok", "stderr": "", "exit_code": 0, "command": command}

    monkeypatch.setattr(remote_kubernetes, "SshClient", FakeSshClient)
    monkeypatch.setattr(server, "SshClient", FakeSshClient)

    get_result = server.kubectl_get("pods", namespace="default")
    describe_result = server.kubectl_describe("node", name="phzou-nuc")
    events_result = server.cluster_events(namespace="all")

    assert get_result["ok"] is True
    assert describe_result["ok"] is True
    assert events_result["ok"] is True
    assert "kubectl get pods -n default -o wide" in calls[0]
    assert "kubectl describe node phzou-nuc" in calls[1]
    assert "kubectl get events -A -o wide" in calls[2]

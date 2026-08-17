from __future__ import annotations

import pytest

from vermay.app_factory import RuntimeFactoryConfig, build_runtime
from vermay.langgraph_runtime import LangGraphAgentRuntime, ModelProviderConfig, OllamaModelAdapter


def test_app_factory_builds_runtime_with_registered_tools(tmp_path):
    runtime = build_runtime(
        RuntimeFactoryConfig(
            model=ModelProviderConfig(provider="ollama", options={"model": "test-model"}),
            trace_path=tmp_path / "trace.jsonl",
            checkpoint_path=tmp_path / "checkpoints" / "langgraph.sqlite",
            agent_store_path=tmp_path / "agent.sqlite",
            skills_path=tmp_path / "skills",
            skill_proposals_path=tmp_path / "skill_proposals",
            mcp_config_path=tmp_path / "mcp_servers.json",
            max_loops=7,
            max_tool_calls=9,
            max_failures=3,
            max_elapsed_seconds=42.0,
            show_progress=False,
        )
    )

    tool_names = {tool.name for tool in runtime.tools}

    assert isinstance(runtime, LangGraphAgentRuntime)
    assert isinstance(runtime.model, OllamaModelAdapter)
    assert runtime.model.client.model == "test-model"
    assert runtime.permission_gate is not None
    assert runtime.trace is not None
    assert runtime.trace.path == tmp_path / "trace.jsonl"
    assert runtime.progress is not None
    assert runtime.progress.enabled is False
    assert runtime.execution_policy is not None
    assert runtime.execution_policy.to_dict() == {
        "max_model_calls": 7,
        "max_tool_calls": 9,
        "max_failures": 3,
        "max_tool_argument_corrections": 1,
        "max_loop_steps": 7,
        "max_elapsed_seconds": 42.0,
    }
    assert runtime.checkpointer is not None
    assert "ssh_kubectl_get" in tool_names
    assert "weather_forecast" in tool_names
    assert runtime.context_provider is not None
    assert len(runtime.close_callbacks) == 2
    runtime.close()


def test_app_factory_rejects_mcp_resource_without_selected_server(tmp_path):
    with pytest.raises(ValueError, match="requires at least one --mcp-server"):
        build_runtime(
            RuntimeFactoryConfig(
                model=ModelProviderConfig(provider="ollama", options={"model": "test-model"}),
                trace_path=tmp_path / "trace.jsonl",
                checkpoint_path=tmp_path / "checkpoints" / "langgraph.sqlite",
                agent_store_path=tmp_path / "agent.sqlite",
                skills_path=tmp_path / "skills",
                skill_proposals_path=tmp_path / "skill_proposals",
                mcp_config_path=tmp_path / "mcp_servers.json",
                mcp_resources=("docs://guide",),
                show_progress=False,
            )
        )


def test_app_factory_rejects_mcp_prompt_without_selected_server(tmp_path):
    with pytest.raises(ValueError, match="requires at least one --mcp-server"):
        build_runtime(
            RuntimeFactoryConfig(
                model=ModelProviderConfig(provider="ollama", options={"model": "test-model"}),
                trace_path=tmp_path / "trace.jsonl",
                checkpoint_path=tmp_path / "checkpoints" / "langgraph.sqlite",
                agent_store_path=tmp_path / "agent.sqlite",
                skills_path=tmp_path / "skills",
                skill_proposals_path=tmp_path / "skill_proposals",
                mcp_config_path=tmp_path / "mcp_servers.json",
                mcp_prompts=("debug",),
                show_progress=False,
            )
        )


def test_app_factory_passes_selected_mcp_servers_and_logs_zero_eligible_tools(tmp_path, monkeypatch):
    captured_servers = []

    def fake_loader(config_path, *, selected_servers=(), **kwargs):
        captured_servers.extend(selected_servers)
        return type("Loader", (), {"load_tools": lambda self: []})()

    monkeypatch.setattr("vermay.app_factory.MCPClientManager", fake_loader)
    trace_path = tmp_path / "trace.jsonl"

    runtime = build_runtime(
        RuntimeFactoryConfig(
            model=ModelProviderConfig(provider="ollama", options={"model": "test-model"}),
            trace_path=trace_path,
            checkpoint_path=tmp_path / "checkpoints" / "langgraph.sqlite",
            agent_store_path=tmp_path / "agent.sqlite",
            skills_path=tmp_path / "skills",
            skill_proposals_path=tmp_path / "skill_proposals",
            mcp_config_path=tmp_path / "mcp_servers.json",
            mcp_servers=("docs",),
            show_progress=False,
        )
    )

    assert captured_servers == ["docs"]
    assert "mcp_selection_no_eligible_tools" in trace_path.read_text(encoding="utf-8")

    runtime.close()

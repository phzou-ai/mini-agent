from __future__ import annotations

import json
from types import SimpleNamespace

from vermay.cli import subcommands as cli


def test_memory_cli_add_list_disable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DEFAULT_AGENT_STORE_PATH", tmp_path / "agent.sqlite")

    cli.run_memory_command(["add", "Prefer safe tools.", "--tag", "preference"])
    cli.run_memory_command(["list"])
    output = capsys.readouterr().out

    assert "added memory 1" in output
    assert "Prefer safe tools." in output

    cli.run_memory_command(["disable", "1"])
    assert "disabled memory 1" in capsys.readouterr().out


def test_eval_cli_replay_scenario(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "DEFAULT_AGENT_STORE_PATH", tmp_path / "agent.sqlite")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "input": "weather",
                "tool_observations": [{"name": "weather_forecast"}],
                "final_answer": "Shanghai Weather",
                "expect": {"tool_sequence": ["weather_forecast"], "final_contains": ["Shanghai"]},
            }
        ),
        encoding="utf-8",
    )

    cli.run_eval_command(["replay", "--scenario", str(scenario)])
    output = capsys.readouterr().out

    assert '"status": "passed"' in output
    assert (tmp_path / "data" / "eval_runs").exists()


def test_mcp_cli_list_servers(tmp_path, capsys):
    config = tmp_path / "mcp_servers.json"
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "docs": {
                        "transport": "stdio",
                        "command": "server",
                        "read_only": True,
                        "tool_exposure": "allowlist",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cli.run_mcp_command(["list-servers", "--config", str(config)])

    output = capsys.readouterr().out
    assert "name=docs" in output
    assert "tool_exposure=allowlist" in output


def test_mcp_cli_list_resources_and_prompts(tmp_path, monkeypatch, capsys):
    server = SimpleNamespace(name="docs")

    class FakeMCPClientManager:
        def __init__(self, config_path):
            self.config_path = config_path

        def list_resources(self, server_name=None):
            assert server_name == "docs"
            return [
                SimpleNamespace(
                    server=server,
                    uri="docs://guide",
                    name="guide",
                    title="Guide",
                    mime_type="text/markdown",
                    size=10,
                    is_template=False,
                    description="Documentation guide.",
                )
            ]

        def list_prompts(self, server_name=None):
            assert server_name == "docs"
            return [
                SimpleNamespace(
                    server=server,
                    name="debug",
                    title="Debug",
                    arguments=[{"name": "service"}],
                    description="Debug prompt.",
                )
            ]

    monkeypatch.setattr(cli, "MCPClientManager", FakeMCPClientManager)

    cli.run_mcp_command(["list-resources", "--config", str(tmp_path / "mcp_servers.json"), "--server", "docs"])
    resource_output = capsys.readouterr().out
    assert "server=docs" in resource_output
    assert "uri=docs://guide" in resource_output
    assert "mime_type=text/markdown" in resource_output

    cli.run_mcp_command(["list-prompts", "--config", str(tmp_path / "mcp_servers.json"), "--server", "docs"])
    prompt_output = capsys.readouterr().out
    assert "server=docs" in prompt_output
    assert "name=debug" in prompt_output
    assert "arguments=service" in prompt_output

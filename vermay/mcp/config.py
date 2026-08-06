from __future__ import annotations

import json
from pathlib import Path

from .models import MCPServerConfig


TOOL_EXPOSURE_POLICIES = {"none", "read_only", "allowlist", "all"}


def load_mcp_server_configs(path: Path) -> list[MCPServerConfig]:
    if not path.exists():
        return []
    body = json.loads(path.read_text(encoding="utf-8"))
    servers = body.get("servers") or {}
    if not isinstance(servers, dict):
        raise ValueError("MCP config 'servers' must be an object")
    configs = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            raise ValueError(f"MCP server '{name}' must be an object")
        transport = str(raw.get("transport") or "stdio")
        if transport != "stdio":
            raise ValueError(f"MCP server '{name}' transport is unsupported: {transport}")
        args = raw.get("args") or []
        env = raw.get("env") or {}
        read_only_tools = raw.get("read_only_tools") or []
        tool_overrides = raw.get("tools") or {}
        tool_metadata = raw.get("metadata", {})
        tool_exposure = str(raw.get("tool_exposure") or "read_only")
        timeout_seconds = _timeout_seconds(raw.get("timeout_seconds", 30), name)
        if not isinstance(tool_metadata, dict):
            raise ValueError(f"MCP server '{name}' metadata must be an object")
        if tool_exposure not in TOOL_EXPOSURE_POLICIES:
            raise ValueError(
                f"MCP server '{name}' has unsupported tool_exposure '{tool_exposure}'. "
                f"Expected one of: {', '.join(sorted(TOOL_EXPOSURE_POLICIES))}"
            )
        configs.append(
            MCPServerConfig(
                name=str(name),
                transport=transport,
                command=raw.get("command"),
                args=[str(item) for item in args],
                env={str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else {},
                timeout_seconds=timeout_seconds,
                read_only=bool(raw.get("read_only", False)),
                read_only_tools={str(item) for item in read_only_tools},
                tool_overrides=tool_overrides if isinstance(tool_overrides, dict) else {},
                tool_metadata={str(key): value for key, value in tool_metadata.items()},
                tool_exposure=tool_exposure,
            )
        )
    return configs


def _timeout_seconds(value: object, server_name: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MCP server '{server_name}' timeout_seconds must be a positive number") from exc
    if timeout <= 0:
        raise ValueError(f"MCP server '{server_name}' timeout_seconds must be a positive number")
    return timeout

from __future__ import annotations

import asyncio

import pytest

from vermay.mcp.models import MCPServerConfig
from vermay.mcp.transport import MCPTransportTimeout, _with_transport_handling


async def _slow_operation() -> str:
    await asyncio.sleep(0.05)
    return "done"


def test_mcp_transport_times_out_operations():
    server = MCPServerConfig(name="docs", transport="stdio", command="server", timeout_seconds=0.001)

    with pytest.raises(MCPTransportTimeout, match="timed out after"):
        asyncio.run(_with_transport_handling(server, "test/slow", _slow_operation()))


def test_mcp_transport_returns_completed_operations():
    async def fast_operation() -> str:
        return "done"

    server = MCPServerConfig(name="docs", transport="stdio", command="server", timeout_seconds=1)

    assert asyncio.run(_with_transport_handling(server, "test/fast", fast_operation())) == "done"

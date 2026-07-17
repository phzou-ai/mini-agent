from __future__ import annotations

"""Small project value types retained for model-adapter and legacy harness bridges.

The active LangGraph runtime uses LangChain messages and ToolNode for graph
execution. `Message`, `ModelResponse`, and `ToolCall` remain useful at the
model-adapter and permission boundaries. `ToolResult` and `Observation` are
kept for the archived hands-on harness path and focused compatibility tests.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class ToolResult:
    name: str
    ok: bool
    output: Any = None
    error: str | None = None


@dataclass
class Observation:
    tool_name: str
    content: str
    ok: bool


@dataclass
class PermissionDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    decision: str | None = None
    risk_level: str | None = None
    approval_summary: str | None = None
    safe_argument_preview: dict[str, Any] = field(default_factory=dict)
    policy_tags: list[str] = field(default_factory=list)


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def tool_call(self) -> ToolCall | None:
        """Compatibility view for callers that only support one tool call."""
        return self.tool_calls[0] if self.tool_calls else None

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_calls)

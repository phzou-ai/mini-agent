from __future__ import annotations

"""Small project value types used at model-adapter and permission boundaries.

The active LangGraph runtime uses LangChain messages and ToolNode for graph
execution. These types normalize provider responses before they enter that
runtime without introducing a second graph message model.
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

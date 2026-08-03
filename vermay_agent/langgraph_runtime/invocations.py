from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolInvocationReference:
    """A durable side-effect invocation selected for one model tool call.

    The graph owns neither the persistence schema nor the overall Task
    lifecycle. It only carries this reference across permission, interrupt,
    and ToolNode execution boundaries.
    """

    invocation_id: str
    arguments_digest: str
    status: str
    execution_blocked: bool = False
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ToolInvocationExecution:
    """Whether ToolNode may invoke the external capability now."""

    invocation_id: str
    execute: bool
    message: str | None = None


class ToolInvocationRecorder(Protocol):
    """Persistence adapter for side-effecting ToolNode calls.

    Implementations may return ``None`` when an invocation is outside their
    durable process boundary, such as an interactive CLI run without a local
    A2A Task.
    """

    def prepare(
        self,
        *,
        runtime_thread_id: str | None,
        loop_index: int,
        tool_call: dict[str, Any],
        tool_metadata: dict[str, Any] | None,
        approval_required: bool,
    ) -> ToolInvocationReference | None:
        """Durably identify a side effect before it can execute."""

    def begin_execution(self, invocation_id: str) -> ToolInvocationExecution:
        """Claim a prepared invocation immediately before external execution."""

    def finish_execution(self, invocation_id: str, *, response: object) -> None:
        """Persist a successful or uncertain ToolNode result."""

    def mark_execution_uncertain(
        self,
        invocation_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        """Record that an external effect may have happened but is unproven."""

    def cancel(self, invocation_id: str, *, reason: str) -> None:
        """Cancel a prepared invocation that will not be executed."""

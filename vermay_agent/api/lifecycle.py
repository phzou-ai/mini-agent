from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from vermay_agent.trace import TraceLogger


class LifecycleObserver(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


class NullLifecycleObserver:
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        return None


@dataclass(frozen=True)
class TraceLifecycleObserver:
    trace: TraceLogger

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.trace.log_event(event_type, payload)


@dataclass(frozen=True)
class LifecycleContext:
    session_id: str
    task_id: str
    thread_id: str
    operation: str
    model_provider: str
    max_loops: int
    mcp_selected: bool
    started_at: float

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        task_id: str,
        thread_id: str,
        operation: str,
        model_provider: str,
        max_loops: int,
        mcp_selected: bool,
    ) -> LifecycleContext:
        return cls(
            session_id=session_id,
            task_id=task_id,
            thread_id=thread_id,
            operation=operation,
            model_provider=model_provider,
            max_loops=max_loops,
            mcp_selected=mcp_selected,
            started_at=monotonic(),
        )


def lifecycle_payload(
    context: LifecycleContext,
    *,
    status: str,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": context.session_id,
        "task_id": context.task_id,
        "thread_id": context.thread_id,
        "operation": context.operation,
        "status": status,
        "model_provider": context.model_provider,
        "max_loops": context.max_loops,
        "mcp_selected": context.mcp_selected,
        "duration_ms": int((monotonic() - context.started_at) * 1000),
        "error_code": error_code,
    }
    if retryable is not None:
        payload["retryable"] = retryable
    return payload

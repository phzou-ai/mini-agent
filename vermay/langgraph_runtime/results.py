from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RunResult:
    thread_id: str
    final_answer: str | None = None
    interrupt: Any | None = None
    interrupt_message: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    stop_message: str | None = None
    stop_reason: str | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    execution: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.interrupt_message is not None or self.interrupt is not None:
            return "interrupted"
        if self.final_answer is not None:
            return "completed"
        if self.stop_message is not None:
            return "stopped"
        return "unknown"

    def to_output(self) -> str:
        if self.interrupt_message is not None:
            return self.interrupt_message
        if self.final_answer is not None:
            return self.final_answer
        if self.stop_message is not None:
            return self.stop_message
        return f"Run finished for thread {self.thread_id}, but no final answer was produced."

    def to_dict(self, include_state: bool = False) -> dict[str, Any]:
        payload = {
            "thread_id": self.thread_id,
            "status": self.status,
            "final_answer": self.final_answer,
            "interrupt": _safe_payload(self.interrupt),
            "interrupt_message": self.interrupt_message,
            "stop_message": self.stop_message,
            "stop_reason": self.stop_reason,
            "observations": _safe_payload(self.observations),
            "execution": _safe_payload(self.execution),
        }
        if include_state:
            payload["state"] = _safe_payload(self.state)
        return payload


def _safe_payload(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_safe_payload(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _safe_payload(asdict(value))
    return str(value)

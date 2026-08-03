from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ExecutionStopReason(str, Enum):
    """Why one local execution slice reached a stable boundary.

    This is intentionally separate from A2A Task state and from LangGraph's
    checkpoint position.  The main-agent control plane projects the outcome
    into the public Task lifecycle.
    """

    COMPLETED = "completed"
    INPUT_REQUIRED = "input_required"
    APPROVAL_REQUIRED = "approval_required"
    BUDGET_EXHAUSTED = "budget_exhausted"
    REPEATED_FAILURE = "repeated_failure"
    POLICY_BLOCKED = "policy_blocked"
    CANCELED = "canceled"
    ENVIRONMENT_FAILURE = "environment_failure"


@dataclass(frozen=True)
class ExecutionPolicy:
    """Bounded limits assigned to one LangGraph Task execution.

    ``max_elapsed_seconds`` is deliberately opt-in.  When configured, it is a
    wall-clock deadline for the process, including time spent waiting for a
    continuation.  The default leaves operator-paced approvals and input
    requests unbounded while still constraining model and tool work.
    """

    max_model_calls: int = 5
    max_tool_calls: int = 20
    max_failures: int = 2
    max_loop_steps: int = 5
    max_elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.max_model_calls < 1:
            raise ValueError("max_model_calls must be at least 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be non-negative")
        if self.max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        if self.max_loop_steps < 1:
            raise ValueError("max_loop_steps must be at least 1")
        if self.max_elapsed_seconds is not None and self.max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds must be positive when configured")

    @classmethod
    def from_max_loops(
        cls,
        max_loops: int,
        *,
        max_tool_calls: int | None = None,
        max_failures: int = 2,
        max_elapsed_seconds: float | None = None,
    ) -> "ExecutionPolicy":
        if max_loops < 1:
            raise ValueError("max_loops must be at least 1")
        return cls(
            max_model_calls=max_loops,
            max_tool_calls=max_tool_calls if max_tool_calls is not None else max(8, max_loops * 4),
            max_failures=max_failures,
            max_loop_steps=max_loops,
            max_elapsed_seconds=max_elapsed_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_failures": self.max_failures,
            "max_loop_steps": self.max_loop_steps,
            "max_elapsed_seconds": self.max_elapsed_seconds,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ExecutionPolicy":
        if isinstance(value, ExecutionPolicy):
            return value
        if not isinstance(value, dict):
            return cls()
        return cls(
            max_model_calls=_positive_int(value.get("max_model_calls"), default=5),
            max_tool_calls=_non_negative_int(value.get("max_tool_calls"), default=20),
            max_failures=_positive_int(value.get("max_failures"), default=2),
            max_loop_steps=_positive_int(value.get("max_loop_steps"), default=5),
            max_elapsed_seconds=_positive_float_or_none(value.get("max_elapsed_seconds")),
        )


@dataclass(frozen=True)
class ExecutionLimit:
    reason: ExecutionStopReason
    message: str
    detail: dict[str, Any]


def policy_from_state(state: dict[str, Any]) -> ExecutionPolicy:
    raw_policy = state.get("execution_policy")
    if isinstance(raw_policy, dict):
        return ExecutionPolicy.from_dict(raw_policy)

    # Checkpoints created before R2 only contain the former loop limit. Keep
    # their historical bound instead of silently assigning a new fixed default
    # when they are resumed after an upgrade.
    return ExecutionPolicy.from_max_loops(_positive_int(state.get("max_loops"), default=5))


def elapsed_seconds(state: dict[str, Any]) -> float:
    started_at = state.get("execution_started_at")
    if not isinstance(started_at, int | float):
        return 0.0
    return max(0.0, time.time() - float(started_at))


def model_call_limit(state: dict[str, Any]) -> ExecutionLimit | None:
    policy = policy_from_state(state)
    elapsed_limit = _elapsed_limit(state, policy)
    if elapsed_limit is not None:
        return elapsed_limit
    model_calls = _non_negative_int(state.get("model_calls"), default=0)
    if model_calls >= policy.max_model_calls:
        return ExecutionLimit(
            reason=ExecutionStopReason.BUDGET_EXHAUSTED,
            message=f"Execution budget exhausted: maximum model calls ({policy.max_model_calls}) reached.",
            detail={
                "limit": "max_model_calls",
                "limit_value": policy.max_model_calls,
                "observed_value": model_calls,
            },
        )
    failures = _non_negative_int(state.get("failure_count"), default=0)
    if failures >= policy.max_failures:
        return ExecutionLimit(
            reason=ExecutionStopReason.REPEATED_FAILURE,
            message=f"Execution stopped after {failures} tool failure(s).",
            detail={
                "limit": "max_failures",
                "limit_value": policy.max_failures,
                "observed_value": failures,
            },
        )
    return None


def tool_call_limit(state: dict[str, Any], *, requested_calls: int) -> ExecutionLimit | None:
    policy = policy_from_state(state)
    elapsed_limit = _elapsed_limit(state, policy)
    if elapsed_limit is not None:
        return elapsed_limit
    tool_calls = _non_negative_int(state.get("tool_calls"), default=0)
    projected_calls = tool_calls + requested_calls
    if projected_calls > policy.max_tool_calls:
        return ExecutionLimit(
            reason=ExecutionStopReason.BUDGET_EXHAUSTED,
            message=(
                "Execution budget exhausted: requested tool calls would exceed "
                f"the limit ({policy.max_tool_calls})."
            ),
            detail={
                "limit": "max_tool_calls",
                "limit_value": policy.max_tool_calls,
                "observed_value": tool_calls,
                "requested_value": requested_calls,
            },
        )
    return None


def loop_step_limit(state: dict[str, Any], *, next_loop_index: int) -> ExecutionLimit | None:
    policy = policy_from_state(state)
    elapsed_limit = _elapsed_limit(state, policy)
    if elapsed_limit is not None:
        return elapsed_limit
    if next_loop_index > policy.max_loop_steps:
        return ExecutionLimit(
            reason=ExecutionStopReason.BUDGET_EXHAUSTED,
            message=f"Execution budget exhausted: maximum loop steps ({policy.max_loop_steps}) reached.",
            detail={
                "limit": "max_loop_steps",
                "limit_value": policy.max_loop_steps,
                "observed_value": next_loop_index - 1,
            },
        )
    return None


def execution_summary(state: dict[str, Any], *, final_answer: str | None = None) -> dict[str, Any]:
    """Build an inspectable, deterministic task-execution summary.

    The summary deliberately cites tool observations and residual risks rather
    than using an LLM to assert that work is complete.
    """

    observations = [item for item in state.get("observations", []) if isinstance(item, dict)]
    stop_reason = _stop_reason_from_state(state, final_answer=final_answer)
    evidence = [
        {
            "tool_call_id": observation.get("tool_call_id"),
            "tool_name": observation.get("tool_name"),
            "summary": observation.get("summary"),
            "artifact_refs": list(observation.get("artifact_refs") or []),
        }
        for observation in observations
        if observation.get("ok") is True
    ]
    residual_risks: list[dict[str, Any]] = [
        {
            "category": observation.get("error_category") or "tool_execution_error",
            "tool_call_id": observation.get("tool_call_id"),
            "tool_name": observation.get("tool_name"),
            "summary": observation.get("summary"),
            "retryable": bool(observation.get("retryable")),
        }
        for observation in observations
        if observation.get("ok") is False
    ]
    if final_answer is not None and not evidence and not residual_risks:
        residual_risks.append(
            {
                "category": "no_tool_evidence",
                "summary": "The final answer is model-generated; no tool-backed observation was produced.",
                "retryable": False,
            }
        )
    if stop_reason not in {ExecutionStopReason.COMPLETED.value, ExecutionStopReason.POLICY_BLOCKED.value}:
        residual_risks.append(
            {
                "category": stop_reason,
                "summary": str(state.get("stop_message") or "Execution did not reach a completed answer."),
                "retryable": stop_reason == ExecutionStopReason.ENVIRONMENT_FAILURE.value,
            }
        )
    completion_claimed = final_answer is not None
    return {
        "stop_reason": stop_reason,
        "stop_detail": _safe_dict(state.get("stop_detail")),
        "policy": policy_from_state(state).to_dict(),
        "metrics": {
            "model_calls": _non_negative_int(state.get("model_calls"), default=0),
            "tool_calls": _non_negative_int(state.get("tool_calls"), default=0),
            "failure_count": _non_negative_int(state.get("failure_count"), default=0),
            "loop_index": _positive_int(state.get("loop_index"), default=1),
            "elapsed_seconds": round(elapsed_seconds(state), 3),
        },
        "evidence": evidence,
        "residual_risks": residual_risks,
        "completion": {
            # A final model answer is a claim that this execution slice has
            # finished. Evidence and risk counts below are derived facts, not
            # another model judgement about whether the answer is correct.
            "claimed": completion_claimed,
            "evidence_count": len(evidence),
            "residual_risk_count": len(residual_risks),
        },
    }


def _elapsed_limit(state: dict[str, Any], policy: ExecutionPolicy) -> ExecutionLimit | None:
    if policy.max_elapsed_seconds is None:
        return None
    elapsed = elapsed_seconds(state)
    if elapsed <= policy.max_elapsed_seconds:
        return None
    return ExecutionLimit(
        reason=ExecutionStopReason.BUDGET_EXHAUSTED,
        message=(
            "Execution budget exhausted: elapsed time "
            f"({elapsed:.3f}s) exceeded the limit ({policy.max_elapsed_seconds:.3f}s)."
        ),
        detail={
            "limit": "max_elapsed_seconds",
            "limit_value": policy.max_elapsed_seconds,
            "observed_value": round(elapsed, 3),
        },
    )


def _stop_reason_from_state(state: dict[str, Any], *, final_answer: str | None) -> str:
    value = state.get("stop_reason")
    if isinstance(value, ExecutionStopReason):
        return value.value
    if isinstance(value, str) and value:
        return value
    if final_answer is not None:
        return ExecutionStopReason.COMPLETED.value
    return ExecutionStopReason.ENVIRONMENT_FAILURE.value


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _non_negative_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default


def _positive_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value > 0:
        return float(value)
    return None


def _safe_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}

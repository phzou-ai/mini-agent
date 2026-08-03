from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .models import MainAgentRequest, MessageRecord, RouteDecisionKind, TaskStatus
from .router_classifier import (
    DirectModelRouterModelClient,
    RouterModelClient,
    RouterModelDecision,
    RouterRawJsonClient,
)
from .store import MainAgentStore


@dataclass(frozen=True)
class MainAgentRouteDecision:
    kind: RouteDecisionKind
    reason: str
    confidence: float | None = None
    target_agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MainAgentRouter(Protocol):
    def decide(
        self,
        *,
        request: MainAgentRequest,
        context_id: str,
        input_message_id: str,
        messages: list[MessageRecord],
        store: MainAgentStore,
    ) -> MainAgentRouteDecision:
        """Return a protocol-independent route decision for one user message."""


class DefaultMainAgentRouter:
    def __init__(self, router_model: RouterModelClient | None = None, *, confidence_threshold: float = 0.65) -> None:
        self.router_model = router_model
        self.confidence_threshold = confidence_threshold

    def decide(
        self,
        *,
        request: MainAgentRequest,
        context_id: str,
        input_message_id: str,
        messages: list[MessageRecord],
        store: MainAgentStore,
    ) -> MainAgentRouteDecision:
        metadata = request.metadata
        execution_mode = str(metadata.get("executionMode") or "auto")
        explicit = _explicit_route(metadata)
        if explicit is not None:
            if explicit == RouteDecisionKind.REMOTE_AGENT:
                target_agent_id = _target_agent_id(metadata)
                if target_agent_id is None:
                    raise ValueError("remote_agent route requires metadata.targetAgentId")
                return MainAgentRouteDecision(
                    kind=RouteDecisionKind.REMOTE_AGENT,
                    reason=_reason(metadata, fallback="metadata requested remote agent"),
                    confidence=_confidence(metadata),
                    target_agent_id=target_agent_id,
                    metadata=_decision_metadata("explicit", executionMode=execution_mode),
                )
            return MainAgentRouteDecision(
                kind=explicit,
                reason=_reason(metadata, fallback=f"metadata requested {explicit.value}"),
                confidence=_confidence(metadata),
                metadata=_decision_metadata("explicit", executionMode=execution_mode),
            )

        if execution_mode == "message":
            return MainAgentRouteDecision(
                kind=RouteDecisionKind.LOCAL_MESSAGE,
                reason=_reason(metadata, fallback="executionMode requested message"),
                confidence=_confidence(metadata),
                metadata=_decision_metadata("explicit", executionMode=execution_mode),
            )
        if execution_mode == "task":
            return MainAgentRouteDecision(
                kind=RouteDecisionKind.LOCAL_TASK,
                reason=_reason(metadata, fallback="executionMode requested task"),
                confidence=_confidence(metadata),
                metadata=_decision_metadata("explicit", executionMode=execution_mode),
            )
        if execution_mode != "auto":
            raise ValueError(f"unsupported executionMode: {execution_mode}")

        hard_signal = _hard_signal_route(metadata, store=store)
        if hard_signal is not None:
            return hard_signal

        if self.router_model is not None:
            model_decision = self.router_model.classify(
                request=request,
                messages=messages,
                registered_agents=store.list_registered_agents(enabled_only=True),
            )
            if (
                model_decision.confidence is not None
                and model_decision.confidence < self.confidence_threshold
                and model_decision.kind != RouteDecisionKind.LOCAL_MESSAGE
            ):
                return _fallback_local_message(
                    reason="router model confidence below threshold",
                    confidence=model_decision.confidence,
                    metadata={
                        "source": "fallback",
                        "executionMode": execution_mode,
                        "fallbackReason": "low_confidence",
                        "modelRoute": model_decision.kind.value,
                        "modelReason": model_decision.reason,
                        "confidenceThreshold": self.confidence_threshold,
                    },
                )
            if model_decision.kind == RouteDecisionKind.REMOTE_AGENT:
                target_agent_id = model_decision.target_agent_id
                if target_agent_id is None or store.get_registered_agent(target_agent_id) is None:
                    return _fallback_local_message(
                        reason="router model selected unknown remote agent",
                        confidence=model_decision.confidence,
                        metadata={
                            "source": "fallback",
                            "fallbackReason": "unknown_remote_agent",
                            "modelRoute": model_decision.kind.value,
                            "modelTargetAgentId": target_agent_id,
                            "modelReason": model_decision.reason,
                        },
                    )
            return MainAgentRouteDecision(
                kind=model_decision.kind,
                reason=model_decision.reason,
                confidence=model_decision.confidence,
                target_agent_id=model_decision.target_agent_id,
                metadata={
                    **_decision_metadata("model", executionMode=execution_mode),
                    **model_decision.metadata,
                },
            )

        return _fallback_local_message(reason="auto fallback to local message")


def _explicit_route(metadata: dict[str, object]) -> RouteDecisionKind | None:
    route = metadata.get("route")
    if route == "remote_agent":
        return RouteDecisionKind.REMOTE_AGENT
    if route in {"local_message", "local_task"}:
        return RouteDecisionKind(str(route))
    return None


def _target_agent_id(metadata: dict[str, object]) -> str | None:
    for key in ("targetAgentId", "target_agent_id", "remoteAgentId", "remote_agent_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _hard_signal_route(metadata: dict[str, object], *, store: MainAgentStore) -> MainAgentRouteDecision | None:
    task_id = _metadata_string(metadata, "taskId", "task_id", "localTaskId", "local_task_id")
    if task_id is not None:
        task = store.get_task(task_id)
        if task is not None and task.status not in {TaskStatus.COMPLETED, TaskStatus.CANCELED, TaskStatus.FAILED}:
            return MainAgentRouteDecision(
                kind=RouteDecisionKind.LOCAL_TASK,
                reason=f"auto route continues active task: {task_id}",
                confidence=1.0,
                metadata=_decision_metadata(
                    "hard_signal",
                    executionMode="auto",
                    taskId=task_id,
                    signal="active_task",
                ),
            )

    intent = _metadata_string(metadata, "taskAction", "task_action", "action")
    if intent in {"cancel", "retry", "resume", "approve"}:
        return MainAgentRouteDecision(
            kind=RouteDecisionKind.LOCAL_TASK,
            reason=f"auto route matched task lifecycle action: {intent}",
            confidence=1.0,
            metadata=_decision_metadata(
                "hard_signal",
                executionMode="auto",
                signal="task_lifecycle_action",
                taskAction=intent,
            ),
        )
    return None


def _metadata_string(metadata: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _reason(metadata: dict[str, object], *, fallback: str) -> str:
    value = metadata.get("routeReason")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _confidence(metadata: dict[str, object]) -> float | None:
    value = metadata.get("routeConfidence")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _decision_metadata(source: str, **values: object) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": source}
    for key, value in values.items():
        if value is not None:
            metadata[key] = value
    return metadata


def _fallback_local_message(
    *,
    reason: str,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> MainAgentRouteDecision | RouterModelDecision:
    return MainAgentRouteDecision(
        kind=RouteDecisionKind.LOCAL_MESSAGE,
        reason=reason,
        confidence=confidence,
        metadata=metadata or _decision_metadata("fallback", executionMode="auto"),
    )

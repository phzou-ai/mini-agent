from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import ValidationError

from vermay.errors import InvalidRequestError, InvalidSessionStateError, TaskNotFoundError
from vermay.main_agent import (
    LocalMessageDelta,
    LocalMessageResult,
    LocalTaskResult,
    MainAgentCore,
    MainAgentRequest,
    MessageRole,
    RemoteAgentResult,
    RouteDecisionKind,
)
from vermay.main_agent.models import RegisteredAgentRecord
from vermay.main_agent.models import is_terminal_task_status
from vermay.main_agent.projection import (
    task_event_to_a2a_artifact_update,
    task_event_to_a2a_status_update,
    task_to_a2a_payload,
)

from .agent_card import A2AAgentCardConfig, build_agent_card
from .models import A2AJsonRpcMessageSendRequest, A2AMessage, A2ASendMessageRequest


@dataclass(frozen=True)
class A2AAdapterConfig:
    agent_card: A2AAgentCardConfig = field(default_factory=A2AAgentCardConfig)


@dataclass(frozen=True)
class A2AEventBatch:
    last_event_id: int
    events: list[dict[str, Any]]


class A2AAdapter:
    def __init__(
        self,
        *,
        config: A2AAdapterConfig | None = None,
        main_agent_core: MainAgentCore | None = None,
    ) -> None:
        self.config = config or A2AAdapterConfig()
        self.main_agent_core = main_agent_core

    def get_agent_card(self) -> dict[str, Any]:
        card = build_agent_card(self.config.agent_card)
        if self.main_agent_core is None:
            return card

        metadata = dict(card.get("metadata") or {})
        metadata["registeredAgents"] = [
            _registered_agent_summary(agent)
            for agent in self.main_agent_core.store.list_registered_agents(enabled_only=True)
        ]
        card["metadata"] = metadata
        return card

    def send_message(self, request: A2ASendMessageRequest, *, wait: bool = True) -> dict[str, Any]:
        core = self._require_main_agent_core("A2A message/send")
        # Path-style bindings are only a transport compatibility surface. They
        # must use the same lifecycle as the JSON-RPC binding.
        _ = wait  # MainAgentCore owns task scheduling and continuation semantics.
        message = request.message
        _validate_jsonrpc_user_message(message)
        metadata = dict(message.metadata)
        metadata.update(request.metadata)
        if message.task_id is not None:
            task = core.submit_task_input(
                message.task_id,
                _main_agent_request(message, metadata=metadata),
            )
            return _main_task_payload(task, store=core.store)
        result = core.handle_message(_main_agent_request(message, metadata=metadata))
        return _main_agent_result_payload(result, store=core.store)

    def send_message_payload(self, payload: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        if _is_jsonrpc_message_send(payload):
            return self._send_jsonrpc_message(payload)
        return self.send_message(A2ASendMessageRequest.model_validate(payload), wait=wait)

    def stream_message_payload(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        core = self._require_main_agent_core("A2A message/stream")
        if not _is_jsonrpc_message_send(payload):
            yield self.send_message_payload(payload)
            return

        request_id = payload.get("id")
        params = _jsonrpc_params(payload)
        message = _jsonrpc_message(params)
        _validate_jsonrpc_user_message(message)
        metadata = _merged_metadata(params, message=message)
        if message.task_id is not None:
            task = core.submit_task_input(
                message.task_id,
                _main_agent_request(message, metadata=metadata),
            )
            yield _jsonrpc_success(
                request_id,
                _main_task_payload(task, store=core.store),
            )
            return
        for result in core.stream_message(_main_agent_request(message, metadata=metadata)):
            if isinstance(result, LocalMessageDelta):
                yield _jsonrpc_success(request_id, _local_message_delta_payload(result))
            else:
                yield {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _main_agent_result_payload(result, store=core.store),
                }

    def _send_jsonrpc_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        core = self._require_main_agent_core("A2A message/send")
        params = _jsonrpc_params(payload)
        message = _jsonrpc_message(params)
        _validate_jsonrpc_user_message(message)
        metadata = _merged_metadata(params, message=message)
        if message.task_id is not None:
            task = core.submit_task_input(
                message.task_id,
                _main_agent_request(message, metadata=metadata),
            )
            return _jsonrpc_success(
                payload.get("id"),
                _main_task_payload(task, store=core.store),
            )
        result = core.handle_message(_main_agent_request(message, metadata=metadata))
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": _main_agent_result_payload(result, store=core.store),
        }

    def get_task(self, task_id: str) -> dict[str, Any]:
        core = self._require_main_agent_core("A2A tasks/get")
        task = core.get_task(task_id, refresh_remote=True)
        if task is None:
            raise TaskNotFoundError(task_id)
        return _jsonrpc_success(
            f"task-get-{task_id}",
            _main_task_payload(task, store=core.store),
        )

    def cancel_task(self, task_id: str, *, reason: str | None = None) -> dict[str, Any]:
        core = self._require_main_agent_core("A2A tasks/cancel")
        task = core.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if is_terminal_task_status(task.status):
            raise InvalidSessionStateError(f"task is terminal and cannot be canceled: {task_id}")
        updated = core.cancel_task(task_id, reason=reason)
        return _jsonrpc_success(f"cancel-{task_id}", task_to_a2a_payload(updated))

    def resume_task(self, task_id: str, *, approved: bool, reason: str | None = None) -> dict[str, Any]:
        core = self._require_main_agent_core("A2A tasks/resume")
        task = core.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        updated = core.resume_task(task_id, approved=approved, reason=reason)
        return _jsonrpc_success(f"resume-{task_id}", task_to_a2a_payload(updated))

    def project_task_events(self, task_id: str, *, after_event_id: int = 0) -> list[dict[str, Any]]:
        core = self._require_main_agent_core("A2A task event projection")
        task = core.get_task(task_id, refresh_remote=True)
        if task is None:
            raise TaskNotFoundError(task_id)
        return [
            payload
            for event in core.store.list_task_events(task_id, after_event_id=after_event_id)
            if (payload := self._project_main_agent_task_event(event, task=task)) is not None
        ]

    def wait_for_task_events(
        self,
        task_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> A2AEventBatch:
        core = self._require_main_agent_core("A2A task event subscription")
        task = core.get_task(task_id, refresh_remote=True)
        if task is None:
            raise TaskNotFoundError(task_id)
        events = core.store.wait_for_task_events(
            task_id,
            after_event_id=after_event_id,
            timeout_seconds=timeout_seconds,
        )
        return A2AEventBatch(
            last_event_id=_last_main_event_id(events, fallback=after_event_id),
            events=[
                _jsonrpc_success(f"event-{event.event_id}", payload)
                for event in events
                if (payload := self._project_main_agent_task_event(event, task=task)) is not None
            ],
        )

    def is_main_agent_task(self, task_id: str) -> bool:
        return self._get_main_agent_task(task_id) is not None

    def _get_main_agent_task(self, task_id: str):
        if self.main_agent_core is None:
            return None
        return self.main_agent_core.store.get_task(task_id)

    def _require_main_agent_core(self, operation: str) -> MainAgentCore:
        if self.main_agent_core is None:
            raise InvalidRequestError(f"{operation} requires MainAgentCore.")
        return self.main_agent_core

    def _project_main_agent_task_event(self, event, *, task):
        artifact_id = event.payload.get("artifact_id")
        artifact = self.main_agent_core.store.get_artifact(str(artifact_id)) if artifact_id else None
        return task_event_to_a2a_artifact_update(event, task=task, artifact=artifact) or task_event_to_a2a_status_update(
            event,
            task=task,
        )

def _validate_jsonrpc_user_message(message: A2AMessage) -> None:
    if message.role not in {None, "user"}:
        raise InvalidRequestError("A2A message role must be 'user'.")
    text_parts = [str(part["text"]).strip() for part in message.parts if isinstance(part.get("text"), str)]
    if not any(text_parts):
        raise InvalidRequestError("A2A message must include at least one text part.")


def _is_jsonrpc_message_send(payload: dict[str, Any]) -> bool:
    return payload.get("jsonrpc") == "2.0" or payload.get("method") == "message/send"


def _jsonrpc_params(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        request = A2AJsonRpcMessageSendRequest.model_validate(payload)
    except ValidationError as exc:
        raise InvalidRequestError(_jsonrpc_validation_message(exc)) from exc
    return request.params


def _jsonrpc_message(params: dict[str, Any]) -> A2AMessage:
    raw_message = params.get("message")
    if not isinstance(raw_message, dict):
        raise InvalidRequestError("JSON-RPC params.message must be an object.")
    try:
        return A2AMessage.model_validate(raw_message)
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first_error.get("loc", ())) or "message"
        error_type = str(first_error.get("type") or "invalid")
        raise InvalidRequestError(f"JSON-RPC params.message.{location} is invalid: {error_type}") from exc


def _jsonrpc_validation_message(exc: ValidationError) -> str:
    first_error = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first_error.get("loc", ())) or "request"
    error_type = str(first_error.get("type") or "invalid")
    if location == "jsonrpc":
        return "JSON-RPC request jsonrpc must be '2.0'."
    if location == "method":
        return "JSON-RPC method must be 'message/send'."
    if location == "params":
        return "JSON-RPC params must be an object."
    return f"JSON-RPC {location} is invalid: {error_type}"


def _merged_metadata(
    params: dict[str, Any],
    *,
    message: A2AMessage | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(message.metadata) if message is not None else {}
    request_metadata = params.get("metadata")
    if isinstance(request_metadata, dict):
        metadata.update(request_metadata)
    configuration = params.get("configuration")
    if isinstance(configuration, dict) and "executionMode" in configuration and "executionMode" not in metadata:
        metadata["executionMode"] = configuration["executionMode"]
    return metadata


def _main_agent_request(
    message: A2AMessage,
    *,
    metadata: dict[str, Any],
) -> MainAgentRequest:
    return MainAgentRequest(
        context_id=message.context_id,
        message_id=message.message_id,
        role=MessageRole(str(message.role or "user")),
        parts=message.parts,
        metadata=metadata,
    )


def _main_task_payload(task, *, store) -> dict[str, Any]:
    return task_to_a2a_payload(
        task,
        input_request=store.get_pending_input_request(task.task_id),
    )


def _main_agent_result_payload(result: LocalMessageResult | LocalTaskResult | RemoteAgentResult, *, store) -> dict[str, Any]:
    if isinstance(result, LocalMessageResult):
        return {
            "kind": "message",
            "role": "agent",
            "messageId": result.message_id,
            "contextId": result.context_id,
            "parts": result.parts,
            "metadata": {
                "localContextId": result.context_id,
                "localMessageId": result.message_id,
                "inputMessageId": result.input_message_id,
                "routeDecisionId": result.route_decision_id,
                "routeKind": RouteDecisionKind.LOCAL_MESSAGE.value,
                "partial": False,
                "append": False,
                "final": True,
            },
        }
    if isinstance(result, LocalTaskResult):
        task = store.get_task(result.task_id)
        if task is None:
            raise TaskNotFoundError(result.task_id)
        payload = _main_task_payload(task, store=store)
        payload["metadata"].update(
            {
                "routeDecisionId": result.route_decision_id,
                "routeKind": RouteDecisionKind.LOCAL_TASK.value,
            }
        )
        return payload
    if isinstance(result, RemoteAgentResult):
        if result.message_id is not None:
            message = store.get_message(result.message_id)
            if message is None:
                raise TaskNotFoundError(result.message_id)
            return {
                "kind": "message",
                "role": "agent",
                "messageId": message.message_id,
                "contextId": message.context_id,
                "parts": message.parts,
                "metadata": {
                    "localContextId": message.context_id,
                    "localMessageId": message.message_id,
                    "inputMessageId": result.input_message_id,
                    "routeDecisionId": result.route_decision_id,
                    "routeKind": RouteDecisionKind.REMOTE_AGENT.value,
                    "remoteAgentId": result.target_agent_id,
                    "delegationId": result.delegation_id,
                },
            }
        if result.task_id is not None:
            task = store.get_task(result.task_id)
            if task is None:
                raise TaskNotFoundError(result.task_id)
            payload = _main_task_payload(task, store=store)
            payload["metadata"].update(
                {
                    "routeDecisionId": result.route_decision_id,
                    "routeKind": RouteDecisionKind.REMOTE_AGENT.value,
                    "remoteAgentId": result.target_agent_id,
                    "delegationId": result.delegation_id,
                }
            )
            return payload
        raise InvalidRequestError("remote_agent result did not include a message or task.")
    raise InvalidRequestError("unsupported main agent result.")


def _local_message_delta_payload(delta: LocalMessageDelta) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": delta.message_id,
        "contextId": delta.context_id,
        "parts": [{"kind": "text", "text": delta.text}],
        "metadata": {
            "localContextId": delta.context_id,
            "localMessageId": delta.message_id,
            "inputMessageId": delta.input_message_id,
            "routeDecisionId": delta.route_decision_id,
            "routeKind": RouteDecisionKind.LOCAL_MESSAGE.value,
            "partial": True,
            "append": True,
            "sequence": delta.sequence,
        },
    }


def _jsonrpc_success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _last_main_event_id(events: list[Any], *, fallback: int) -> int:
    if not events:
        return fallback
    return max(event.event_id for event in events)


def _registered_agent_summary(agent: RegisteredAgentRecord) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "agentId": agent.agent_id,
        "name": agent.name,
        "enabled": agent.enabled,
    }
    keywords = _string_list(agent.metadata.get("keywords"))
    if keywords:
        summary["keywords"] = keywords
    skill_tags = _agent_card_skill_tags(agent.card_json)
    if skill_tags:
        summary["skillTags"] = skill_tags
    skill_ids = _agent_card_skill_ids(agent.card_json)
    if skill_ids:
        summary["skillIds"] = skill_ids
    return summary


def _agent_card_skill_tags(card_json: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for skill in _agent_card_skills(card_json):
        tags.extend(_string_list(skill.get("tags")))
    return _dedupe_strings(tags)


def _agent_card_skill_ids(card_json: dict[str, Any]) -> list[str]:
    return _dedupe_strings(
        str(skill.get("id")).strip()
        for skill in _agent_card_skills(card_json)
        if skill.get("id") is not None
    )


def _agent_card_skills(card_json: dict[str, Any]) -> list[dict[str, Any]]:
    skills = card_json.get("skills")
    if not isinstance(skills, list):
        return []
    return [skill for skill in skills if isinstance(skill, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result

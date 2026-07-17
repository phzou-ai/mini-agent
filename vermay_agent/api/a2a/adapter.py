from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable
from uuid import uuid4

from pydantic import ValidationError

from vermay_agent.api.task_contract import ARTIFACT_TASK_EVENT_TYPES
from vermay_agent.errors import InvalidRequestError, InvalidSessionStateError, TaskNotFoundError
from vermay_agent.main_agent import (
    LocalMessageDelta,
    LocalMessageResult,
    LocalTaskResult,
    MainAgentCore,
    MainAgentRequest,
    MessageRole,
    RemoteAgentResult,
    RouteDecisionKind,
)
from vermay_agent.main_agent.models import RegisteredAgentRecord, TaskStatus as MainAgentTaskStatus
from vermay_agent.main_agent.models import is_terminal_task_status
from vermay_agent.main_agent.projection import (
    task_event_to_a2a_artifact_update,
    task_event_to_a2a_status_update,
    task_to_a2a_payload,
)

from ..service import AgentService
from ..session_store import SessionRecord, TaskEventRecord, TaskRecord
from .agent_card import A2AAgentCardConfig, build_agent_card
from .models import A2AJsonRpcMessageSendRequest, A2AMessage, A2ASendMessageRequest
from .projection import A2AProjectionKind, project_task, project_task_artifact_event, project_task_event


@dataclass(frozen=True)
class A2AAdapterConfig:
    agent_card: A2AAgentCardConfig = field(default_factory=A2AAgentCardConfig)
    message_metadata_allowlist: frozenset[str] = frozenset({"tenant", "client"})


@dataclass(frozen=True)
class A2AEventBatch:
    last_event_id: int
    events: list[dict[str, Any]]


class A2AAdapter:
    def __init__(
        self,
        *,
        service: AgentService,
        config: A2AAdapterConfig | None = None,
        main_agent_core: MainAgentCore | None = None,
    ) -> None:
        self.service = service
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
        user_input = _extract_user_input(request.message)
        session = self._resolve_session(request.message.context_id, request=request)
        task = self.service.start_task(
            session.session_id,
            user_input,
            task_id=request.message.task_id,
            wait=wait,
        )
        return self.project_task(task)

    def send_message_payload(self, payload: dict[str, Any], *, wait: bool = True) -> dict[str, Any]:
        if _is_jsonrpc_message_send(payload):
            return self._send_jsonrpc_message(payload)
        return self.send_message(A2ASendMessageRequest.model_validate(payload), wait=wait)

    def stream_message_payload(self, payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
        if not _is_jsonrpc_message_send(payload):
            yield self.send_message_payload(payload)
            return
        if self.main_agent_core is None:
            raise InvalidRequestError("A2A JSON-RPC message/stream requires main agent core.")

        request_id = payload.get("id")
        params = _jsonrpc_params(payload)
        message = _jsonrpc_message(params)
        _validate_jsonrpc_user_message(message)
        metadata = _merged_metadata(params)
        for result in self.main_agent_core.stream_message(
            MainAgentRequest(
                context_id=message.context_id,
                message_id=message.message_id,
                role=MessageRole(str(message.role or "user")),
                parts=message.parts,
                metadata=metadata,
            )
        ):
            if isinstance(result, LocalMessageDelta):
                yield _jsonrpc_success(request_id, _local_message_delta_payload(result))
            else:
                yield {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": _main_agent_result_payload(result, store=self.main_agent_core.store),
                }

    def _send_jsonrpc_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.main_agent_core is None:
            raise InvalidRequestError("A2A JSON-RPC message/send requires main agent core.")
        params = _jsonrpc_params(payload)
        message = _jsonrpc_message(params)
        _validate_jsonrpc_user_message(message)
        metadata = _merged_metadata(params)
        result = self.main_agent_core.handle_message(
            MainAgentRequest(
                context_id=message.context_id,
                message_id=message.message_id,
                role=MessageRole(str(message.role or "user")),
                parts=message.parts,
                metadata=metadata,
            )
        )
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id"),
            "result": _main_agent_result_payload(result, store=self.main_agent_core.store),
        }

    def get_task(self, task_id: str) -> dict[str, Any]:
        main_task = self._get_main_agent_task(task_id)
        if main_task is not None:
            main_task = self._sync_remote_proxy_task(main_task)
            return _jsonrpc_success(f"task-get-{task_id}", task_to_a2a_payload(main_task))
        task = self.service.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return self.project_task(task)

    def cancel_task(self, task_id: str, *, reason: str | None = None) -> dict[str, Any]:
        main_task = self._get_main_agent_task(task_id)
        if main_task is not None:
            if is_terminal_task_status(main_task.status):
                raise InvalidSessionStateError(f"task is terminal and cannot be canceled: {task_id}")
            delegation = self.main_agent_core.store.get_delegated_task_by_local_task_id(task_id)
            if delegation is not None:
                updated = self._cancel_remote_proxy_task(main_task, delegation=delegation, reason=reason)
                return _jsonrpc_success(f"cancel-{task_id}", task_to_a2a_payload(updated))
            updated = self.main_agent_core.cancel_task(task_id, reason=reason)
            return _jsonrpc_success(f"cancel-{task_id}", task_to_a2a_payload(updated))
        task = self.service.cancel_task(task_id, reason=reason)
        return self.project_task(task)

    def resume_task(self, task_id: str, *, approved: bool, reason: str | None = None) -> dict[str, Any]:
        main_task = self._get_main_agent_task(task_id)
        if main_task is not None:
            delegation = self.main_agent_core.store.get_delegated_task_by_local_task_id(task_id)
            if delegation is not None:
                raise InvalidSessionStateError(f"delegated task resume is not supported yet: {task_id}")
            updated = self.main_agent_core.resume_task(task_id, approved=approved, reason=reason)
            return _jsonrpc_success(f"resume-{task_id}", task_to_a2a_payload(updated))
        task = self.service.resume_task(task_id, approved=approved, reason=reason)
        return self.project_task(task)

    def project_task(self, task: TaskRecord) -> dict[str, Any]:
        session = self.service.get_session(task.session_id)
        artifacts = self.service.list_task_artifacts(task.task_id)
        projection = project_task(
            task,
            context_id=session.context_id if session is not None else None,
            artifacts=artifacts,
        )
        if projection.payload is None:
            raise RuntimeError(f"failed to project task: {task.task_id}")
        return projection.payload

    def project_task_events(self, task_id: str, *, after_event_id: int = 0) -> list[dict[str, Any]]:
        events = [event for event in self.service.list_task_events(task_id) if event.event_id > after_event_id]
        return list(self._project_events(events))

    def wait_for_task_events(
        self,
        task_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> A2AEventBatch:
        main_task = self._get_main_agent_task(task_id)
        if main_task is not None:
            main_task = self._sync_remote_proxy_task(main_task)
            events = self.main_agent_core.store.wait_for_task_events(
                task_id,
                after_event_id=after_event_id,
                timeout_seconds=timeout_seconds,
            )
            projected = [
                _jsonrpc_success(
                    f"event-{event.event_id}",
                    payload,
                )
                for event in events
                if (payload := self._project_main_agent_task_event(event, task=main_task)) is not None
            ]
            return A2AEventBatch(last_event_id=_last_main_event_id(events, fallback=after_event_id), events=projected)
        events = self.service.wait_for_task_events(
            task_id,
            after_event_id=after_event_id,
            timeout_seconds=timeout_seconds,
        )
        return A2AEventBatch(
            last_event_id=_last_event_id(events, fallback=after_event_id),
            events=list(self._project_events(events)),
        )

    def is_main_agent_task(self, task_id: str) -> bool:
        return self._get_main_agent_task(task_id) is not None

    def _get_main_agent_task(self, task_id: str):
        if self.main_agent_core is None:
            return None
        return self.main_agent_core.store.get_task(task_id)

    def _sync_remote_proxy_task(self, task):
        delegation = self.main_agent_core.store.get_delegated_task_by_local_task_id(task.task_id)
        if delegation is None or delegation.remote_task_id is None:
            return task
        client = self.main_agent_core.remote_agent_client
        if client is None:
            return task
        agent = self.main_agent_core.store.get_registered_agent(delegation.remote_agent_id)
        if agent is None or not agent.enabled:
            return task
        snapshot = client.get_task(agent=agent, task_id=delegation.remote_task_id)
        return self._apply_remote_task_snapshot(task, delegation=delegation, snapshot=snapshot)

    def _cancel_remote_proxy_task(self, task, *, delegation, reason: str | None):
        client = self.main_agent_core.remote_agent_client
        if client is None:
            raise InvalidRequestError("remote_agent client is not configured")
        if delegation.remote_task_id is None:
            raise InvalidRequestError("delegated task is missing remote task id")
        agent = self.main_agent_core.store.get_registered_agent(delegation.remote_agent_id)
        if agent is None:
            raise InvalidRequestError(f"unknown registered agent: {delegation.remote_agent_id}")
        if not agent.enabled:
            raise InvalidRequestError(f"registered agent is disabled: {delegation.remote_agent_id}")
        snapshot = client.cancel_task(agent=agent, task_id=delegation.remote_task_id, reason=reason)
        return self._apply_remote_task_snapshot(task, delegation=delegation, snapshot=snapshot)

    def _apply_remote_task_snapshot(self, task, *, delegation, snapshot):
        next_status = _remote_status_to_main_status(snapshot.status, fallback=task.status)
        metadata = {
            **delegation.metadata,
            "remoteTaskId": snapshot.task_id,
            "remoteContextId": snapshot.context_id,
            "remoteStatus": snapshot.status,
        }
        if snapshot.raw:
            metadata["lastRemoteSnapshot"] = snapshot.raw
        self.main_agent_core.store.update_delegated_task_status(
            delegation.delegation_id,
            status=snapshot.status or next_status.value,
            metadata=metadata,
        )
        if next_status == MainAgentTaskStatus.COMPLETED:
            task = self._sync_remote_proxy_final_answer(task, delegation=delegation, snapshot=snapshot)
        if next_status == task.status:
            return task
        updated = self.main_agent_core.store.update_task_status(task.task_id, next_status)
        self.main_agent_core.store.append_task_event(
            task_id=task.task_id,
            type="remote_task_status_synced",
            status=next_status,
            payload={
                "remote_agent_id": delegation.remote_agent_id,
                "remote_task_id": snapshot.task_id,
                "remote_context_id": snapshot.context_id,
                "remote_status": snapshot.status,
            },
        )
        return updated

    def _sync_remote_proxy_final_answer(self, task, *, delegation, snapshot):
        parts, remote_artifact_id = _remote_final_artifact(snapshot.artifacts)
        if not parts:
            return task

        store = self.main_agent_core.store
        artifact_id = f"{task.task_id}:remote_final_answer"
        if store.get_artifact(artifact_id) is not None:
            return store.get_task(task.task_id) or task

        with store.transaction():
            assistant_message = store.append_message(
                message_id=f"msg-{uuid4().hex}",
                context_id=task.context_id,
                role=MessageRole.AGENT,
                parts=parts,
                task_id=task.task_id,
                metadata={
                    "routeKind": RouteDecisionKind.REMOTE_AGENT.value,
                    "remoteAgentId": delegation.remote_agent_id,
                    "remoteTaskId": snapshot.task_id,
                    "remoteContextId": snapshot.context_id,
                    "remoteArtifactId": remote_artifact_id,
                    "delegationId": delegation.delegation_id,
                },
            )
            artifact = store.upsert_artifact(
                artifact_id=artifact_id,
                task_id=task.task_id,
                context_id=task.context_id,
                parts=parts,
                metadata={
                    "kind": "final_answer",
                    "source": "remote_agent",
                    "outputMessageId": assistant_message.message_id,
                    "remoteAgentId": delegation.remote_agent_id,
                    "remoteTaskId": snapshot.task_id,
                    "remoteArtifactId": remote_artifact_id,
                },
            )
            store.append_task_event(
                task_id=task.task_id,
                type="task_artifact_created",
                status=MainAgentTaskStatus.COMPLETED,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "kind": "final_answer",
                    "source": "remote_agent",
                    "remote_agent_id": delegation.remote_agent_id,
                    "remote_task_id": snapshot.task_id,
                    "remote_artifact_id": remote_artifact_id,
                },
            )
            return store.set_task_output_message(task.task_id, assistant_message.message_id)

    def _project_main_agent_task_event(self, event, *, task):
        artifact_id = event.payload.get("artifact_id")
        artifact = self.main_agent_core.store.get_artifact(str(artifact_id)) if artifact_id else None
        return task_event_to_a2a_artifact_update(event, task=task, artifact=artifact) or task_event_to_a2a_status_update(
            event,
            task=task,
        )

    def _resolve_session(self, context_id: str | None, *, request: A2ASendMessageRequest) -> SessionRecord:
        if context_id:
            existing = self.service.get_session_by_context_id(context_id)
            if existing is not None:
                return existing
            return self.service.create_session(
                context_id=context_id,
                metadata=_session_metadata(request, allowlist=self.config.message_metadata_allowlist),
            )
        return self.service.create_session(
            metadata=_session_metadata(request, allowlist=self.config.message_metadata_allowlist),
        )

    def _project_events(self, events: Iterable[TaskEventRecord]) -> Iterable[dict[str, Any]]:
        for event in events:
            artifact_payload = _artifact_event_payload(event)
            if artifact_payload is not None:
                artifact = self.service.get_task_artifact_by_a2a_id(
                    task_id=event.task_id,
                    a2a_artifact_id=artifact_payload["a2a_artifact_id"],
                )
                projection = project_task_artifact_event(event, artifact=artifact)
            else:
                projection = project_task_event(event)
            if projection.kind in {A2AProjectionKind.STATUS_UPDATE, A2AProjectionKind.ARTIFACT_UPDATE}:
                if projection.payload is not None:
                    yield projection.payload


def _extract_user_input(message: A2AMessage) -> str:
    if message.role not in {None, "user"}:
        raise InvalidRequestError("A2A message role must be 'user'.")
    text_parts = [str(part["text"]).strip() for part in message.parts if isinstance(part.get("text"), str)]
    text = "\n".join(part for part in text_parts if part)
    if not text:
        raise InvalidRequestError("A2A message must include at least one text part.")
    return text


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


def _merged_metadata(params: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    request_metadata = params.get("metadata")
    if isinstance(request_metadata, dict):
        metadata.update(request_metadata)
    configuration = params.get("configuration")
    if isinstance(configuration, dict) and "executionMode" in configuration and "executionMode" not in metadata:
        metadata["executionMode"] = configuration["executionMode"]
    return metadata


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
        payload = task_to_a2a_payload(task)
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
            payload = task_to_a2a_payload(task)
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


def _session_metadata(request: A2ASendMessageRequest, *, allowlist: frozenset[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"source": "a2a"}
    for source in (request.metadata, request.message.metadata):
        for key in allowlist:
            if key in source:
                metadata[key] = source[key]
    return metadata


def _artifact_event_payload(event: TaskEventRecord) -> dict[str, Any] | None:
    if event.event_type not in ARTIFACT_TASK_EVENT_TYPES:
        return None
    a2a_artifact_id = event.payload.get("a2a_artifact_id")
    if not isinstance(a2a_artifact_id, str) or not a2a_artifact_id:
        return None
    return {"a2a_artifact_id": a2a_artifact_id}


def _last_event_id(events: list[TaskEventRecord], *, fallback: int) -> int:
    if not events:
        return fallback
    return max(event.event_id for event in events)


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


def _remote_status_to_main_status(status: str | None, *, fallback: MainAgentTaskStatus) -> MainAgentTaskStatus:
    if status in {"submitted", "TASK_STATE_SUBMITTED", "created", "queued"}:
        return MainAgentTaskStatus.QUEUED
    if status in {"working", "TASK_STATE_WORKING", "running"}:
        return MainAgentTaskStatus.RUNNING
    if status in {"completed", "TASK_STATE_COMPLETED"}:
        return MainAgentTaskStatus.COMPLETED
    if status in {"canceled", "cancelled", "TASK_STATE_CANCELED"}:
        return MainAgentTaskStatus.CANCELED
    if status in {"failed", "rejected", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}:
        return MainAgentTaskStatus.FAILED
    if status in {"input-required", "TASK_STATE_INPUT_REQUIRED"}:
        return MainAgentTaskStatus.INPUT_REQUIRED
    if status in {"auth-required", "TASK_STATE_AUTH_REQUIRED"}:
        return MainAgentTaskStatus.AUTH_REQUIRED
    return fallback


def _remote_final_artifact(artifacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw_parts = artifact.get("parts")
        if not isinstance(raw_parts, list):
            continue
        parts = [_normalize_remote_part(part) for part in raw_parts]
        parts = [part for part in parts if part is not None]
        if parts:
            return parts, _optional_str(artifact.get("artifactId") or artifact.get("id"))
    return [], None


def _normalize_remote_part(part: object) -> dict[str, Any] | None:
    if not isinstance(part, dict):
        return None
    if isinstance(part.get("text"), str):
        return {"kind": "text", "text": part["text"]}
    if isinstance(part.get("kind"), str):
        return dict(part)
    return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)

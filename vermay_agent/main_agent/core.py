from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol
from uuid import uuid4

from vermay_agent.errors import error_info_from_exception

from .context import recent_messages
from .models import (
    LocalMessageDelta,
    LocalMessageResult,
    LocalTaskResult,
    MainAgentRequest,
    MainAgentResult,
    MainAgentStreamResult,
    MessageRole,
    RemoteAgentResult,
    RouteDecisionKind,
    TaskStatus,
    is_terminal_task_status,
)
from .remote_agent import RemoteAgentClient
from .responder import LocalMessageResponder
from .router import DefaultMainAgentRouter, MainAgentRouteDecision, MainAgentRouter
from .store import MainAgentStore
from .task_runner import LocalTaskRunResult, LocalTaskRunner


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PreparedMessageRoute:
    context_id: str
    input_message_id: str
    route_decision: MainAgentRouteDecision


class TaskSubmitter(Protocol):
    def submit(self, func: Callable[..., object], *args: object) -> object: ...


class MainAgentCore:
    def __init__(
        self,
        *,
        store: MainAgentStore,
        local_message_responder: LocalMessageResponder,
        local_task_runner: LocalTaskRunner | None = None,
        remote_agent_client: RemoteAgentClient | None = None,
        router: MainAgentRouter | None = None,
        task_submitter: TaskSubmitter | None = None,
    ) -> None:
        self.store = store
        self.local_message_responder = local_message_responder
        self.local_task_runner = local_task_runner
        self.remote_agent_client = remote_agent_client
        self.router = router or DefaultMainAgentRouter()
        self.task_submitter = task_submitter
        self._active_task_guard = threading.RLock()
        self._active_task_ids: set[str] = set()

    def handle_message(self, request: MainAgentRequest) -> MainAgentResult:
        prepared = self._prepare_message_route(request)
        context_id = prepared.context_id
        input_message_id = prepared.input_message_id
        route_decision = prepared.route_decision
        if route_decision.kind == RouteDecisionKind.LOCAL_MESSAGE:
            return self._handle_local_message(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            )
        if route_decision.kind == RouteDecisionKind.LOCAL_TASK:
            return self._handle_local_task(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            )
        if route_decision.kind == RouteDecisionKind.REMOTE_AGENT:
            return self._handle_remote_agent(
                context_id=context_id,
                input_message_id=input_message_id,
                request=request,
                route_decision=route_decision,
            )
        raise ValueError(f"unsupported route decision: {route_decision.kind.value}")

    def stream_message(self, request: MainAgentRequest) -> Iterator[MainAgentStreamResult]:
        prepared = self._prepare_message_route(request)
        context_id = prepared.context_id
        input_message_id = prepared.input_message_id
        route_decision = prepared.route_decision
        if route_decision.kind == RouteDecisionKind.LOCAL_MESSAGE:
            yield from self._stream_local_message(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            )
            return
        if route_decision.kind == RouteDecisionKind.LOCAL_TASK:
            yield self._handle_local_task(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            )
            return
        if route_decision.kind == RouteDecisionKind.REMOTE_AGENT:
            yield self._handle_remote_agent(
                context_id=context_id,
                input_message_id=input_message_id,
                request=request,
                route_decision=route_decision,
            )
            return
        raise ValueError(f"unsupported route decision: {route_decision.kind.value}")

    def _prepare_message_route(self, request: MainAgentRequest) -> _PreparedMessageRoute:
        if request.role != MessageRole.USER:
            raise ValueError("main agent request role must be user")
        self._validate_explicit_route_metadata(request.metadata)
        context_id = self._resolve_context_id(request.context_id, title=_title_from_parts(request.parts))
        message_id = request.message_id or _new_id("msg")
        user_message = self.store.append_message(
            message_id=message_id,
            context_id=context_id,
            role=request.role,
            parts=request.parts,
            metadata=request.metadata,
        )
        route_decision = self.router.decide(
            request=request,
            context_id=context_id,
            input_message_id=user_message.message_id,
            messages=recent_messages(self.store, context_id, limit=10),
            store=self.store,
        )
        return _PreparedMessageRoute(
            context_id=context_id,
            input_message_id=user_message.message_id,
            route_decision=route_decision,
        )

    def _validate_explicit_route_metadata(self, metadata: dict) -> None:
        execution_mode = str(metadata.get("executionMode") or "auto")
        if execution_mode not in {"auto", "message", "task"}:
            raise ValueError(f"unsupported executionMode: {execution_mode}")

        if metadata.get("route") != RouteDecisionKind.REMOTE_AGENT.value:
            return

        target_agent_id = _target_agent_id_from_metadata(metadata)
        if target_agent_id is None:
            raise ValueError("remote_agent route requires metadata.targetAgentId")
        agent = self.store.get_registered_agent(target_agent_id)
        if agent is None:
            raise ValueError(f"unknown registered agent: {target_agent_id}")
        if not agent.enabled:
            raise ValueError(f"registered agent is disabled: {target_agent_id}")
        if self.remote_agent_client is None:
            raise ValueError("remote_agent client is not configured")

    def _resolve_context_id(self, context_id: str | None, *, title: str | None = None) -> str:
        if context_id is None:
            context = self.store.create_context(context_id=_new_id("ctx"), title=title)
            return context.context_id
        if self.store.get_context(context_id) is None:
            raise ValueError(f"unknown context: {context_id}")
        return context_id

    def resume_task(self, task_id: str, *, approved: bool, reason: str | None = None):
        if self.store.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        if self.task_submitter is None:
            return self._resume_local_task(task_id, approved=approved, reason=reason)

        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.status not in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
                raise ValueError(f"task is not waiting for approval: {task_id}")
            self.store.append_task_event(
                task_id=task_id,
                type="task_resumed",
                status=task.status,
                payload={"approved": approved, **({"reason": reason} if reason else {})},
            )
            task = self.store.update_task_status(task_id, TaskStatus.QUEUED)
            self.store.append_task_event(task_id=task_id, type="task_queued", status=TaskStatus.QUEUED)
        try:
            self.task_submitter.submit(self._resume_local_task_in_background, task_id, approved, reason)
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)
        return task

    def submit_task_input(self, task_id: str, request: MainAgentRequest):
        if request.role != MessageRole.USER:
            raise ValueError("task input role must be user")
        if not request.parts:
            raise ValueError("task input parts are required")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.assigned_agent_id is not None:
                raise ValueError(f"delegated task input continuation is not supported yet: {task_id}")
            if request.context_id is not None and request.context_id != task.context_id:
                raise ValueError(f"task context mismatch: {task_id}")
            if task.status != TaskStatus.INPUT_REQUIRED:
                raise ValueError(f"task is not waiting for input: {task_id}")
            input_request = self.store.get_pending_input_request(task_id)
            if input_request is None:
                raise ValueError(f"task has no pending input request: {task_id}")
            if input_request.get("kind") != "user_input_required":
                raise ValueError(f"task is waiting for approval, not general input: {task_id}")

            message = self.store.append_message(
                message_id=request.message_id or _new_id("msg"),
                context_id=task.context_id,
                role=MessageRole.USER,
                parts=request.parts,
                task_id=task_id,
                metadata={**request.metadata, "inputRequestKind": input_request.get("kind")},
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_input_submitted",
                status=task.status,
                payload={"message_id": message.message_id},
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_resumed",
                status=task.status,
                payload={"input_message_id": message.message_id},
            )
            if self.task_submitter is not None:
                task = self.store.update_task_status(task_id, TaskStatus.QUEUED)
                self.store.append_task_event(
                    task_id=task_id,
                    type="task_queued",
                    status=TaskStatus.QUEUED,
                )

        if self.task_submitter is None:
            return self._resume_local_task_with_input(
                task_id,
                parts=request.parts,
                metadata=request.metadata,
            )
        try:
            self.task_submitter.submit(
                self._resume_local_task_with_input_in_background,
                task_id,
                request.parts,
                request.metadata,
            )
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)
        return task

    def cancel_task(self, task_id: str, *, reason: str | None = None):
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                raise ValueError(f"task is terminal and cannot be canceled: {task_id}")
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return task

            payload = {"reason": reason} if reason else {}
            if task.status == TaskStatus.RUNNING and self._is_task_active(task_id):
                task = self.store.update_task_status(task_id, TaskStatus.CANCEL_REQUESTED)
                self.store.append_task_event(
                    task_id=task_id,
                    type="task_cancel_requested",
                    status=TaskStatus.CANCEL_REQUESTED,
                    payload=payload,
                )
                return task

            self.store.append_task_event(
                task_id=task_id,
                type="task_cancel_requested",
                status=TaskStatus.CANCEL_REQUESTED,
                payload=payload,
            )
            task = self.store.update_task_status(task_id, TaskStatus.CANCELED)
            self.store.append_task_event(
                task_id=task_id,
                type="task_cancelled",
                status=TaskStatus.CANCELED,
                payload=payload,
            )
            return task

    def _handle_local_message(
        self,
        *,
        context_id: str,
        input_message_id: str,
        route_decision: MainAgentRouteDecision,
    ) -> LocalMessageResult:
        decision = self.store.record_route_decision(
            decision_id=_new_id("route"),
            context_id=context_id,
            message_id=input_message_id,
            kind=RouteDecisionKind.LOCAL_MESSAGE,
            reason=route_decision.reason,
            confidence=route_decision.confidence,
            metadata=route_decision.metadata,
        )
        parts = self.local_message_responder.respond(recent_messages(self.store, context_id, limit=10))
        assistant_message = self.store.append_message(
            message_id=_new_id("msg"),
            context_id=context_id,
            role=MessageRole.AGENT,
            parts=parts,
            metadata={
                "inputMessageId": input_message_id,
                "routeDecisionId": decision.decision_id,
                "routeKind": RouteDecisionKind.LOCAL_MESSAGE.value,
            },
        )
        return LocalMessageResult(
            kind=RouteDecisionKind.LOCAL_MESSAGE,
            context_id=context_id,
            message_id=assistant_message.message_id,
            input_message_id=input_message_id,
            route_decision_id=decision.decision_id,
            parts=assistant_message.parts,
        )

    def _stream_local_message(
        self,
        *,
        context_id: str,
        input_message_id: str,
        route_decision: MainAgentRouteDecision,
    ) -> Iterator[LocalMessageDelta | LocalMessageResult]:
        stream_method = getattr(self.local_message_responder, "stream", None)
        if not callable(stream_method):
            yield self._handle_local_message(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            )
            return

        decision = self.store.record_route_decision(
            decision_id=_new_id("route"),
            context_id=context_id,
            message_id=input_message_id,
            kind=RouteDecisionKind.LOCAL_MESSAGE,
            reason=route_decision.reason,
            confidence=route_decision.confidence,
            metadata=route_decision.metadata,
        )
        assistant_message_id = _new_id("msg")
        full_text = ""
        sequence = 0
        for delta in stream_method(recent_messages(self.store, context_id, limit=10)):
            if not isinstance(delta, str) or not delta:
                continue
            full_text += delta
            sequence += 1
            yield LocalMessageDelta(
                kind=RouteDecisionKind.LOCAL_MESSAGE,
                context_id=context_id,
                message_id=assistant_message_id,
                input_message_id=input_message_id,
                route_decision_id=decision.decision_id,
                text=delta,
                sequence=sequence,
            )

        parts = [{"kind": "text", "text": full_text}]
        assistant_message = self.store.append_message(
            message_id=assistant_message_id,
            context_id=context_id,
            role=MessageRole.AGENT,
            parts=parts,
            metadata={
                "inputMessageId": input_message_id,
                "routeDecisionId": decision.decision_id,
                "routeKind": RouteDecisionKind.LOCAL_MESSAGE.value,
            },
        )
        yield LocalMessageResult(
            kind=RouteDecisionKind.LOCAL_MESSAGE,
            context_id=context_id,
            message_id=assistant_message.message_id,
            input_message_id=input_message_id,
            route_decision_id=decision.decision_id,
            parts=assistant_message.parts,
        )

    def _handle_local_task(
        self,
        *,
        context_id: str,
        input_message_id: str,
        route_decision: MainAgentRouteDecision,
    ) -> LocalTaskResult:
        decision = self.store.record_route_decision(
            decision_id=_new_id("route"),
            context_id=context_id,
            message_id=input_message_id,
            kind=RouteDecisionKind.LOCAL_TASK,
            reason=route_decision.reason,
            confidence=route_decision.confidence,
            metadata=route_decision.metadata,
        )
        with self.store.transaction():
            task = self.store.create_task(
                task_id=_new_id("task"),
                context_id=context_id,
                input_message_id=input_message_id,
                runtime_thread_id=_new_id("thread"),
                status=TaskStatus.CREATED,
            )
            self.store.append_task_event(task_id=task.task_id, type="task_created", status=TaskStatus.CREATED)
        if self.local_task_runner is not None:
            if self.task_submitter is None:
                task = self._run_local_task(
                    task.task_id,
                    context_id=context_id,
                    route_decision_id=decision.decision_id,
                )
            else:
                with self.store.transaction():
                    task = self.store.update_task_status(task.task_id, TaskStatus.QUEUED)
                    self.store.append_task_event(
                        task_id=task.task_id,
                        type="task_queued",
                        status=TaskStatus.QUEUED,
                    )
                try:
                    self.task_submitter.submit(
                        self._run_local_task_in_background,
                        task.task_id,
                        context_id,
                        decision.decision_id,
                    )
                except Exception as exc:
                    task = self._mark_local_task_failed(task.task_id, exc)
        return LocalTaskResult(
            kind=RouteDecisionKind.LOCAL_TASK,
            context_id=context_id,
            task_id=task.task_id,
            input_message_id=input_message_id,
            route_decision_id=decision.decision_id,
        )

    def _run_local_task(self, task_id: str, *, context_id: str, route_decision_id: str):
        try:
            with self._track_active_task(task_id):
                with self.store.transaction():
                    task = self.store.get_task(task_id)
                    if task is None:
                        raise ValueError(f"unknown task: {task_id}")
                    if is_terminal_task_status(task.status):
                        return task
                    if task.status == TaskStatus.CANCEL_REQUESTED:
                        return self._mark_local_task_canceled_after_safe_boundary(task_id)
                    task = self.store.update_task_status(task_id, TaskStatus.RUNNING)
                    self.store.append_task_event(task_id=task_id, type="task_started", status=TaskStatus.RUNNING)
                result = self.local_task_runner.run(
                    recent_messages(self.store, context_id, limit=10),
                    thread_id=task.runtime_thread_id,
                )
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)

        return self._save_local_task_result(
            task_id,
            result,
            metadata={"routeDecisionId": route_decision_id},
        )

    def _run_local_task_in_background(self, task_id: str, context_id: str, route_decision_id: str) -> None:
        try:
            self._run_local_task(task_id, context_id=context_id, route_decision_id=route_decision_id)
        except Exception as exc:
            self._mark_local_task_failed(task_id, exc)

    def _resume_local_task(self, task_id: str, *, approved: bool, reason: str | None = None):
        with self._track_active_task(task_id):
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    if self.task_submitter is None:
                        raise ValueError(f"task is not waiting for approval: {task_id}")
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                resumable_statuses = (
                    {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}
                    if self.task_submitter is None
                    else {TaskStatus.QUEUED}
                )
                if task.status not in resumable_statuses:
                    raise ValueError(f"task is not waiting for approval: {task_id}")
                if self.task_submitter is None:
                    self.store.append_task_event(
                        task_id=task_id,
                        type="task_resumed",
                        status=task.status,
                        payload={"approved": approved, **({"reason": reason} if reason else {})},
                    )
                task = self.store.update_task_status(task_id, TaskStatus.RUNNING)
                self.store.append_task_event(
                    task_id=task_id,
                    type="task_started",
                    status=TaskStatus.RUNNING,
                )
            try:
                result = self.local_task_runner.resume(
                    thread_id=task.runtime_thread_id,
                    approved=approved,
                    reason=reason,
                )
            except Exception as exc:
                return self._mark_local_task_failed(task_id, exc)
        return self._save_local_task_result(task_id, result)

    def _resume_local_task_in_background(self, task_id: str, approved: bool, reason: str | None) -> None:
        try:
            self._resume_local_task(task_id, approved=approved, reason=reason)
        except Exception as exc:
            self._mark_local_task_failed(task_id, exc)

    def _resume_local_task_with_input(
        self,
        task_id: str,
        *,
        parts: list[dict],
        metadata: dict | None = None,
    ):
        with self._track_active_task(task_id):
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    if self.task_submitter is None:
                        raise ValueError(f"task is not waiting for input: {task_id}")
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                resumable_statuses = (
                    {TaskStatus.INPUT_REQUIRED}
                    if self.task_submitter is None
                    else {TaskStatus.QUEUED}
                )
                if task.status not in resumable_statuses:
                    raise ValueError(f"task is not waiting for input: {task_id}")
                task = self.store.update_task_status(task_id, TaskStatus.RUNNING)
                self.store.append_task_event(task_id=task_id, type="task_started", status=TaskStatus.RUNNING)
            try:
                result = self.local_task_runner.resume_input(
                    thread_id=task.runtime_thread_id,
                    parts=parts,
                    metadata=metadata,
                )
            except Exception as exc:
                return self._mark_local_task_failed(task_id, exc)
        return self._save_local_task_result(task_id, result)

    def _resume_local_task_with_input_in_background(
        self,
        task_id: str,
        parts: list[dict],
        metadata: dict | None,
    ) -> None:
        try:
            self._resume_local_task_with_input(
                task_id,
                parts=parts,
                metadata=metadata,
            )
        except Exception as exc:
            self._mark_local_task_failed(task_id, exc)

    def _save_local_task_result(self, task_id: str, result, *, metadata: dict | None = None):
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if is_terminal_task_status(task.status):
            return task
        if task.status == TaskStatus.CANCEL_REQUESTED:
            return self._mark_local_task_canceled_after_safe_boundary(task_id)
        if result.status == TaskStatus.COMPLETED:
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                assistant_message = self.store.append_message(
                    message_id=_new_id("msg"),
                    context_id=task.context_id,
                    role=MessageRole.AGENT,
                    parts=result.parts,
                    task_id=task_id,
                    metadata={
                        **(metadata or {}),
                        "routeKind": RouteDecisionKind.LOCAL_TASK.value,
                    },
                )
                artifact_parts = result.artifact_parts or result.parts
                artifact = self.store.upsert_artifact(
                    artifact_id=f"{task_id}:final_answer",
                    task_id=task_id,
                    context_id=task.context_id,
                    parts=artifact_parts,
                    metadata={"kind": "final_answer", "outputMessageId": assistant_message.message_id},
                )
                self.store.append_task_event(
                    task_id=task_id,
                    type="task_artifact_created",
                    status=TaskStatus.COMPLETED,
                    payload={"artifact_id": artifact.artifact_id, "kind": "final_answer"},
                )
                task = self.store.set_task_output_message(task_id, assistant_message.message_id)
                task = self.store.update_task_status(task_id, TaskStatus.COMPLETED)
                self.store.append_task_event(task_id=task_id, type="task_completed", status=TaskStatus.COMPLETED)
            return task

        if result.status in {
            TaskStatus.CREATED,
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.CANCEL_REQUESTED,
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
        }:
            if result.status == TaskStatus.RUNNING:
                return task
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                active = self.store.update_task_status(task_id, result.status)
                self.store.append_task_event(
                    task_id=task_id,
                    type=_task_event_type_for_status(result.status),
                    status=result.status,
                    payload=_task_result_error_payload(result),
                )
            return active

        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self._mark_local_task_canceled_after_safe_boundary(task_id)
            failed = self.store.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error_code=result.error_code or "task_not_completed",
                error_message=result.error_message or f"local task ended with unsupported status: {result.status.value}",
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_failed",
                status=TaskStatus.FAILED,
                payload={"error_code": failed.error_code, "error_message": failed.error_message},
            )
        return failed

    def _mark_local_task_failed(self, task_id: str, exc: Exception):
        error = error_info_from_exception(exc)
        error_code = error.code.value
        error_message = error.public_message
        failure_payload = {
            "error_code": error_code,
            "error_message": error_message,
            "retryable": error.retryable,
        }
        logger.exception("Local task %s failed: %s", task_id, error.message)
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self._mark_local_task_canceled_after_safe_boundary(task_id)
            failed = self.store.update_task_status(
                task_id,
                TaskStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_failed",
                status=TaskStatus.FAILED,
                payload=failure_payload,
            )
        return failed

    def _mark_local_task_canceled_after_safe_boundary(self, task_id: str):
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.status == TaskStatus.CANCELED or is_terminal_task_status(task.status):
                return task
            task = self.store.update_task_status(task_id, TaskStatus.CANCELED)
            self.store.append_task_event(
                task_id=task_id,
                type="task_cancelled",
                status=TaskStatus.CANCELED,
            )
            return task

    def _track_active_task(self, task_id: str):
        return _ActiveTaskExecution(self._active_task_guard, self._active_task_ids, task_id)

    def _is_task_active(self, task_id: str) -> bool:
        with self._active_task_guard:
            return task_id in self._active_task_ids

    def _handle_remote_agent(
        self,
        *,
        context_id: str,
        input_message_id: str,
        request: MainAgentRequest,
        route_decision: MainAgentRouteDecision,
    ) -> RemoteAgentResult:
        target_agent_id = route_decision.target_agent_id
        if target_agent_id is None:
            raise ValueError("remote_agent route requires metadata.targetAgentId")
        agent = self.store.get_registered_agent(target_agent_id)
        if agent is None:
            raise ValueError(f"unknown registered agent: {target_agent_id}")
        if not agent.enabled:
            raise ValueError(f"registered agent is disabled: {target_agent_id}")
        if self.remote_agent_client is None:
            raise ValueError("remote_agent client is not configured")

        decision = self.store.record_route_decision(
            decision_id=_new_id("route"),
            context_id=context_id,
            message_id=input_message_id,
            kind=RouteDecisionKind.REMOTE_AGENT,
            target_agent_id=target_agent_id,
            reason=route_decision.reason,
            confidence=route_decision.confidence,
            metadata=route_decision.metadata,
        )
        remote = self.remote_agent_client.send_message(
            agent=agent,
            request=request,
            context_id=context_id,
            message_id=input_message_id,
        )
        delegation_id = _new_id("delegate")

        if remote.kind == "message":
            with self.store.transaction():
                assistant_message = self.store.append_message(
                    message_id=_new_id("msg"),
                    context_id=context_id,
                    role=MessageRole.AGENT,
                    parts=remote.parts,
                    metadata={
                        "inputMessageId": input_message_id,
                        "routeDecisionId": decision.decision_id,
                        "routeKind": RouteDecisionKind.REMOTE_AGENT.value,
                        "remoteAgentId": target_agent_id,
                        "remoteContextId": remote.context_id,
                        "remoteMessageId": remote.message_id,
                    },
                )
                self.store.create_delegated_task(
                    delegation_id=delegation_id,
                    context_id=context_id,
                    input_message_id=input_message_id,
                    route_decision_id=decision.decision_id,
                    remote_agent_id=target_agent_id,
                    remote_context_id=remote.context_id,
                    remote_message_id=remote.message_id,
                    result_kind="message",
                    status="completed",
                    metadata={"localMessageId": assistant_message.message_id},
                )
            return RemoteAgentResult(
                kind=RouteDecisionKind.REMOTE_AGENT,
                context_id=context_id,
                input_message_id=input_message_id,
                target_agent_id=target_agent_id,
                route_decision_id=decision.decision_id,
                delegation_id=delegation_id,
                message_id=assistant_message.message_id,
                parts=assistant_message.parts,
            )

        if remote.kind == "task":
            with self.store.transaction():
                task = self.store.create_task(
                    task_id=_new_id("task"),
                    context_id=context_id,
                    input_message_id=input_message_id,
                    runtime_thread_id=_new_id("remote-thread"),
                    assigned_agent_id=target_agent_id,
                    status=_remote_task_status(remote.status),
                )
                self.store.append_task_event(
                    task_id=task.task_id,
                    type="task_delegated",
                    status=task.status,
                    payload={
                        "remote_agent_id": target_agent_id,
                        "remote_task_id": remote.task_id,
                        "remote_context_id": remote.context_id,
                    },
                )
                self.store.create_delegated_task(
                    delegation_id=delegation_id,
                    context_id=context_id,
                    input_message_id=input_message_id,
                    route_decision_id=decision.decision_id,
                    remote_agent_id=target_agent_id,
                    local_task_id=task.task_id,
                    remote_task_id=remote.task_id,
                    remote_context_id=remote.context_id,
                    result_kind="task",
                    status=remote.status or task.status.value,
                )
            return RemoteAgentResult(
                kind=RouteDecisionKind.REMOTE_AGENT,
                context_id=context_id,
                input_message_id=input_message_id,
                target_agent_id=target_agent_id,
                route_decision_id=decision.decision_id,
                delegation_id=delegation_id,
                task_id=task.task_id,
            )

        raise ValueError(f"unsupported remote agent result kind: {remote.kind}")


def _remote_task_status(status: str | None) -> TaskStatus:
    if status in {"submitted", "TASK_STATE_SUBMITTED", "created", "queued"}:
        return TaskStatus.QUEUED
    if status in {"working", "TASK_STATE_WORKING", "running"}:
        return TaskStatus.RUNNING
    if status in {"completed", "TASK_STATE_COMPLETED"}:
        return TaskStatus.COMPLETED
    if status in {"canceled", "cancelled", "TASK_STATE_CANCELED"}:
        return TaskStatus.CANCELED
    if status in {"input-required", "TASK_STATE_INPUT_REQUIRED"}:
        return TaskStatus.INPUT_REQUIRED
    if status in {"auth-required", "TASK_STATE_AUTH_REQUIRED"}:
        return TaskStatus.AUTH_REQUIRED
    return TaskStatus.FAILED


def _task_event_type_for_status(status: TaskStatus) -> str:
    return {
        TaskStatus.CREATED: "task_created",
        TaskStatus.QUEUED: "task_queued",
        TaskStatus.RUNNING: "task_started",
        TaskStatus.CANCEL_REQUESTED: "task_cancel_requested",
        TaskStatus.INPUT_REQUIRED: "task_interrupted",
        TaskStatus.AUTH_REQUIRED: "task_interrupted",
    }.get(status, "task_updated")


def _task_result_error_payload(result: LocalTaskRunResult) -> dict:
    payload: dict = {}
    if result.error_code:
        payload["error_code"] = result.error_code
    if result.error_message:
        payload["error_message"] = result.error_message
    if result.input_request:
        payload["input_request"] = result.input_request
    return payload


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _title_from_parts(parts: list[dict]) -> str | None:
    text = " ".join(str(part.get("text", "")).strip() for part in parts if isinstance(part.get("text"), str))
    normalized = " ".join(text.split())
    return normalized or None


def _target_agent_id_from_metadata(metadata: dict) -> str | None:
    for key in ("targetAgentId", "target_agent_id", "remoteAgentId", "remote_agent_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class _ActiveTaskExecution:
    def __init__(self, guard: threading.RLock, active_task_ids: set[str], task_id: str) -> None:
        self._guard = guard
        self._active_task_ids = active_task_ids
        self._task_id = task_id

    def __enter__(self) -> None:
        with self._guard:
            self._active_task_ids.add(self._task_id)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        with self._guard:
            self._active_task_ids.discard(self._task_id)

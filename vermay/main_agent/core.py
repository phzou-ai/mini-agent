from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Protocol
from uuid import uuid4

from vermay.errors import (
    ContextDeletionConflictError,
    InvalidSessionStateError,
    MessageIngressInProgressError,
    MessageIngressStaleError,
    MessageStreamAbortedError,
    PersistedMessageIngressError,
    RegisteredAgentDeletionConflictError,
    TaskNotFoundError,
    error_info_from_exception,
)

from .context import (
    direct_message_context_through_input,
    local_task_context,
    router_context_through_input,
)
from .lifecycle import accepts_remote_proxy_snapshot
from .models import (
    LocalMessageDelta,
    LocalMessageResult,
    LocalTaskResult,
    MainAgentRequest,
    MainAgentResult,
    MainAgentStreamResult,
    MessageIngressOutcomeKind,
    MessageIngressRecord,
    MessageIngressState,
    MessageRole,
    QueuedTaskExecutionKind,
    QueuedTaskExecutionRecord,
    RemoteAgentResult,
    RouteDecisionKind,
    TaskRecord,
    TaskStatus,
    is_terminal_task_status,
)
from .remote_agent import RemoteAgentClient, RemoteAgentProtocolError, RemoteAgentTaskSnapshot
from .responder import LocalMessageResponder
from .router import DefaultMainAgentRouter, MainAgentRouteDecision, MainAgentRouter
from .store import MainAgentStore
from .task_result_projection import (
    continuation_input_request as _continuation_input_request,
    local_process_status as _local_process_status,
    task_result_error_payload as _task_result_error_payload,
    task_result_execution_metadata as _task_result_execution_metadata,
    task_result_lifecycle_payload as _task_result_lifecycle_payload,
    task_result_observations as _task_result_observations,
)
from .task_runner import LocalTaskRunResult, LocalTaskRunner


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PreparedMessageRoute:
    context_id: str
    input_message_id: str
    route_decision: MainAgentRouteDecision | None = None
    existing_result: MainAgentResult | None = None


@dataclass(frozen=True)
class _ApprovalContinuationCommand:
    task_id: str
    runtime_thread_id: str
    expected_status: TaskStatus
    approved: bool
    reason: str | None


@dataclass(frozen=True)
class _InputContinuationCommand:
    task_id: str
    runtime_thread_id: str
    expected_status: TaskStatus
    parts: list[dict]
    metadata: dict | None


@dataclass(frozen=True)
class StartupReconciliationResult:
    """Inspectable outcome of one conservative in-process worker recovery pass."""

    scheduled_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()
    retained_task_ids: tuple[str, ...] = ()
    failed_message_ids: tuple[str, ...] = ()


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
        self._scheduled_task_ids: set[str] = set()

    def handle_message(self, request: MainAgentRequest) -> MainAgentResult:
        prepared = self._prepare_message_route(request)
        if prepared.existing_result is not None:
            return prepared.existing_result
        if prepared.route_decision is None:
            raise RuntimeError("message route was not prepared")
        try:
            return self._dispatch_message(request, prepared)
        except Exception as exc:
            self._record_message_ingress_failure(prepared.input_message_id, exc)
            raise

    def stream_message(self, request: MainAgentRequest) -> Iterator[MainAgentStreamResult]:
        prepared = self._prepare_message_route(request)
        if prepared.existing_result is not None:
            yield prepared.existing_result
            return
        if prepared.route_decision is None:
            raise RuntimeError("message route was not prepared")
        context_id = prepared.context_id
        input_message_id = prepared.input_message_id
        route_decision = prepared.route_decision
        try:
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
        except GeneratorExit:
            self._record_message_ingress_failure(input_message_id, MessageStreamAbortedError())
            raise
        except Exception as exc:
            self._record_message_ingress_failure(input_message_id, exc)
            raise

    def _dispatch_message(
        self,
        request: MainAgentRequest,
        prepared: _PreparedMessageRoute,
    ) -> MainAgentResult:
        context_id = prepared.context_id
        input_message_id = prepared.input_message_id
        route_decision = prepared.route_decision
        if route_decision is None:
            raise RuntimeError("message route was not prepared")
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

    def _prepare_message_route(self, request: MainAgentRequest) -> _PreparedMessageRoute:
        if request.role != MessageRole.USER:
            raise ValueError("main agent request role must be user")
        self._validate_explicit_route_metadata(request.metadata)
        message_id = request.message_id or _new_id("msg")
        request_fingerprint = _message_request_fingerprint(request)
        with self.store.transaction():
            ingress = self.store.get_message_ingress(message_id)
            if ingress is not None:
                return self._prepared_existing_message_ingress(
                    request=request,
                    request_fingerprint=request_fingerprint,
                    ingress=ingress,
                )

            existing_message = self.store.get_message(message_id)
            if existing_message is not None:
                raise RuntimeError(f"message is missing durable ingress: {existing_message.message_id}")
            else:
                context_id = self._resolve_context_id(
                    request.context_id,
                    title=_title_from_parts(request.parts),
                )
                user_message = self.store.append_message(
                    message_id=message_id,
                    context_id=context_id,
                    role=request.role,
                    parts=request.parts,
                    metadata=request.metadata,
                )
                ingress, created = self.store.reserve_message_ingress(
                    message_id=message_id,
                    context_id=context_id,
                    request_fingerprint=request_fingerprint,
                )
                if not created:
                    return self._prepared_existing_message_ingress(
                        request=request,
                        request_fingerprint=request_fingerprint,
                        ingress=ingress,
                    )

        try:
            route_decision = self.router.decide(
                request=request,
                context_id=context_id,
                input_message_id=user_message.message_id,
                messages=router_context_through_input(self.store, context_id, user_message.message_id),
                store=self.store,
            )
        except Exception as exc:
            self._record_message_ingress_failure(user_message.message_id, exc)
            raise
        return _PreparedMessageRoute(
            context_id=context_id,
            input_message_id=user_message.message_id,
            route_decision=route_decision,
        )

    def _prepared_existing_message_ingress(
        self,
        *,
        request: MainAgentRequest,
        request_fingerprint: str,
        ingress: MessageIngressRecord,
    ) -> _PreparedMessageRoute:
        if request.context_id is not None and request.context_id != ingress.context_id:
            raise ValueError(f"message context mismatch: {ingress.message_id}")
        if request_fingerprint != ingress.request_fingerprint:
            raise ValueError(f"message conflict: {ingress.message_id}")
        message = self.store.get_message(ingress.message_id)
        if message is None:
            raise RuntimeError(f"message ingress is missing its message: {ingress.message_id}")
        if message.context_id != ingress.context_id:
            raise RuntimeError(f"message ingress context mismatch: {ingress.message_id}")
        if ingress.state == MessageIngressState.RESOLVED:
            return _PreparedMessageRoute(
                context_id=ingress.context_id,
                input_message_id=ingress.message_id,
                existing_result=self._result_from_message_ingress(ingress),
            )
        if ingress.state == MessageIngressState.FAILED:
            raise PersistedMessageIngressError(
                code=ingress.error_code or "runtime_error",
                message=ingress.error_message or "Agent execution failed.",
                http_status=ingress.error_http_status,
                retryable=ingress.error_retryable,
            )
        raise MessageIngressInProgressError(ingress.message_id)

    def _result_from_message_ingress(self, ingress: MessageIngressRecord) -> MainAgentResult:
        if ingress.route_decision_id is None or ingress.outcome_kind is None or ingress.outcome_id is None:
            raise RuntimeError(f"resolved message ingress is incomplete: {ingress.message_id}")
        decision = self.store.get_route_decision(ingress.route_decision_id)
        if decision is None:
            raise RuntimeError(f"message ingress route decision is missing: {ingress.message_id}")
        if decision.context_id != ingress.context_id or decision.message_id != ingress.message_id:
            raise RuntimeError(f"message ingress route decision mismatch: {ingress.message_id}")

        if decision.kind == RouteDecisionKind.LOCAL_MESSAGE:
            if ingress.outcome_kind != MessageIngressOutcomeKind.MESSAGE:
                raise RuntimeError(f"message ingress outcome mismatch: {ingress.message_id}")
            message = self.store.get_message(ingress.outcome_id)
            if message is None:
                raise RuntimeError(f"message ingress output is missing: {ingress.message_id}")
            return LocalMessageResult(
                kind=decision.kind,
                context_id=ingress.context_id,
                message_id=message.message_id,
                input_message_id=ingress.message_id,
                route_decision_id=decision.decision_id,
                parts=message.parts,
            )

        if decision.kind == RouteDecisionKind.LOCAL_TASK:
            if ingress.outcome_kind != MessageIngressOutcomeKind.TASK:
                raise RuntimeError(f"message ingress outcome mismatch: {ingress.message_id}")
            task = self.store.get_task(ingress.outcome_id)
            if task is None:
                raise RuntimeError(f"message ingress task is missing: {ingress.message_id}")
            return LocalTaskResult(
                kind=decision.kind,
                context_id=ingress.context_id,
                task_id=task.task_id,
                input_message_id=ingress.message_id,
                route_decision_id=decision.decision_id,
            )

        if ingress.outcome_kind != MessageIngressOutcomeKind.DELEGATION:
            raise RuntimeError(f"message ingress outcome mismatch: {ingress.message_id}")
        delegation = self.store.get_delegated_task(ingress.outcome_id)
        if delegation is None:
            raise RuntimeError(f"message ingress delegation is missing: {ingress.message_id}")
        if delegation.result_kind == "message":
            local_message_id = delegation.metadata.get("localMessageId")
            message = self.store.get_message(str(local_message_id)) if local_message_id else None
            if message is None:
                raise RuntimeError(f"message ingress delegated output is missing: {ingress.message_id}")
            return RemoteAgentResult(
                kind=decision.kind,
                context_id=ingress.context_id,
                input_message_id=ingress.message_id,
                target_agent_id=delegation.remote_agent_id,
                route_decision_id=decision.decision_id,
                delegation_id=delegation.delegation_id,
                message_id=message.message_id,
                parts=message.parts,
            )
        if delegation.local_task_id is None:
            raise RuntimeError(f"message ingress delegated task is missing: {ingress.message_id}")
        task = self.store.get_task(delegation.local_task_id)
        if task is None:
            raise RuntimeError(f"message ingress delegated task is missing: {ingress.message_id}")
        return RemoteAgentResult(
            kind=decision.kind,
            context_id=ingress.context_id,
            input_message_id=ingress.message_id,
            target_agent_id=delegation.remote_agent_id,
            route_decision_id=decision.decision_id,
            delegation_id=delegation.delegation_id,
            task_id=task.task_id,
        )

    def _record_message_ingress_failure(self, message_id: str, exc: Exception) -> None:
        error = error_info_from_exception(exc)
        self.store.fail_message_ingress(
            message_id,
            error_code=error.code.value,
            error_message=error.public_message,
            error_http_status=error.http_status,
            retryable=error.retryable,
        )

    def _record_message_ingress_route(
        self,
        *,
        context_id: str,
        input_message_id: str,
        route_decision: MainAgentRouteDecision,
    ):
        with self.store.transaction():
            decision = self.store.record_route_decision(
                decision_id=_new_id("route"),
                context_id=context_id,
                message_id=input_message_id,
                kind=route_decision.kind,
                target_agent_id=route_decision.target_agent_id,
                reason=route_decision.reason,
                confidence=route_decision.confidence,
                metadata=route_decision.metadata,
            )
            self.store.set_message_ingress_route_decision(
                input_message_id,
                route_decision_id=decision.decision_id,
            )
        return decision

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

    def delete_context(self, context_id: str, *, force: bool = False):
        """Delete a Context only after all of its durable processes are idle.

        ``force`` remains accepted at the HTTP boundary for now, but it does
        not rewrite a live Task to a terminal status. Callers must cancel work
        through the lifecycle API and retry deletion once the worker has
        reached a durable terminal state.
        """

        if self.store.get_context(context_id) is None:
            return None
        tasks = self.store.list_context_tasks(context_id)
        active_tasks = [task for task in tasks if not is_terminal_task_status(task.status)]
        if active_tasks:
            detail = "active local or remote tasks exist"
            if force:
                detail += "; force cannot delete live processes"
            raise ContextDeletionConflictError(context_id, reason=detail)
        live_callbacks = [task for task in tasks if self._is_task_live(task.task_id)]
        if live_callbacks:
            raise ContextDeletionConflictError(
                context_id,
                reason="terminal tasks still have a live worker callback",
            )

        for task in tasks:
            self._discard_terminal_task_checkpoint(task)
        return self.store.delete_terminal_context(context_id)

    def delete_registered_agent(self, agent_id: str) -> bool:
        """Hard-delete only an unused child-agent registration.

        Delegation history is part of the Context audit trail. Registrations
        with any historical reference stay intact; callers can disable them
        through the existing registration update API instead of erasing the
        record behind those facts.
        """

        if self.store.get_registered_agent(agent_id) is None:
            return False
        delegations = self.store.list_delegated_tasks_for_remote_agent(agent_id)
        active_task_ids = [
            delegation.local_task_id
            for delegation in delegations
            if delegation.local_task_id is not None
            and (task := self.store.get_task(delegation.local_task_id)) is not None
            and not is_terminal_task_status(task.status)
        ]
        if active_task_ids:
            raise RegisteredAgentDeletionConflictError(agent_id, reason="active delegated tasks exist")
        if delegations:
            raise RegisteredAgentDeletionConflictError(agent_id, reason="delegation history exists")
        return self.store.delete_registered_agent(agent_id)

    def reconcile_startup(self) -> StartupReconciliationResult:
        """Recover only worker slices that are provably safe to submit again.

        A queued command has not yet been claimed by a worker. Once a worker
        claims it, the command is deleted and the Task becomes `running` in the
        same transaction. Therefore a restart must fail `running` and
        `cancel_requested` Tasks rather than guessing whether model or tool
        work happened after the last durable event.
        """

        scheduled_task_ids: list[str] = []
        failed_task_ids: list[str] = []
        retained_task_ids: list[str] = []
        stale_message_error = MessageIngressStaleError()
        failed_message_ids = self.store.fail_in_progress_message_ingresses(
            error_code=stale_message_error.code.value,
            error_message=stale_message_error.public_message,
            error_http_status=stale_message_error.http_status,
            retryable=True,
        )

        interrupted = self.store.list_local_tasks_by_statuses(
            {TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED}
        )
        for task in interrupted:
            self._mark_local_task_runtime_recovery_failed(
                task.task_id,
                error_code="runtime_restart_interrupted",
                error_message="Local task execution was interrupted by a runtime restart.",
            )
            failed_task_ids.append(task.task_id)

        for task in self.store.list_local_tasks_by_statuses({TaskStatus.QUEUED}):
            try:
                command = self.store.get_queued_task_execution(task.task_id)
            except (TypeError, ValueError):
                self._mark_local_task_runtime_recovery_failed(
                    task.task_id,
                    error_code="runtime_recovery_invalid_command",
                    error_message="Queued local task has an invalid persisted execution command.",
                )
                failed_task_ids.append(task.task_id)
                continue

            if command is None:
                self._mark_local_task_runtime_recovery_failed(
                    task.task_id,
                    error_code="runtime_recovery_command_missing",
                    error_message="Queued local task has no recoverable execution command.",
                )
                failed_task_ids.append(task.task_id)
                continue
            if command.runtime_thread_id != task.runtime_thread_id:
                self._mark_local_task_runtime_recovery_failed(
                    task.task_id,
                    error_code="runtime_recovery_thread_mismatch",
                    error_message="Queued local task execution does not match its runtime thread.",
                )
                failed_task_ids.append(task.task_id)
                continue
            if self.local_task_runner is None or self.task_submitter is None:
                self._mark_local_task_runtime_recovery_failed(
                    task.task_id,
                    error_code="runtime_recovery_unavailable",
                    error_message="No local worker is available to recover the queued task.",
                )
                failed_task_ids.append(task.task_id)
                continue

            try:
                if self._schedule_queued_task_execution(task.task_id):
                    scheduled_task_ids.append(task.task_id)
                else:
                    retained_task_ids.append(task.task_id)
            except Exception:
                self._mark_local_task_runtime_recovery_failed(
                    task.task_id,
                    error_code="runtime_recovery_submission_failed",
                    error_message="Unable to submit the queued local task for recovery.",
                )
                failed_task_ids.append(task.task_id)

        return StartupReconciliationResult(
            scheduled_task_ids=tuple(scheduled_task_ids),
            failed_task_ids=tuple(failed_task_ids),
            retained_task_ids=tuple(retained_task_ids),
            failed_message_ids=failed_message_ids,
        )

    def resume_task(self, task_id: str, *, approved: bool, reason: str | None = None):
        task = self.store.get_task(task_id)
        if task is not None and task.assigned_agent_id is not None:
            raise ValueError(f"delegated task resume is not supported yet: {task_id}")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        command_payload = {
            "approved": approved,
            **({"reason": reason} if reason else {}),
        }
        with self.store.transaction():
            task, expected_status = self._accept_pending_continuation(
                task_id,
                expected_kind="approval_required",
                resume_payload=command_payload,
                operation="approval",
                queued_execution_kind=(
                    QueuedTaskExecutionKind.APPROVAL if self.task_submitter is not None else None
                ),
                queued_execution_payload=command_payload,
            )
            command = _ApprovalContinuationCommand(
                task_id=task.task_id,
                runtime_thread_id=task.runtime_thread_id,
                expected_status=expected_status,
                approved=approved,
                reason=reason,
            )

        if self.task_submitter is None:
            return self._resume_local_task(command)
        try:
            self._schedule_queued_task_execution(task.task_id)
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)
        return task

    def retry_failed_task(self, task_id: str) -> TaskRecord:
        """Create one new, safe local Task attempt from a retryable failure.

        The original Task remains immutable. Retrying creates a new user
        message, ingress record, route decision, Task, and LangGraph thread in
        the same Context. It deliberately does not re-route or replay any
        previous tool invocation.
        """

        if self.local_task_runner is None:
            raise InvalidSessionStateError("local task runner is not configured")

        source = self._retryable_failed_local_task(task_id)
        existing = self.store.get_direct_task_retry(source.task_id)
        if existing is not None:
            return existing

        input_message = self.store.get_message(source.input_message_id)
        if input_message is None or input_message.role != MessageRole.USER:
            raise InvalidSessionStateError("The original task input is unavailable.")

        retry_message_id = _new_id("msg")
        retry_task_id = _new_id("task")
        retry_thread_id = _new_id("thread")
        retry_attempt = source.attempt + 1
        retry_metadata = _retry_message_metadata(input_message.metadata, source, retry_attempt)
        queue_execution = self._queue_execution_enabled()

        try:
            with self.store.transaction():
                # Re-check inside the durable acceptance boundary so retry
                # eligibility cannot change between the initial read and insert.
                source = self._retryable_failed_local_task(task_id)
                existing = self.store.get_direct_task_retry(source.task_id)
                if existing is not None:
                    return existing

                retry_message = self.store.append_message(
                    message_id=retry_message_id,
                    context_id=source.context_id,
                    role=MessageRole.USER,
                    parts=deepcopy(input_message.parts),
                    metadata=retry_metadata,
                )
                self.store.reserve_message_ingress(
                    message_id=retry_message.message_id,
                    context_id=source.context_id,
                    request_fingerprint=_message_fingerprint(
                        role=MessageRole.USER,
                        parts=retry_message.parts,
                        metadata=retry_metadata,
                    ),
                )
                _decision, task = self.store.accept_local_task_from_message(
                    decision_id=_new_id("route"),
                    context_id=source.context_id,
                    input_message_id=retry_message.message_id,
                    route_kind=RouteDecisionKind.LOCAL_TASK,
                    route_reason="Manual retry of a retryable failed local task.",
                    route_target_agent_id=None,
                    route_confidence=1.0,
                    route_metadata={
                        "source": "manual_retry",
                        "retryOfTaskId": source.task_id,
                        "retryAttempt": retry_attempt,
                    },
                    task_id=retry_task_id,
                    runtime_thread_id=retry_thread_id,
                    queue_execution=queue_execution,
                    retry_of_task_id=source.task_id,
                    attempt=retry_attempt,
                )
                self.store.append_task_event(
                    task_id=source.task_id,
                    type="task_retry_requested",
                    status=None,
                    payload={"retry_task_id": task.task_id, "retry_attempt": task.attempt},
                )
                self.store.append_task_event(
                    task_id=task.task_id,
                    type="task_retried",
                    status=None,
                    payload={"retry_of_task_id": source.task_id, "retry_attempt": task.attempt},
                )
        except sqlite3.IntegrityError:
            # The direct-retry lineage index is the concurrency boundary. A
            # simultaneous click should converge on the already-created child.
            existing = self.store.get_direct_task_retry(task_id)
            if existing is not None:
                return existing
            raise

        return self._start_accepted_local_task(task)

    def _retryable_failed_local_task(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if task.assigned_agent_id is not None:
            raise InvalidSessionStateError("Only locally owned tasks can be retried.")
        if task.status != TaskStatus.FAILED:
            raise InvalidSessionStateError("Only failed tasks can be retried.")
        if not task.error_retryable:
            raise InvalidSessionStateError("This task failure is not retryable.")
        if self.store.has_potentially_side_effecting_tool_invocation(task.task_id):
            raise InvalidSessionStateError(
                "This task cannot be retried because it has potentially side-effecting tool work."
            )
        return task

    def _accept_pending_continuation(
        self,
        task_id: str,
        *,
        expected_kind: str,
        resume_payload: dict,
        operation: str,
        queued_execution_kind: QueuedTaskExecutionKind | None = None,
        queued_execution_payload: dict | None = None,
    ) -> tuple[TaskRecord, TaskStatus]:
        self._validate_pending_continuation(
            task_id,
            expected_kind=expected_kind,
            operation=operation,
        )
        # This is the control-plane handoff. The pending record, audit event,
        # and queue transition commit together before a worker can run.
        pending = self.store.consume_pending_continuation(task_id, expected_kind=expected_kind)
        if expected_kind == "approval_required":
            self.store.resolve_pending_tool_invocation_approval(
                task_id=task_id,
                input_request=pending.input_request,
                approved=bool(resume_payload.get("approved")),
                reason=(str(resume_payload["reason"]) if resume_payload.get("reason") else None),
            )
        self.store.append_task_event(
            task_id=task_id,
            type="task_resumed",
            status=None,
            payload=resume_payload,
        )
        task = self.store.transition_local_task(task_id, TaskStatus.QUEUED)
        if queued_execution_kind is not None:
            self.store.enqueue_task_execution(
                task.task_id,
                kind=queued_execution_kind,
                runtime_thread_id=task.runtime_thread_id,
                payload=queued_execution_payload,
            )
        return task, task.status

    def _validate_pending_continuation(
        self,
        task_id: str,
        *,
        expected_kind: str,
        operation: str,
    ) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if is_terminal_task_status(task.status) or task.status == TaskStatus.CANCEL_REQUESTED:
            raise ValueError(f"task is not waiting for {operation}: {task_id}")
        if task.status not in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
            raise ValueError(f"task is not waiting for {operation}: {task_id}")

        pending = self.store.get_pending_continuation(task_id)
        if pending is None:
            raise ValueError(f"task has no pending {expected_kind} request: {task_id}")
        if pending.kind != expected_kind:
            expected_label = "approval" if expected_kind == "approval_required" else "general input"
            actual_label = "approval" if pending.kind == "approval_required" else "general input"
            raise ValueError(f"task is waiting for {actual_label}, not {expected_label}: {task_id}")
        return task

    def submit_task_input(self, task_id: str, request: MainAgentRequest):
        if request.role != MessageRole.USER:
            raise ValueError("task input role must be user")
        if not request.parts:
            raise ValueError("task input parts are required")
        task = self.store.get_task(task_id)
        if task is not None and task.assigned_agent_id is not None:
            raise ValueError(f"delegated task input continuation is not supported yet: {task_id}")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        queued_execution_payload = {
            "parts": deepcopy(request.parts),
            "metadata": deepcopy(request.metadata) if request.metadata else None,
        }
        with self.store.transaction():
            task = self._validate_pending_continuation(
                task_id,
                expected_kind="user_input_required",
                operation="input",
            )
            if request.context_id is not None and request.context_id != task.context_id:
                raise ValueError(f"task context mismatch: {task_id}")
            message = self.store.append_message(
                message_id=request.message_id or _new_id("msg"),
                context_id=task.context_id,
                role=MessageRole.USER,
                parts=request.parts,
                task_id=task_id,
                metadata={**request.metadata, "inputRequestKind": "user_input_required"},
            )
            self.store.append_task_event(
                task_id=task_id,
                type="task_input_submitted",
                status=None,
                payload={"message_id": message.message_id},
            )
            task, expected_status = self._accept_pending_continuation(
                task_id,
                expected_kind="user_input_required",
                resume_payload={"input_message_id": message.message_id},
                operation="input",
                queued_execution_kind=(
                    QueuedTaskExecutionKind.USER_INPUT if self.task_submitter is not None else None
                ),
                queued_execution_payload=queued_execution_payload,
            )
            command = _InputContinuationCommand(
                task_id=task.task_id,
                runtime_thread_id=task.runtime_thread_id,
                expected_status=expected_status,
                parts=deepcopy(request.parts),
                metadata=deepcopy(request.metadata) if request.metadata else None,
            )

        if self.task_submitter is None:
            return self._resume_local_task_with_input(command)
        try:
            self._schedule_queued_task_execution(task.task_id)
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)
        return task

    def cancel_task(self, task_id: str, *, reason: str | None = None):
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if is_terminal_task_status(task.status):
            raise ValueError(f"task is terminal and cannot be canceled: {task_id}")
        if task.assigned_agent_id is not None:
            return self._cancel_remote_proxy_task(task_id, reason=reason)

        active_runtime_thread_id: str | None = None
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                raise ValueError(f"task is terminal and cannot be canceled: {task_id}")
            if task.status == TaskStatus.CANCEL_REQUESTED:
                if self._is_task_active(task_id):
                    active_runtime_thread_id = task.runtime_thread_id
                canceled_task = task
            else:
                payload = {"reason": reason} if reason else {}
                if task.status == TaskStatus.RUNNING:
                    cancel_requested = self.store.transition_local_task(
                        task_id=task_id,
                        target_status=TaskStatus.CANCEL_REQUESTED,
                        payload=payload,
                    )
                    if self._is_task_active(task_id):
                        active_runtime_thread_id = cancel_requested.runtime_thread_id
                        canceled_task = cancel_requested
                    else:
                        task = cancel_requested
                if active_runtime_thread_id is None:
                    canceled_task = self.store.transition_local_task(
                        task_id=task_id,
                        target_status=TaskStatus.CANCELED,
                        payload=payload,
                    )
                    self.store.cancel_prepared_tool_invocations(
                        task_id,
                        reason=reason or "task canceled before tool execution",
                    )
                    self.store.clear_pending_continuation(task_id)
                    self.store.clear_queued_task_execution(task_id)

        if active_runtime_thread_id is not None:
            self._request_local_execution_cancellation(
                thread_id=active_runtime_thread_id,
                reason=reason,
            )
        return canceled_task

    def get_task(self, task_id: str, *, refresh_remote: bool = False) -> TaskRecord | None:
        """Return a local Task record and optionally refresh its remote proxy."""

        task = self.store.get_task(task_id)
        if task is None or not refresh_remote or task.assigned_agent_id is None:
            return task
        return self._sync_remote_proxy_task(task_id)

    def _sync_remote_proxy_task(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        delegation = self.store.get_delegated_task_by_local_task_id(task_id)
        if delegation is None or delegation.remote_task_id is None:
            return task
        client = self.remote_agent_client
        if client is None:
            return task
        agent = self.store.get_registered_agent(delegation.remote_agent_id)
        if agent is None or not agent.enabled:
            return task
        snapshot = client.get_task(agent=agent, task_id=delegation.remote_task_id)
        return self._apply_remote_proxy_snapshot(task_id, snapshot=snapshot)

    def _cancel_remote_proxy_task(self, task_id: str, *, reason: str | None) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        delegation = self.store.get_delegated_task_by_local_task_id(task_id)
        if delegation is None or delegation.remote_task_id is None:
            raise ValueError(f"delegated task is missing remote task id: {task_id}")
        client = self.remote_agent_client
        if client is None:
            raise ValueError("remote_agent client is not configured")
        agent = self.store.get_registered_agent(delegation.remote_agent_id)
        if agent is None:
            raise ValueError(f"unknown registered agent: {delegation.remote_agent_id}")
        if not agent.enabled:
            raise ValueError(f"registered agent is disabled: {delegation.remote_agent_id}")
        snapshot = client.cancel_task(agent=agent, task_id=delegation.remote_task_id, reason=reason)
        return self._apply_remote_proxy_snapshot(task_id, snapshot=snapshot)

    def _apply_remote_proxy_snapshot(
        self,
        task_id: str,
        *,
        snapshot: RemoteAgentTaskSnapshot,
    ) -> TaskRecord:
        """Persist an accepted child-agent snapshot without regressing its proxy."""

        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            delegation = self.store.get_delegated_task_by_local_task_id(task_id)
            if delegation is None:
                raise ValueError(f"remote proxy task is missing delegation: {task_id}")
            _validate_remote_proxy_snapshot(delegation, snapshot)

            next_status = _remote_task_status(snapshot.status, fallback=task.status)
            if not accepts_remote_proxy_snapshot(task.status, next_status):
                return task

            metadata = {
                **delegation.metadata,
                "remoteTaskId": snapshot.task_id,
                "remoteContextId": snapshot.context_id,
                "remoteStatus": snapshot.status,
            }
            if snapshot.raw:
                metadata["lastRemoteSnapshot"] = snapshot.raw
            self.store.update_delegated_task_status(
                delegation.delegation_id,
                status=snapshot.status or next_status.value,
                metadata=metadata,
            )

            if next_status == TaskStatus.COMPLETED:
                task = self._materialize_remote_proxy_final_answer(
                    task,
                    delegation=delegation,
                    snapshot=snapshot,
                )
            if next_status == task.status:
                return task

            updated = self.store.update_task_status(task.task_id, next_status)
            self.store.append_task_event(
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

    def _materialize_remote_proxy_final_answer(
        self,
        task: TaskRecord,
        *,
        delegation,
        snapshot: RemoteAgentTaskSnapshot,
    ) -> TaskRecord:
        parts, remote_artifact_id = _remote_final_artifact(snapshot.artifacts)
        if not parts:
            return task

        artifact_id = f"{task.task_id}:remote_final_answer"
        if self.store.get_artifact(artifact_id) is not None:
            return self.store.get_task(task.task_id) or task

        assistant_message = self.store.append_message(
            message_id=_new_id("msg"),
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
        artifact = self.store.upsert_artifact(
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
        self.store.append_task_event(
            task_id=task.task_id,
            type="task_artifact_created",
            status=None,
            payload={
                "artifact_id": artifact.artifact_id,
                "kind": "final_answer",
                "source": "remote_agent",
                "remote_agent_id": delegation.remote_agent_id,
                "remote_task_id": snapshot.task_id,
                "remote_artifact_id": remote_artifact_id,
            },
        )
        return self.store.set_task_output_message(task.task_id, assistant_message.message_id)

    def _handle_local_message(
        self,
        *,
        context_id: str,
        input_message_id: str,
        route_decision: MainAgentRouteDecision,
    ) -> LocalMessageResult:
        decision = self._record_message_ingress_route(
            context_id=context_id,
            input_message_id=input_message_id,
            route_decision=route_decision,
        )
        parts = self.local_message_responder.respond(
            direct_message_context_through_input(self.store, context_id, input_message_id)
        )
        with self.store.transaction():
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
            self.store.resolve_message_ingress(
                input_message_id,
                outcome_kind=MessageIngressOutcomeKind.MESSAGE,
                outcome_id=assistant_message.message_id,
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

        decision = self._record_message_ingress_route(
            context_id=context_id,
            input_message_id=input_message_id,
            route_decision=route_decision,
        )
        assistant_message_id = _new_id("msg")
        full_text = ""
        sequence = 0
        for delta in stream_method(direct_message_context_through_input(self.store, context_id, input_message_id)):
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
        with self.store.transaction():
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
            self.store.resolve_message_ingress(
                input_message_id,
                outcome_kind=MessageIngressOutcomeKind.MESSAGE,
                outcome_id=assistant_message.message_id,
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
        queue_execution = self._queue_execution_enabled()
        decision, task = self.store.accept_local_task_from_message(
            decision_id=_new_id("route"),
            context_id=context_id,
            input_message_id=input_message_id,
            route_kind=route_decision.kind,
            route_reason=route_decision.reason,
            route_target_agent_id=route_decision.target_agent_id,
            route_confidence=route_decision.confidence,
            route_metadata=route_decision.metadata,
            task_id=_new_id("task"),
            runtime_thread_id=_new_id("thread"),
            queue_execution=queue_execution,
        )
        task = self._start_accepted_local_task(task)
        return LocalTaskResult(
            kind=RouteDecisionKind.LOCAL_TASK,
            context_id=context_id,
            task_id=task.task_id,
            input_message_id=input_message_id,
            route_decision_id=decision.decision_id,
        )

    def _queue_execution_enabled(self) -> bool:
        return self.local_task_runner is not None and self.task_submitter is not None

    def _start_accepted_local_task(self, task: TaskRecord) -> TaskRecord:
        """Schedule or synchronously run an already-durable local Task."""

        if self.local_task_runner is None:
            return task
        if self._queue_execution_enabled():
            try:
                self._schedule_queued_task_execution(task.task_id)
            except Exception as exc:
                return self._mark_local_task_failed(task.task_id, exc)
            return task
        return self._run_local_task(task.task_id)

    def _run_local_task(
        self,
        task_id: str,
    ):
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
                    if task.status not in {TaskStatus.CREATED, TaskStatus.QUEUED}:
                        return task
                    task = self.store.transition_local_task(task_id, TaskStatus.RUNNING)
                    input_messages = local_task_context(self.store, task_id)
                    route_decision = self.store.get_route_decision_by_message_id(task.input_message_id)
                result = self.local_task_runner.run(
                    input_messages,
                    thread_id=task.runtime_thread_id,
                )
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)

        return self._save_local_task_result(
            task_id,
            result,
            metadata=({"routeDecisionId": route_decision.decision_id} if route_decision is not None else None),
        )

    def _run_queued_task_execution(self, task_id: str):
        """Claim and execute one durably queued local worker command."""

        try:
            with self._track_active_task(task_id):
                with self.store.transaction():
                    claimed = self.store.claim_queued_task_execution(task_id)
                    if claimed is None:
                        task = self.store.get_task(task_id)
                        if task is None:
                            raise ValueError(f"unknown task: {task_id}")
                        return task
                    task, command = claimed
                    route_decision = None
                    input_messages = None
                    if command.kind == QueuedTaskExecutionKind.INITIAL:
                        input_messages = local_task_context(self.store, task_id)
                        route_decision = self.store.get_route_decision_by_message_id(task.input_message_id)

                result = self._run_queued_execution_command(
                    task=task,
                    command=command,
                    input_messages=input_messages,
                )
        except Exception as exc:
            return self._mark_local_task_failed(task_id, exc)

        return self._save_local_task_result(
            task_id,
            result,
            metadata=({"routeDecisionId": route_decision.decision_id} if route_decision is not None else None),
        )

    def _run_queued_execution_command(
        self,
        *,
        task: TaskRecord,
        command: QueuedTaskExecutionRecord,
        input_messages,
    ) -> LocalTaskRunResult:
        if self.local_task_runner is None:
            raise RuntimeError("local task runner is not configured")
        if command.kind == QueuedTaskExecutionKind.INITIAL:
            if input_messages is None:
                raise RuntimeError(f"queued initial task has no input messages: {task.task_id}")
            return self.local_task_runner.run(input_messages, thread_id=task.runtime_thread_id)
        if command.kind == QueuedTaskExecutionKind.APPROVAL:
            approved = command.payload.get("approved")
            reason = command.payload.get("reason")
            if not isinstance(approved, bool) or (reason is not None and not isinstance(reason, str)):
                raise ValueError(f"queued approval command is invalid: {task.task_id}")
            return self.local_task_runner.resume(
                thread_id=task.runtime_thread_id,
                approved=approved,
                reason=reason,
            )
        if command.kind == QueuedTaskExecutionKind.USER_INPUT:
            parts = command.payload.get("parts")
            metadata = command.payload.get("metadata")
            if not isinstance(parts, list) or (metadata is not None and not isinstance(metadata, dict)):
                raise ValueError(f"queued input command is invalid: {task.task_id}")
            return self.local_task_runner.resume_input(
                thread_id=task.runtime_thread_id,
                parts=parts,
                metadata=metadata,
            )
        raise ValueError(f"unsupported queued local execution kind: {command.kind}")

    def _run_queued_task_execution_in_background(self, task_id: str) -> None:
        try:
            self._run_queued_task_execution(task_id)
        except Exception as exc:
            self._mark_local_task_failed(task_id, exc)
        finally:
            self._forget_scheduled_task(task_id)

    def _resume_local_task(self, command: _ApprovalContinuationCommand):
        with self._track_active_task(command.task_id):
            with self.store.transaction():
                task = self.store.get_task(command.task_id)
                if task is None:
                    raise ValueError(f"unknown task: {command.task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(command.task_id)
                self._validate_continuation_command(task, command)
                task = self.store.transition_local_task(command.task_id, TaskStatus.RUNNING)
            try:
                result = self.local_task_runner.resume(
                    thread_id=command.runtime_thread_id,
                    approved=command.approved,
                    reason=command.reason,
                )
            except Exception as exc:
                return self._mark_local_task_failed(command.task_id, exc)
        return self._save_local_task_result(command.task_id, result)

    def _resume_local_task_with_input(
        self,
        command: _InputContinuationCommand,
    ):
        with self._track_active_task(command.task_id):
            with self.store.transaction():
                task = self.store.get_task(command.task_id)
                if task is None:
                    raise ValueError(f"unknown task: {command.task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(command.task_id)
                self._validate_continuation_command(task, command)
                task = self.store.transition_local_task(command.task_id, TaskStatus.RUNNING)
            try:
                result = self.local_task_runner.resume_input(
                    thread_id=command.runtime_thread_id,
                    parts=command.parts,
                    metadata=command.metadata,
                )
            except Exception as exc:
                return self._mark_local_task_failed(command.task_id, exc)
        return self._save_local_task_result(command.task_id, result)

    @staticmethod
    def _validate_continuation_command(
        task: TaskRecord,
        command: _ApprovalContinuationCommand | _InputContinuationCommand,
    ) -> None:
        if task.status != command.expected_status:
            raise ValueError(f"task is not ready for its accepted continuation: {task.task_id}")
        if task.runtime_thread_id != command.runtime_thread_id:
            raise ValueError(f"task runtime thread changed after continuation acceptance: {task.task_id}")

    def _save_local_task_result(self, task_id: str, result, *, metadata: dict | None = None):
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if is_terminal_task_status(task.status):
            return task
        if task.status == TaskStatus.CANCEL_REQUESTED:
            return self._mark_local_task_canceled_after_safe_boundary(task_id)
        result_status = _local_process_status(result)
        input_request = _continuation_input_request(result) if result_status in {
            TaskStatus.INPUT_REQUIRED,
            TaskStatus.AUTH_REQUIRED,
        } else None
        if result_status in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED} and input_request is None:
            return self._mark_local_task_failed(
                task_id,
                ValueError("interrupted task result must include a supported input_request.kind"),
            )

        if result_status == TaskStatus.COMPLETED:
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                observation_artifact_id = self._persist_task_observations(task, result)
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
                    metadata={
                        "kind": "final_answer",
                        "outputMessageId": assistant_message.message_id,
                        **_task_result_execution_metadata(result, observation_artifact_id=observation_artifact_id),
                    },
                )
                self.store.append_task_event(
                    task_id=task_id,
                    type="task_artifact_created",
                    status=None,
                    payload={"artifact_id": artifact.artifact_id, "kind": "final_answer"},
                )
                task = self.store.set_task_output_message(task_id, assistant_message.message_id)
                task = self.store.transition_local_task(
                    task_id,
                    TaskStatus.COMPLETED,
                    payload=_task_result_lifecycle_payload(result, observation_artifact_id=observation_artifact_id),
                )
                self.store.clear_pending_continuation(task_id)
                self.store.clear_queued_task_execution(task_id)
            return task

        if result_status == TaskStatus.RUNNING:
            return task

        if result_status in {TaskStatus.INPUT_REQUIRED, TaskStatus.AUTH_REQUIRED}:
            with self.store.transaction():
                task = self.store.get_task(task_id)
                if task is None:
                    raise ValueError(f"unknown task: {task_id}")
                if is_terminal_task_status(task.status):
                    return task
                if task.status == TaskStatus.CANCEL_REQUESTED:
                    return self._mark_local_task_canceled_after_safe_boundary(task_id)
                observation_artifact_id = self._persist_task_observations(task, result)
                if input_request is not None:
                    self.store.set_pending_continuation(
                        task_id,
                        kind=str(input_request["kind"]),
                        input_request=input_request,
                    )
                active = self.store.transition_local_task(
                    task_id=task_id,
                    target_status=result_status,
                    payload=_task_result_error_payload(result, observation_artifact_id=observation_artifact_id),
                )
                self.store.clear_queued_task_execution(task_id)
            return active

        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                return task
            if task.status == TaskStatus.CANCEL_REQUESTED:
                return self._mark_local_task_canceled_after_safe_boundary(task_id)
            observation_artifact_id = self._persist_task_observations(task, result)
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload={
                    "error_code": result.error_code or "task_not_completed",
                    "error_message": result.error_message
                    or f"local task ended with unsupported status: {result_status.value}",
                    "retryable": result.error_retryable,
                    **_task_result_lifecycle_payload(result, observation_artifact_id=observation_artifact_id),
                },
                error_code=result.error_code or "task_not_completed",
                error_message=result.error_message or f"local task ended with unsupported status: {result_status.value}",
                error_retryable=result.error_retryable,
            )
            self.store.mark_running_tool_invocations_uncertain(
                task_id,
                error_code="task_ended_before_tool_outcome",
                error_message="Task ended before a side-effecting tool outcome was durably recorded.",
                retryable=True,
            )
            self.store.cancel_prepared_tool_invocations(
                task_id,
                reason="task failed before prepared tool execution",
            )
            self.store.clear_pending_continuation(task_id)
            self.store.clear_queued_task_execution(task_id)
        return failed

    def _persist_task_observations(self, task: TaskRecord, result) -> str | None:
        """Persist normalized LangGraph observations without creating another lifecycle owner."""

        observations = _task_result_observations(result)
        if not observations:
            return None
        artifact_id = f"{task.task_id}:tool_observations"
        existing = self.store.get_artifact(artifact_id)
        artifact = self.store.upsert_artifact(
            artifact_id=artifact_id,
            task_id=task.task_id,
            context_id=task.context_id,
            parts=[{"kind": "data", "data": {"observations": observations}}],
            metadata={
                "kind": "tool_observations",
                "observationCount": len(observations),
                **_task_result_execution_metadata(result),
            },
        )
        self.store.append_task_event(
            task_id=task.task_id,
            type="task_artifact_updated" if existing is not None else "task_artifact_created",
            status=None,
            payload={"artifact_id": artifact.artifact_id, "kind": "tool_observations"},
        )
        return artifact.artifact_id

    def _mark_local_task_failed(self, task_id: str, exc: Exception):
        error = error_info_from_exception(exc)
        error_code = error.code.value
        error_message = error.public_message
        failure_payload = {
            "error_code": error_code,
            "error_message": error_message,
            "retryable": error.retryable,
            "execution": {
                "stop_reason": "environment_failure",
                "stop_detail": {"error_code": error_code},
                "residual_risks": [
                    {
                        "category": "environment_failure",
                        "summary": error_message,
                        "retryable": error.retryable,
                    }
                ],
            },
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
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload=failure_payload,
                error_code=error_code,
                error_message=error_message,
                error_retryable=error.retryable,
            )
            self.store.mark_running_tool_invocations_uncertain(
                task_id,
                error_code=error_code,
                error_message=error_message,
                retryable=error.retryable,
            )
            self.store.cancel_prepared_tool_invocations(
                task_id,
                reason="task failed before prepared tool execution",
            )
            self.store.clear_pending_continuation(task_id)
            self.store.clear_queued_task_execution(task_id)
        return failed

    def _mark_local_task_runtime_recovery_failed(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
    ):
        """End an unsafe post-restart slice without pretending it completed."""

        payload = {
            "error_code": error_code,
            "error_message": error_message,
            "retryable": True,
        }
        logger.warning("Local task %s was not recovered: %s", task_id, error_code)
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if is_terminal_task_status(task.status):
                return task
            failed = self.store.transition_local_task(
                task_id,
                TaskStatus.FAILED,
                payload=payload,
                error_code=error_code,
                error_message=error_message,
                error_retryable=True,
            )
            self.store.mark_running_tool_invocations_uncertain(
                task_id,
                error_code=error_code,
                error_message=error_message,
                retryable=True,
            )
            self.store.cancel_prepared_tool_invocations(
                task_id,
                reason="runtime recovery ended before prepared tool execution",
            )
            self.store.clear_pending_continuation(task_id)
            self.store.clear_queued_task_execution(task_id)
        return failed

    def _mark_local_task_canceled_after_safe_boundary(self, task_id: str):
        with self.store.transaction():
            task = self.store.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.status == TaskStatus.CANCELED or is_terminal_task_status(task.status):
                return task
            task = self.store.transition_local_task(
                task_id,
                TaskStatus.CANCELED,
                payload={
                    "execution": {
                        "stop_reason": "canceled",
                        "stop_detail": {"source": "control_plane_cancellation"},
                    }
                },
            )
            self.store.mark_running_tool_invocations_uncertain(
                task_id,
                error_code="task_canceled_during_tool_execution",
                error_message="Task cancellation reached a boundary while a side-effecting tool outcome was unresolved.",
                retryable=False,
            )
            self.store.cancel_prepared_tool_invocations(
                task_id,
                reason="task canceled before prepared tool execution",
            )
            self.store.clear_pending_continuation(task_id)
            self.store.clear_queued_task_execution(task_id)
            return task

    def _track_active_task(self, task_id: str):
        return _ActiveTaskExecution(self._active_task_guard, self._active_task_ids, task_id)

    def _is_task_active(self, task_id: str) -> bool:
        with self._active_task_guard:
            return task_id in self._active_task_ids

    def _request_local_execution_cancellation(self, *, thread_id: str, reason: str | None) -> None:
        """Expose durable cancellation to the active local capability boundary.

        The local runner may not support immediate cancellation (for example a
        lightweight test runner). The Task stays ``cancel_requested`` either
        way, and the worker will project it to ``canceled`` at its next safe
        boundary.
        """

        if self.local_task_runner is None:
            return
        request_cancellation = getattr(self.local_task_runner, "request_cancellation", None)
        if not callable(request_cancellation):
            return
        try:
            request_cancellation(thread_id=thread_id, reason=reason)
        except Exception:
            logger.warning(
                "Unable to signal active local execution cancellation for runtime thread %s",
                thread_id,
                exc_info=True,
            )

    def _is_task_live(self, task_id: str) -> bool:
        with self._active_task_guard:
            return task_id in self._active_task_ids or task_id in self._scheduled_task_ids

    def _discard_terminal_task_checkpoint(self, task: TaskRecord) -> None:
        if task.assigned_agent_id is not None or self.local_task_runner is None:
            return
        discard = getattr(self.local_task_runner, "discard_checkpoint", None)
        if callable(discard):
            discard(thread_id=task.runtime_thread_id)

    def _schedule_queued_task_execution(self, task_id: str) -> bool:
        """Submit once per process; durable claiming prevents duplicate execution."""

        if self.task_submitter is None:
            raise RuntimeError("task submitter is not configured")
        with self._active_task_guard:
            if task_id in self._active_task_ids or task_id in self._scheduled_task_ids:
                return False
            self._scheduled_task_ids.add(task_id)
        try:
            self.task_submitter.submit(self._run_queued_task_execution_in_background, task_id)
        except Exception:
            self._forget_scheduled_task(task_id)
            raise
        return True

    def _forget_scheduled_task(self, task_id: str) -> None:
        with self._active_task_guard:
            self._scheduled_task_ids.discard(task_id)

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

        decision = self._record_message_ingress_route(
            context_id=context_id,
            input_message_id=input_message_id,
            route_decision=route_decision,
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
                self.store.resolve_message_ingress(
                    input_message_id,
                    outcome_kind=MessageIngressOutcomeKind.DELEGATION,
                    outcome_id=delegation_id,
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
            if not remote.task_id or not remote.task_id.strip():
                raise RemoteAgentProtocolError("remote agent task result is missing a task id")
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
                self.store.resolve_message_ingress(
                    input_message_id,
                    outcome_kind=MessageIngressOutcomeKind.DELEGATION,
                    outcome_id=delegation_id,
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


def _remote_task_status(
    status: str | None,
    *,
    fallback: TaskStatus = TaskStatus.FAILED,
) -> TaskStatus:
    if status in {"submitted", "TASK_STATE_SUBMITTED", "created", "queued"}:
        return TaskStatus.QUEUED
    if status in {"working", "TASK_STATE_WORKING", "running"}:
        return TaskStatus.RUNNING
    if status in {"completed", "TASK_STATE_COMPLETED"}:
        return TaskStatus.COMPLETED
    if status in {"canceled", "cancelled", "TASK_STATE_CANCELED"}:
        return TaskStatus.CANCELED
    if status in {"failed", "rejected", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}:
        return TaskStatus.FAILED
    if status in {"input-required", "TASK_STATE_INPUT_REQUIRED"}:
        return TaskStatus.INPUT_REQUIRED
    if status in {"auth-required", "TASK_STATE_AUTH_REQUIRED"}:
        return TaskStatus.AUTH_REQUIRED
    return fallback


def _validate_remote_proxy_snapshot(delegation, snapshot: RemoteAgentTaskSnapshot) -> None:
    """Accept child updates only for the exact persisted remote task identity."""

    expected_task_id = delegation.remote_task_id
    if not expected_task_id:
        raise RemoteAgentProtocolError(
            f"delegated task has no persisted remote task id: {delegation.delegation_id}"
        )
    if snapshot.task_id != expected_task_id:
        raise RemoteAgentProtocolError(
            "remote agent snapshot task id does not match the delegated task "
            f"({snapshot.task_id!r} != {expected_task_id!r})"
        )
    expected_context_id = delegation.remote_context_id
    if (
        expected_context_id is not None
        and snapshot.context_id is not None
        and snapshot.context_id != expected_context_id
    ):
        raise RemoteAgentProtocolError(
            "remote agent snapshot context id does not match the delegated context "
            f"({snapshot.context_id!r} != {expected_context_id!r})"
        )


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


def _message_request_fingerprint(request: MainAgentRequest) -> str:
    return _message_fingerprint(
        role=request.role,
        parts=request.parts,
        metadata=request.metadata,
    )


def _message_fingerprint(*, role: MessageRole, parts: list[dict], metadata: dict) -> str:
    payload = {
        "role": role.value,
        "parts": parts,
        "metadata": metadata,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _retry_message_metadata(
    metadata: dict,
    source_task: TaskRecord,
    retry_attempt: int,
) -> dict:
    """Carry user-facing metadata forward without reusing its routing intent."""

    retry_metadata = deepcopy(metadata)
    retry_metadata.pop("route", None)
    retry_metadata.pop("targetAgentId", None)
    retry_metadata.update(
        {
            "executionMode": "task",
            "retryOfTaskId": source_task.task_id,
            "retryAttempt": retry_attempt,
        }
    )
    return retry_metadata


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

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterator
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

from .commands import (
    AdmitMessageCommand,
    CancelTaskCommand,
    MainAgentCommand,
    MainAgentCommandOutcome,
    MessageCommandOutcome,
    MessageStreamOutcome,
    ReconcileStartupCommand,
    RecordLocalTaskFailureCommand,
    RecordLocalTaskResultCommand,
    RecordRemoteTaskSnapshotCommand,
    RecordRuntimeRecoveryFailureCommand,
    RecordTaskCancellationCommand,
    ResolveApprovalCommand,
    RetryTaskCommand,
    StartupReconciliationOutcome,
    SubmitTaskInputCommand,
    TaskCommandOutcome,
)
from .context import (
    direct_message_context_through_input,
    local_task_context,
    router_context_through_input,
)
from .lifecycle_transactions import (
    LifecyclePostCommitAction,
    LifecyclePostCommitActionKind,
    LifecycleTransactionRunner,
)
from .local_execution import (
    ClaimedLocalExecution,
    InProcessLocalExecutionAdapter,
    LocalExecutionFailed,
    LocalExecutionLifecycleCallbacks,
    LocalExecutionOutcome,
    LocalExecutionSucceeded,
    TaskSubmitter,
)
from .models import (
    ApprovalTaskExecutionPayload,
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
    QueuedTaskExecutionPayload,
    RemoteAgentResult,
    RouteDecisionKind,
    TaskRecord,
    TaskStatus,
    UserInputTaskExecutionPayload,
    is_terminal_task_status,
)
from .remote_agent import RemoteAgentClient, RemoteAgentProtocolError, RemoteAgentTaskSnapshot
from .responder import LocalMessageResponder
from .router import DefaultMainAgentRouter, MainAgentRouteDecision, MainAgentRouter
from .store import MainAgentStore
from .task_outcomes import TaskOutcomeRecorder, remote_task_status
from .task_runner import LocalTaskRunResult, LocalTaskRunner


@dataclass(frozen=True)
class _PreparedMessageRoute:
    context_id: str
    input_message_id: str
    route_decision: MainAgentRouteDecision | None = None
    existing_result: MainAgentResult | None = None


@dataclass(frozen=True)
class _AcceptedRetry:
    task: TaskRecord
    should_start: bool


@dataclass(frozen=True)
class _CommittedCancellation:
    task: TaskRecord
    active_runtime_thread_id: str | None


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
        self._task_outcomes = TaskOutcomeRecorder(store)
        self._lifecycle_transactions = LifecycleTransactionRunner(store)
        self._local_execution = (
            InProcessLocalExecutionAdapter(
                runner=local_task_runner,
                submitter=task_submitter,
                lifecycle=LocalExecutionLifecycleCallbacks(
                    claim=self._claim_local_execution,
                    current_task=self.store.get_task,
                    record_outcome=self._record_local_execution_outcome,
                ),
            )
            if local_task_runner is not None
            else None
        )

    def execute(self, command: MainAgentCommand) -> MainAgentCommandOutcome:
        """Execute one typed lifecycle command through the sole application surface."""

        if isinstance(command, AdmitMessageCommand):
            return MessageCommandOutcome(self._admit_message(command.request))
        if isinstance(command, CancelTaskCommand):
            return TaskCommandOutcome(self._cancel_task(command.task_id, reason=command.reason))
        if isinstance(command, ResolveApprovalCommand):
            return TaskCommandOutcome(
                self._resolve_approval(
                    command.task_id,
                    approved=command.approved,
                    reason=command.reason,
                )
            )
        if isinstance(command, SubmitTaskInputCommand):
            return TaskCommandOutcome(self._submit_task_input(command.task_id, command.request))
        if isinstance(command, RetryTaskCommand):
            return TaskCommandOutcome(self._retry_failed_task(command.task_id))
        if isinstance(command, ReconcileStartupCommand):
            return self._reconcile_startup()
        if isinstance(command, RecordLocalTaskResultCommand):
            return TaskCommandOutcome(
                self._task_outcomes.record_local_result(
                    command.task_id,
                    command.result,
                    metadata=command.metadata,
                )
            )
        if isinstance(command, RecordLocalTaskFailureCommand):
            return TaskCommandOutcome(
                self._task_outcomes.record_failure(command.task_id, command.error)
            )
        if isinstance(command, RecordRuntimeRecoveryFailureCommand):
            return TaskCommandOutcome(
                self._task_outcomes.record_runtime_recovery_failure(
                    command.task_id,
                    error_code=command.error_code,
                    error_message=command.error_message,
                )
            )
        if isinstance(command, RecordTaskCancellationCommand):
            return TaskCommandOutcome(self._task_outcomes.record_cancellation(command.task_id))
        if isinstance(command, RecordRemoteTaskSnapshotCommand):
            return TaskCommandOutcome(
                self._task_outcomes.record_remote_snapshot(command.task_id, command.snapshot)
            )
        raise TypeError(f"unsupported main-agent command: {type(command).__name__}")

    def stream(self, command: AdmitMessageCommand) -> Iterator[MessageStreamOutcome]:
        """Stream one admitted message through the same typed command boundary."""

        if not isinstance(command, AdmitMessageCommand):
            raise TypeError(f"unsupported streaming command: {type(command).__name__}")
        for result in self._stream_admitted_message(command.request):
            yield MessageStreamOutcome(result)

    def handle_message(self, request: MainAgentRequest) -> MainAgentResult:
        outcome = self.execute(AdmitMessageCommand(request))
        if not isinstance(outcome, MessageCommandOutcome):
            raise RuntimeError("message command returned an invalid outcome")
        return outcome.result

    def stream_message(self, request: MainAgentRequest) -> Iterator[MainAgentStreamResult]:
        for outcome in self.stream(AdmitMessageCommand(request)):
            yield outcome.result

    def reconcile_startup(self) -> StartupReconciliationOutcome:
        outcome = self.execute(ReconcileStartupCommand())
        if not isinstance(outcome, StartupReconciliationOutcome):
            raise RuntimeError("startup reconciliation returned an invalid outcome")
        return outcome

    def resume_task(
        self,
        task_id: str,
        *,
        approved: bool,
        reason: str | None = None,
    ) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(
                ResolveApprovalCommand(
                    task_id=task_id,
                    approved=approved,
                    reason=reason,
                )
            )
        )

    def submit_task_input(self, task_id: str, request: MainAgentRequest) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(SubmitTaskInputCommand(task_id=task_id, request=request))
        )

    def retry_failed_task(self, task_id: str) -> TaskRecord:
        return self._task_from_outcome(self.execute(RetryTaskCommand(task_id)))

    def cancel_task(self, task_id: str, *, reason: str | None = None) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(CancelTaskCommand(task_id=task_id, reason=reason))
        )

    @staticmethod
    def _task_from_outcome(outcome: MainAgentCommandOutcome) -> TaskRecord:
        if not isinstance(outcome, TaskCommandOutcome):
            raise RuntimeError("task command returned an invalid outcome")
        return outcome.task

    def _admit_message(self, request: MainAgentRequest) -> MainAgentResult:
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

    def _stream_admitted_message(
        self,
        request: MainAgentRequest,
    ) -> Iterator[MainAgentStreamResult]:
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

    def _reconcile_startup(self) -> StartupReconciliationOutcome:
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
            self._record_runtime_recovery_failure(
                task.task_id,
                error_code="runtime_restart_interrupted",
                error_message="Local task execution was interrupted by a runtime restart.",
            )
            failed_task_ids.append(task.task_id)

        for task in self.store.list_local_tasks_by_statuses({TaskStatus.QUEUED}):
            try:
                command = self.store.get_queued_task_execution(task.task_id)
            except (TypeError, ValueError):
                self._record_runtime_recovery_failure(
                    task.task_id,
                    error_code="runtime_recovery_invalid_command",
                    error_message="Queued local task has an invalid persisted execution command.",
                )
                failed_task_ids.append(task.task_id)
                continue

            if command is None:
                self._record_runtime_recovery_failure(
                    task.task_id,
                    error_code="runtime_recovery_command_missing",
                    error_message="Queued local task has no recoverable execution command.",
                )
                failed_task_ids.append(task.task_id)
                continue
            if command.runtime_thread_id != task.runtime_thread_id:
                self._record_runtime_recovery_failure(
                    task.task_id,
                    error_code="runtime_recovery_thread_mismatch",
                    error_message="Queued local task execution does not match its runtime thread.",
                )
                failed_task_ids.append(task.task_id)
                continue
            if self._local_execution is None or not self._local_execution.supports_startup_recovery:
                self._record_runtime_recovery_failure(
                    task.task_id,
                    error_code="runtime_recovery_unavailable",
                    error_message="No local worker is available to recover the queued task.",
                )
                failed_task_ids.append(task.task_id)
                continue

            try:
                if self._local_execution.wake(task.task_id):
                    scheduled_task_ids.append(task.task_id)
                else:
                    retained_task_ids.append(task.task_id)
            except Exception:
                self._record_runtime_recovery_failure(
                    task.task_id,
                    error_code="runtime_recovery_submission_failed",
                    error_message="Unable to submit the queued local task for recovery.",
                )
                failed_task_ids.append(task.task_id)

        return StartupReconciliationOutcome(
            scheduled_task_ids=tuple(scheduled_task_ids),
            failed_task_ids=tuple(failed_task_ids),
            retained_task_ids=tuple(retained_task_ids),
            failed_message_ids=failed_message_ids,
        )

    def _resolve_approval(
        self,
        task_id: str,
        *,
        approved: bool,
        reason: str | None = None,
    ) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is not None and task.assigned_agent_id is not None:
            raise ValueError(f"delegated task resume is not supported yet: {task_id}")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        command_payload = {
            "approved": approved,
            **({"reason": reason} if reason else {}),
        }

        def accept_continuation() -> TaskRecord:
            task, _expected_status = self._accept_pending_continuation(
                task_id,
                expected_kind="approval_required",
                resume_payload=command_payload,
                operation="approval",
                queued_execution_kind=QueuedTaskExecutionKind.APPROVAL,
                queued_execution_payload=ApprovalTaskExecutionPayload(
                    approved=approved,
                    reason=reason,
                ),
            )
            return task

        outcome = self._lifecycle_transactions.execute(
            accept_continuation,
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                callback=self._wake_committed_local_task,
            ),
        )
        if outcome.post_commit_result is None:
            raise RuntimeError(f"accepted approval did not start local execution: {task_id}")
        return outcome.post_commit_result

    def _retry_failed_task(self, task_id: str) -> TaskRecord:
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

        def accept_retry() -> _AcceptedRetry:
            # Re-check inside the durable acceptance boundary so retry
            # eligibility cannot change between the initial read and insert.
            source = self._retryable_failed_local_task(task_id)
            existing = self.store.get_direct_task_retry(source.task_id)
            if existing is not None:
                return _AcceptedRetry(task=existing, should_start=False)

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
            return _AcceptedRetry(task=task, should_start=True)

        try:
            outcome = self._lifecycle_transactions.execute(
                accept_retry,
                post_commit=LifecyclePostCommitAction(
                    kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                    callback=lambda accepted: (
                        self._start_committed_local_task(accepted.task)
                        if accepted.should_start
                        else accepted.task
                    ),
                ),
            )
        except sqlite3.IntegrityError:
            # The direct-retry lineage index is the concurrency boundary. A
            # simultaneous click should converge on the already-created child.
            existing = self.store.get_direct_task_retry(task_id)
            if existing is not None:
                return existing
            raise
        if outcome.post_commit_result is None:
            raise RuntimeError(f"accepted retry did not start local execution: {task_id}")
        return outcome.post_commit_result

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
        queued_execution_payload: QueuedTaskExecutionPayload | None = None,
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

    def _submit_task_input(
        self,
        task_id: str,
        request: MainAgentRequest,
    ) -> TaskRecord:
        if request.role != MessageRole.USER:
            raise ValueError("task input role must be user")
        if not request.parts:
            raise ValueError("task input parts are required")
        task = self.store.get_task(task_id)
        if task is not None and task.assigned_agent_id is not None:
            raise ValueError(f"delegated task input continuation is not supported yet: {task_id}")
        if self.local_task_runner is None:
            raise ValueError("local task runner is not configured")

        queued_execution_payload = UserInputTaskExecutionPayload.from_values(
            parts=request.parts,
            metadata=request.metadata or None,
        )

        def accept_continuation() -> TaskRecord:
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
            task, _expected_status = self._accept_pending_continuation(
                task_id,
                expected_kind="user_input_required",
                resume_payload={"input_message_id": message.message_id},
                operation="input",
                queued_execution_kind=QueuedTaskExecutionKind.USER_INPUT,
                queued_execution_payload=queued_execution_payload,
            )
            return task

        outcome = self._lifecycle_transactions.execute(
            accept_continuation,
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                callback=self._wake_committed_local_task,
            ),
        )
        if outcome.post_commit_result is None:
            raise RuntimeError(f"accepted input did not start local execution: {task_id}")
        return outcome.post_commit_result

    def _cancel_task(self, task_id: str, *, reason: str | None = None) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if is_terminal_task_status(task.status):
            raise ValueError(f"task is terminal and cannot be canceled: {task_id}")
        if task.assigned_agent_id is not None:
            return self._cancel_remote_proxy_task(task_id, reason=reason)

        def commit_cancellation() -> _CommittedCancellation:
            active_runtime_thread_id: str | None = None
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
            return _CommittedCancellation(
                task=canceled_task,
                active_runtime_thread_id=active_runtime_thread_id,
            )

        outcome = self._lifecycle_transactions.execute(
            commit_cancellation,
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.SIGNAL_LOCAL_CANCELLATION,
                callback=lambda committed: self._signal_committed_cancellation(
                    committed,
                    reason=reason,
                ),
            ),
        )
        return outcome.committed.task

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
        return self._record_remote_snapshot(task_id, snapshot)

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
        return self._record_remote_snapshot(task_id, snapshot)

    def _record_remote_snapshot(
        self,
        task_id: str,
        snapshot: RemoteAgentTaskSnapshot,
    ) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(RecordRemoteTaskSnapshotCommand(task_id=task_id, snapshot=snapshot))
        )

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
        outcome = self._lifecycle_transactions.execute(
            lambda: self.store.accept_local_task_from_message(
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
            ),
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                callback=lambda accepted: self._start_committed_local_task(accepted[1]),
            ),
        )
        decision, accepted_task = outcome.committed
        task = outcome.post_commit_result or accepted_task
        return LocalTaskResult(
            kind=RouteDecisionKind.LOCAL_TASK,
            context_id=context_id,
            task_id=task.task_id,
            input_message_id=input_message_id,
            route_decision_id=decision.decision_id,
        )

    def _queue_execution_enabled(self) -> bool:
        return self._local_execution is not None

    def _start_committed_local_task(self, task: TaskRecord) -> TaskRecord:
        """Wake a committed local execution command through the sole adapter."""

        if self._local_execution is None:
            return task
        try:
            self._local_execution.wake(task.task_id)
        except Exception as exc:
            return self._record_local_task_failure(task.task_id, exc)
        return self.store.get_task(task.task_id) or task

    def _wake_committed_local_task(self, task: TaskRecord) -> TaskRecord:
        return self._start_committed_local_task(task)

    def _signal_committed_cancellation(
        self,
        committed: _CommittedCancellation,
        *,
        reason: str | None,
    ) -> None:
        if committed.active_runtime_thread_id is None:
            return
        if self._local_execution is not None:
            self._local_execution.request_cancellation(
                thread_id=committed.active_runtime_thread_id,
                reason=reason,
            )

    def _claim_local_execution(self, task_id: str) -> ClaimedLocalExecution | None:
        """Atomically claim one durable command and prepare its immutable input."""

        with self.store.transaction():
            claimed = self.store.claim_queued_task_execution(task_id)
            if claimed is None:
                return None
            task, command = claimed
            input_messages = ()
            route_decision_id = None
            if command.kind == QueuedTaskExecutionKind.INITIAL:
                input_messages = tuple(local_task_context(self.store, task_id))
                route_decision = self.store.get_route_decision_by_message_id(
                    task.input_message_id
                )
                if route_decision is not None:
                    route_decision_id = route_decision.decision_id
        return ClaimedLocalExecution(
            task=task,
            command=command,
            input_messages=input_messages,
            route_decision_id=route_decision_id,
        )

    def _record_local_execution_outcome(
        self,
        outcome: LocalExecutionOutcome,
    ) -> TaskRecord:
        """Project one typed adapter outcome through Core lifecycle commands."""

        if isinstance(outcome, LocalExecutionSucceeded):
            metadata = (
                {"routeDecisionId": outcome.route_decision_id}
                if outcome.route_decision_id is not None
                else None
            )
            try:
                return self._record_local_task_result(
                    outcome.task_id,
                    outcome.result,
                    metadata=metadata,
                )
            except Exception as exc:
                return self._record_local_task_failure(outcome.task_id, exc)
        if isinstance(outcome, LocalExecutionFailed):
            return self._record_local_task_failure(outcome.task_id, outcome.error)
        raise TypeError(f"unsupported local execution outcome: {type(outcome)!r}")

    def _record_local_task_result(
        self,
        task_id: str,
        result: LocalTaskRunResult,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(
                RecordLocalTaskResultCommand(
                    task_id=task_id,
                    result=result,
                    metadata=dict(metadata or {}),
                )
            )
        )

    def _record_local_task_failure(self, task_id: str, error: Exception) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(RecordLocalTaskFailureCommand(task_id=task_id, error=error))
        )

    def _record_runtime_recovery_failure(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(
                RecordRuntimeRecoveryFailureCommand(
                    task_id=task_id,
                    error_code=error_code,
                    error_message=error_message,
                )
            )
        )

    def _record_task_cancellation(self, task_id: str) -> TaskRecord:
        return self._task_from_outcome(
            self.execute(RecordTaskCancellationCommand(task_id=task_id))
        )

    def _is_task_active(self, task_id: str) -> bool:
        return self._local_execution is not None and self._local_execution.is_active(task_id)

    def _is_task_live(self, task_id: str) -> bool:
        return self._local_execution is not None and self._local_execution.is_live(task_id)

    def _discard_terminal_task_checkpoint(self, task: TaskRecord) -> None:
        if task.assigned_agent_id is not None or self._local_execution is None:
            return
        self._local_execution.discard_checkpoint(thread_id=task.runtime_thread_id)

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

        client = self.remote_agent_client
        delegation_start = self._lifecycle_transactions.execute(
            lambda: self._record_message_ingress_route(
                context_id=context_id,
                input_message_id=input_message_id,
                route_decision=route_decision,
            ),
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.SEND_REMOTE_MESSAGE,
                callback=lambda _decision: client.send_message(
                    agent=agent,
                    request=request,
                    context_id=context_id,
                    message_id=input_message_id,
                ),
            ),
        )
        decision = delegation_start.committed
        remote = delegation_start.post_commit_result
        if remote is None:
            raise RuntimeError("remote delegation returned no result")
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
                    status=remote_task_status(remote.status),
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

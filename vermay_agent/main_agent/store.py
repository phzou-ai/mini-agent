from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from vermay_agent.storage import AgentStore, utc_now

from .lifecycle import lifecycle_event_type_for_status, validate_local_task_transition
from .models import (
    ArtifactRecord,
    ContextRecord,
    DelegatedTaskRecord,
    DeleteContextResult,
    MessageIngressOutcomeKind,
    MessageIngressRecord,
    MessageIngressState,
    MessageRecord,
    MessageRole,
    PendingContinuationRecord,
    QueuedTaskExecutionKind,
    QueuedTaskExecutionRecord,
    RegisteredAgentRecord,
    RouteDecisionKind,
    RouteDecisionRecord,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
    ToolInvocationApprovalStatus,
    ToolInvocationRecord,
    ToolInvocationStatus,
    is_terminal_task_status,
    normalize_task_status,
)


class MainAgentStore:
    def __init__(self, store: AgentStore) -> None:
        self.store = store
        self._task_event_condition = threading.Condition()
        self._task_event_versions: dict[str, int] = {}

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.store.transaction():
            yield

    def create_context(
        self,
        *,
        context_id: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextRecord:
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO contexts(context_id, title, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (context_id, title, _dumps(metadata or {}), now, now),
        )
        record = self.get_context(context_id)
        if record is None:
            raise RuntimeError(f"failed to create context: {context_id}")
        return record

    def get_context(self, context_id: str) -> ContextRecord | None:
        rows = self.store.query(
            """
            SELECT context_id, title, metadata, created_at, updated_at
            FROM contexts
            WHERE context_id=?
            """,
            (context_id,),
        )
        if not rows:
            return None
        return _context_from_row(rows[0])

    def list_contexts(self) -> list[ContextRecord]:
        rows = self.store.query(
            """
            SELECT context_id, title, metadata, created_at, updated_at
            FROM contexts
            ORDER BY updated_at DESC
            """
        )
        return [_context_from_row(row) for row in rows]

    def touch_context(self, context_id: str) -> None:
        self.store.execute("UPDATE contexts SET updated_at=? WHERE context_id=?", (utc_now(), context_id))

    def update_context_title(self, context_id: str, *, title: str | None) -> ContextRecord | None:
        self.store.execute("UPDATE contexts SET title=? WHERE context_id=?", (title, context_id))
        return self.get_context(context_id)

    def append_message(
        self,
        *,
        message_id: str,
        context_id: str,
        role: MessageRole,
        parts: list[dict[str, Any]],
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageRecord:
        # Allocation and insert share one transaction so independent SQLite
        # connections cannot assign the same Context-local sequence.
        with self.transaction():
            if self.get_context(context_id) is None:
                raise ValueError(f"unknown context: {context_id}")
            existing = self.get_message(message_id)
            if existing is not None:
                if (
                    existing.context_id == context_id
                    and existing.role == role
                    and existing.parts == parts
                    and existing.task_id == task_id
                ):
                    return existing
                raise ValueError(f"message conflict: {message_id}")

            now = utc_now()
            cursor = self.store.execute(
                """
                UPDATE contexts
                SET next_message_sequence=next_message_sequence + 1, updated_at=?
                WHERE context_id=?
                """,
                (now, context_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"unknown context: {context_id}")
            rows = self.store.query(
                "SELECT next_message_sequence FROM contexts WHERE context_id=?",
                (context_id,),
            )
            context_sequence = int(rows[0]["next_message_sequence"]) - 1
            self.store.execute(
                """
                INSERT INTO messages(
                    message_id, context_id, context_sequence, role, parts, task_id, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    context_id,
                    context_sequence,
                    role.value,
                    _dumps(parts),
                    task_id,
                    _dumps(metadata or {}),
                    now,
                ),
            )
            record = self.get_message(message_id)
            if record is None:
                raise RuntimeError(f"failed to append message: {message_id}")
            return record

    def get_message(self, message_id: str) -> MessageRecord | None:
        rows = self.store.query(
            """
            SELECT message_id, context_id, context_sequence, role, parts, task_id, metadata, created_at
            FROM messages
            WHERE message_id=?
            """,
            (message_id,),
        )
        if not rows:
            return None
        return _message_from_row(rows[0])

    def reserve_message_ingress(
        self,
        *,
        message_id: str,
        context_id: str,
        request_fingerprint: str,
    ) -> tuple[MessageIngressRecord, bool]:
        """Create the durable execution owner for a top-level Message once.

        Callers should use this in the same transaction that persisted the
        input Message. The primary key is the cross-process idempotency
        boundary; `created` identifies the one caller allowed to route it.
        """

        if self.get_context(context_id) is None:
            raise ValueError(f"unknown context: {context_id}")
        message = self.get_message(message_id)
        if message is None:
            raise ValueError(f"unknown message: {message_id}")
        if message.context_id != context_id:
            raise ValueError(f"message context mismatch: {message_id}")

        now = utc_now()
        cursor = self.store.execute(
            """
            INSERT INTO main_agent_message_ingress(
                message_id, context_id, request_fingerprint, state,
                route_decision_id, outcome_kind, outcome_id,
                error_code, error_message, error_http_status, error_retryable,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, 0, ?, ?)
            ON CONFLICT(message_id) DO NOTHING
            """,
            (
                message_id,
                context_id,
                request_fingerprint,
                MessageIngressState.IN_PROGRESS.value,
                now,
                now,
            ),
        )
        record = self.get_message_ingress(message_id)
        if record is None:
            raise RuntimeError(f"failed to reserve message ingress: {message_id}")
        return record, cursor.rowcount > 0

    def get_message_ingress(self, message_id: str) -> MessageIngressRecord | None:
        rows = self.store.query(
            """
            SELECT message_id, context_id, request_fingerprint, state,
                   route_decision_id, outcome_kind, outcome_id,
                   error_code, error_message, error_http_status, error_retryable,
                   created_at, updated_at
            FROM main_agent_message_ingress
            WHERE message_id=?
            """,
            (message_id,),
        )
        if not rows:
            return None
        return _message_ingress_from_row(rows[0])

    def list_failed_message_ingresses(self, context_id: str) -> list[MessageIngressRecord]:
        """Return terminal direct-message failures for one Context.

        The ingress record is the durable owner of a direct-message failure.
        Callers use this read helper to project failures beside their input
        Messages without storing a synthetic agent Message.
        """

        rows = self.store.query(
            """
            SELECT message_id, context_id, request_fingerprint, state,
                   route_decision_id, outcome_kind, outcome_id,
                   error_code, error_message, error_http_status, error_retryable,
                   created_at, updated_at
            FROM main_agent_message_ingress
            WHERE context_id=? AND state=?
            ORDER BY updated_at ASC, message_id ASC
            """,
            (context_id, MessageIngressState.FAILED.value),
        )
        return [_message_ingress_from_row(row) for row in rows]

    def fail_in_progress_message_ingresses(
        self,
        *,
        error_code: str,
        error_message: str,
        error_http_status: int,
        retryable: bool,
    ) -> tuple[str, ...]:
        """Terminally fail ingress rows abandoned by a previous runtime process.

        This is intentionally a startup-recovery primitive, not a request-time
        timeout. A live process may legitimately be waiting on a slow model,
        whereas an ingress that survived a process boundary has no owner left
        that can resolve it.
        """

        with self.transaction():
            rows = self.store.query(
                """
                SELECT message_id
                FROM main_agent_message_ingress
                WHERE state=?
                ORDER BY created_at ASC, message_id ASC
                """,
                (MessageIngressState.IN_PROGRESS.value,),
            )
            message_ids: list[str] = []
            for row in rows:
                message_id = str(row["message_id"])
                cursor = self.store.execute(
                    """
                    UPDATE main_agent_message_ingress
                    SET state=?, error_code=?, error_message=?, error_http_status=?,
                        error_retryable=?, updated_at=?
                    WHERE message_id=? AND state=?
                    """,
                    (
                        MessageIngressState.FAILED.value,
                        error_code,
                        error_message,
                        error_http_status,
                        int(retryable),
                        utc_now(),
                        message_id,
                        MessageIngressState.IN_PROGRESS.value,
                    ),
                )
                if cursor.rowcount == 1:
                    message_ids.append(message_id)
            return tuple(message_ids)

    def set_message_ingress_route_decision(
        self,
        message_id: str,
        *,
        route_decision_id: str,
    ) -> MessageIngressRecord:
        if self.get_route_decision(route_decision_id) is None:
            raise ValueError(f"unknown route decision: {route_decision_id}")
        cursor = self.store.execute(
            """
            UPDATE main_agent_message_ingress
            SET route_decision_id=?, updated_at=?
            WHERE message_id=? AND state=?
            """,
            (
                route_decision_id,
                utc_now(),
                message_id,
                MessageIngressState.IN_PROGRESS.value,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"message ingress is not in progress: {message_id}")
        record = self.get_message_ingress(message_id)
        if record is None:
            raise RuntimeError(f"failed to update message ingress route: {message_id}")
        return record

    def resolve_message_ingress(
        self,
        message_id: str,
        *,
        outcome_kind: MessageIngressOutcomeKind,
        outcome_id: str,
    ) -> MessageIngressRecord:
        cursor = self.store.execute(
            """
            UPDATE main_agent_message_ingress
            SET state=?, outcome_kind=?, outcome_id=?,
                error_code=NULL, error_message=NULL, error_http_status=NULL,
                error_retryable=0, updated_at=?
            WHERE message_id=? AND state=?
            """,
            (
                MessageIngressState.RESOLVED.value,
                outcome_kind.value,
                outcome_id,
                utc_now(),
                message_id,
                MessageIngressState.IN_PROGRESS.value,
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"message ingress is not in progress: {message_id}")
        record = self.get_message_ingress(message_id)
        if record is None:
            raise RuntimeError(f"failed to resolve message ingress: {message_id}")
        return record

    def fail_message_ingress(
        self,
        message_id: str,
        *,
        error_code: str,
        error_message: str,
        error_http_status: int,
        retryable: bool,
    ) -> MessageIngressRecord:
        cursor = self.store.execute(
            """
            UPDATE main_agent_message_ingress
            SET state=?, error_code=?, error_message=?, error_http_status=?,
                error_retryable=?, updated_at=?
            WHERE message_id=? AND state=?
            """,
            (
                MessageIngressState.FAILED.value,
                error_code,
                error_message,
                error_http_status,
                int(retryable),
                utc_now(),
                message_id,
                MessageIngressState.IN_PROGRESS.value,
            ),
        )
        if cursor.rowcount == 0:
            record = self.get_message_ingress(message_id)
            if record is not None:
                return record
            raise ValueError(f"unknown message ingress: {message_id}")
        record = self.get_message_ingress(message_id)
        if record is None:
            raise RuntimeError(f"failed to record message ingress failure: {message_id}")
        return record

    def list_context_messages(
        self,
        context_id: str,
        *,
        limit: int | None = None,
        through_sequence: int | None = None,
    ) -> list[MessageRecord]:
        where = ["context_id=?"]
        values: list[Any] = [context_id]
        if through_sequence is not None:
            where.append("context_sequence <= ?")
            values.append(through_sequence)
        sql = """
            SELECT message_id, context_id, context_sequence, role, parts, task_id, metadata, created_at
            FROM messages
            WHERE {where}
            ORDER BY context_sequence ASC
        """.format(where=" AND ".join(where))
        if limit is not None:
            sql = f"SELECT * FROM ({sql}) ORDER BY context_sequence DESC LIMIT ?"
            values.append(limit)
        rows = self.store.query(sql, values)
        records = [_message_from_row(row) for row in rows]
        if limit is not None:
            return list(reversed(records))
        return records

    def record_route_decision(
        self,
        *,
        decision_id: str,
        context_id: str,
        message_id: str,
        kind: RouteDecisionKind,
        reason: str,
        target_agent_id: str | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RouteDecisionRecord:
        if self.get_message(message_id) is None:
            raise ValueError(f"unknown message: {message_id}")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO route_decisions(
                decision_id, context_id, message_id, kind, target_agent_id, reason, confidence, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                context_id,
                message_id,
                kind.value,
                target_agent_id,
                reason,
                confidence,
                _dumps(metadata or {}),
                now,
            ),
        )
        record = self.get_route_decision(decision_id)
        if record is None:
            raise RuntimeError(f"failed to record route decision: {decision_id}")
        return record

    def get_route_decision(self, decision_id: str) -> RouteDecisionRecord | None:
        rows = self.store.query(
            """
            SELECT decision_id, context_id, message_id, kind, target_agent_id, reason, confidence, metadata, created_at
            FROM route_decisions
            WHERE decision_id=?
            """,
            (decision_id,),
        )
        if not rows:
            return None
        return _route_decision_from_row(rows[0])

    def get_route_decision_by_message_id(self, message_id: str) -> RouteDecisionRecord | None:
        rows = self.store.query(
            """
            SELECT decision_id, context_id, message_id, kind, target_agent_id, reason, confidence, metadata, created_at
            FROM route_decisions
            WHERE message_id=?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (message_id,),
        )
        if not rows:
            return None
        return _route_decision_from_row(rows[0])

    def list_context_route_decisions(self, context_id: str) -> list[RouteDecisionRecord]:
        rows = self.store.query(
            """
            SELECT decision_id, context_id, message_id, kind, reason, confidence, target_agent_id, metadata, created_at
            FROM route_decisions
            WHERE context_id=?
            ORDER BY created_at ASC
            """,
            (context_id,),
        )
        return [_route_decision_from_row(row) for row in rows]

    def create_task(
        self,
        *,
        task_id: str,
        context_id: str,
        input_message_id: str,
        runtime_thread_id: str,
        status: TaskStatus = TaskStatus.CREATED,
        assigned_agent_id: str | None = None,
        retry_of_task_id: str | None = None,
        attempt: int = 1,
        model: dict[str, Any] | None = None,
        max_loops: int | None = None,
        mcp: dict[str, Any] | None = None,
    ) -> TaskRecord:
        input_message = self.get_message(input_message_id)
        if input_message is None:
            raise ValueError(f"unknown input message: {input_message_id}")
        if input_message.context_id != context_id:
            raise ValueError(f"input message context mismatch: {input_message_id}")
        if input_message.context_sequence <= 0:
            raise ValueError(f"input message has no context sequence: {input_message_id}")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO main_agent_tasks(
                task_id, context_id, status, input_message_id, input_context_sequence, output_message_id, runtime_thread_id,
                assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp, error_code,
                error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                context_id,
                status.value,
                input_message_id,
                input_message.context_sequence,
                None,
                runtime_thread_id,
                assigned_agent_id,
                retry_of_task_id,
                attempt,
                _dumps(model) if model is not None else None,
                max_loops,
                _dumps(mcp) if mcp is not None else None,
                None,
                None,
                now,
                now,
            ),
        )
        record = self.get_task(task_id)
        if record is None:
            raise RuntimeError(f"failed to create task: {task_id}")
        return record

    def create_local_task(
        self,
        *,
        task_id: str,
        context_id: str,
        input_message_id: str,
        runtime_thread_id: str,
        retry_of_task_id: str | None = None,
        attempt: int = 1,
        model: dict[str, Any] | None = None,
        max_loops: int | None = None,
        mcp: dict[str, Any] | None = None,
    ) -> TaskRecord:
        """Create a locally owned process and record its initial lifecycle fact."""

        with self.transaction():
            task = self.create_task(
                task_id=task_id,
                context_id=context_id,
                input_message_id=input_message_id,
                runtime_thread_id=runtime_thread_id,
                status=TaskStatus.CREATED,
                retry_of_task_id=retry_of_task_id,
                attempt=attempt,
                model=model,
                max_loops=max_loops,
                mcp=mcp,
            )
            self.append_task_event(
                task_id=task.task_id,
                type="task_created",
                status=TaskStatus.CREATED,
            )
            return task

    def accept_local_task_from_message(
        self,
        *,
        decision_id: str,
        context_id: str,
        input_message_id: str,
        route_kind: RouteDecisionKind,
        route_reason: str,
        route_target_agent_id: str | None,
        route_confidence: float | None,
        route_metadata: dict[str, Any] | None,
        task_id: str,
        runtime_thread_id: str,
        queue_execution: bool,
    ) -> tuple[RouteDecisionRecord, TaskRecord]:
        """Persist one local Task acceptance as a single durable boundary.

        The routing decision, local Task, Message ingress result, queue state,
        and initial execution command become visible together. A worker is
        scheduled only after this method returns, so it can never observe a
        partially accepted Task.
        """

        if route_kind != RouteDecisionKind.LOCAL_TASK:
            raise ValueError(f"local task acceptance requires local_task route: {route_kind.value}")

        with self.transaction():
            decision = self.record_route_decision(
                decision_id=decision_id,
                context_id=context_id,
                message_id=input_message_id,
                kind=route_kind,
                target_agent_id=route_target_agent_id,
                reason=route_reason,
                confidence=route_confidence,
                metadata=route_metadata,
            )
            self.set_message_ingress_route_decision(
                input_message_id,
                route_decision_id=decision.decision_id,
            )
            task = self.create_local_task(
                task_id=task_id,
                context_id=context_id,
                input_message_id=input_message_id,
                runtime_thread_id=runtime_thread_id,
            )
            self.resolve_message_ingress(
                input_message_id,
                outcome_kind=MessageIngressOutcomeKind.TASK,
                outcome_id=task.task_id,
            )
            if queue_execution:
                task = self.transition_local_task(task.task_id, TaskStatus.QUEUED)
                self.enqueue_task_execution(
                    task.task_id,
                    kind=QueuedTaskExecutionKind.INITIAL,
                    runtime_thread_id=task.runtime_thread_id,
                )
            return decision, task

    def get_task(self, task_id: str) -> TaskRecord | None:
        rows = self.store.query(
            """
            SELECT task_id, context_id, status, input_message_id, input_context_sequence, output_message_id, runtime_thread_id,
                   assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp, error_code,
                   error_message, created_at, updated_at
            FROM main_agent_tasks
            WHERE task_id=?
            """,
            (task_id,),
        )
        if not rows:
            return None
        return _task_from_row(rows[0])

    def get_task_by_runtime_thread_id(self, runtime_thread_id: str) -> TaskRecord | None:
        rows = self.store.query(
            """
            SELECT task_id, context_id, status, input_message_id, input_context_sequence, output_message_id,
                   runtime_thread_id, assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp,
                   error_code, error_message, created_at, updated_at
            FROM main_agent_tasks
            WHERE runtime_thread_id=?
            """,
            (runtime_thread_id,),
        )
        if not rows:
            return None
        return _task_from_row(rows[0])

    def list_context_tasks(self, context_id: str) -> list[TaskRecord]:
        rows = self.store.query(
            """
            SELECT task_id, context_id, status, input_message_id, input_context_sequence, output_message_id, runtime_thread_id,
                   assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp, error_code,
                   error_message, created_at, updated_at
            FROM main_agent_tasks
            WHERE context_id=?
            ORDER BY created_at ASC
            """,
            (context_id,),
        )
        return [_task_from_row(row) for row in rows]

    def list_local_tasks_by_statuses(self, statuses: set[TaskStatus]) -> list[TaskRecord]:
        """Return locally owned processes in one of the supplied states."""

        if not statuses:
            return []
        values = tuple(sorted(status.value for status in statuses))
        placeholders = ", ".join("?" for _ in values)
        rows = self.store.query(
            f"""
            SELECT task_id, context_id, status, input_message_id, input_context_sequence, output_message_id,
                   runtime_thread_id, assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp,
                   error_code, error_message, created_at, updated_at
            FROM main_agent_tasks
            WHERE assigned_agent_id IS NULL AND status IN ({placeholders})
            ORDER BY created_at ASC, task_id ASC
            """,
            values,
        )
        return [_task_from_row(row) for row in rows]

    def create_or_get_tool_invocation(
        self,
        *,
        invocation_id: str,
        task_id: str,
        context_id: str,
        runtime_thread_id: str,
        loop_index: int,
        tool_call_id: str,
        tool_name: str,
        normalized_arguments: dict[str, Any],
        arguments_digest: str,
        capability: dict[str, Any],
        side_effect_level: str,
        idempotency_key: str | None,
        approval_required: bool,
    ) -> ToolInvocationRecord:
        """Create one durable external-effect identity, or load its replay.

        The caller supplies deterministic identifiers derived from the stable
        Task/runtime/tool-call boundary. Replaying the same graph checkpoint
        must return the same record rather than creating another side effect.
        """

        if loop_index < 1:
            raise ValueError("tool invocation loop_index must be positive")
        if not invocation_id or not tool_call_id or not tool_name or not arguments_digest:
            raise ValueError("tool invocation identity fields are required")

        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.context_id != context_id:
                raise ValueError(f"tool invocation context mismatch: {task_id}")
            if task.runtime_thread_id != runtime_thread_id:
                raise ValueError(f"tool invocation runtime thread mismatch: {task_id}")
            if task.assigned_agent_id is not None:
                raise ValueError(f"remote proxy task cannot own a local tool invocation: {task_id}")

            existing = self.get_tool_invocation(invocation_id)
            if existing is not None:
                _validate_tool_invocation_identity(
                    existing,
                    task_id=task_id,
                    context_id=context_id,
                    runtime_thread_id=runtime_thread_id,
                    loop_index=loop_index,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments_digest=arguments_digest,
                )
                return existing

            identity_match = self.get_tool_invocation_by_execution_identity(
                task_id=task_id,
                runtime_thread_id=runtime_thread_id,
                loop_index=loop_index,
                tool_call_id=tool_call_id,
                arguments_digest=arguments_digest,
            )
            if identity_match is not None:
                _validate_tool_invocation_identity(
                    identity_match,
                    task_id=task_id,
                    context_id=context_id,
                    runtime_thread_id=runtime_thread_id,
                    loop_index=loop_index,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments_digest=arguments_digest,
                )
                return identity_match

            now = utc_now()
            self.store.execute(
                """
                INSERT INTO main_agent_tool_invocations(
                    invocation_id, task_id, context_id, runtime_thread_id, loop_index, tool_call_id, tool_name,
                    normalized_arguments, arguments_digest, capability, side_effect_level, idempotency_key,
                    approval_required, approval_status, approval_reason, status, result_artifact_id,
                    error_code, error_message, error_retryable, created_at, started_at, completed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, 0, ?, NULL, NULL, ?)
                """,
                (
                    invocation_id,
                    task_id,
                    context_id,
                    runtime_thread_id,
                    loop_index,
                    tool_call_id,
                    tool_name,
                    _dumps(normalized_arguments),
                    arguments_digest,
                    _dumps(capability),
                    side_effect_level,
                    idempotency_key,
                    int(approval_required),
                    (
                        ToolInvocationApprovalStatus.PENDING.value
                        if approval_required
                        else ToolInvocationApprovalStatus.NOT_REQUIRED.value
                    ),
                    ToolInvocationStatus.PREPARED.value,
                    now,
                    now,
                ),
            )
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise RuntimeError(f"failed to create tool invocation: {invocation_id}")
            return record

    def get_tool_invocation(self, invocation_id: str) -> ToolInvocationRecord | None:
        rows = self.store.query(
            """
            SELECT invocation_id, task_id, context_id, runtime_thread_id, loop_index, tool_call_id, tool_name,
                   normalized_arguments, arguments_digest, capability, side_effect_level, idempotency_key,
                   approval_required, approval_status, approval_reason, status, result_artifact_id,
                   error_code, error_message, error_retryable, created_at, started_at, completed_at, updated_at
            FROM main_agent_tool_invocations
            WHERE invocation_id=?
            """,
            (invocation_id,),
        )
        if not rows:
            return None
        return _tool_invocation_from_row(rows[0])

    def get_tool_invocation_by_execution_identity(
        self,
        *,
        task_id: str,
        runtime_thread_id: str,
        loop_index: int,
        tool_call_id: str,
        arguments_digest: str,
    ) -> ToolInvocationRecord | None:
        rows = self.store.query(
            """
            SELECT invocation_id, task_id, context_id, runtime_thread_id, loop_index, tool_call_id, tool_name,
                   normalized_arguments, arguments_digest, capability, side_effect_level, idempotency_key,
                   approval_required, approval_status, approval_reason, status, result_artifact_id,
                   error_code, error_message, error_retryable, created_at, started_at, completed_at, updated_at
            FROM main_agent_tool_invocations
            WHERE task_id=? AND runtime_thread_id=? AND loop_index=?
              AND tool_call_id=? AND arguments_digest=?
            """,
            (task_id, runtime_thread_id, loop_index, tool_call_id, arguments_digest),
        )
        if not rows:
            return None
        return _tool_invocation_from_row(rows[0])

    def find_latest_tool_invocation_for_effect(
        self,
        *,
        task_id: str,
        tool_name: str,
        arguments_digest: str,
    ) -> ToolInvocationRecord | None:
        rows = self.store.query(
            """
            SELECT invocation_id, task_id, context_id, runtime_thread_id, loop_index, tool_call_id, tool_name,
                   normalized_arguments, arguments_digest, capability, side_effect_level, idempotency_key,
                   approval_required, approval_status, approval_reason, status, result_artifact_id,
                   error_code, error_message, error_retryable, created_at, started_at, completed_at, updated_at
            FROM main_agent_tool_invocations
            WHERE task_id=? AND tool_name=? AND arguments_digest=?
            ORDER BY created_at DESC, invocation_id DESC
            LIMIT 1
            """,
            (task_id, tool_name, arguments_digest),
        )
        if not rows:
            return None
        return _tool_invocation_from_row(rows[0])

    def list_task_tool_invocations(self, task_id: str) -> list[ToolInvocationRecord]:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        rows = self.store.query(
            """
            SELECT invocation_id, task_id, context_id, runtime_thread_id, loop_index, tool_call_id, tool_name,
                   normalized_arguments, arguments_digest, capability, side_effect_level, idempotency_key,
                   approval_required, approval_status, approval_reason, status, result_artifact_id,
                   error_code, error_message, error_retryable, created_at, started_at, completed_at, updated_at
            FROM main_agent_tool_invocations
            WHERE task_id=?
            ORDER BY created_at ASC, invocation_id ASC
            """,
            (task_id,),
        )
        return [_tool_invocation_from_row(row) for row in rows]

    def begin_tool_invocation(self, invocation_id: str) -> ToolInvocationRecord:
        """Move a prepared side effect to running at the execution boundary."""

        with self.transaction():
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise ValueError(f"unknown tool invocation: {invocation_id}")
            if record.status != ToolInvocationStatus.PREPARED:
                return record
            if record.approval_required and record.approval_status != ToolInvocationApprovalStatus.APPROVED:
                return record
            now = utc_now()
            self.store.execute(
                """
                UPDATE main_agent_tool_invocations
                SET status=?, started_at=?, updated_at=?
                WHERE invocation_id=? AND status=?
                """,
                (
                    ToolInvocationStatus.RUNNING.value,
                    now,
                    now,
                    invocation_id,
                    ToolInvocationStatus.PREPARED.value,
                ),
            )
            updated = self.get_tool_invocation(invocation_id)
            if updated is None:
                raise RuntimeError(f"failed to start tool invocation: {invocation_id}")
            return updated

    def resolve_tool_invocation_approval(
        self,
        *,
        task_id: str,
        invocation_id: str,
        tool_name: str,
        arguments_digest: str,
        approved: bool,
        reason: str | None,
    ) -> ToolInvocationRecord:
        """Bind an operator decision to the exact prepared effect request."""

        with self.transaction():
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise ValueError(f"unknown tool invocation: {invocation_id}")
            if record.task_id != task_id or record.tool_name != tool_name or record.arguments_digest != arguments_digest:
                raise ValueError(f"approval does not match tool invocation: {invocation_id}")
            if not record.approval_required:
                raise ValueError(f"tool invocation does not require approval: {invocation_id}")

            target_approval = (
                ToolInvocationApprovalStatus.APPROVED
                if approved
                else ToolInvocationApprovalStatus.REJECTED
            )
            if record.approval_status == target_approval:
                return record
            if record.approval_status != ToolInvocationApprovalStatus.PENDING:
                raise ValueError(f"tool invocation approval is already resolved: {invocation_id}")
            if record.status != ToolInvocationStatus.PREPARED:
                raise ValueError(f"tool invocation is no longer awaiting approval: {invocation_id}")

            now = utc_now()
            if approved:
                self.store.execute(
                    """
                    UPDATE main_agent_tool_invocations
                    SET approval_status=?, approval_reason=?, updated_at=?
                    WHERE invocation_id=?
                    """,
                    (target_approval.value, reason, now, invocation_id),
                )
            else:
                self.store.execute(
                    """
                    UPDATE main_agent_tool_invocations
                    SET approval_status=?, approval_reason=?, status=?, completed_at=?, updated_at=?
                    WHERE invocation_id=?
                    """,
                    (
                        target_approval.value,
                        reason,
                        ToolInvocationStatus.CANCELED.value,
                        now,
                        now,
                        invocation_id,
                    ),
                )
            updated = self.get_tool_invocation(invocation_id)
            if updated is None:
                raise RuntimeError(f"failed to resolve tool invocation approval: {invocation_id}")
            return updated

    def resolve_pending_tool_invocation_approval(
        self,
        *,
        task_id: str,
        input_request: dict[str, Any],
        approved: bool,
        reason: str | None,
    ) -> ToolInvocationRecord | None:
        """Resolve an invocation only when a runtime interrupt carries its binding."""

        invocation_id = input_request.get("invocationId")
        tool_name = input_request.get("toolName")
        arguments_digest = input_request.get("argumentsDigest")
        if invocation_id is None and tool_name is None and arguments_digest is None:
            return None
        if not all(isinstance(value, str) and value for value in (invocation_id, tool_name, arguments_digest)):
            raise ValueError("approval request has an invalid tool invocation binding")
        return self.resolve_tool_invocation_approval(
            task_id=task_id,
            invocation_id=invocation_id,
            tool_name=tool_name,
            arguments_digest=arguments_digest,
            approved=approved,
            reason=reason,
        )

    def complete_tool_invocation_success(
        self,
        *,
        invocation_id: str,
        artifact_parts: list[dict[str, Any]],
        artifact_metadata: dict[str, Any],
    ) -> ToolInvocationRecord:
        """Persist a tool result artifact and the succeeded effect fact together."""

        with self.transaction():
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise ValueError(f"unknown tool invocation: {invocation_id}")
            if record.status == ToolInvocationStatus.SUCCEEDED:
                return record
            if record.status != ToolInvocationStatus.RUNNING:
                raise ValueError(f"tool invocation is not running: {invocation_id}")
            artifact_id = f"{invocation_id}:result"
            artifact = self.upsert_artifact(
                artifact_id=artifact_id,
                task_id=record.task_id,
                context_id=record.context_id,
                parts=artifact_parts,
                metadata=artifact_metadata,
            )
            self.append_task_event(
                task_id=record.task_id,
                type="task_artifact_created",
                status=None,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "kind": "tool_invocation_result",
                    "invocation_id": invocation_id,
                },
            )
            now = utc_now()
            self.store.execute(
                """
                UPDATE main_agent_tool_invocations
                SET status=?, result_artifact_id=?, error_code=NULL, error_message=NULL,
                    error_retryable=0, completed_at=?, updated_at=?
                WHERE invocation_id=?
                """,
                (
                    ToolInvocationStatus.SUCCEEDED.value,
                    artifact.artifact_id,
                    now,
                    now,
                    invocation_id,
                ),
            )
            updated = self.get_tool_invocation(invocation_id)
            if updated is None:
                raise RuntimeError(f"failed to complete tool invocation: {invocation_id}")
            return updated

    def mark_tool_invocation_uncertain(
        self,
        invocation_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = False,
    ) -> ToolInvocationRecord:
        """Record a possibly executed effect that must not be replayed automatically."""

        with self.transaction():
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise ValueError(f"unknown tool invocation: {invocation_id}")
            if record.status == ToolInvocationStatus.UNCERTAIN:
                return record
            if record.status in {
                ToolInvocationStatus.SUCCEEDED,
                ToolInvocationStatus.FAILED,
                ToolInvocationStatus.CANCELED,
            }:
                return record
            now = utc_now()
            self.store.execute(
                """
                UPDATE main_agent_tool_invocations
                SET status=?, error_code=?, error_message=?, error_retryable=?, completed_at=?, updated_at=?
                WHERE invocation_id=?
                """,
                (
                    ToolInvocationStatus.UNCERTAIN.value,
                    error_code,
                    error_message,
                    int(retryable),
                    now,
                    now,
                    invocation_id,
                ),
            )
            updated = self.get_tool_invocation(invocation_id)
            if updated is None:
                raise RuntimeError(f"failed to mark tool invocation uncertain: {invocation_id}")
            return updated

    def cancel_tool_invocation(self, invocation_id: str, *, reason: str) -> ToolInvocationRecord:
        with self.transaction():
            record = self.get_tool_invocation(invocation_id)
            if record is None:
                raise ValueError(f"unknown tool invocation: {invocation_id}")
            if record.status == ToolInvocationStatus.CANCELED:
                return record
            if record.status != ToolInvocationStatus.PREPARED:
                return record
            now = utc_now()
            self.store.execute(
                """
                UPDATE main_agent_tool_invocations
                SET status=?, error_code=?, error_message=?, completed_at=?, updated_at=?
                WHERE invocation_id=?
                """,
                (
                    ToolInvocationStatus.CANCELED.value,
                    "tool_invocation_canceled",
                    reason,
                    now,
                    now,
                    invocation_id,
                ),
            )
            updated = self.get_tool_invocation(invocation_id)
            if updated is None:
                raise RuntimeError(f"failed to cancel tool invocation: {invocation_id}")
            return updated

    def cancel_prepared_tool_invocations(self, task_id: str, *, reason: str) -> int:
        now = utc_now()
        cursor = self.store.execute(
            """
            UPDATE main_agent_tool_invocations
            SET status=?, error_code=?, error_message=?, completed_at=?, updated_at=?
            WHERE task_id=? AND status=?
            """,
            (
                ToolInvocationStatus.CANCELED.value,
                "tool_invocation_canceled",
                reason,
                now,
                now,
                task_id,
                ToolInvocationStatus.PREPARED.value,
            ),
        )
        return int(cursor.rowcount)

    def mark_running_tool_invocations_uncertain(
        self,
        task_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> int:
        now = utc_now()
        cursor = self.store.execute(
            """
            UPDATE main_agent_tool_invocations
            SET status=?, error_code=?, error_message=?, error_retryable=?, completed_at=?, updated_at=?
            WHERE task_id=? AND status=?
            """,
            (
                ToolInvocationStatus.UNCERTAIN.value,
                error_code,
                error_message,
                int(retryable),
                now,
                now,
                task_id,
                ToolInvocationStatus.RUNNING.value,
            ),
        )
        return int(cursor.rowcount)

    def list_task_input_messages(self, task_id: str, *, limit: int = 10) -> list[MessageRecord]:
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if task.input_context_sequence <= 0:
            raise RuntimeError(f"task input cut is unavailable: {task_id}")
        return self.list_context_messages(
            task.context_id,
            limit=limit,
            through_sequence=task.input_context_sequence,
        )

    def transition_local_task(
        self,
        task_id: str,
        target_status: TaskStatus,
        *,
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> TaskRecord:
        """Apply one validated local-process transition and its lifecycle event.

        Remote proxy Tasks deliberately use their own synchronization path. A
        real local transition updates the record and appends its target-state
        event in the same SQLite transaction; a duplicate target is a no-op.
        """

        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.assigned_agent_id is not None:
                raise ValueError(f"remote proxy task cannot use local transition policy: {task_id}")
            if not validate_local_task_transition(task.status, target_status):
                return task

            event_type = lifecycle_event_type_for_status(target_status)
            self.store.execute(
                """
                UPDATE main_agent_tasks
                SET status=?, error_code=?, error_message=?, updated_at=?
                WHERE task_id=?
                """,
                (target_status.value, error_code, error_message, utc_now(), task_id),
            )
            updated = self.get_task(task_id)
            if updated is None:
                raise RuntimeError(f"failed to transition task: {task_id}")
            self.append_task_event(
                task_id=task_id,
                type=event_type,
                status=target_status,
                payload=payload,
            )
            return updated

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> TaskRecord:
        """Raw update reserved for core-owned remote proxy synchronization.

        Locally owned process transitions must use `transition_local_task()`.
        """

        self.store.execute(
            """
            UPDATE main_agent_tasks
            SET status=?, error_code=?, error_message=?, updated_at=?
            WHERE task_id=?
            """,
            (status.value, error_code, error_message, utc_now(), task_id),
        )
        record = self.get_task(task_id)
        if record is None:
            raise RuntimeError(f"failed to update task: {task_id}")
        return record

    def set_task_output_message(self, task_id: str, output_message_id: str) -> TaskRecord:
        if self.get_message(output_message_id) is None:
            raise ValueError(f"unknown output message: {output_message_id}")
        self.store.execute(
            """
            UPDATE main_agent_tasks
            SET output_message_id=?, updated_at=?
            WHERE task_id=?
            """,
            (output_message_id, utc_now(), task_id),
        )
        record = self.get_task(task_id)
        if record is None:
            raise RuntimeError(f"failed to set task output: {task_id}")
        return record

    def append_task_event(
        self,
        *,
        task_id: str,
        type: str,
        status: TaskStatus | None = None,
        payload: dict[str, Any] | None = None,
    ) -> TaskEventRecord:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        cursor = self.store.execute(
            """
            INSERT INTO main_agent_task_events(task_id, type, status, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, type, status.value if status is not None else None, _dumps(payload or {}), utc_now()),
        )
        record = self.get_task_event(int(cursor.lastrowid))
        if record is None:
            raise RuntimeError(f"failed to append task event: {task_id}")
        with self._task_event_condition:
            self._task_event_versions[task_id] = self._task_event_versions.get(task_id, 0) + 1
            self._task_event_condition.notify_all()
        return record

    def get_task_event(self, event_id: int) -> TaskEventRecord | None:
        rows = self.store.query(
            """
            SELECT event_id, task_id, type, status, payload, created_at
            FROM main_agent_task_events
            WHERE event_id=?
            """,
            (event_id,),
        )
        if not rows:
            return None
        return _task_event_from_row(rows[0])

    def list_task_events(self, task_id: str, *, after_event_id: int = 0) -> list[TaskEventRecord]:
        rows = self.store.query(
            """
            SELECT event_id, task_id, type, status, payload, created_at
            FROM main_agent_task_events
            WHERE task_id=? AND event_id > ?
            ORDER BY event_id ASC
            """,
            (task_id, after_event_id),
        )
        return [_task_event_from_row(row) for row in rows]

    def set_pending_continuation(
        self,
        task_id: str,
        *,
        kind: str,
        input_request: dict[str, Any],
    ) -> PendingContinuationRecord:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        if not kind:
            raise ValueError("pending continuation kind is required")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO main_agent_pending_continuations(task_id, kind, input_request, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                kind=excluded.kind,
                input_request=excluded.input_request,
                created_at=excluded.created_at
            """,
            (task_id, kind, _dumps(input_request), now),
        )
        record = self.get_pending_continuation(task_id)
        if record is None:
            raise RuntimeError(f"failed to persist pending continuation: {task_id}")
        return record

    def get_pending_continuation(self, task_id: str) -> PendingContinuationRecord | None:
        rows = self.store.query(
            """
            SELECT task_id, kind, input_request, created_at
            FROM main_agent_pending_continuations
            WHERE task_id=?
            """,
            (task_id,),
        )
        if not rows:
            return None
        return _pending_continuation_from_row(rows[0])

    def consume_pending_continuation(
        self,
        task_id: str,
        *,
        expected_kind: str,
    ) -> PendingContinuationRecord:
        record = self.get_pending_continuation(task_id)
        if record is None:
            raise ValueError(f"task has no pending {expected_kind} request: {task_id}")
        if record.kind != expected_kind:
            expected_label = "approval" if expected_kind == "approval_required" else "general input"
            actual_label = "approval" if record.kind == "approval_required" else "general input"
            raise ValueError(f"task is waiting for {actual_label}, not {expected_label}: {task_id}")

        cursor = self.store.execute(
            """
            DELETE FROM main_agent_pending_continuations
            WHERE task_id=? AND kind=?
            """,
            (task_id, expected_kind),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"task has no pending {expected_kind} request: {task_id}")
        return record

    def clear_pending_continuation(self, task_id: str) -> None:
        self.store.execute("DELETE FROM main_agent_pending_continuations WHERE task_id=?", (task_id,))

    def enqueue_task_execution(
        self,
        task_id: str,
        *,
        kind: QueuedTaskExecutionKind,
        runtime_thread_id: str,
        payload: dict[str, Any] | None = None,
    ) -> QueuedTaskExecutionRecord:
        """Persist the next worker slice before exposing a task as queued."""

        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        if task.assigned_agent_id is not None:
            raise ValueError(f"remote proxy task cannot queue a local execution: {task_id}")
        if task.status != TaskStatus.QUEUED:
            raise ValueError(f"local task is not queued: {task_id}")
        if task.runtime_thread_id != runtime_thread_id:
            raise ValueError(f"task runtime thread mismatch: {task_id}")

        if payload is not None and not isinstance(payload, dict):
            raise ValueError(f"queued execution payload must be an object: {task_id}")
        normalized_payload = payload or {}
        existing = self.get_queued_task_execution(task_id)
        if existing is not None:
            if (
                existing.kind == kind
                and existing.runtime_thread_id == runtime_thread_id
                and existing.payload == normalized_payload
            ):
                return existing
            raise ValueError(f"local task already has a queued execution: {task_id}")

        self.store.execute(
            """
            INSERT INTO main_agent_queued_executions(task_id, kind, runtime_thread_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, kind.value, runtime_thread_id, _dumps(normalized_payload), utc_now()),
        )
        record = self.get_queued_task_execution(task_id)
        if record is None:
            raise RuntimeError(f"failed to enqueue local task execution: {task_id}")
        return record

    def get_queued_task_execution(self, task_id: str) -> QueuedTaskExecutionRecord | None:
        rows = self.store.query(
            """
            SELECT task_id, kind, runtime_thread_id, payload, created_at
            FROM main_agent_queued_executions
            WHERE task_id=?
            """,
            (task_id,),
        )
        if not rows:
            return None
        return _queued_task_execution_from_row(rows[0])

    def claim_queued_task_execution(
        self,
        task_id: str,
    ) -> tuple[TaskRecord, QueuedTaskExecutionRecord] | None:
        """Atomically claim one queued slice and mark its process running."""

        with self.transaction():
            task = self.get_task(task_id)
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            if task.assigned_agent_id is not None or task.status != TaskStatus.QUEUED:
                return None
            command = self.get_queued_task_execution(task_id)
            if command is None:
                return None
            if command.runtime_thread_id != task.runtime_thread_id:
                raise ValueError(f"queued execution runtime thread mismatch: {task_id}")
            deleted = self.store.execute(
                "DELETE FROM main_agent_queued_executions WHERE task_id=?",
                (task_id,),
            )
            if deleted.rowcount != 1:
                return None
            task = self.transition_local_task(task_id, TaskStatus.RUNNING)
            return task, command

    def clear_queued_task_execution(self, task_id: str) -> None:
        self.store.execute("DELETE FROM main_agent_queued_executions WHERE task_id=?", (task_id,))

    def get_pending_input_request(self, task_id: str) -> dict[str, Any] | None:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        record = self.get_pending_continuation(task_id)
        return dict(record.input_request) if record is not None else None

    def wait_for_task_events(
        self,
        task_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[TaskEventRecord]:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        events = self.list_task_events(task_id, after_event_id=after_event_id)
        if events or timeout_seconds <= 0:
            return events

        with self._task_event_condition:
            version = self._task_event_versions.get(task_id, 0)
        deadline = time.monotonic() + timeout_seconds
        while True:
            events = self.list_task_events(task_id, after_event_id=after_event_id)
            if events:
                return events
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            with self._task_event_condition:
                if self._task_event_versions.get(task_id, 0) <= version:
                    self._task_event_condition.wait(timeout=remaining)
                version = self._task_event_versions.get(task_id, 0)

    def upsert_artifact(
        self,
        *,
        artifact_id: str,
        task_id: str,
        context_id: str,
        parts: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO artifacts(artifact_id, task_id, context_id, parts, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                parts=excluded.parts,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (artifact_id, task_id, context_id, _dumps(parts), _dumps(metadata or {}), now, now),
        )
        record = self.get_artifact(artifact_id)
        if record is None:
            raise RuntimeError(f"failed to upsert artifact: {artifact_id}")
        return record

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        rows = self.store.query(
            """
            SELECT artifact_id, task_id, context_id, parts, metadata, created_at, updated_at
            FROM artifacts
            WHERE artifact_id=?
            """,
            (artifact_id,),
        )
        if not rows:
            return None
        return _artifact_from_row(rows[0])

    def list_task_artifacts(self, task_id: str) -> list[ArtifactRecord]:
        rows = self.store.query(
            """
            SELECT artifact_id, task_id, context_id, parts, metadata, created_at, updated_at
            FROM artifacts
            WHERE task_id=?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_artifact_from_row(row) for row in rows]

    def upsert_registered_agent(
        self,
        *,
        agent_id: str,
        name: str,
        card_url: str,
        card_json: dict[str, Any] | None = None,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> RegisteredAgentRecord:
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO registered_agents(agent_id, name, card_url, card_json, enabled, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name=excluded.name,
                card_url=excluded.card_url,
                card_json=excluded.card_json,
                enabled=excluded.enabled,
                metadata=excluded.metadata,
                updated_at=excluded.updated_at
            """,
            (
                agent_id,
                name,
                card_url,
                _dumps(card_json or {}),
                1 if enabled else 0,
                _dumps(metadata or {}),
                now,
                now,
            ),
        )
        record = self.get_registered_agent(agent_id)
        if record is None:
            raise RuntimeError(f"failed to upsert registered agent: {agent_id}")
        return record

    def get_registered_agent(self, agent_id: str) -> RegisteredAgentRecord | None:
        rows = self.store.query(
            """
            SELECT agent_id, name, card_url, card_json, enabled, metadata, created_at, updated_at
            FROM registered_agents
            WHERE agent_id=?
            """,
            (agent_id,),
        )
        if not rows:
            return None
        return _registered_agent_from_row(rows[0])

    def list_registered_agents(self, *, enabled_only: bool = False) -> list[RegisteredAgentRecord]:
        sql = """
            SELECT agent_id, name, card_url, card_json, enabled, metadata, created_at, updated_at
            FROM registered_agents
        """
        values: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY updated_at DESC"
        rows = self.store.query(sql, values)
        return [_registered_agent_from_row(row) for row in rows]

    def update_registered_agent_card(
        self,
        agent_id: str,
        *,
        card_json: dict[str, Any],
    ) -> RegisteredAgentRecord | None:
        now = utc_now()
        cursor = self.store.execute(
            """
            UPDATE registered_agents
            SET card_json=?, updated_at=?
            WHERE agent_id=?
            """,
            (_dumps(card_json), now, agent_id),
        )
        if cursor.rowcount == 0:
            return None
        return self.get_registered_agent(agent_id)

    def delete_registered_agent(self, agent_id: str) -> bool:
        cursor = self.store.execute("DELETE FROM registered_agents WHERE agent_id=?", (agent_id,))
        return cursor.rowcount > 0

    def create_delegated_task(
        self,
        *,
        delegation_id: str,
        context_id: str,
        input_message_id: str,
        route_decision_id: str,
        remote_agent_id: str,
        result_kind: str,
        status: str,
        local_task_id: str | None = None,
        remote_task_id: str | None = None,
        remote_context_id: str | None = None,
        remote_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DelegatedTaskRecord:
        if self.get_context(context_id) is None:
            raise ValueError(f"unknown context: {context_id}")
        if self.get_message(input_message_id) is None:
            raise ValueError(f"unknown input message: {input_message_id}")
        if self.get_route_decision(route_decision_id) is None:
            raise ValueError(f"unknown route decision: {route_decision_id}")
        if self.get_registered_agent(remote_agent_id) is None:
            raise ValueError(f"unknown registered agent: {remote_agent_id}")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO delegated_tasks(
                delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                status, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delegation_id,
                context_id,
                input_message_id,
                route_decision_id,
                remote_agent_id,
                local_task_id,
                remote_task_id,
                remote_context_id,
                remote_message_id,
                result_kind,
                status,
                _dumps(metadata or {}),
                now,
                now,
            ),
        )
        record = self.get_delegated_task(delegation_id)
        if record is None:
            raise RuntimeError(f"failed to create delegated task: {delegation_id}")
        return record

    def get_delegated_task(self, delegation_id: str) -> DelegatedTaskRecord | None:
        rows = self.store.query(
            """
            SELECT delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                   local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                   status, metadata, created_at, updated_at
            FROM delegated_tasks
            WHERE delegation_id=?
            """,
            (delegation_id,),
        )
        if not rows:
            return None
        return _delegated_task_from_row(rows[0])

    def get_delegated_task_by_local_task_id(self, local_task_id: str) -> DelegatedTaskRecord | None:
        rows = self.store.query(
            """
            SELECT delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                   local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                   status, metadata, created_at, updated_at
            FROM delegated_tasks
            WHERE local_task_id=?
            """,
            (local_task_id,),
        )
        if not rows:
            return None
        return _delegated_task_from_row(rows[0])

    def get_delegated_task_by_input_message_id(self, input_message_id: str) -> DelegatedTaskRecord | None:
        rows = self.store.query(
            """
            SELECT delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                   local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                   status, metadata, created_at, updated_at
            FROM delegated_tasks
            WHERE input_message_id=?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (input_message_id,),
        )
        if not rows:
            return None
        return _delegated_task_from_row(rows[0])

    def update_delegated_task_status(
        self,
        delegation_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> DelegatedTaskRecord:
        current = self.get_delegated_task(delegation_id)
        if current is None:
            raise ValueError(f"unknown delegated task: {delegation_id}")
        next_metadata = current.metadata if metadata is None else metadata
        self.store.execute(
            """
            UPDATE delegated_tasks
            SET status=?, metadata=?, updated_at=?
            WHERE delegation_id=?
            """,
            (status, _dumps(next_metadata), utc_now(), delegation_id),
        )
        record = self.get_delegated_task(delegation_id)
        if record is None:
            raise RuntimeError(f"failed to update delegated task: {delegation_id}")
        return record

    def list_context_delegations(self, context_id: str) -> list[DelegatedTaskRecord]:
        rows = self.store.query(
            """
            SELECT delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                   local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                   status, metadata, created_at, updated_at
            FROM delegated_tasks
            WHERE context_id=?
            ORDER BY created_at ASC
            """,
            (context_id,),
        )
        return [_delegated_task_from_row(row) for row in rows]

    def list_delegated_tasks_for_remote_agent(self, agent_id: str) -> list[DelegatedTaskRecord]:
        rows = self.store.query(
            """
            SELECT delegation_id, context_id, input_message_id, route_decision_id, remote_agent_id,
                   local_task_id, remote_task_id, remote_context_id, remote_message_id, result_kind,
                   status, metadata, created_at, updated_at
            FROM delegated_tasks
            WHERE remote_agent_id=?
            ORDER BY created_at ASC
            """,
            (agent_id,),
        )
        return [_delegated_task_from_row(row) for row in rows]

    def delete_terminal_context(self, context_id: str) -> DeleteContextResult:
        """Delete only a Context whose Task records are already terminal.

        Lifecycle decisions live in ``MainAgentCore``. This store primitive is
        deliberately narrow so it cannot cancel a running process by directly
        rewriting database state.
        """

        tasks = self.list_context_tasks(context_id)
        active_tasks = [task for task in tasks if not is_terminal_task_status(task.status)]
        if active_tasks:
            raise ValueError(f"context has non-terminal tasks: {context_id}")

        task_ids = [task.task_id for task in tasks]
        with self.store.transaction() as conn:
            deleted_artifacts = conn.execute("DELETE FROM artifacts WHERE context_id=?", (context_id,)).rowcount
            conn.execute("DELETE FROM main_agent_message_ingress WHERE context_id=?", (context_id,))
            conn.execute("DELETE FROM delegated_tasks WHERE context_id=?", (context_id,))
            deleted_task_events = 0
            for task_id in task_ids:
                conn.execute("DELETE FROM main_agent_pending_continuations WHERE task_id=?", (task_id,))
                conn.execute("DELETE FROM main_agent_queued_executions WHERE task_id=?", (task_id,))
                conn.execute("DELETE FROM main_agent_tool_invocations WHERE task_id=?", (task_id,))
                deleted_task_events += conn.execute(
                    "DELETE FROM main_agent_task_events WHERE task_id=?",
                    (task_id,),
                ).rowcount
            deleted_tasks = conn.execute("DELETE FROM main_agent_tasks WHERE context_id=?", (context_id,)).rowcount
            deleted_route_decisions = conn.execute(
                "DELETE FROM route_decisions WHERE context_id=?",
                (context_id,),
            ).rowcount
            deleted_messages = conn.execute("DELETE FROM messages WHERE context_id=?", (context_id,)).rowcount
            conn.execute("DELETE FROM contexts WHERE context_id=?", (context_id,))

        return DeleteContextResult(
            context_id=context_id,
            deleted_messages=deleted_messages,
            deleted_tasks=deleted_tasks,
            deleted_task_events=deleted_task_events,
            deleted_artifacts=deleted_artifacts,
            deleted_route_decisions=deleted_route_decisions,
        )


def _context_from_row(row: Any) -> ContextRecord:
    return ContextRecord(
        context_id=str(row["context_id"]),
        title=row["title"],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _message_from_row(row: Any) -> MessageRecord:
    return MessageRecord(
        message_id=str(row["message_id"]),
        context_id=str(row["context_id"]),
        context_sequence=int(row["context_sequence"]),
        role=MessageRole(str(row["role"])),
        parts=_loads(row["parts"]) or [],
        task_id=row["task_id"],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
    )


def _message_ingress_from_row(row: Any) -> MessageIngressRecord:
    return MessageIngressRecord(
        message_id=str(row["message_id"]),
        context_id=str(row["context_id"]),
        request_fingerprint=str(row["request_fingerprint"]),
        state=MessageIngressState(str(row["state"])),
        route_decision_id=row["route_decision_id"],
        outcome_kind=(
            MessageIngressOutcomeKind(str(row["outcome_kind"]))
            if row["outcome_kind"] is not None
            else None
        ),
        outcome_id=row["outcome_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_http_status=(int(row["error_http_status"]) if row["error_http_status"] is not None else None),
        error_retryable=bool(row["error_retryable"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _route_decision_from_row(row: Any) -> RouteDecisionRecord:
    return RouteDecisionRecord(
        decision_id=str(row["decision_id"]),
        context_id=str(row["context_id"]),
        message_id=str(row["message_id"]),
        kind=RouteDecisionKind(str(row["kind"])),
        target_agent_id=row["target_agent_id"],
        reason=str(row["reason"]),
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
    )


def _task_from_row(row: Any) -> TaskRecord:
    return TaskRecord(
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        status=normalize_task_status(row["status"]),
        input_message_id=str(row["input_message_id"]),
        input_context_sequence=int(row["input_context_sequence"]),
        output_message_id=row["output_message_id"],
        runtime_thread_id=str(row["runtime_thread_id"]),
        assigned_agent_id=row["assigned_agent_id"],
        retry_of_task_id=row["retry_of_task_id"],
        attempt=int(row["attempt"]),
        model=_loads(row["model"]) if row["model"] is not None else None,
        max_loops=int(row["max_loops"]) if row["max_loops"] is not None else None,
        mcp=_loads(row["mcp"]) if row["mcp"] is not None else None,
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _task_event_from_row(row: Any) -> TaskEventRecord:
    return TaskEventRecord(
        event_id=int(row["event_id"]),
        task_id=str(row["task_id"]),
        type=str(row["type"]),
        status=normalize_task_status(row["status"]) if row["status"] is not None else None,
        payload=_loads(row["payload"]) or {},
        created_at=str(row["created_at"]),
    )


def _pending_continuation_from_row(row: Any) -> PendingContinuationRecord:
    return PendingContinuationRecord(
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        input_request=_loads(row["input_request"]) or {},
        created_at=str(row["created_at"]),
    )


def _queued_task_execution_from_row(row: Any) -> QueuedTaskExecutionRecord:
    payload = _loads(row["payload"])
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError(f"queued execution payload must be an object: {row['task_id']}")
    return QueuedTaskExecutionRecord(
        task_id=str(row["task_id"]),
        kind=QueuedTaskExecutionKind(str(row["kind"])),
        runtime_thread_id=str(row["runtime_thread_id"]),
        payload=payload,
        created_at=str(row["created_at"]),
    )


def _artifact_from_row(row: Any) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        parts=_loads(row["parts"]) or [],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _tool_invocation_from_row(row: Any) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=str(row["invocation_id"]),
        task_id=str(row["task_id"]),
        context_id=str(row["context_id"]),
        runtime_thread_id=str(row["runtime_thread_id"]),
        loop_index=int(row["loop_index"]),
        tool_call_id=str(row["tool_call_id"]),
        tool_name=str(row["tool_name"]),
        normalized_arguments=_loads(row["normalized_arguments"]) or {},
        arguments_digest=str(row["arguments_digest"]),
        capability=_loads(row["capability"]) or {},
        side_effect_level=str(row["side_effect_level"]),
        idempotency_key=row["idempotency_key"],
        approval_required=bool(row["approval_required"]),
        approval_status=ToolInvocationApprovalStatus(str(row["approval_status"])),
        approval_reason=row["approval_reason"],
        status=ToolInvocationStatus(str(row["status"])),
        result_artifact_id=row["result_artifact_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_retryable=bool(row["error_retryable"]),
        created_at=str(row["created_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
        updated_at=str(row["updated_at"]),
    )


def _registered_agent_from_row(row: Any) -> RegisteredAgentRecord:
    return RegisteredAgentRecord(
        agent_id=str(row["agent_id"]),
        name=str(row["name"]),
        card_url=str(row["card_url"]),
        card_json=_loads(row["card_json"]) or {},
        enabled=bool(row["enabled"]),
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _delegated_task_from_row(row: Any) -> DelegatedTaskRecord:
    return DelegatedTaskRecord(
        delegation_id=str(row["delegation_id"]),
        context_id=str(row["context_id"]),
        input_message_id=str(row["input_message_id"]),
        route_decision_id=str(row["route_decision_id"]),
        remote_agent_id=str(row["remote_agent_id"]),
        local_task_id=row["local_task_id"],
        remote_task_id=row["remote_task_id"],
        remote_context_id=row["remote_context_id"],
        remote_message_id=row["remote_message_id"],
        result_kind=str(row["result_kind"]),
        status=str(row["status"]),
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(str(value))


def _validate_tool_invocation_identity(
    record: ToolInvocationRecord,
    *,
    task_id: str,
    context_id: str,
    runtime_thread_id: str,
    loop_index: int,
    tool_call_id: str,
    tool_name: str,
    arguments_digest: str,
) -> None:
    if (
        record.task_id != task_id
        or record.context_id != context_id
        or record.runtime_thread_id != runtime_thread_id
        or record.loop_index != loop_index
        or record.tool_call_id != tool_call_id
        or record.tool_name != tool_name
        or record.arguments_digest != arguments_digest
    ):
        raise ValueError(f"tool invocation identity conflict: {record.invocation_id}")

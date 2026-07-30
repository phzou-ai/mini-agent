from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

from vermay_agent.storage import AgentStore, utc_now

from .models import (
    ArtifactRecord,
    ContextRecord,
    DelegatedTaskRecord,
    DeleteContextResult,
    MessageRecord,
    MessageRole,
    RegisteredAgentRecord,
    RouteDecisionKind,
    RouteDecisionRecord,
    TaskEventRecord,
    TaskRecord,
    TaskStatus,
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
        self.store.execute(
            """
            INSERT INTO messages(message_id, context_id, role, parts, task_id, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, context_id, role.value, _dumps(parts), task_id, _dumps(metadata or {}), now),
        )
        self.touch_context(context_id)
        record = self.get_message(message_id)
        if record is None:
            raise RuntimeError(f"failed to append message: {message_id}")
        return record

    def get_message(self, message_id: str) -> MessageRecord | None:
        rows = self.store.query(
            """
            SELECT message_id, context_id, role, parts, task_id, metadata, created_at
            FROM messages
            WHERE message_id=?
            """,
            (message_id,),
        )
        if not rows:
            return None
        return _message_from_row(rows[0])

    def list_context_messages(self, context_id: str, *, limit: int | None = None) -> list[MessageRecord]:
        values: tuple[Any, ...] = (context_id,)
        sql = """
            SELECT message_id, context_id, role, parts, task_id, metadata, created_at
            FROM messages
            WHERE context_id=?
            ORDER BY created_at ASC
        """
        if limit is not None:
            sql = f"SELECT * FROM ({sql}) ORDER BY created_at DESC LIMIT ?"
            values = (context_id, limit)
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
        if self.get_message(input_message_id) is None:
            raise ValueError(f"unknown input message: {input_message_id}")
        now = utc_now()
        self.store.execute(
            """
            INSERT INTO main_agent_tasks(
                task_id, context_id, status, input_message_id, output_message_id, runtime_thread_id,
                assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp, error_code,
                error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                context_id,
                status.value,
                input_message_id,
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

    def get_task(self, task_id: str) -> TaskRecord | None:
        rows = self.store.query(
            """
            SELECT task_id, context_id, status, input_message_id, output_message_id, runtime_thread_id,
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

    def list_context_tasks(self, context_id: str) -> list[TaskRecord]:
        rows = self.store.query(
            """
            SELECT task_id, context_id, status, input_message_id, output_message_id, runtime_thread_id,
                   assigned_agent_id, retry_of_task_id, attempt, model, max_loops, mcp, error_code,
                   error_message, created_at, updated_at
            FROM main_agent_tasks
            WHERE context_id=?
            ORDER BY created_at ASC
            """,
            (context_id,),
        )
        return [_task_from_row(row) for row in rows]

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> TaskRecord:
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

    def get_pending_input_request(self, task_id: str) -> dict[str, Any] | None:
        if self.get_task(task_id) is None:
            raise ValueError(f"unknown task: {task_id}")
        for event in reversed(self.list_task_events(task_id)):
            if event.type in {
                "task_input_submitted",
                "task_resumed",
                "task_completed",
                "task_failed",
                "task_cancelled",
            }:
                return None
            if event.type == "task_interrupted":
                request = event.payload.get("input_request")
                return dict(request) if isinstance(request, dict) else None
        return None

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

    def delete_context(self, context_id: str, *, force: bool = False) -> DeleteContextResult:
        tasks = self.list_context_tasks(context_id)
        active_tasks = [task for task in tasks if not is_terminal_task_status(task.status)]
        if active_tasks and not force:
            raise ValueError(f"context has non-terminal tasks: {context_id}")

        if force:
            for task in active_tasks:
                self.update_task_status(task.task_id, TaskStatus.CANCELED)

        task_ids = [task.task_id for task in tasks]
        with self.store.transaction() as conn:
            deleted_artifacts = conn.execute("DELETE FROM artifacts WHERE context_id=?", (context_id,)).rowcount
            conn.execute("DELETE FROM delegated_tasks WHERE context_id=?", (context_id,))
            deleted_task_events = 0
            for task_id in task_ids:
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
        role=MessageRole(str(row["role"])),
        parts=_loads(row["parts"]) or [],
        task_id=row["task_id"],
        metadata=_loads(row["metadata"]) or {},
        created_at=str(row["created_at"]),
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

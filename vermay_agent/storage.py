from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

SCHEMA_VERSION = 3
STORE_SCHEMA_FAMILY = "main_agent_clean_slate_v1"
SQLITE_BUSY_TIMEOUT_MS = 5_000


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    """Apply the runtime's durable SQLite connection contract.

    Foreign-key enforcement is connection-local in SQLite. WAL and a bounded
    busy timeout make the Agent store and LangGraph checkpoint store tolerate
    the short concurrent reads and writes introduced by background workers.
    """

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SchemaMigration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _apply_schema_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS store_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        INSERT OR REPLACE INTO store_metadata(key, value)
        VALUES ('schema_family', 'main_agent_clean_slate_v1');

        CREATE TABLE IF NOT EXISTS memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            source TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_index (
            name TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            description TEXT,
            triggers TEXT NOT NULL DEFAULT '[]',
            version TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_runs (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_path TEXT NOT NULL,
            status TEXT NOT NULL,
            input TEXT,
            report_path TEXT NOT NULL,
            summary TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS eval_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            name TEXT NOT NULL,
            passed INTEGER NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES eval_runs(id)
        );

        CREATE TABLE IF NOT EXISTS model_profiles (
            name TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            options TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """
    )
    _create_main_agent_core_tables(conn)
    _create_registered_agent_tables(conn)
    _create_pending_continuation_tables(conn)
    _create_message_ingress_tables(conn)
    _create_queued_execution_tables(conn)


def _create_main_agent_core_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contexts (
            context_id TEXT PRIMARY KEY,
            title TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            next_message_sequence INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_contexts_updated_at ON contexts(updated_at);

        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            context_id TEXT NOT NULL,
            context_sequence INTEGER NOT NULL,
            role TEXT NOT NULL,
            parts TEXT NOT NULL DEFAULT '[]',
            task_id TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id)
        );

        CREATE INDEX IF NOT EXISTS idx_messages_context_created ON messages(context_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_task_id ON messages(task_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_context_sequence
            ON messages(context_id, context_sequence);

        CREATE TABLE IF NOT EXISTS route_decisions (
            decision_id TEXT PRIMARY KEY,
            context_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            target_agent_id TEXT,
            reason TEXT NOT NULL,
            confidence REAL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id),
            FOREIGN KEY(message_id) REFERENCES messages(message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_route_decisions_context_created
            ON route_decisions(context_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_route_decisions_message_id ON route_decisions(message_id);

        CREATE TABLE IF NOT EXISTS main_agent_tasks (
            task_id TEXT PRIMARY KEY,
            context_id TEXT NOT NULL,
            status TEXT NOT NULL,
            input_message_id TEXT NOT NULL,
            input_context_sequence INTEGER NOT NULL DEFAULT 0,
            output_message_id TEXT,
            runtime_thread_id TEXT NOT NULL UNIQUE,
            assigned_agent_id TEXT,
            retry_of_task_id TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            model TEXT,
            max_loops INTEGER,
            mcp TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id),
            FOREIGN KEY(input_message_id) REFERENCES messages(message_id),
            FOREIGN KEY(output_message_id) REFERENCES messages(message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_main_agent_tasks_context_updated
            ON main_agent_tasks(context_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_main_agent_tasks_input_message_id
            ON main_agent_tasks(input_message_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_main_agent_tasks_one_direct_retry
            ON main_agent_tasks(retry_of_task_id)
            WHERE retry_of_task_id IS NOT NULL;

        CREATE TABLE IF NOT EXISTS main_agent_task_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES main_agent_tasks(task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_main_agent_task_events_task_event
            ON main_agent_task_events(task_id, event_id);

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            context_id TEXT NOT NULL,
            parts TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES main_agent_tasks(task_id),
            FOREIGN KEY(context_id) REFERENCES contexts(context_id)
        );

        CREATE INDEX IF NOT EXISTS idx_artifacts_task_id ON artifacts(task_id);
        CREATE INDEX IF NOT EXISTS idx_artifacts_context_id ON artifacts(context_id);
        """
    )


def _create_registered_agent_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registered_agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            card_url TEXT NOT NULL,
            card_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_registered_agents_enabled
            ON registered_agents(enabled, updated_at);

        CREATE TABLE IF NOT EXISTS delegated_tasks (
            delegation_id TEXT PRIMARY KEY,
            context_id TEXT NOT NULL,
            input_message_id TEXT NOT NULL,
            route_decision_id TEXT NOT NULL,
            remote_agent_id TEXT NOT NULL,
            local_task_id TEXT,
            remote_task_id TEXT,
            remote_context_id TEXT,
            remote_message_id TEXT,
            result_kind TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(context_id) REFERENCES contexts(context_id),
            FOREIGN KEY(input_message_id) REFERENCES messages(message_id),
            FOREIGN KEY(route_decision_id) REFERENCES route_decisions(decision_id),
            FOREIGN KEY(remote_agent_id) REFERENCES registered_agents(agent_id),
            FOREIGN KEY(local_task_id) REFERENCES main_agent_tasks(task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_delegated_tasks_context_updated
            ON delegated_tasks(context_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_delegated_tasks_local_task_id
            ON delegated_tasks(local_task_id);
        CREATE INDEX IF NOT EXISTS idx_delegated_tasks_remote_agent_id
            ON delegated_tasks(remote_agent_id);
        """
    )


def _create_pending_continuation_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS main_agent_pending_continuations (
            task_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            input_request TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES main_agent_tasks(task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_main_agent_pending_continuations_kind
            ON main_agent_pending_continuations(kind);
        """
    )


def _create_message_ingress_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS main_agent_message_ingress (
            message_id TEXT PRIMARY KEY,
            context_id TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL,
            route_decision_id TEXT,
            outcome_kind TEXT,
            outcome_id TEXT,
            error_code TEXT,
            error_message TEXT,
            error_http_status INTEGER,
            error_retryable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(message_id) REFERENCES messages(message_id),
            FOREIGN KEY(context_id) REFERENCES contexts(context_id),
            FOREIGN KEY(route_decision_id) REFERENCES route_decisions(decision_id)
        );

        CREATE INDEX IF NOT EXISTS idx_main_agent_message_ingress_context_updated
            ON main_agent_message_ingress(context_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_main_agent_message_ingress_state_updated
            ON main_agent_message_ingress(state, updated_at);
        """
    )


def _create_queued_execution_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS main_agent_queued_executions (
            task_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            runtime_thread_id TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES main_agent_tasks(task_id)
        );

        CREATE INDEX IF NOT EXISTS idx_main_agent_queued_executions_created
            ON main_agent_queued_executions(created_at);
        """
    )


def _apply_schema_v2(conn: sqlite3.Connection) -> None:
    """Add the durable boundary for non-read-only tool invocations."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS main_agent_tool_invocations (
            invocation_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            context_id TEXT NOT NULL,
            runtime_thread_id TEXT NOT NULL,
            loop_index INTEGER NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            normalized_arguments TEXT NOT NULL DEFAULT '{}',
            arguments_digest TEXT NOT NULL,
            capability TEXT NOT NULL DEFAULT '{}',
            side_effect_level TEXT NOT NULL,
            idempotency_key TEXT,
            approval_required INTEGER NOT NULL DEFAULT 0,
            approval_status TEXT NOT NULL DEFAULT 'not_required',
            approval_reason TEXT,
            status TEXT NOT NULL,
            result_artifact_id TEXT,
            error_code TEXT,
            error_message TEXT,
            error_retryable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES main_agent_tasks(task_id),
            FOREIGN KEY(context_id) REFERENCES contexts(context_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_invocations_execution_identity
            ON main_agent_tool_invocations(
                task_id, runtime_thread_id, loop_index, tool_call_id, arguments_digest
            );
        CREATE INDEX IF NOT EXISTS idx_tool_invocations_task_created
            ON main_agent_tool_invocations(task_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_tool_invocations_status
            ON main_agent_tool_invocations(status, updated_at);
        """
    )


def _apply_schema_v3(conn: sqlite3.Connection) -> None:
    """Persist Task retryability and make retry lineage idempotent."""

    conn.executescript(
        """
        ALTER TABLE main_agent_tasks
            ADD COLUMN error_retryable INTEGER NOT NULL DEFAULT 0;

        DROP INDEX IF EXISTS idx_main_agent_tasks_retry_of_task_id;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_main_agent_tasks_one_direct_retry
            ON main_agent_tasks(retry_of_task_id)
            WHERE retry_of_task_id IS NOT NULL;
        """
    )


MIGRATIONS: tuple[SchemaMigration, ...] = (
    SchemaMigration(1, "main_agent_clean_slate_baseline", _apply_schema_v1),
    SchemaMigration(2, "tool_invocation_ledger", _apply_schema_v2),
    SchemaMigration(3, "task_failure_retryability", _apply_schema_v3),
)


@dataclass
class AgentStore:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.setup()
            configure_sqlite_connection(self.conn)
        except Exception:
            self.conn.close()
            raise

    def setup(self) -> None:
        with self._lock:
            _ensure_schema_migrations_table(self.conn)
            self._reset_retired_schema_if_required()
            self._apply_pending_migrations()

    def _reset_retired_schema_if_required(self) -> None:
        if not _requires_clean_slate_reset(self.conn):
            return
        _reset_sqlite_schema(self.conn)
        _ensure_schema_migrations_table(self.conn)

    def _apply_pending_migrations(self) -> None:
        applied_versions = self._applied_schema_versions()
        for migration in MIGRATIONS:
            if migration.version in applied_versions:
                continue
            with self.conn:
                migration.apply(self.conn)
                self.conn.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (?, ?)
                    """,
                    (migration.version, utc_now()),
                )
            applied_versions.add(migration.version)

    def _applied_schema_versions(self) -> set[int]:
        return {
            int(row["version"])
            for row in self.conn.execute("SELECT version FROM schema_migrations")
        }

    def execute(self, sql: str, values: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self.conn.execute(sql, tuple(values))
            if self._transaction_depth == 0:
                self.conn.commit()
            return cursor

    def query(self, sql: str, values: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, tuple(values)))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._transaction_depth += 1
            try:
                if self._transaction_depth == 1:
                    with self.conn:
                        yield self.conn
                else:
                    yield self.conn
            finally:
                self._transaction_depth -= 1

    def schema_version(self) -> int:
        rows = self.query("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
        return int(rows[0]["version"])

    def upsert_skill_index(
        self,
        *,
        name: str,
        path: Path,
        description: str,
        triggers: list[str],
        version: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO skill_index(name, path, description, triggers, version, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                path=excluded.path,
                description=excluded.description,
                triggers=excluded.triggers,
                version=excluded.version,
                updated_at=excluded.updated_at
            """,
            (
                name,
                str(path),
                description,
                json.dumps(triggers, ensure_ascii=False),
                version,
                utc_now(),
            ),
        )

    def record_eval_run(
        self,
        *,
        run_id: str,
        source_type: str,
        source_path: Path,
        status: str,
        input_text: str,
        report_path: Path,
        summary: dict[str, Any],
    ) -> None:
        self.execute(
            """
            INSERT INTO eval_runs(id, source_type, source_path, status, input, report_path, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_type,
                str(source_path),
                status,
                input_text,
                str(report_path),
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )

    def list_eval_runs(self) -> list[dict[str, Any]]:
        rows = self.query(
            """
            SELECT id, source_type, source_path, status, input, report_path, summary, created_at
            FROM eval_runs
            ORDER BY created_at DESC
            """
        )
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _requires_clean_slate_reset(conn: sqlite3.Connection) -> bool:
    family = _store_schema_family(conn)
    if family == STORE_SCHEMA_FAMILY:
        return False
    if family is not None:
        raise RuntimeError(f"unsupported agent store schema family: {family}")

    versions = {int(row["version"]) for row in conn.execute("SELECT version FROM schema_migrations")}
    return bool(versions) or _has_user_schema_objects(conn)


def _store_schema_family(conn: sqlite3.Connection) -> str | None:
    if not _table_exists(conn, "store_metadata"):
        return None
    row = conn.execute(
        "SELECT value FROM store_metadata WHERE key='schema_family'"
    ).fetchone()
    return str(row["value"]) if row is not None else None


def _has_user_schema_objects(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'view', 'trigger')
          AND name NOT LIKE 'sqlite_%'
          AND name != 'schema_migrations'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _reset_sqlite_schema(conn: sqlite3.Connection) -> None:
    with conn:
        rows = conn.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'trigger', 'index')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY CASE type
                WHEN 'view' THEN 0
                WHEN 'trigger' THEN 1
                WHEN 'index' THEN 2
                WHEN 'table' THEN 3
                ELSE 4
              END
            """
        ).fetchall()
        for row in rows:
            conn.execute(f"DROP {row['type'].upper()} IF EXISTS {_quote_identifier(str(row['name']))}")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None

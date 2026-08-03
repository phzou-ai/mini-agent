from __future__ import annotations

import sqlite3

import pytest

from vermay_agent import storage
from vermay_agent.storage import AgentStore, SchemaMigration


def _table_names(store: AgentStore) -> set[str]:
    return {row["name"] for row in store.query("SELECT name FROM sqlite_master WHERE type='table'")}


def test_agent_store_creates_the_active_main_agent_baseline(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    names = _table_names(store)

    assert {
        "store_metadata",
        "memory_items",
        "skill_index",
        "eval_runs",
        "eval_results",
        "model_profiles",
        "contexts",
        "messages",
        "route_decisions",
        "main_agent_tasks",
        "main_agent_task_events",
        "artifacts",
        "registered_agents",
        "delegated_tasks",
        "main_agent_pending_continuations",
        "main_agent_message_ingress",
        "main_agent_queued_executions",
        "schema_migrations",
    } <= names
    assert {"sessions", "legacy_sessions", "tasks", "task_events", "task_artifacts"}.isdisjoint(names)
    assert store.schema_version() == storage.SCHEMA_VERSION == 2
    assert store.query("SELECT value FROM store_metadata WHERE key='schema_family'")[0]["value"] == (
        storage.STORE_SCHEMA_FAMILY
    )

    context_columns = {row["name"] for row in store.query("PRAGMA table_info(contexts)")}
    message_columns = {row["name"] for row in store.query("PRAGMA table_info(messages)")}
    task_columns = {row["name"] for row in store.query("PRAGMA table_info(main_agent_tasks)")}
    assert "next_message_sequence" in context_columns
    assert "context_sequence" in message_columns
    assert "input_context_sequence" in task_columns
    store.close()


def test_agent_store_enforces_runtime_sqlite_connection_contract(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")

    assert store.query("PRAGMA foreign_keys")[0]["foreign_keys"] == 1
    assert store.query("PRAGMA busy_timeout")[0]["timeout"] == storage.SQLITE_BUSY_TIMEOUT_MS
    assert store.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"

    with pytest.raises(sqlite3.IntegrityError):
        store.execute(
            """
            INSERT INTO messages(
                message_id, context_id, context_sequence, role, parts, task_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("orphan", "missing-context", 1, "user", "[]", None, "{}", "2026-08-02T00:00:00+00:00"),
        )
    store.close()


def test_agent_store_baseline_is_idempotent_across_reopening(tmp_path):
    path = tmp_path / "agent.sqlite"
    first = AgentStore(path)
    first.close()

    reopened = AgentStore(path)
    rows = reopened.query("SELECT version, COUNT(*) AS count FROM schema_migrations GROUP BY version")

    assert {int(row["version"]): int(row["count"]) for row in rows} == {1: 1, 2: 1}
    assert reopened.schema_version() == storage.SCHEMA_VERSION == 2
    reopened.close()


def test_agent_store_discards_a_retired_schema_and_recreates_the_baseline(tmp_path):
    path = tmp_path / "agent.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version, applied_at) VALUES (12, '2026-08-02T00:00:00+00:00');
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO sessions(session_id, title, created_at, updated_at)
        VALUES ('session-old', 'discard me', '2026-08-02T00:00:00+00:00', '2026-08-02T00:00:00+00:00');
        """
    )
    connection.close()

    store = AgentStore(path)

    names = _table_names(store)
    assert "sessions" not in names
    assert store.schema_version() == storage.SCHEMA_VERSION == 2
    assert store.query("SELECT value FROM store_metadata WHERE key='schema_family'")[0]["value"] == (
        storage.STORE_SCHEMA_FAMILY
    )
    store.close()


def test_agent_store_discards_unversioned_retired_schema(tmp_path):
    path = tmp_path / "agent.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sessions (thread_id TEXT PRIMARY KEY, input TEXT NOT NULL)")
    connection.execute("INSERT INTO sessions(thread_id, input) VALUES ('old-thread', 'discard me')")
    connection.commit()
    connection.close()

    store = AgentStore(path)

    assert "sessions" not in _table_names(store)
    assert store.schema_version() == storage.SCHEMA_VERSION == 2
    store.close()


def test_agent_store_rejects_an_unknown_schema_family(tmp_path):
    path = tmp_path / "agent.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE store_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO store_metadata(key, value) VALUES ('schema_family', 'future_agent_store');
        """
    )
    connection.close()

    with pytest.raises(RuntimeError, match="unsupported agent store schema family"):
        AgentStore(path)


def test_agent_store_transaction_rolls_back_execute_calls(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")

    with pytest.raises(RuntimeError, match="rollback probe"):
        with store.transaction():
            store.execute(
                """
                INSERT INTO skill_index(name, path, triggers, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                ("probe", "/tmp/probe", "[]", "2026-08-02T00:00:00+00:00"),
            )
            raise RuntimeError("rollback probe")

    assert store.query("SELECT name FROM skill_index WHERE name=?", ("probe",)) == []
    store.close()


def test_agent_store_failed_future_migration_is_not_marked_applied(tmp_path, monkeypatch):
    def broken_migration(conn):
        conn.execute("CREATE TABLE broken_migration_probe (id INTEGER PRIMARY KEY)")
        raise RuntimeError("migration failed")

    monkeypatch.setattr(
        storage,
        "MIGRATIONS",
        storage.MIGRATIONS + (SchemaMigration(3, "broken", broken_migration),),
    )
    path = tmp_path / "agent.sqlite"

    with pytest.raises(RuntimeError, match="migration failed"):
        AgentStore(path)

    connection = sqlite3.connect(path)
    rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    connection.close()

    assert [int(row[0]) for row in rows] == [1, 2]

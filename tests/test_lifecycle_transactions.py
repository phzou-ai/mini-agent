from __future__ import annotations

import pytest

from vermay.main_agent import MainAgentStore
from vermay.main_agent.lifecycle_transactions import (
    LifecyclePostCommitAction,
    LifecyclePostCommitActionKind,
    LifecycleTransactionRunner,
)
from vermay.storage import AgentStore


def test_lifecycle_transaction_runs_side_effect_after_commit(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = LifecycleTransactionRunner(store)
    observed: list[tuple[int, str]] = []

    def persist_context():
        return store.create_context(context_id="ctx-1")

    def observe_committed_context(context):
        persisted = store.get_context(context.context_id)
        assert persisted is not None
        observed.append((store.store._transaction_depth, persisted.context_id))
        return "started"

    outcome = runner.execute(
        persist_context,
        post_commit=LifecyclePostCommitAction(
            kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
            callback=observe_committed_context,
        ),
    )

    assert outcome.committed.context_id == "ctx-1"
    assert outcome.post_commit_result == "started"
    assert observed == [(0, "ctx-1")]


def test_lifecycle_transaction_does_not_run_side_effect_after_rollback(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = LifecycleTransactionRunner(store)
    observed: list[str] = []

    def fail_after_write():
        store.create_context(context_id="ctx-rollback")
        raise RuntimeError("reject transaction")

    with pytest.raises(RuntimeError, match="reject transaction"):
        runner.execute(
            fail_after_write,
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                callback=lambda _context: observed.append("unexpected"),
            ),
        )

    assert store.get_context("ctx-rollback") is None
    assert observed == []


def test_lifecycle_transaction_preserves_commit_when_side_effect_fails(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = LifecycleTransactionRunner(store)

    def fail_after_commit(_context):
        raise RuntimeError("worker unavailable")

    with pytest.raises(RuntimeError, match="worker unavailable"):
        runner.execute(
            lambda: store.create_context(context_id="ctx-committed"),
            post_commit=LifecyclePostCommitAction(
                kind=LifecyclePostCommitActionKind.START_LOCAL_EXECUTION,
                callback=fail_after_commit,
            ),
        )

    assert store.get_context("ctx-committed") is not None

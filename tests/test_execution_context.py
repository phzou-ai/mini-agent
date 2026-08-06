from __future__ import annotations

import time

from vermay.execution_context import ExecutionContextRegistry, current_execution_context


def test_execution_context_registry_exposes_only_an_active_runtime_thread():
    registry = ExecutionContextRegistry()

    assert registry.request_cancellation("thread-r3", reason="before start") is False

    with registry.activate("thread-r3") as context:
        assert current_execution_context() is context
        assert context.cancellation is not None
        assert registry.request_cancellation("thread-r3", reason="operator canceled") is True
        assert context.cancellation.requested is True
        assert context.cancellation.reason == "operator canceled"

    assert current_execution_context() is None
    assert registry.request_cancellation("thread-r3", reason="after completion") is False


def test_execution_context_registry_binds_model_context_to_active_execution():
    registry = ExecutionContextRegistry()

    with registry.activate("thread-model") as active:
        with registry.bind_model_context(
            runtime_thread_id="thread-model",
            deadline_monotonic=time.monotonic() + 5,
        ) as context:
            assert current_execution_context() is context
            assert context.runtime_thread_id == "thread-model"
            assert context.invocation_id is None
            assert context.cancellation is active.cancellation
            assert context.remaining_seconds() is not None
            assert 0 < context.remaining_seconds() <= 5

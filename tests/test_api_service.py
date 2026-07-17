from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import pytest

from vermay_agent.api.output_envelope import final_answer_envelope
from vermay_agent.api.session_models import TaskStatus
from vermay_agent.api.service import AgentService, AgentStartOptions, SessionConflictError, TaskExecutionLocks
from vermay_agent.api.session_store import SessionStore
from vermay_agent.app_factory import RuntimeFactoryConfig
from vermay_agent.errors import ModelProviderError
from vermay_agent.langgraph_runtime import ModelProviderConfig
from vermay_agent.langgraph_runtime.results import RunResult
from vermay_agent.mcp.selection import MCPPromptSelectionConfig, MCPResourceSelectionConfig, MCPSelectionConfig
from vermay_agent.storage import AgentStore


class FakeRuntime:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.closed = False

    def start(self, user_input, thread_id=None):
        return self._next(thread_id)

    def resume(self, thread_id, approved, reason=None):
        return self._next(thread_id)

    def close(self):
        self.closed = True

    def _next(self, thread_id):
        response = self.responses.pop(0)
        if callable(response):
            return response(thread_id)
        return response


class FailingRuntime:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False

    def start(self, user_input, thread_id=None):
        raise self.exc

    def resume(self, thread_id, approved, reason=None):
        raise self.exc

    def close(self):
        self.closed = True


class FakeLifecycleObserver:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event_type, payload):
        self.events.append((event_type, dict(payload)))


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.closed = False

    def start(self, user_input, thread_id=None):
        self.started.set()
        self.release.wait(timeout=5)
        self.finished.set()
        return RunResult(thread_id=thread_id, final_answer="done")

    def resume(self, thread_id, approved, reason=None):
        self.started.set()
        self.release.wait(timeout=5)
        self.finished.set()
        return RunResult(thread_id=thread_id, final_answer="resumed")

    def close(self):
        self.closed = True


class BlockingFailingRuntime(BlockingRuntime):
    def start(self, user_input, thread_id=None):
        self.started.set()
        self.release.wait(timeout=5)
        raise RuntimeError("runtime failed after cancel")


class ConcurrentRuntime:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.started = 0
        self.two_started = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def start(self, user_input, thread_id=None):
        with self._guard:
            self.started += 1
            if self.started >= 2:
                self.two_started.set()
        self.release.wait(timeout=5)
        return RunResult(thread_id=thread_id, final_answer=user_input)

    def resume(self, thread_id, approved, reason=None):
        return self.start(reason or "resumed", thread_id=thread_id)

    def close(self):
        self.closed = True


class ManualTaskExecutionService:
    def __init__(self) -> None:
        self.submitted = []

    def submit(self, func, *args):
        self.submitted.append((func, args))
        future = Future()
        future.set_result(None)
        return future

    def shutdown(self):
        return None

    def run_next(self):
        func, args = self.submitted.pop(0)
        return func(*args)


def completed(answer="done"):
    return lambda thread_id: RunResult(thread_id=thread_id, final_answer=answer)


def interrupted():
    return lambda thread_id: RunResult(
        thread_id=thread_id,
        interrupt={"kind": "approval_required"},
        interrupt_message="Approval required.",
    )


def make_service(tmp_path, runtime, *, observer=None, built_configs=None, task_execution_service=None):
    store = AgentStore(tmp_path / "agent.sqlite")

    def build(config):
        if built_configs is not None:
            built_configs.append(config)
        return runtime

    service = AgentService(
        session_store=SessionStore(store),
        default_config=RuntimeFactoryConfig(show_progress=False),
        runtime_builder=build,
        lifecycle_observer=observer,
        task_execution_service=task_execution_service,
    )
    return service, store


def wait_for_task_status(service, task_id, status, *, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = service.get_task(task_id)
        if task is not None and task.status == status:
            return task
        time.sleep(0.01)
    task = service.get_task(task_id)
    raise AssertionError(f"task {task_id} did not reach {status}; current={task.status if task else None}")


def test_service_starts_task_in_session_with_default_runtime(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed("done")]))
    session = service.create_session(session_id="session-1", title="Ops")

    task = service.start_task(session.session_id, "hello", task_id="task-1")

    assert task.task_id == "task-1"
    assert task.session_id == "session-1"
    assert task.thread_id == "task:task-1:attempt:1"
    assert task.final_answer == "done"
    assert task.status == TaskStatus.COMPLETED
    events = service.list_task_events("task-1")
    assert [event.event_type for event in events] == [
        "task_created",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    assert events[2].payload == {
        "artifact_id": "task-1:final_answer",
        "a2a_artifact_id": "final_answer",
        "name": "Final answer",
    }
    assert "done" not in str(events[2].payload)
    artifacts = service.session_store.list_task_artifacts("task-1")
    assert len(artifacts) == 1
    assert artifacts[0].parts == [{"text": "done", "mediaType": "text/plain"}]
    assert artifacts[0].metadata == final_answer_envelope().to_metadata()
    service.close()
    store.close()


def test_service_start_task_wait_false_returns_queued_and_completes_in_background(tmp_path):
    runtime = BlockingRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")

    task = service.start_task("session-1", "hello", task_id="task-1", wait=False)

    assert task.status == TaskStatus.QUEUED
    assert [event.event_type for event in service.list_task_events("task-1")] == ["task_created", "task_queued"]
    assert runtime.started.wait(timeout=5)
    running = wait_for_task_status(service, "task-1", TaskStatus.RUNNING)
    assert running.final_answer is None

    runtime.release.set()
    completed_task = wait_for_task_status(service, "task-1", TaskStatus.COMPLETED)

    assert completed_task.final_answer == "done"
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    service.close()
    store.close()


def test_service_cancels_queued_task_before_worker_runs(tmp_path):
    executor = ManualTaskExecutionService()
    service, store = make_service(tmp_path, FakeRuntime([completed("done")]), task_execution_service=executor)
    service.create_session(session_id="session-1")
    queued = service.start_task("session-1", "hello", task_id="task-1", wait=False)

    canceled = service.cancel_task("task-1", reason="operator requested")
    executor.run_next()
    final_record = service.get_task("task-1")

    assert queued.status == TaskStatus.QUEUED
    assert canceled.status == TaskStatus.CANCELED
    assert canceled.stop_message == "Task canceled: operator requested"
    assert final_record is not None
    assert final_record.status == TaskStatus.CANCELED
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_queued",
        "task_cancel_requested",
        "task_cancelled",
    ]
    service.close()
    store.close()


def test_service_running_task_records_cancel_request_and_cancels_at_safe_boundary(tmp_path):
    runtime = BlockingRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1", wait=False)
    assert runtime.started.wait(timeout=5)

    cancel_requested = service.cancel_task("task-1", reason="operator requested")
    runtime.release.set()
    canceled = wait_for_task_status(service, "task-1", TaskStatus.CANCELED)

    assert cancel_requested.status == TaskStatus.CANCEL_REQUESTED
    assert cancel_requested.stop_message == "Task canceled: operator requested"
    assert canceled.final_answer is None
    assert canceled.stop_message == "Task canceled: operator requested"
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_cancel_requested",
        "task_cancelled",
    ]
    service.close()
    store.close()


def test_service_cancel_request_wins_over_late_runtime_failure(tmp_path):
    runtime = BlockingFailingRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1", wait=False)
    assert runtime.started.wait(timeout=5)

    service.cancel_task("task-1", reason="operator requested")
    runtime.release.set()
    canceled = wait_for_task_status(service, "task-1", TaskStatus.CANCELED)

    assert canceled.error_code is None
    assert canceled.error_message is None
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_cancel_requested",
        "task_cancelled",
    ]
    service.close()
    store.close()


def test_service_start_task_wait_false_persists_background_failure(tmp_path):
    service, store = make_service(tmp_path, FailingRuntime(RuntimeError("model unavailable")))
    service.create_session(session_id="session-1")

    task = service.start_task("session-1", "hello", task_id="task-1", wait=False)
    failed = wait_for_task_status(service, task.task_id, TaskStatus.FAILED)

    assert failed.error_code == "runtime_error"
    assert failed.error_message == "model unavailable"
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_failed",
    ]
    service.close()
    store.close()


def test_service_completed_event_payload_contract_excludes_final_answer_text(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed("sensitive answer")]))
    service.create_session(session_id="session-1")

    service.start_task("session-1", "hello", task_id="task-1")
    events = service.list_task_events("task-1")
    payload_by_type = {event.event_type: event.payload for event in events}

    assert payload_by_type["task_artifact_created"] == {
        "artifact_id": "task-1:final_answer",
        "a2a_artifact_id": "final_answer",
        "name": "Final answer",
    }
    assert payload_by_type["task_completed"] == {}
    assert "sensitive answer" not in str([event.payload for event in events])
    service.close()
    store.close()


def test_service_failed_event_payload_contract_excludes_error_detail(tmp_path):
    service, store = make_service(tmp_path, FailingRuntime(RuntimeError("sensitive backend detail")))
    service.create_session(session_id="session-1")

    with pytest.raises(RuntimeError, match="sensitive backend detail"):
        service.start_task("session-1", "hello", task_id="task-1")

    events = service.list_task_events("task-1")

    assert events[-1].event_type == "task_failed"
    assert events[-1].payload == {"error_code": "runtime_error"}
    assert "sensitive backend detail" not in str([event.payload for event in events])
    service.close()
    store.close()


def test_service_resumes_interrupted_task_with_default_runtime(tmp_path):
    runtime = FakeRuntime([interrupted(), completed("approved")])
    built_configs = []
    service, store = make_service(tmp_path, runtime, built_configs=built_configs)
    session = service.create_session(session_id="session-1")

    interrupted_task = service.start_task(session.session_id, "dangerous", task_id="task-1")
    result = service.resume_task(interrupted_task.task_id, approved=True)

    assert result.final_answer == "approved"
    assert result.status == TaskStatus.COMPLETED
    assert len(built_configs) == 1
    events = service.list_task_events("task-1")
    assert [event.event_type for event in events] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    service.close()
    store.close()


def test_service_does_not_create_artifact_for_non_completed_results(tmp_path):
    stopped = lambda thread_id: RunResult(thread_id=thread_id, stop_message="Stopped.")
    service, store = make_service(tmp_path, FakeRuntime([interrupted(), stopped]))
    service.create_session(session_id="session-1")

    service.start_task("session-1", "dangerous", task_id="task-1")
    service.start_task("session-1", "stop", task_id="task-2")

    assert service.session_store.list_task_artifacts("task-1") == []
    assert service.session_store.list_task_artifacts("task-2") == []
    service.close()
    store.close()


def test_service_retries_completed_task_as_new_task_with_lineage(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed("first"), completed("retry")]))
    service.create_session(session_id="session-1")
    source = service.start_task("session-1", "hello", task_id="task-1")

    retry = service.retry_task(source.task_id, new_task_id="task-2", reason="try again")

    assert source.status == TaskStatus.COMPLETED
    assert retry.task_id == "task-2"
    assert retry.session_id == "session-1"
    assert retry.thread_id == "task:task-2:attempt:2"
    assert retry.root_task_id == "task-1"
    assert retry.retry_of_task_id == "task-1"
    assert retry.attempt == 2
    assert retry.input == "hello"
    assert retry.final_answer == "retry"
    assert retry.status == TaskStatus.COMPLETED
    source_events = service.list_task_events("task-1")
    retry_events = service.list_task_events("task-2")
    assert [event.event_type for event in source_events] == [
        "task_created",
        "task_started",
        "task_artifact_created",
        "task_completed",
        "task_retry_requested",
        "task_retried",
    ]
    assert source_events[-2].payload == {"reason": "try again"}
    assert source_events[-1].payload == {"new_task_id": "task-2", "attempt": 2}
    assert [event.event_type for event in retry_events] == [
        "task_created",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    service.close()
    store.close()


def test_service_retries_failed_task_as_new_task(tmp_path):
    service, store = make_service(tmp_path, FailingRuntime(RuntimeError("model unavailable")))
    service.create_session(session_id="session-1")
    with pytest.raises(RuntimeError, match="model unavailable"):
        service.start_task("session-1", "hello", task_id="task-1")
    service.close()

    service = AgentService(
        session_store=SessionStore(store),
        default_config=RuntimeFactoryConfig(show_progress=False),
        runtime_builder=lambda config: FakeRuntime([completed("retry")]),
    )

    retry = service.retry_task("task-1", new_task_id="task-2")

    assert retry.status == TaskStatus.COMPLETED
    assert retry.final_answer == "retry"
    assert retry.root_task_id == "task-1"
    assert retry.retry_of_task_id == "task-1"
    assert retry.attempt == 2
    service.close()
    store.close()


def test_service_retries_latest_chain_attempt_number(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed("first"), completed("second"), completed("third")]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1")
    second = service.retry_task("task-1", new_task_id="task-2")

    third = service.retry_task(second.task_id, new_task_id="task-3")

    assert second.attempt == 2
    assert third.attempt == 3
    assert third.root_task_id == "task-1"
    assert third.retry_of_task_id == "task-2"
    assert third.thread_id == "task:task-3:attempt:3"
    service.close()
    store.close()


def test_service_rejects_retry_for_active_task(tmp_path):
    runtime = BlockingRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1", wait=False)
    assert runtime.started.wait(timeout=5)

    with pytest.raises(SessionConflictError, match="not retryable"):
        service.retry_task("task-1", new_task_id="task-2")

    runtime.release.set()
    wait_for_task_status(service, "task-1", TaskStatus.COMPLETED)
    service.close()
    store.close()


def test_service_retry_wait_false_returns_queued_and_completes_in_background(tmp_path):
    runtime = FakeRuntime([completed("first"), completed("retry")])
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1")

    queued = service.retry_task("task-1", new_task_id="task-2", wait=False)
    completed_task = wait_for_task_status(service, "task-2", TaskStatus.COMPLETED)

    assert queued.status == TaskStatus.QUEUED
    assert queued.attempt == 2
    assert completed_task.final_answer == "retry"
    assert [event.event_type for event in service.list_task_events("task-2")] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    service.close()
    store.close()


def test_service_retry_preserves_model_mcp_and_max_loops(tmp_path):
    built_configs = []
    service, store = make_service(
        tmp_path,
        FakeRuntime([completed("first"), completed("retry")]),
        built_configs=built_configs,
    )
    selection = MCPSelectionConfig(
        servers=("k8s",),
        prompts=(MCPPromptSelectionConfig(server="k8s", name="k8s-debug", arguments={"service": "phzou-core"}),),
        resources=(MCPResourceSelectionConfig(server="k8s", uri="k8s://cluster/default/services"),),
    )
    model = ModelProviderConfig(provider="openai_compatible", options={"model": "qwen"})
    service.create_session(session_id="session-1")
    service.start_task(
        "session-1",
        "hello",
        task_id="task-1",
        options=AgentStartOptions(model=model, max_loops=2, mcp=selection),
    )

    retry = service.retry_task("task-1", new_task_id="task-2")

    assert retry.model == {"provider": "openai_compatible", "options": {"model": "qwen"}}
    assert retry.max_loops == 2
    assert retry.mcp == selection.to_payload()
    assert built_configs[-1].model.provider == "openai_compatible"
    assert built_configs[-1].max_loops == 2
    assert built_configs[-1].mcp_servers == ("k8s",)
    service.close()
    store.close()


def test_service_resume_task_wait_false_returns_queued_and_completes_in_background(tmp_path):
    runtime = FakeRuntime([interrupted(), completed("approved")])
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.start_task("session-1", "dangerous", task_id="task-1")

    queued = service.resume_task("task-1", approved=True, reason="approved", wait=False)
    completed_task = wait_for_task_status(service, "task-1", TaskStatus.COMPLETED)

    assert queued.status == TaskStatus.QUEUED
    assert completed_task.final_answer == "approved"
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_queued",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    service.close()
    store.close()


def test_service_cancels_interrupted_task_and_rejects_resume(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([interrupted()]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "dangerous", task_id="task-1")

    canceled = service.cancel_task("task-1")

    assert canceled.status == TaskStatus.CANCELED
    with pytest.raises(SessionConflictError, match="not waiting for resume"):
        service.resume_task("task-1", approved=True)
    assert [event.event_type for event in service.list_task_events("task-1")] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_cancel_requested",
        "task_cancelled",
    ]
    service.close()
    store.close()


def test_service_rejects_cancel_for_terminal_task(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed()]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1")

    with pytest.raises(SessionConflictError, match="already terminal"):
        service.cancel_task("task-1")

    service.close()
    store.close()


def test_service_persists_explicit_max_loops_override(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed()]))
    service.create_session(session_id="session-1")

    task = service.start_task("session-1", "hello", task_id="task-1", options=AgentStartOptions(max_loops=2))

    assert task.max_loops == 2
    service.close()
    store.close()


def test_service_emits_lifecycle_events_for_completed_task(tmp_path):
    observer = FakeLifecycleObserver()
    service, store = make_service(tmp_path, FakeRuntime([completed()]), observer=observer)
    selection = MCPSelectionConfig(servers=("k8s",))
    service.create_session(session_id="session-1")

    service.start_task(
        "session-1",
        "hello",
        task_id="task-1",
        options=AgentStartOptions(
            model=ModelProviderConfig(provider="openai_compatible", options={"model": "qwen"}),
            max_loops=2,
            mcp=selection,
        ),
    )

    assert [event for event, _ in observer.events] == [
        "task_created",
        "task_started",
        "task_completed",
    ]
    for _, payload in observer.events:
        assert set(payload) == {
            "session_id",
            "task_id",
            "thread_id",
            "operation",
            "status",
            "model_provider",
            "max_loops",
            "mcp_selected",
            "duration_ms",
            "error_code",
        }
        assert payload["session_id"] == "session-1"
        assert payload["task_id"] == "task-1"
        assert payload["thread_id"] == "task:task-1:attempt:1"
        assert payload["operation"] == "start_task"
        assert payload["model_provider"] == "openai_compatible"
        assert payload["max_loops"] == 2
        assert payload["mcp_selected"] is True
        assert isinstance(payload["duration_ms"], int)
        assert payload["duration_ms"] >= 0
        assert payload["error_code"] is None
        assert "input" not in payload
        assert "final_answer" not in payload
    assert observer.events[-1][1]["status"] == "completed"
    service.close()
    store.close()


def test_service_rejects_duplicate_task_start(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed(), completed("second")]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1")

    with pytest.raises(SessionConflictError, match="task already exists"):
        service.start_task("session-1", "hello again", task_id="task-1")

    service.close()
    store.close()


def test_service_rejects_resume_for_non_interrupted_task(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([completed()]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "hello", task_id="task-1")

    with pytest.raises(SessionConflictError, match="not waiting for resume"):
        service.resume_task("task-1", approved=True)

    service.close()
    store.close()


def test_service_rejects_concurrent_same_task_start(tmp_path):
    runtime = BlockingRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    errors: list[Exception] = []

    def run_first() -> None:
        try:
            service.start_task("session-1", "hello", task_id="task-1")
        except Exception as exc:  # pragma: no cover - asserted after thread joins
            errors.append(exc)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert runtime.started.wait(timeout=5)

    with pytest.raises(SessionConflictError, match="already running"):
        service.start_task("session-1", "hello again", task_id="task-1")

    runtime.release.set()
    thread.join(timeout=5)

    assert errors == []
    service.close()
    store.close()


def test_service_runs_different_tasks_concurrently_on_default_runtime(tmp_path):
    runtime = ConcurrentRuntime()
    service, store = make_service(tmp_path, runtime)
    service.create_session(session_id="session-1")
    service.create_session(session_id="session-2")

    first = service.start_task("session-1", "first", task_id="task-1", wait=False)
    second = service.start_task("session-2", "second", task_id="task-2", wait=False)

    assert first.status == TaskStatus.QUEUED
    assert second.status == TaskStatus.QUEUED
    assert runtime.two_started.wait(timeout=2)
    runtime.release.set()
    wait_for_task_status(service, "task-1", TaskStatus.COMPLETED)
    wait_for_task_status(service, "task-2", TaskStatus.COMPLETED)

    service.close()
    store.close()


def test_service_marks_active_task_failed_when_runtime_returns_wrong_thread_id(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([RunResult(thread_id="wrong-thread", final_answer="done")]))
    service.create_session(session_id="session-1")

    with pytest.raises(RuntimeError, match="mismatched thread_id"):
        service.start_task("session-1", "hello", task_id="task-1")

    active_record = service.get_task("task-1")
    assert active_record is not None
    assert active_record.status == TaskStatus.FAILED
    assert active_record.error_code == "runtime_error"
    service.close()
    store.close()


def test_service_emits_lifecycle_events_for_interrupt_and_resume(tmp_path):
    observer = FakeLifecycleObserver()
    service, store = make_service(tmp_path, FakeRuntime([interrupted(), completed("approved")]), observer=observer)
    service.create_session(session_id="session-1")

    service.start_task("session-1", "dangerous", task_id="task-1")
    service.resume_task("task-1", approved=True)

    assert [event for event, _ in observer.events] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_started",
        "task_completed",
    ]
    assert observer.events[2][1]["status"] == "interrupted"
    assert observer.events[3][1]["operation"] == "resume_task"
    assert observer.events[3][1]["status"] == "interrupted"
    assert observer.events[-1][1]["operation"] == "resume_task"
    assert observer.events[-1][1]["status"] == "completed"
    service.close()
    store.close()


def test_service_marks_resume_task_failed_when_runtime_returns_wrong_thread_id(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([interrupted()]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "dangerous", task_id="task-1")
    service.close()

    service = AgentService(
        session_store=SessionStore(store),
        default_config=RuntimeFactoryConfig(show_progress=False),
        runtime_builder=lambda config: FakeRuntime([RunResult(thread_id="wrong-thread", final_answer="approved")]),
    )

    with pytest.raises(RuntimeError, match="mismatched thread_id"):
        service.resume_task("task-1", approved=True)

    active_record = service.get_task("task-1")
    assert active_record is not None
    assert active_record.status == TaskStatus.FAILED
    assert active_record.error_code == "runtime_error"
    service.close()
    store.close()


def test_service_persists_failed_start_task(tmp_path):
    service, store = make_service(tmp_path, FailingRuntime(RuntimeError("model unavailable")))
    service.create_session(session_id="session-1")

    with pytest.raises(RuntimeError, match="model unavailable"):
        service.start_task("session-1", "hello", task_id="task-1")

    record = service.get_task("task-1")
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert record.error_code == "runtime_error"
    assert record.error_message == "model unavailable"
    assert record.to_dict()["error"] == {"code": "runtime_error", "message": "model unavailable"}
    service.close()
    store.close()


def test_service_persists_model_error_and_retryability(tmp_path):
    model_error = ModelProviderError(
        "OpenAI-compatible request failed: HTTP 503 Service Unavailable",
        provider="openai_compatible",
        status_code=503,
        retryable=True,
    )
    service, store = make_service(tmp_path, FailingRuntime(model_error))
    service.create_session(session_id="session-1")

    with pytest.raises(ModelProviderError, match="HTTP 503"):
        service.start_task("session-1", "hello", task_id="task-1")

    record = service.get_task("task-1")
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert record.error_code == "model_error"
    assert record.error_message == "OpenAI-compatible request failed: HTTP 503 Service Unavailable"
    assert service.list_task_events("task-1")[-1].payload == {
        "error_code": "model_error",
        "retryable": True,
    }
    service.close()
    store.close()


def test_service_emits_lifecycle_event_for_failed_task_start(tmp_path):
    observer = FakeLifecycleObserver()
    service, store = make_service(
        tmp_path,
        FailingRuntime(RuntimeError("model unavailable")),
        observer=observer,
    )
    service.create_session(session_id="session-1")

    with pytest.raises(RuntimeError, match="model unavailable"):
        service.start_task("session-1", "hello", task_id="task-1")

    assert [event for event, _ in observer.events] == [
        "task_created",
        "task_started",
        "task_failed",
    ]
    assert observer.events[-1][1]["status"] == "failed"
    assert observer.events[-1][1]["error_code"] == "runtime_error"
    service.close()
    store.close()


def test_service_persists_invalid_request_error_code(tmp_path):
    service, store = make_service(tmp_path, FailingRuntime(ValueError("bad model config")))
    service.create_session(session_id="session-1")

    with pytest.raises(ValueError, match="bad model config"):
        service.start_task("session-1", "hello", task_id="task-1")

    record = service.get_task("task-1")
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert record.error_code == "invalid_request"
    assert record.error_message == "bad model config"
    service.close()
    store.close()


def test_service_preserves_mcp_selection_on_resume(tmp_path):
    built_configs = []
    service, store = make_service(tmp_path, FakeRuntime([interrupted(), completed("approved")]), built_configs=built_configs)
    selection = MCPSelectionConfig(
        servers=("k8s",),
        prompts=(MCPPromptSelectionConfig(server="k8s", name="k8s-debug", arguments={"service": "phzou-core"}),),
        resources=(MCPResourceSelectionConfig(server="k8s", uri="k8s://cluster/default/services"),),
    )
    service.create_session(session_id="session-1")

    service.start_task("session-1", "dangerous", task_id="task-1", options=AgentStartOptions(mcp=selection))
    service.resume_task("task-1", approved=True)

    assert built_configs[1].mcp_servers == ("k8s",)
    assert built_configs[1].mcp_prompts == ("k8s:k8s-debug?service=phzou-core",)
    assert built_configs[1].mcp_resources == ("k8s:k8s://cluster/default/services",)
    assert built_configs[2].mcp_servers == ("k8s",)
    assert built_configs[2].mcp_prompts == ("k8s:k8s-debug?service=phzou-core",)
    assert built_configs[2].mcp_resources == ("k8s:k8s://cluster/default/services",)
    service.close()
    store.close()


def test_task_execution_locks_prune_completed_task_ids():
    locks = TaskExecutionLocks()

    with locks.acquire("task-1"):
        assert "task-1" in locks._locks

    assert locks._locks == {}


def test_service_persists_failed_resume_task(tmp_path):
    service, store = make_service(tmp_path, FakeRuntime([interrupted()]))
    service.create_session(session_id="session-1")
    service.start_task("session-1", "dangerous", task_id="task-1")
    service.close()

    service = AgentService(
        session_store=SessionStore(store),
        default_config=RuntimeFactoryConfig(show_progress=False),
        runtime_builder=lambda config: FailingRuntime(RuntimeError("checkpoint unavailable")),
    )

    with pytest.raises(RuntimeError, match="checkpoint unavailable"):
        service.resume_task("task-1", approved=True)

    record = service.get_task("task-1")
    assert record is not None
    assert record.status == TaskStatus.FAILED
    assert record.error_message == "checkpoint unavailable"
    service.close()
    store.close()

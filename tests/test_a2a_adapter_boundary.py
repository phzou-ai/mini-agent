from __future__ import annotations

from concurrent.futures import Future

import pytest

from vermay.api.a2a import A2AAdapter
from vermay.errors import TaskNotFoundError
from vermay.main_agent import (
    LocalTaskRunResult,
    MainAgentCore,
    MainAgentRequest,
    MainAgentStore,
    MessageRole,
)
from vermay.main_agent.models import TaskStatus
from vermay.storage import AgentStore


class FakeLocalMessageResponder:
    def respond(self, messages):
        return [{"kind": "text", "text": "direct answer"}]


class FakeLocalTaskRunner:
    def run(self, messages, *, thread_id: str) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "task answer"}],
        )

    def resume(
        self,
        *,
        thread_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> LocalTaskRunResult:
        return LocalTaskRunResult(status=TaskStatus.COMPLETED)

    def resume_input(
        self,
        *,
        thread_id: str,
        parts: list[dict],
        metadata: dict | None = None,
    ) -> LocalTaskRunResult:
        return LocalTaskRunResult(status=TaskStatus.COMPLETED)


class ManualTaskSubmitter:
    def __init__(self) -> None:
        self.submitted = []

    def submit(self, func, *args):
        self.submitted.append((func, args))
        future = Future()
        future.set_result(None)
        return future

    def shutdown(self):
        return None


def test_a2a_adapter_get_cancel_and_event_replay_use_core_boundary(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
        task_submitter=ManualTaskSubmitter(),
    )
    core.store.create_context(context_id="ctx-1")
    task = core.handle_message(
        MainAgentRequest(
            context_id="ctx-1",
            message_id=None,
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run a task"}],
            metadata={"executionMode": "task"},
        )
    )
    adapter = A2AAdapter(main_agent_core=core)

    queued = adapter.get_task(task.task_id)
    canceled = adapter.cancel_task(task.task_id, reason="operator requested")
    replay = adapter.wait_for_task_events(
        task.task_id,
        after_event_id=0,
        timeout_seconds=0,
    )

    assert queued["result"]["kind"] == "task"
    assert queued["result"]["status"]["state"] == "submitted"
    assert canceled["result"]["status"]["state"] == "canceled"
    assert core.store.get_task(task.task_id).status == TaskStatus.CANCELED
    assert replay.events
    assert all(event["jsonrpc"] == "2.0" for event in replay.events)
    with pytest.raises(TaskNotFoundError):
        adapter.get_task("missing-task")

    agent_store.close()

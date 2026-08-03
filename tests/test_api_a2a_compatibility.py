from __future__ import annotations

from concurrent.futures import Future

import pytest
from fastapi.testclient import TestClient

from vermay_agent.api.a2a import A2AAdapter, A2ASendMessageRequest
from vermay_agent.api.app import create_app
from vermay_agent.errors import InvalidRequestError, TaskNotFoundError
from vermay_agent.main_agent import (
    LocalTaskRunResult,
    MainAgentCore,
    MainAgentRequest,
    MainAgentStore,
    MessageRole,
)
from vermay_agent.main_agent.models import TaskStatus
from vermay_agent.storage import AgentStore


class FakeLocalMessageResponder:
    def respond(self, messages):
        return [{"kind": "text", "text": "direct answer"}]


class FakeLocalTaskRunner:
    def __init__(self, answer: str = "task answer") -> None:
        self.answer = answer
        self.run_calls = []

    def run(self, messages, *, thread_id: str) -> LocalTaskRunResult:
        self.run_calls.append((messages, thread_id))
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": self.answer}],
        )

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "resumed task answer"}],
        )

    def resume_input(self, *, thread_id: str, parts: list[dict], metadata: dict | None = None) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "input resumed answer"}],
        )


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


def make_core(tmp_path, *, task_submitter=None, answer: str = "task answer"):
    store = AgentStore(tmp_path / "agent.sqlite")
    runner = FakeLocalTaskRunner(answer)
    core = MainAgentCore(
        store=MainAgentStore(store),
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=runner,
        task_submitter=task_submitter,
    )
    return core, store, runner


def make_task(core: MainAgentCore, *, context_id: str = "ctx-1"):
    core.store.create_context(context_id=context_id)
    return core.handle_message(
        MainAgentRequest(
            context_id=context_id,
            message_id=None,
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run a task"}],
            metadata={"executionMode": "task"},
        )
    )


def test_a2a_routes_map_invalid_message_and_unknown_task_errors(tmp_path):
    core, store, _runner = make_core(tmp_path)
    client = TestClient(create_app(enable_a2a=True, main_agent_core=core))

    invalid = client.post("/message:send", json={"message": {"role": "agent", "parts": [{"text": "hello"}]}})
    missing = client.get("/tasks/missing-task")

    assert invalid.status_code == 400
    assert invalid.json()["detail"] == {
        "code": "invalid_request",
        "message": "A2A message role must be 'user'.",
        "retryable": False,
    }
    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "code": "task_not_found",
        "message": "task not found",
        "retryable": False,
    }
    store.close()


def test_a2a_subscribe_route_maps_unknown_task_to_http_error_without_jsonrpc_body(tmp_path):
    core, store, _runner = make_core(tmp_path)
    client = TestClient(create_app(enable_a2a=True, main_agent_core=core))

    response = client.post("/tasks/missing-task:subscribe")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "task_not_found",
        "message": "task not found",
        "retryable": False,
    }
    store.close()


def test_a2a_path_binding_creates_and_projects_core_owned_task(tmp_path):
    core, store, runner = make_core(tmp_path, answer="weather done")
    core.store.create_context(context_id="ctx-1")
    adapter = A2AAdapter(main_agent_core=core)
    request = A2ASendMessageRequest.model_validate(
        {
            "message": {
                "role": "user",
                "contextId": "ctx-1",
                "parts": [{"text": "weather forecast for Beijing"}],
            },
            "metadata": {"executionMode": "task", "client": "pytest"},
        }
    )

    payload = adapter.send_message(request)

    assert payload["kind"] == "task"
    assert payload["id"].startswith("task-")
    assert payload["contextId"] == "ctx-1"
    assert payload["status"]["state"] == "completed"
    assert payload["metadata"]["localStatus"] == "completed"
    assert core.store.get_task(payload["id"]) is not None
    assert runner.run_calls[0][0][-1].parts == [{"text": "weather forecast for Beijing"}]
    store.close()


def test_a2a_path_binding_reuses_existing_core_context(tmp_path):
    core, store, _runner = make_core(tmp_path)
    core.store.create_context(context_id="ctx-1")
    adapter = A2AAdapter(main_agent_core=core)

    payload = adapter.send_message(
        A2ASendMessageRequest.model_validate(
            {
                "message": {
                    "role": "user",
                    "contextId": "ctx-1",
                    "parts": [{"text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            }
        )
    )

    assert payload["kind"] == "message"
    assert payload["contextId"] == "ctx-1"
    assert len(core.store.list_context_messages("ctx-1")) == 2
    store.close()


def test_a2a_send_message_rejects_empty_or_non_user_message(tmp_path):
    core, store, _runner = make_core(tmp_path)
    adapter = A2AAdapter(main_agent_core=core)

    with pytest.raises(InvalidRequestError, match="at least one text part"):
        adapter.send_message(A2ASendMessageRequest.model_validate({"message": {"role": "user", "parts": []}}))

    with pytest.raises(InvalidRequestError, match="role must be 'user'"):
        adapter.send_message(
            A2ASendMessageRequest.model_validate({"message": {"role": "agent", "parts": [{"text": "hello"}]}})
        )

    store.close()


def test_a2a_get_cancel_and_subscribe_use_core_boundary(tmp_path):
    executor = ManualTaskSubmitter()
    core, store, _runner = make_core(tmp_path, task_submitter=executor)
    task = make_task(core)
    adapter = A2AAdapter(main_agent_core=core)

    queued = adapter.get_task(task.task_id)
    canceled = adapter.cancel_task(task.task_id, reason="operator requested")
    subscribed = adapter.wait_for_task_events(task.task_id, after_event_id=0, timeout_seconds=0)

    assert queued["result"]["kind"] == "task"
    assert queued["result"]["status"]["state"] == "submitted"
    assert canceled["result"]["status"]["state"] == "canceled"
    assert core.store.get_task(task.task_id).status == TaskStatus.CANCELED
    assert subscribed.events
    assert all(event["jsonrpc"] == "2.0" for event in subscribed.events)
    with pytest.raises(TaskNotFoundError):
        adapter.get_task("missing-task")
    store.close()


def test_a2a_subscribe_path_replays_core_status_and_artifact_events(tmp_path):
    core, store, _runner = make_core(tmp_path, answer="done")
    client = TestClient(create_app(enable_a2a=True, main_agent_core=core))
    sent = client.post(
        "/message:send",
        json={
            "message": {
                "role": "user",
                "parts": [{"text": "hello"}],
            },
            "metadata": {"executionMode": "task"},
        },
    )
    task_id = sent.json()["id"]

    response = client.post(f"/tasks/{task_id}:subscribe")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: status-update" in response.text
    assert "event: artifact-update" in response.text
    assert "completed" in response.text
    assert "final_answer" in response.text
    assert "thread_id" not in response.text.lower()
    assert "localThreadId" in response.text
    store.close()

from __future__ import annotations

from concurrent.futures import Future

import pytest
from fastapi.testclient import TestClient

from vermay_agent.api.a2a import A2AAdapter, A2ASendMessageRequest
from vermay_agent.api.app import create_app
from vermay_agent.api.output_envelope import OutputVisibility, final_answer_envelope
from vermay_agent.api.service import AgentService
from vermay_agent.api.session_models import TaskStatus
from vermay_agent.api.session_store import SessionStore
from vermay_agent.errors import InvalidRequestError, TaskNotFoundError
from vermay_agent.langgraph_runtime.results import RunResult
from vermay_agent.storage import AgentStore


class FakeRuntime:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.started = []

    def start(self, user_input, thread_id=None):
        self.started.append((user_input, thread_id))
        response = self.responses.pop(0)
        if callable(response):
            return response(thread_id)
        return response

    def resume(self, thread_id, approved, reason=None):
        raise RuntimeError("not used")

    def close(self):
        return None


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


def completed(answer="done"):
    return lambda thread_id: RunResult(thread_id=thread_id, final_answer=answer)


def make_adapter(tmp_path, runtime, *, task_execution_service=None):
    store = AgentStore(tmp_path / "agent.sqlite")
    service = AgentService(
        session_store=SessionStore(store),
        runtime_builder=lambda config: runtime,
        task_execution_service=task_execution_service,
    )
    adapter = A2AAdapter(service=service)
    return adapter, store, service, runtime


def test_a2a_routes_map_invalid_message_and_unknown_task_errors(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    service = AgentService(session_store=SessionStore(store), runtime_builder=lambda config: FakeRuntime([completed()]))
    client = TestClient(create_app(service=service, enable_a2a=True))

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
    service.close()
    store.close()


def test_a2a_subscribe_route_maps_unknown_task_to_http_error_without_jsonrpc_body(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    service = AgentService(session_store=SessionStore(store), runtime_builder=lambda config: FakeRuntime([completed()]))
    client = TestClient(create_app(service=service, enable_a2a=True))

    response = client.post("/tasks/missing-task:subscribe")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "task_not_found",
        "message": "task not found",
        "retryable": False,
    }
    service.close()
    store.close()


def test_a2a_cancel_route_maps_to_service_boundary(tmp_path):
    executor = ManualTaskExecutionService()
    store = AgentStore(tmp_path / "agent.sqlite")
    service = AgentService(
        session_store=SessionStore(store),
        runtime_builder=lambda config: FakeRuntime([completed("unused")]),
        task_execution_service=executor,
    )
    service.create_session(session_id="session-1", context_id="ctx-1")
    service.start_task("session-1", "hello", task_id="task-1", wait=False)
    client = TestClient(create_app(service=service, enable_a2a=True))

    response = client.post("/tasks/task-1:cancel", json={"reason": "operator requested"})

    assert response.status_code == 200
    assert response.json()["jsonrpc"] == "2.0"
    assert response.json()["result"]["status"]["state"] == "canceled"
    assert service.get_task("task-1").status == TaskStatus.CANCELED
    service.close()
    store.close()


def test_a2a_subscribe_route_streams_projected_status_and_artifact_events(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    service = AgentService(
        session_store=SessionStore(store),
        runtime_builder=lambda config: FakeRuntime([completed("done")]),
    )
    client = TestClient(create_app(service=service, enable_a2a=True))
    client.post(
        "/message:send",
        json={"message": {"role": "user", "taskId": "task-1", "contextId": "ctx-1", "parts": [{"text": "hello"}]}},
    )

    response = client.post("/tasks/task-1:subscribe")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: status-update" in body
    assert "event: artifact-update" in body
    assert "completed" in body
    assert "final_answer" in body
    assert "thread_id" not in body.lower()
    assert "localThreadId" in body


def test_a2a_send_message_creates_context_session_task_and_projected_artifact(tmp_path):
    adapter, store, service, runtime = make_adapter(tmp_path, FakeRuntime([completed("weather done")]))
    request = A2ASendMessageRequest.model_validate(
        {
            "message": {
                "role": "user",
                "taskId": "task-1",
                "contextId": "ctx-1",
                "parts": [{"text": "weather forecast for Beijing"}],
                "metadata": {"tenant": "local", "ignored": "secret"},
            },
            "metadata": {"client": "pytest"},
        }
    )

    payload = adapter.send_message(request)

    assert payload["kind"] == "task"
    assert payload["id"] == "task-1"
    assert payload["contextId"] == "ctx-1"
    assert payload["status"]["state"] == "completed"
    assert payload["artifacts"][0]["artifactId"] == "final_answer"
    assert payload["artifacts"][0]["parts"] == [{"text": "weather done", "mediaType": "text/plain"}]
    assert "thread_id" not in str(payload).lower()
    assert payload["metadata"]["localThreadId"] == "task:task-1:attempt:1"
    assert runtime.started[0] == ("weather forecast for Beijing", "task:task-1:attempt:1")
    session = service.get_session_by_context_id("ctx-1")
    assert session is not None
    assert session.metadata == {"client": "pytest", "source": "a2a", "tenant": "local"}
    service.close()
    store.close()


def test_a2a_send_message_reuses_existing_context_session(tmp_path):
    adapter, store, service, _runtime = make_adapter(tmp_path, FakeRuntime([completed()]))
    existing = service.create_session(session_id="session-1", context_id="ctx-1")
    request = A2ASendMessageRequest.model_validate(
        {"message": {"role": "user", "taskId": "task-1", "contextId": "ctx-1", "parts": [{"text": "hello"}]}}
    )

    payload = adapter.send_message(request)

    task = service.get_task("task-1")
    assert task is not None
    assert task.session_id == existing.session_id
    assert payload["kind"] == "task"
    assert payload["contextId"] == "ctx-1"
    assert [session.session_id for session in service.list_sessions()] == ["session-1"]
    service.close()
    store.close()


def test_a2a_send_message_rejects_empty_or_non_user_message(tmp_path):
    adapter, store, service, _runtime = make_adapter(tmp_path, FakeRuntime([completed()]))

    with pytest.raises(InvalidRequestError, match="at least one text part"):
        adapter.send_message(A2ASendMessageRequest.model_validate({"message": {"role": "user", "parts": []}}))

    with pytest.raises(InvalidRequestError, match="role must be 'user'"):
        adapter.send_message(
            A2ASendMessageRequest.model_validate({"message": {"role": "agent", "parts": [{"text": "hello"}]}})
        )

    service.close()
    store.close()


def test_a2a_get_task_and_cancel_task_use_service_boundary(tmp_path):
    executor = ManualTaskExecutionService()
    adapter, store, service, _runtime = make_adapter(
        tmp_path,
        FakeRuntime([completed("unused")]),
        task_execution_service=executor,
    )
    service.create_session(session_id="session-1", context_id="ctx-1")
    service.start_task("session-1", "hello", task_id="task-1", wait=False)

    queued = adapter.get_task("task-1")
    canceled = adapter.cancel_task("task-1", reason="operator requested")

    assert queued["kind"] == "task"
    assert queued["status"]["state"] == "submitted"
    assert canceled["kind"] == "task"
    assert canceled["status"]["state"] == "canceled"
    assert service.get_task("task-1").status == TaskStatus.CANCELED
    with pytest.raises(TaskNotFoundError):
        adapter.get_task("missing-task")
    service.close()
    store.close()


def test_a2a_adapter_projects_status_and_artifact_events_without_internal_payloads(tmp_path):
    adapter, store, service, _runtime = make_adapter(tmp_path, FakeRuntime([completed("done")]))
    request = A2ASendMessageRequest.model_validate(
        {"message": {"role": "user", "taskId": "task-1", "contextId": "ctx-1", "parts": [{"text": "hello"}]}}
    )
    adapter.send_message(request)

    projections = adapter.project_task_events("task-1")

    assert [projection["kind"] for projection in projections] == [
        "status-update",
        "status-update",
        "artifact-update",
        "status-update",
    ]
    assert projections[-1]["status"]["state"] == "completed"
    assert projections[2]["artifact"]["artifactId"] == "final_answer"
    assert "thread_id" not in str(projections).lower()
    assert projections[0]["metadata"]["localThreadId"] == "task:task-1:attempt:1"
    service.close()
    store.close()


def test_a2a_adapter_filters_non_projectable_artifacts_from_task_and_event_projection(tmp_path):
    adapter, store, service, _runtime = make_adapter(tmp_path, FakeRuntime([completed("done")]))
    request = A2ASendMessageRequest.model_validate(
        {"message": {"role": "user", "taskId": "task-1", "contextId": "ctx-1", "parts": [{"text": "hello"}]}}
    )
    adapter.send_message(request)
    metadata = final_answer_envelope().to_metadata()
    metadata["visibility"] = OutputVisibility.INTERNAL.value
    service.session_store.upsert_task_artifact(
        artifact_id="task-1:final_answer",
        task_id="task-1",
        a2a_artifact_id="final_answer",
        name="Final answer",
        description="Final text answer returned by the agent.",
        parts=[{"text": "done", "mediaType": "text/plain"}],
        metadata=metadata,
        extensions=[],
    )

    task_payload = adapter.get_task("task-1")
    projections = adapter.project_task_events("task-1")

    assert "artifacts" not in task_payload
    assert [projection["kind"] for projection in projections] == [
        "status-update",
        "status-update",
        "status-update",
    ]
    assert "artifact-update" not in str(projections)
    service.close()
    store.close()

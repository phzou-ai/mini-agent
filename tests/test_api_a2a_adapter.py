from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from vermay.api.a2a import A2AAdapter, create_a2a_router
from vermay.api.a2a import routes as a2a_routes
from vermay.api.app import create_app
from vermay.errors import ModelProviderError
from vermay.main_agent import (
    LocalTaskRunResult,
    MainAgentCore,
    MainAgentStore,
    MessageRecord,
    RemoteAgentSendResult,
    RemoteAgentTaskSnapshot,
    RouteDecisionKind,
)
from vermay.main_agent.executor import InProcessTaskExecutor
from vermay.main_agent.models import TaskStatus as MainAgentTaskStatus
from vermay.storage import AgentStore


class FakeLocalMessageResponder:
    def __init__(self) -> None:
        self.calls = []

    def respond(self, messages: list[MessageRecord]) -> list[dict]:
        self.calls.append(messages)
        return [{"kind": "text", "text": "direct answer"}]


class FakeStreamingLocalMessageResponder(FakeLocalMessageResponder):
    def stream(self, messages: list[MessageRecord]):
        self.calls.append(messages)
        yield "streamed "
        yield "answer"


class FailingLocalMessageResponder(FakeLocalMessageResponder):
    def respond(self, messages: list[MessageRecord]) -> list[dict]:
        raise ModelProviderError(
            "Ollama request failed: <urlopen error [Errno 61] Connection refused>",
            provider="ollama",
            retryable=True,
        )


class FakeLocalTaskRunner:
    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "task answer"}],
        )

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "resumed task answer"}],
        )

    def resume_input(self, *, thread_id: str, parts: list[dict], metadata: dict | None = None) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "input resumed answer"}],
        )


class FailingLocalTaskRunner(FakeLocalTaskRunner):
    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        raise ModelProviderError(
            "Ollama request failed: <urlopen error [Errno 61] Connection refused>",
            provider="ollama",
            retryable=True,
        )


class SlowFakeLocalTaskRunner(FakeLocalTaskRunner):
    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        time.sleep(0.05)
        return super().run(messages, thread_id=thread_id)


class FakeInputTaskRunner(FakeLocalTaskRunner):
    def __init__(self) -> None:
        self.resume_input_calls = []

    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.INPUT_REQUIRED,
            parts=[{"kind": "text", "text": "Which environment?"}],
            input_request={
                "kind": "user_input_required",
                "prompt": "Which environment?",
                "choices": ["staging", "production"],
                "inputSchema": {"type": "string", "enum": ["staging", "production"]},
            },
        )

    def resume_input(self, *, thread_id: str, parts: list[dict], metadata: dict | None = None) -> LocalTaskRunResult:
        self.resume_input_calls.append((thread_id, parts, metadata))
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "staging is healthy"}],
        )


class FakeHoldingLocalTaskRunner(FakeLocalTaskRunner):
    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        return LocalTaskRunResult(status=MainAgentTaskStatus.RUNNING)


class FakeApprovalTaskRunner:
    def __init__(self) -> None:
        self.resume_calls = []

    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.INPUT_REQUIRED,
            parts=[{"kind": "text", "text": "approval required"}],
            error_code="input_required",
            error_message="approval required",
            input_request={"kind": "approval_required", "prompt": "Approve?"},
        )

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        self.resume_calls.append((thread_id, approved, reason))
        return LocalTaskRunResult(
            status=MainAgentTaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "approved answer"}],
        )


class FakeRemoteAgentClient:
    def __init__(self, responses, *, task_snapshots=None, cancel_snapshots=None) -> None:
        self.responses = list(responses)
        self.task_snapshots = list(task_snapshots or [])
        self.cancel_snapshots = list(cancel_snapshots or [])
        self.last_task_snapshot = None
        self.calls = []
        self.get_task_calls = []
        self.cancel_task_calls = []

    def send_message(self, *, agent, request, context_id, message_id):
        self.calls.append((agent.agent_id, context_id, message_id))
        return self.responses.pop(0)

    def get_task(self, *, agent, task_id):
        self.get_task_calls.append((agent.agent_id, task_id))
        if self.task_snapshots:
            self.last_task_snapshot = self.task_snapshots.pop(0)
        if self.last_task_snapshot is None:
            raise AssertionError("unexpected remote get_task call")
        return self.last_task_snapshot

    def cancel_task(self, *, agent, task_id, reason=None):
        self.cancel_task_calls.append((agent.agent_id, task_id, reason))
        snapshot = self.cancel_snapshots.pop(0)
        self.last_task_snapshot = snapshot
        return snapshot


def make_adapter(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    adapter = A2AAdapter()
    return adapter, store


def jsonrpc_error_data(local_code: str) -> dict:
    return {
        "localCode": local_code,
        "retryable": False,
        "errorInfo": {
            "reason": local_code,
            "domain": "vermay",
            "metadata": {"localCode": local_code},
        },
    }


def test_a2a_agent_card_declares_local_skeleton_capabilities(tmp_path):
    adapter, store = make_adapter(tmp_path)

    card = adapter.get_agent_card()

    assert card["name"] == "Vermay"
    assert card["url"] == "http://127.0.0.1:8000/rpc"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["capabilities"] == {
        "streaming": False,
        "pushNotifications": False,
        "extendedAgentCard": False,
    }
    assert card["securitySchemes"] == {}
    assert card["security"] == []
    assert card["defaultInputModes"] == ["text/plain"]
    assert card["defaultOutputModes"] == ["text/plain"]
    assert [skill["id"] for skill in card["skills"]] == [
        "direct-answer",
        "local-task-execution",
        "child-agent-delegation",
    ]
    assert card["metadata"]["routeKinds"] == ["local_message", "local_task", "remote_agent"]
    assert card["metadata"]["executionModes"] == ["message", "task", "auto"]
    store.close()


def test_a2a_agent_card_includes_enabled_registered_agent_summaries(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(store)
    main_store.upsert_registered_agent(
        agent_id="agent-sql",
        name="SQL Agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        card_json={"skills": [{"id": "sqlite-debug", "tags": ["sqlite", "database", "sqlite"]}]},
        metadata={"keywords": ["sql", "database", "SQL"]},
    )
    main_store.upsert_registered_agent(
        agent_id="agent-disabled",
        name="Disabled Agent",
        card_url="http://127.0.0.1:9002/.well-known/agent-card.json",
        enabled=False,
    )
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    adapter = A2AAdapter(main_agent_core=core)

    card = adapter.get_agent_card()

    assert card["metadata"]["registeredAgents"] == [
        {
            "agentId": "agent-sql",
            "name": "SQL Agent",
            "enabled": True,
            "keywords": ["sql", "database", "SQL"],
            "skillTags": ["sqlite", "database"],
            "skillIds": ["sqlite-debug"],
        }
    ]
    assert "card_url" not in str(card)
    assert "9001" not in str(card)
    assert "agent-disabled" not in str(card)
    store.close()


def test_a2a_routes_are_exposed_by_the_default_app(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    core = MainAgentCore(
        store=MainAgentStore(store),
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    card = client.get("/.well-known/agent-card.json")
    sent = client.post(
        "/message:send",
        json={
            "message": {
                "role": "user",
                "parts": [{"text": "weather forecast for Beijing"}],
            },
            "metadata": {"executionMode": "task"},
        },
    )
    task_id = sent.json()["id"]
    fetched = client.get(f"/tasks/{task_id}")
    legacy_local_fetched = client.get(f"/api/tasks/{task_id}")

    assert card.status_code == 200
    assert card.json()["capabilities"]["streaming"] is True
    assert [skill["id"] for skill in card.json()["skills"]] == [
        "direct-answer",
        "local-task-execution",
        "child-agent-delegation",
    ]
    assert card.json()["metadata"]["routeKinds"] == ["local_message", "local_task", "remote_agent"]
    assert sent.status_code == 200
    assert sent.json()["kind"] == "task"
    assert sent.json()["status"]["state"] == "completed"
    assert sent.json()["contextId"].startswith("ctx-")
    assert sent.json()["metadata"]["localStatus"] == "completed"
    assert "thread_id" not in str(sent.json()).lower()
    assert sent.json()["metadata"]["localThreadId"].startswith("thread-")
    assert fetched.status_code == 200
    assert fetched.json()["jsonrpc"] == "2.0"
    assert fetched.json()["result"]["id"] == task_id
    assert legacy_local_fetched.status_code == 404
    assert legacy_local_fetched.json() == {"detail": "Not Found"}
    store.close()


def test_create_app_with_fake_main_agent_supports_a2a_message_task_get_and_subscribe(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    message_response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-message",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-fake-message",
                    "parts": [{"kind": "text", "text": "hello fake"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )
    task_response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-fake-task",
                    "parts": [{"kind": "text", "text": "run fake task"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = task_response.json()["result"]["id"]
    fetched = client.get(f"/tasks/{task_id}")
    subscribed = client.post(f"/tasks/{task_id}:subscribe")

    assert message_response.status_code == 200
    assert message_response.json()["result"]["kind"] == "message"
    assert message_response.json()["result"]["parts"] == [
        {"kind": "text", "text": "direct answer"}
    ]
    assert task_response.status_code == 200
    assert task_response.json()["result"]["kind"] == "task"
    assert task_response.json()["result"]["status"]["state"] == "completed"
    assert fetched.status_code == 200
    assert fetched.json()["result"]["id"] == task_id
    assert fetched.json()["result"]["status"]["state"] == "completed"
    assert subscribed.status_code == 200
    assert "event: artifact-update" in subscribed.text
    assert "task answer" in subscribed.text
    agent_store.close()


def test_create_app_with_fake_main_agent_can_hold_and_cancel_task(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeHoldingLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    task_response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-fake-task",
                    "parts": [{"kind": "text", "text": "hold fake task"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = task_response.json()["result"]["id"]
    canceled = client.post(f"/tasks/{task_id}:cancel", json={"reason": "operator requested"})
    subscribed = client.post(f"/tasks/{task_id}:subscribe")

    assert task_response.status_code == 200
    assert task_response.json()["result"]["kind"] == "task"
    assert task_response.json()["result"]["status"]["state"] == "working"
    assert canceled.status_code == 200
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert subscribed.status_code == 200
    assert "task_cancelled" in subscribed.text
    agent_store.close()


def test_a2a_jsonrpc_message_send_can_return_main_agent_message_without_task(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    responder = FakeLocalMessageResponder()
    core = MainAgentCore(store=main_store, local_message_responder=responder)
    adapter = A2AAdapter(main_agent_core=core)

    payload = adapter.send_message_payload(
        {
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        }
    )

    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "req-1"
    assert payload["result"]["kind"] == "message"
    assert payload["result"]["role"] == "agent"
    assert payload["result"]["parts"] == [{"kind": "text", "text": "direct answer"}]
    context_id = payload["result"]["contextId"]
    assert [message.message_id for message in main_store.list_context_messages(context_id)] == [
        "msg-user-1",
        payload["result"]["messageId"],
    ]
    assert main_store.list_context_tasks(context_id) == []
    assert [message.message_id for message in responder.calls[0]] == ["msg-user-1"]
    agent_store.close()


def test_a2a_route_jsonrpc_message_send_uses_injected_main_agent_core(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    responder = FakeLocalMessageResponder()
    core = MainAgentCore(store=main_store, local_message_responder=responder)
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["kind"] == "message"
    assert response.json()["result"]["parts"] == [{"kind": "text", "text": "direct answer"}]
    context_id = response.json()["result"]["contextId"]
    assert main_store.list_context_tasks(context_id) == []
    agent_store.close()


def test_a2a_route_jsonrpc_remote_agent_message_is_projected(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="message",
                context_id="remote-ctx-1",
                message_id="remote-msg-1",
                parts=[{"kind": "text", "text": "remote answer"}],
            )
        ]
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["kind"] == "message"
    assert payload["result"]["parts"] == [{"kind": "text", "text": "remote answer"}]
    assert payload["result"]["metadata"]["routeKind"] == "remote_agent"
    assert payload["result"]["metadata"]["remoteAgentId"] == "agent-child-1"
    assert payload["result"]["metadata"]["delegationId"].startswith("delegate-")
    assert remote_client.calls == [("agent-child-1", payload["result"]["contextId"], "msg-user-1")]
    agent_store.close()


def test_a2a_route_jsonrpc_remote_agent_task_is_projected_as_proxy_task(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="submitted",
            )
        ],
        task_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="submitted",
            )
        ],
        cancel_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="canceled",
            )
        ],
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    fetched = client.get(f"/tasks/{task_id}")
    delegations_response = client.get(f"/api/contexts/{sent.json()['result']['contextId']}/delegations")
    canceled = client.post(f"/tasks/{task_id}:cancel", json={"reason": "test cleanup"})
    subscribed = client.post(f"/tasks/{task_id}:subscribe")

    assert sent.status_code == 200
    assert sent.json()["result"]["kind"] == "task"
    assert sent.json()["result"]["metadata"]["routeKind"] == "remote_agent"
    assert sent.json()["result"]["metadata"]["remoteAgentId"] == "agent-child-1"
    assert sent.json()["result"]["status"]["state"] == "submitted"
    assert fetched.status_code == 200
    assert fetched.json()["result"]["id"] == task_id
    assert delegations_response.status_code == 200
    assert delegations_response.json()[0]["remote_task_id"] == "remote-task-1"
    assert delegations_response.json()[0]["remote_agent_id"] == "agent-child-1"
    assert canceled.status_code == 200
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert remote_client.cancel_task_calls == [("agent-child-1", "remote-task-1", "test cleanup")]
    assert subscribed.status_code == 200
    assert "event: status-update" in subscribed.text
    assert main_store.list_task_events(task_id)[0].payload["remote_task_id"] == "remote-task-1"
    delegations = main_store.list_context_delegations(sent.json()["result"]["contextId"])
    assert delegations[0].remote_task_id == "remote-task-1"
    agent_store.close()


def test_a2a_route_jsonrpc_remote_proxy_task_get_syncs_remote_status(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="submitted",
            )
        ],
        task_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="completed",
                artifacts=[
                    {
                        "artifactId": "remote-final",
                        "parts": [{"text": "remote task answer", "mediaType": "text/plain"}],
                    }
                ],
                raw={"result": {"kind": "task", "id": "remote-task-1"}},
            )
        ],
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))
    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    fetched = client.get(f"/tasks/{task_id}")
    fetched_again = client.get(f"/tasks/{task_id}")

    assert fetched.status_code == 200
    assert fetched_again.status_code == 200
    assert fetched.json()["result"]["status"]["state"] == "completed"
    task = main_store.get_task(task_id)
    assert task.status == MainAgentTaskStatus.COMPLETED
    assert task.output_message_id is not None
    assert main_store.get_message(task.output_message_id).parts == [{"kind": "text", "text": "remote task answer"}]
    artifacts = main_store.list_task_artifacts(task_id)
    assert len(artifacts) == 1
    assert artifacts[0].metadata["kind"] == "final_answer"
    assert artifacts[0].metadata["source"] == "remote_agent"
    assert artifacts[0].metadata["remoteArtifactId"] == "remote-final"
    assert artifacts[0].parts == [{"kind": "text", "text": "remote task answer"}]
    events = main_store.list_task_events(task_id)
    assert [event.type for event in events] == [
        "task_delegated",
        "task_artifact_created",
        "remote_task_status_synced",
    ]
    assert events[1].status is None
    delegation = main_store.get_delegated_task_by_local_task_id(task_id)
    assert delegation.status == "completed"
    assert delegation.metadata["remoteStatus"] == "completed"
    assert remote_client.get_task_calls == [("agent-child-1", "remote-task-1"), ("agent-child-1", "remote-task-1")]
    agent_store.close()


def test_a2a_route_jsonrpc_remote_proxy_ignores_stale_status_regression(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="submitted",
            )
        ],
        task_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="working",
                raw={"revision": "current"},
            ),
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="submitted",
                raw={"revision": "stale"},
            ),
        ],
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    first = client.get(f"/tasks/{task_id}")
    second = client.get(f"/tasks/{task_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["status"]["state"] == "working"
    assert second.json()["result"]["status"]["state"] == "working"
    assert main_store.get_task(task_id).status == MainAgentTaskStatus.RUNNING
    assert [event.type for event in main_store.list_task_events(task_id)] == [
        "task_delegated",
        "remote_task_status_synced",
    ]
    delegation = main_store.get_delegated_task_by_local_task_id(task_id)
    assert delegation.status == "working"
    assert delegation.metadata["remoteStatus"] == "working"
    assert delegation.metadata["lastRemoteSnapshot"] == {"revision": "current"}
    agent_store.close()


def test_a2a_route_jsonrpc_remote_proxy_ignores_terminal_regression(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="submitted",
            )
        ],
        task_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="completed",
                artifacts=[
                    {
                        "artifactId": "remote-final",
                        "parts": [{"text": "remote task answer", "mediaType": "text/plain"}],
                    }
                ],
                raw={"revision": "completed"},
            ),
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="canceled",
                raw={"revision": "stale-terminal"},
            ),
        ],
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    first = client.get(f"/tasks/{task_id}")
    second = client.get(f"/tasks/{task_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"]["status"]["state"] == "completed"
    assert second.json()["result"]["status"]["state"] == "completed"
    assert main_store.get_task(task_id).status == MainAgentTaskStatus.COMPLETED
    assert len(main_store.list_task_artifacts(task_id)) == 1
    assert [event.type for event in main_store.list_task_events(task_id)] == [
        "task_delegated",
        "task_artifact_created",
        "remote_task_status_synced",
    ]
    delegation = main_store.get_delegated_task_by_local_task_id(task_id)
    assert delegation.status == "completed"
    assert delegation.metadata["lastRemoteSnapshot"] == {"revision": "completed"}
    agent_store.close()


def test_a2a_route_jsonrpc_remote_proxy_task_cancel_forwards_to_remote_agent(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    main_store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        [
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="working",
            )
        ],
        cancel_snapshots=[
            RemoteAgentTaskSnapshot(
                task_id="remote-task-1",
                context_id="remote-ctx-1",
                status="canceled",
            )
        ],
    )
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        remote_agent_client=remote_client,
    )
    client = TestClient(create_app(main_agent_core=core))
    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-remote",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delegate"}],
                },
                "metadata": {"route": "remote_agent", "targetAgentId": "agent-child-1"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    canceled = client.post(f"/tasks/{task_id}:cancel", json={"reason": "operator"})

    assert canceled.status_code == 200
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert remote_client.cancel_task_calls == [("agent-child-1", "remote-task-1", "operator")]
    assert main_store.get_task(task_id).status == MainAgentTaskStatus.CANCELED
    assert [event.type for event in main_store.list_task_events(task_id)] == [
        "task_delegated",
        "remote_task_status_synced",
    ]
    agent_store.close()


def test_a2a_route_jsonrpc_errors_use_jsonrpc_error_envelope(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-1",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "contextId": "ctx-missing",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "error": {
            "code": -32602,
            "message": "unknown context: ctx-missing",
            "data": jsonrpc_error_data("invalid_request"),
        },
    }
    agent_store.close()


@pytest.mark.parametrize(
    ("message_payload", "error_message"),
    [
        (
            {
                "kind": "message",
                "role": "agent",
                "messageId": "msg-agent-1",
                "parts": [{"kind": "text", "text": "hello"}],
            },
            "A2A message role must be 'user'.",
        ),
        (
            {
                "kind": "message",
                "role": "user",
                "messageId": "msg-user-1",
                "parts": [{"kind": "text", "text": "   "}],
            },
            "A2A message must include at least one text part.",
        ),
    ],
)
def test_a2a_route_jsonrpc_message_validation_errors_are_jsonrpc_errors(
    tmp_path,
    message_payload,
    error_message,
):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-invalid-message",
            "method": "message/send",
            "params": {
                "message": message_payload,
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "req-invalid-message",
        "error": {
            "code": -32602,
            "message": error_message,
            "data": jsonrpc_error_data("invalid_request"),
        },
    }
    agent_store.close()


@pytest.mark.parametrize(
    ("params", "error_message"),
    [
        (
            {"metadata": {"executionMode": "message"}},
            "JSON-RPC params.message must be an object.",
        ),
        (
            {"message": "hello", "metadata": {"executionMode": "message"}},
            "JSON-RPC params.message must be an object.",
        ),
        (
            {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": "hello",
                },
                "metadata": {"executionMode": "message"},
            },
            "JSON-RPC params.message.parts is invalid: list_type",
        ),
    ],
)
def test_a2a_route_jsonrpc_message_shape_errors_are_jsonrpc_errors(
    tmp_path,
    params,
    error_message,
):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-invalid-message-shape",
            "method": "message/send",
            "params": params,
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "req-invalid-message-shape",
        "error": {
            "code": -32602,
            "message": error_message,
            "data": jsonrpc_error_data("invalid_request"),
        },
    }
    agent_store.close()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "message/send",
                "params": [],
            },
            "JSON-RPC params must be an object.",
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": "req-1",
                "method": "tasks/get",
                "params": {},
            },
            "JSON-RPC method must be 'message/send'.",
        ),
        (
            {
                "jsonrpc": "1.0",
                "id": "req-1",
                "method": "message/send",
                "params": {},
            },
            "JSON-RPC request jsonrpc must be '2.0'.",
        ),
    ],
)
def test_a2a_route_jsonrpc_envelope_validation_errors_are_jsonrpc_errors(tmp_path, payload, message):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post("/message:send", json=payload)

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "error": {
            "code": -32602,
            "message": message,
            "data": jsonrpc_error_data("invalid_request"),
        },
    }
    agent_store.close()


def test_a2a_route_message_stream_emits_local_message_result(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:stream",
        json={
            "jsonrpc": "2.0",
            "id": "req-stream-message",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message" in response.text
    assert '"id": "req-stream-message"' in response.text
    assert '"kind": "message"' in response.text
    assert "direct answer" in response.text
    agent_store.close()


def test_a2a_route_message_stream_emits_local_task_events(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:stream",
        json={
            "jsonrpc": "2.0",
            "id": "req-stream-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )

    assert response.status_code == 200
    assert "event: task" in response.text
    assert "event: artifact-update" in response.text
    assert "event: status-update" in response.text
    assert '"artifactId": "final_answer"' in response.text
    assert '"state": "completed"' in response.text
    assert "task answer" in response.text
    assert "event: error" not in response.text
    agent_store.close()


def test_a2a_failed_task_projects_safe_failure_metadata(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FailingLocalTaskRunner(),
    )
    adapter = A2AAdapter(main_agent_core=core)

    payload = adapter.send_message_payload(
        {
            "jsonrpc": "2.0",
            "id": "req-failed-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-failed-task",
                    "parts": [{"kind": "text", "text": "run failing task"}],
                },
                "metadata": {"executionMode": "task"},
            },
        }
    )

    task = payload["result"]
    assert task["kind"] == "task"
    assert task["status"]["state"] == "failed"
    assert task["metadata"]["localErrorCode"] == "model_error"
    assert task["metadata"]["localErrorMessage"] == "Model request failed."
    assert "Ollama" not in str(task)
    assert "Connection refused" not in str(task)

    events = adapter.wait_for_task_events(
        task["id"], after_event_id=0, timeout_seconds=0.0
    ).events
    failed = next(
        event["result"]
        for event in events
        if event["result"]["kind"] == "status-update"
        and event["result"]["status"]["state"] == "failed"
    )
    assert failed["metadata"]["localErrorCode"] == "model_error"
    assert failed["metadata"]["localErrorMessage"] == "Model request failed."
    assert failed["metadata"]["localErrorRetryable"] is True
    assert "Ollama" not in str(failed)
    assert "Connection refused" not in str(failed)
    agent_store.close()


def test_a2a_rpc_stream_ignores_trailing_failure_after_terminal_task_event(monkeypatch):
    async def failing_stream(*args, **kwargs):
        yield {
            "jsonrpc": "2.0",
            "id": "rpc-stream-terminal",
            "result": {
                "kind": "status-update",
                "taskId": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "completed"},
            },
        }
        raise RuntimeError("trailing task replay failure")

    async def collect_events():
        return [
            event
            async for event in a2a_routes._rpc_stream_message_events(
                object(),
                {"id": "rpc-stream-terminal"},
            )
        ]

    monkeypatch.setattr(a2a_routes, "_stream_message_result_events", failing_stream)

    events = asyncio.run(collect_events())

    assert len(events) == 1
    assert "event: status-update" in events[0]
    assert "event: error" not in events[0]


def test_a2a_route_message_stream_emits_jsonrpc_error_event(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:stream",
        json={
            "jsonrpc": "2.0",
            "id": "req-stream-error",
            "method": "tasks/get",
            "params": {},
        },
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"id": "req-stream-error"' in response.text
    assert "JSON-RPC method must be" in response.text
    agent_store.close()


def test_a2a_rpc_masks_model_provider_details_and_preserves_retryability(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FailingLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "req-model-error",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-model-error",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": -32000,
        "message": "Model request failed.",
        "data": {
            "localCode": "model_error",
            "retryable": True,
            "errorInfo": {
                "reason": "model_error",
                "domain": "vermay",
                "metadata": {"localCode": "model_error"},
            },
        },
    }
    assert "Ollama" not in response.text
    assert "Connection refused" not in response.text
    agent_store.close()


def test_a2a_route_message_stream_emits_jsonrpc_error_event_for_invalid_message(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/message:stream",
        json={
            "jsonrpc": "2.0",
            "id": "req-stream-invalid-message",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "agent",
                    "messageId": "msg-agent-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"id": "req-stream-invalid-message"' in response.text
    assert '"code": -32602' in response.text
    assert "A2A message role must be" in response.text
    agent_store.close()


def test_a2a_route_jsonrpc_local_task_get_cancel_and_subscribe(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    fetched = client.get(f"/tasks/{task_id}")
    canceled = client.post(f"/tasks/{task_id}:cancel", json={"reason": "test cleanup"})
    subscribed = client.post(f"/tasks/{task_id}:subscribe")

    assert sent.status_code == 200
    assert sent.json()["result"]["kind"] == "task"
    assert fetched.status_code == 200
    assert fetched.json()["result"]["id"] == task_id
    assert fetched.json()["result"]["status"]["state"] == "submitted"
    assert canceled.status_code == 200
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert subscribed.status_code == 200
    assert "event: status-update" in subscribed.text
    assert '"state": "canceled"' in subscribed.text
    agent_store.close()


def test_a2a_rpc_send_message_supports_pascal_case_method(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-send-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "rpc-send-1"
    assert payload["result"]["kind"] == "message"
    assert payload["result"]["parts"] == [{"kind": "text", "text": "direct answer"}]
    agent_store.close()


def test_a2a_rpc_get_and_cancel_task_support_pascal_case_methods(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-task-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    fetched = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-get-1",
            "method": "GetTask",
            "params": {"id": task_id},
        },
    )
    canceled = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-cancel-1",
            "method": "CancelTask",
            "params": {"id": task_id, "reason": "operator"},
        },
    )

    assert sent.status_code == 200
    assert sent.json()["id"] == "rpc-task-1"
    assert sent.json()["result"]["kind"] == "task"
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "rpc-get-1"
    assert fetched.json()["result"]["id"] == task_id
    assert fetched.json()["result"]["status"]["state"] == "submitted"
    assert canceled.status_code == 200
    assert canceled.json()["id"] == "rpc-cancel-1"
    assert canceled.json()["result"]["id"] == task_id
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert main_store.list_task_events(task_id)[-1].payload == {"reason": "operator"}
    agent_store.close()


def test_a2a_rpc_resume_task_uses_canonical_method(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    runner = FakeApprovalTaskRunner()
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=runner,
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-task-1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "delete pod"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]
    task = main_store.get_task(task_id)
    assert task is not None
    assert task.status == MainAgentTaskStatus.AUTH_REQUIRED
    adapter = A2AAdapter(main_agent_core=core)
    interrupted_events = adapter.wait_for_task_events(task_id, after_event_id=0, timeout_seconds=0.0).events
    interrupted = [
        event
        for event in interrupted_events
        if event["result"]["kind"] == "status-update"
        and event["result"]["metadata"]["localEventType"] == "task_interrupted"
    ][0]
    interrupted_metadata = interrupted["result"]["metadata"]
    assert interrupted_metadata["localThreadId"] == task.runtime_thread_id
    assert interrupted_metadata["runtimeThreadId"] == task.runtime_thread_id

    legacy_method = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-resume-legacy",
            "method": "ResumeTask",
            "params": {"id": task_id, "approved": True},
        },
    )
    resumed = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-resume-1",
            "method": "tasks/resume",
            "params": {"id": task_id, "approved": True, "reason": "operator"},
        },
    )

    assert legacy_method.status_code == 400
    assert legacy_method.json()["error"]["code"] == -32601
    assert resumed.status_code == 200
    assert resumed.json()["id"] == "rpc-resume-1"
    assert resumed.json()["result"]["id"] == task_id
    assert resumed.json()["result"]["status"]["state"] == "completed"
    assert runner.resume_calls == [(task.runtime_thread_id, True, "operator")]
    assert [event.type for event in main_store.list_task_events(task_id)] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_queued",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    agent_store.close()


def test_a2a_send_message_continues_input_required_task_without_router(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    runner = FakeInputTaskRunner()
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=runner,
    )
    client = TestClient(create_app(main_agent_core=core))

    started = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-input-start",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-input-start",
                    "parts": [{"kind": "text", "text": "check deployment"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    started_result = started.json()["result"]
    task_id = started_result["id"]
    context_id = started_result["contextId"]

    assert started_result["status"]["state"] == "input-required"
    assert started_result["status"]["message"]["parts"] == [
        {"kind": "text", "text": "Which environment?"}
    ]
    assert started_result["metadata"]["inputRequest"]["choices"] == ["staging", "production"]

    mismatched = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-input-mismatch",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-input-mismatch",
                    "taskId": task_id,
                    "contextId": "ctx-wrong",
                    "parts": [{"kind": "text", "text": "production"}],
                }
            },
        },
    )
    assert mismatched.json()["error"]["code"] == -32602
    assert "context mismatch" in mismatched.json()["error"]["message"]

    continued = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-input-continue",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-input-answer",
                    "taskId": task_id,
                    "parts": [{"kind": "text", "text": "staging"}],
                    "metadata": {"source": "a2a-test"},
                }
            },
        },
    )

    continued_result = continued.json()["result"]
    task = main_store.get_task(task_id)
    assert task is not None
    assert continued_result["id"] == task_id
    assert continued_result["contextId"] == context_id
    assert continued_result["status"]["state"] == "completed"
    assert runner.resume_input_calls == [
        (task.runtime_thread_id, [{"kind": "text", "text": "staging"}], {"source": "a2a-test"})
    ]
    assert len(main_store.list_context_route_decisions(context_id)) == 1
    agent_store.close()


def test_a2a_rpc_accepts_current_slash_method_aliases(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-task-slash",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    fetched = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": "rpc-get-slash", "method": "tasks/get", "params": {"taskId": task_id}},
    )

    assert sent.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["id"] == "rpc-get-slash"
    assert fetched.json()["result"]["id"] == task_id
    agent_store.close()


def test_a2a_rpc_missing_task_preserves_request_id_in_jsonrpc_error(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": "rpc-missing", "method": "GetTask", "params": {"id": "missing-task"}},
    )

    assert response.status_code == 404
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "rpc-missing",
        "error": {
            "code": -32004,
            "message": "task not found",
            "data": jsonrpc_error_data("task_not_found"),
        },
    }
    agent_store.close()


def test_a2a_rpc_send_streaming_message_emits_local_message_result(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    responder = FakeLocalMessageResponder()
    core = MainAgentCore(store=main_store, local_message_responder=responder)
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-stream-message",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message" in response.text
    assert '"id": "rpc-stream-message"' in response.text
    assert '"kind": "message"' in response.text
    assert "direct answer" in response.text
    assert len(responder.calls) == 1
    agent_store.close()


def test_a2a_rpc_send_streaming_message_emits_partial_local_message_events(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    responder = FakeStreamingLocalMessageResponder()
    core = MainAgentCore(store=main_store, local_message_responder=responder)
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-stream-message",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "hello"}],
                },
                "metadata": {"executionMode": "message"},
            },
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    message_events = [event["result"] for event in events if event.get("result", {}).get("kind") == "message"]
    assert [event["parts"][0]["text"] for event in message_events] == [
        "streamed ",
        "answer",
        "streamed answer",
    ]
    assert message_events[0]["metadata"]["partial"] is True
    assert message_events[0]["metadata"]["append"] is True
    assert message_events[0]["metadata"]["sequence"] == 1
    assert message_events[-1]["metadata"]["partial"] is False
    assert message_events[-1]["metadata"]["append"] is False
    assert message_events[-1]["metadata"]["final"] is True

    context = main_store.list_contexts()[0]
    stored_messages = main_store.list_context_messages(context.context_id)
    assert [message.role.value for message in stored_messages] == ["user", "agent"]
    assert stored_messages[-1].parts == [{"kind": "text", "text": "streamed answer"}]
    assert len(responder.calls) == 1
    agent_store.close()


def test_a2a_rpc_send_streaming_auto_falls_back_to_local_message(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-stream-auto-message",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "tell me a joke"}],
                },
                "metadata": {"executionMode": "auto"},
            },
        },
    )

    assert response.status_code == 200
    assert "event: message" in response.text
    assert '"kind": "message"' in response.text
    assert "event: task" not in response.text
    decisions = main_store.list_context_route_decisions(
        main_store.list_contexts()[0].context_id
    )
    assert decisions[0].kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decisions[0].metadata == {"source": "fallback", "executionMode": "auto"}
    agent_store.close()


def test_a2a_rpc_send_streaming_message_emits_local_task_events(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-stream-task",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: task" in response.text
    assert "event: artifact-update" in response.text
    assert "event: status-update" in response.text
    assert '"id": "rpc-stream-task"' in response.text
    assert '"artifactId": "final_answer"' in response.text
    assert '"state": "completed"' in response.text
    assert "task answer" in response.text
    agent_store.close()


def test_a2a_rpc_send_streaming_message_follows_background_task_to_terminal_state(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    executor = InProcessTaskExecutor(max_workers=1)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=SlowFakeLocalTaskRunner(),
        task_submitter=executor,
    )
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-stream-background-task",
            "method": "SendStreamingMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-background-task",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )

    assert response.status_code == 200
    assert '"state": "completed"' in response.text
    assert '"artifactId": "final_answer"' in response.text
    assert "task answer" in response.text

    executor.shutdown()
    agent_store.close()


def test_a2a_rpc_subscribe_to_task_replays_artifact_update(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-task-for-subscribe",
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    subscribed = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-subscribe-1",
            "method": "SubscribeToTask",
            "params": {"id": task_id, "afterEventId": 2},
        },
    )

    assert subscribed.status_code == 200
    assert subscribed.headers["content-type"].startswith("text/event-stream")
    assert "event: artifact-update" in subscribed.text
    assert "event: status-update" in subscribed.text
    assert '"id": "rpc-subscribe-1"' in subscribed.text
    assert '"localEventId": 1' not in subscribed.text
    assert '"localEventId": 2' not in subscribed.text
    assert '"localEventId": 3' in subscribed.text
    assert '"artifactId": "final_answer"' in subscribed.text
    agent_store.close()


@pytest.mark.parametrize(
    ("params", "error_message"),
    [
        ({}, "JSON-RPC params.id must be a non-empty string."),
        ({"id": "task-1", "afterEventId": -1}, "JSON-RPC params.afterEventId must be a non-negative integer."),
    ],
)
def test_a2a_rpc_subscribe_to_task_validation_errors_stream_jsonrpc_error(tmp_path, params, error_message):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": "rpc-subscribe-invalid",
            "method": "SubscribeToTask",
            "params": params,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: error" in response.text
    assert '"id": "rpc-subscribe-invalid"' in response.text
    assert '"code": -32602' in response.text
    assert error_message in response.text
    agent_store.close()


@pytest.mark.parametrize(
    ("request_json", "code", "message", "local_code"),
    [
        (
            [],
            -32600,
            "JSON-RPC batch requests are not supported yet.",
            "batch_not_supported",
        ),
        (
            {"jsonrpc": "1.0", "id": "bad-version", "method": "GetTask", "params": {"id": "task-1"}},
            -32600,
            "JSON-RPC request jsonrpc must be '2.0'.",
            "invalid_request",
        ),
        (
            {"jsonrpc": "2.0", "id": "bad-method", "method": "UnknownMethod", "params": {}},
            -32601,
            "JSON-RPC method not found.",
            "method_not_found",
        ),
        (
            {"jsonrpc": "2.0", "id": "bad-params", "method": "GetTask", "params": []},
            -32602,
            "JSON-RPC params must be an object.",
            "invalid_request",
        ),
        (
            {"jsonrpc": "2.0", "id": "missing-id", "method": "GetTask", "params": {}},
            -32602,
            "JSON-RPC params.id must be a non-empty string.",
            "invalid_request",
        ),
    ],
)
def test_a2a_rpc_validation_errors(request_json, code, message, local_code, tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post("/rpc", json=request_json)

    assert response.status_code == 400
    assert response.json()["jsonrpc"] == "2.0"
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] == message
    assert response.json()["error"]["data"]["localCode"] == local_code
    assert response.json()["error"]["data"]["errorInfo"] == {
        "reason": local_code,
        "domain": "vermay",
        "metadata": {"localCode": local_code},
    }
    agent_store.close()


def test_a2a_rpc_invalid_json_returns_parse_error(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.post("/rpc", content="{", headers={"content-type": "application/json"})

    assert response.status_code == 400
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32700,
            "message": "JSON parse error.",
            "data": jsonrpc_error_data("parse_error"),
        },
    }
    agent_store.close()


def test_a2a_route_jsonrpc_task_cancel_accepts_request_body_and_preserves_id(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    canceled = client.post(
        f"/tasks/{task_id}:cancel",
        json={
            "jsonrpc": "2.0",
            "id": "cancel-req-1",
            "method": "tasks/cancel",
            "params": {"id": task_id, "reason": "operator"},
        },
    )

    assert canceled.status_code == 200
    assert canceled.json()["jsonrpc"] == "2.0"
    assert canceled.json()["id"] == "cancel-req-1"
    assert canceled.json()["result"]["id"] == task_id
    assert canceled.json()["result"]["status"]["state"] == "canceled"
    assert main_store.list_task_events(task_id)[-1].payload == {"reason": "operator"}
    agent_store.close()


def test_a2a_route_jsonrpc_local_task_subscribe_replays_artifact_update(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    subscribed = client.post(f"/tasks/{task_id}:subscribe")

    assert sent.status_code == 200
    assert sent.json()["result"]["status"]["state"] == "completed"
    assert subscribed.status_code == 200
    assert "event: artifact-update" in subscribed.text
    assert '"kind": "artifact-update"' in subscribed.text
    assert '"artifactId": "final_answer"' in subscribed.text
    assert '"text": "task answer"' in subscribed.text
    assert "event: status-update" in subscribed.text
    assert '"state": "completed"' in subscribed.text
    agent_store.close()


def test_a2a_route_jsonrpc_task_subscribe_accepts_request_body_after_event_id(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    subscribed = client.post(
        f"/tasks/{task_id}:subscribe",
        json={
            "jsonrpc": "2.0",
            "id": "subscribe-1",
            "method": "tasks/subscribe",
            "params": {"id": task_id, "afterEventId": 2},
        },
    )

    assert subscribed.status_code == 200
    assert "event: artifact-update" in subscribed.text
    assert "event: status-update" in subscribed.text
    assert '"localEventId": 1' not in subscribed.text
    assert '"localEventId": 2' not in subscribed.text
    assert '"localEventId": 3' in subscribed.text
    agent_store.close()


@pytest.mark.parametrize(
    ("params", "error_message"),
    [
        (
            {"id": "different-task", "afterEventId": 0},
            "JSON-RPC params.id must match the route task id.",
        ),
        (
            {"afterEventId": -1},
            "JSON-RPC params.afterEventId must be a non-negative integer.",
        ),
    ],
)
def test_a2a_route_jsonrpc_task_subscribe_request_validation_errors(tmp_path, params, error_message):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    subscribed = client.post(
        "/tasks/task-1:subscribe",
        json={
            "jsonrpc": "2.0",
            "id": "subscribe-invalid",
            "method": "tasks/subscribe",
            "params": params,
        },
    )

    assert subscribed.status_code == 200
    assert "event: error" in subscribed.text
    assert '"id": "subscribe-invalid"' in subscribed.text
    assert error_message in subscribed.text
    agent_store.close()


def test_a2a_route_jsonrpc_task_subscribe_unknown_task_streams_jsonrpc_error(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    subscribed = client.post(
        "/tasks/missing-task:subscribe",
        json={
            "jsonrpc": "2.0",
            "id": "subscribe-missing",
            "method": "tasks/subscribe",
            "params": {"id": "missing-task", "afterEventId": 0},
        },
    )

    assert subscribed.status_code == 200
    assert "event: error" in subscribed.text
    assert '"id": "subscribe-missing"' in subscribed.text
    assert '"code": -32004' in subscribed.text
    assert '"localCode": "task_not_found"' in subscribed.text
    agent_store.close()


@pytest.mark.parametrize(
    ("params", "error_message"),
    [
        (
            {"id": "different-task", "reason": "operator"},
            "JSON-RPC params.id must match the route task id.",
        ),
        (
            {"id": "task-1", "reason": 123},
            "JSON-RPC params.reason must be a string.",
        ),
    ],
)
def test_a2a_route_jsonrpc_task_cancel_request_validation_errors(tmp_path, params, error_message):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))

    canceled = client.post(
        "/tasks/task-1:cancel",
        json={
            "jsonrpc": "2.0",
            "id": "cancel-invalid",
            "method": "tasks/cancel",
            "params": params,
        },
    )

    assert canceled.status_code == 400
    assert canceled.json()["jsonrpc"] == "2.0"
    assert canceled.json()["id"] == "cancel-invalid"
    assert canceled.json()["error"]["code"] == -32602
    assert canceled.json()["error"]["data"]["localCode"] == "invalid_request"
    assert canceled.json()["error"]["message"] == error_message
    agent_store.close()


def test_a2a_route_jsonrpc_completed_local_task_cancel_is_rejected(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    canceled = client.post(f"/tasks/{task_id}:cancel", json={"reason": "too late"})

    assert sent.status_code == 200
    assert sent.json()["result"]["status"]["state"] == "completed"
    assert canceled.status_code == 409
    assert canceled.json()["detail"] == {
        "code": "invalid_session_state",
        "message": f"task is terminal and cannot be canceled: {task_id}",
        "retryable": False,
    }
    assert main_store.get_task(task_id).status == MainAgentTaskStatus.COMPLETED
    agent_store.close()


def test_a2a_route_jsonrpc_completed_local_task_cancel_returns_jsonrpc_error(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(
        store=main_store,
        local_message_responder=FakeLocalMessageResponder(),
        local_task_runner=FakeLocalTaskRunner(),
    )
    client = TestClient(create_app(main_agent_core=core))

    sent = client.post(
        "/message:send",
        json={
            "jsonrpc": "2.0",
            "id": "req-task",
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "msg-user-1",
                    "parts": [{"kind": "text", "text": "run"}],
                },
                "metadata": {"executionMode": "task"},
            },
        },
    )
    task_id = sent.json()["result"]["id"]

    canceled = client.post(
        f"/tasks/{task_id}:cancel",
        json={
            "jsonrpc": "2.0",
            "id": "cancel-terminal",
            "method": "tasks/cancel",
            "params": {"id": task_id, "reason": "too late"},
        },
    )

    assert sent.status_code == 200
    assert sent.json()["result"]["status"]["state"] == "completed"
    assert canceled.status_code == 409
    assert canceled.json()["jsonrpc"] == "2.0"
    assert canceled.json()["id"] == "cancel-terminal"
    assert canceled.json()["error"] == {
        "code": -32009,
        "message": f"task is terminal and cannot be canceled: {task_id}",
        "data": jsonrpc_error_data("invalid_session_state"),
    }
    assert main_store.get_task(task_id).status == MainAgentTaskStatus.COMPLETED
    agent_store.close()

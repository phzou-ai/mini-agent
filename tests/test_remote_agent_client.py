from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from vermay.main_agent.models import MainAgentRequest, MessageRole, RegisteredAgentRecord
from vermay.main_agent.remote_agent import DirectA2ARemoteAgentClient, RemoteAgentProtocolError


@dataclass
class FakeResponse:
    payload: dict

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_direct_a2a_remote_agent_send_message_uses_rpc(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "delegate-msg-1",
                "result": {
                    "kind": "message",
                    "contextId": "remote-ctx-1",
                    "messageId": "remote-msg-1",
                    "parts": [{"kind": "text", "text": "remote answer"}],
                },
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient(timeout_seconds=3.0)

    result = client.send_message(
        agent=_registered_agent(),
        request=MainAgentRequest(
            context_id="ctx-1",
            message_id="msg-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "delegate"}],
            metadata={"executionMode": "task"},
        ),
        context_id="ctx-1",
        message_id="msg-1",
    )

    request, timeout = captured[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://child-agent.local/rpc"
    assert request.get_method() == "POST"
    assert timeout == 3.0
    assert payload["method"] == "SendMessage"
    assert payload["params"]["message"]["contextId"] == "ctx-1"
    assert payload["params"]["metadata"] == {
        "delegatedBy": "vermay-main-agent",
        "sourceContextId": "ctx-1",
        "executionMode": "task",
    }
    assert result.kind == "message"
    assert result.message_id == "remote-msg-1"


def test_direct_a2a_remote_agent_get_task_uses_rpc(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "get-remote-task-remote-task-1",
                "result": {
                    "kind": "task",
                    "id": "remote-task-1",
                    "contextId": "remote-ctx-1",
                    "status": {"state": "completed"},
                    "artifacts": [{"artifactId": "final"}],
                },
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient(timeout_seconds=3.0)

    snapshot = client.get_task(agent=_registered_agent(), task_id="remote-task-1")

    request, timeout = captured[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://child-agent.local/rpc"
    assert request.get_method() == "POST"
    assert timeout == 3.0
    assert payload == {
        "jsonrpc": "2.0",
        "id": "get-remote-task-remote-task-1",
        "method": "GetTask",
        "params": {"id": "remote-task-1"},
    }
    assert snapshot.task_id == "remote-task-1"
    assert snapshot.status == "completed"


def test_direct_a2a_remote_agent_cancel_task_uses_rpc(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "cancel-remote-task-remote-task-1",
                "result": {
                    "kind": "task",
                    "id": "remote-task-1",
                    "contextId": "remote-ctx-1",
                    "status": {"state": "canceled"},
                },
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient(timeout_seconds=3.0)

    snapshot = client.cancel_task(
        agent=_registered_agent(),
        task_id="remote-task-1",
        reason="operator",
    )

    request, timeout = captured[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert request.full_url == "http://child-agent.local/rpc"
    assert request.get_method() == "POST"
    assert timeout == 3.0
    assert payload == {
        "jsonrpc": "2.0",
        "id": "cancel-remote-task-remote-task-1",
        "method": "CancelTask",
        "params": {"id": "remote-task-1", "reason": "operator"},
    }
    assert snapshot.task_id == "remote-task-1"
    assert snapshot.status == "canceled"


def test_direct_a2a_remote_agent_uses_jsonrpc_endpoint_declared_by_card(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(request.full_url)
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "get-remote-task-task-1",
                "result": {"kind": "task", "id": "task-1", "status": {"state": "working"}},
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient()
    agent = _registered_agent(
        card_json={
            "supportedInterfaces": [
                {
                    "url": "https://runtime.example/a2a/jsonrpc",
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0",
                }
            ]
        }
    )

    client.get_task(agent=agent, task_id="task-1")

    assert captured == ["https://runtime.example/a2a/jsonrpc"]


def test_direct_a2a_remote_agent_uses_legacy_card_url_as_complete_endpoint(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(request.full_url)
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "get-remote-task-task-1",
                "result": {"kind": "task", "id": "task-1", "status": {"state": "working"}},
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient()
    agent = _registered_agent(
        card_json={
            "url": "https://runtime.example/custom/jsonrpc",
            "preferredTransport": "JSONRPC",
        }
    )

    client.get_task(agent=agent, task_id="task-1")

    assert captured == ["https://runtime.example/custom/jsonrpc"]


def test_direct_a2a_remote_agent_raises_jsonrpc_error(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "get-remote-task-missing-task",
                "error": {
                    "code": -32001,
                    "message": "Task not found",
                    "data": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo"}],
                },
            }
        )

    monkeypatch.setattr("vermay.main_agent.remote_agent.urlopen", fake_urlopen)
    client = DirectA2ARemoteAgentClient()

    with pytest.raises(RemoteAgentProtocolError, match="Task not found") as raised:
        client.get_task(agent=_registered_agent(), task_id="missing-task")

    assert raised.value.code == -32001
    assert raised.value.data == [{"@type": "type.googleapis.com/google.rpc.ErrorInfo"}]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"id": "get-remote-task-task-1", "result": {}}, "must use JSON-RPC 2.0"),
        ({"jsonrpc": "2.0", "id": "wrong", "result": {}}, "id does not match"),
        ({"jsonrpc": "2.0", "id": "get-remote-task-task-1"}, "missing JSON-RPC result"),
    ],
)
def test_direct_a2a_remote_agent_rejects_invalid_jsonrpc_response(monkeypatch, payload, message):
    monkeypatch.setattr(
        "vermay.main_agent.remote_agent.urlopen",
        lambda request, timeout: FakeResponse(payload),
    )
    client = DirectA2ARemoteAgentClient()

    with pytest.raises(RemoteAgentProtocolError, match=message):
        client.get_task(agent=_registered_agent(), task_id="task-1")


def test_direct_a2a_remote_agent_rejects_task_snapshot_without_an_id(monkeypatch):
    monkeypatch.setattr(
        "vermay.main_agent.remote_agent.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "get-remote-task-task-1",
                "result": {"kind": "task", "status": {"state": "working"}},
            }
        ),
    )
    client = DirectA2ARemoteAgentClient()

    with pytest.raises(RemoteAgentProtocolError, match="snapshot id must be a non-empty string"):
        client.get_task(agent=_registered_agent(), task_id="task-1")


def test_direct_a2a_remote_agent_rejects_send_task_without_an_id(monkeypatch):
    monkeypatch.setattr(
        "vermay.main_agent.remote_agent.urlopen",
        lambda request, timeout: FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": "delegate-msg-1",
                "result": {"kind": "task", "status": {"state": "submitted"}},
            }
        ),
    )
    client = DirectA2ARemoteAgentClient()

    with pytest.raises(RemoteAgentProtocolError, match="task result id must be a non-empty string"):
        client.send_message(
            agent=_registered_agent(),
            request=MainAgentRequest(
                context_id="ctx-1",
                message_id="msg-1",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "delegate"}],
            ),
            context_id="ctx-1",
            message_id="msg-1",
        )


def test_direct_a2a_remote_agent_rejects_card_without_jsonrpc_interface():
    client = DirectA2ARemoteAgentClient()
    agent = _registered_agent(
        card_json={
            "supportedInterfaces": [
                {
                    "url": "https://runtime.example/a2a/grpc",
                    "protocolBinding": "GRPC",
                    "protocolVersion": "1.0",
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="does not declare a JSON-RPC interface"):
        client.get_task(agent=agent, task_id="task-1")


def test_direct_a2a_remote_agent_rejects_invalid_legacy_card_url():
    client = DirectA2ARemoteAgentClient()
    agent = _registered_agent(card_url="file:///tmp/agent-card.json")

    with pytest.raises(ValueError, match=r"must be an absolute HTTP\(S\) URL"):
        client.get_task(agent=agent, task_id="task-1")


def _registered_agent(
    *,
    card_json=None,
    card_url="http://child-agent.local/.well-known/agent-card.json",
) -> RegisteredAgentRecord:
    return RegisteredAgentRecord(
        agent_id="child-agent",
        name="Child Agent",
        card_url=card_url,
        card_json=card_json or {},
        enabled=True,
        metadata={},
        created_at="2026-06-08T00:00:00Z",
        updated_at="2026-06-08T00:00:00Z",
    )

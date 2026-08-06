from __future__ import annotations

from fastapi.testclient import TestClient

from vermay.api.app import create_app
from vermay.main_agent import MainAgentCore, MainAgentStore, MessageRecord
from vermay.storage import AgentStore


class FakeLocalMessageResponder:
    def __init__(self) -> None:
        self.calls = []

    def respond(self, messages: list[MessageRecord]) -> list[dict]:
        self.calls.append(messages)
        return [{"kind": "text", "text": "direct answer"}]


def make_client(tmp_path):
    agent_store = AgentStore(tmp_path / "agent.sqlite")
    main_store = MainAgentStore(agent_store)
    core = MainAgentCore(store=main_store, local_message_responder=FakeLocalMessageResponder())
    client = TestClient(create_app(main_agent_core=core))
    return client, agent_store, core


def send_rpc_message(client: TestClient, *, request_id: str, message_id: str, text: str, execution_mode: str):
    return client.post(
        "/rpc",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "SendMessage",
            "params": {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": message_id,
                    "parts": [{"kind": "text", "text": text}],
                },
                "metadata": {"executionMode": execution_mode},
            },
        },
    )


def test_main_agent_context_api_lists_messages_and_deletes_context(tmp_path):
    client, agent_store, _core = make_client(tmp_path)
    sent = send_rpc_message(
        client,
        request_id="req-1",
        message_id="msg-user-1",
        text="hello",
        execution_mode="message",
    )
    context_id = sent.json()["result"]["contextId"]

    contexts = client.get("/api/contexts")
    messages = client.get(f"/api/contexts/{context_id}/messages")
    route_decisions = client.get(f"/api/contexts/{context_id}/route-decisions")
    deleted = client.delete(f"/api/contexts/{context_id}", params={"force": "true"})

    assert contexts.status_code == 200
    assert contexts.json()[0]["context_id"] == context_id
    assert messages.status_code == 200
    assert [message["message_id"] for message in messages.json()] == [
        "msg-user-1",
        sent.json()["result"]["messageId"],
    ]
    assert route_decisions.status_code == 200
    assert route_decisions.json()[0]["kind"] == "local_message"
    assert route_decisions.json()[0]["message_id"] == "msg-user-1"
    assert deleted.status_code == 204
    assert client.get(f"/api/contexts/{context_id}").status_code == 404
    agent_store.close()


def test_main_agent_context_api_lists_tasks(tmp_path):
    client, agent_store, _core = make_client(tmp_path)
    sent = send_rpc_message(
        client,
        request_id="req-task-1",
        message_id="msg-task-user-1",
        text="run task",
        execution_mode="task",
    )
    context_id = sent.json()["result"]["contextId"]
    task_id = sent.json()["result"]["id"]

    tasks = client.get(f"/api/contexts/{context_id}/tasks")

    assert tasks.status_code == 200
    assert tasks.json()[0]["task_id"] == task_id
    assert tasks.json()[0]["context_id"] == context_id
    assert tasks.json()[0]["status"] == "created"
    assert tasks.json()[0]["input_message_id"] == "msg-task-user-1"
    agent_store.close()


def test_main_agent_context_api_rejects_force_delete_for_live_task(tmp_path):
    client, agent_store, _core = make_client(tmp_path)
    sent = send_rpc_message(
        client,
        request_id="req-task-delete-1",
        message_id="msg-task-delete-1",
        text="run task",
        execution_mode="task",
    )
    context_id = sent.json()["result"]["contextId"]

    deleted = client.delete(f"/api/contexts/{context_id}", params={"force": "true"})

    assert deleted.status_code == 409
    assert deleted.json() == {
        "code": "resource_conflict",
        "message": "Context has work that must finish or be canceled before deletion.",
        "retryable": False,
    }
    assert client.get(f"/api/contexts/{context_id}").status_code == 200
    agent_store.close()


def test_main_agent_registered_agent_api_crud(tmp_path):
    client, agent_store, _core = make_client(tmp_path)

    created = client.post(
        "/api/registered-agents",
        json={
            "agent_id": "agent-child-1",
            "name": "Child agent",
            "card_url": "http://127.0.0.1:9001/.well-known/agent-card.json",
            "card_json": {"name": "Child agent"},
            "enabled": True,
            "metadata": {"team": "local", "keywords": ["sqlite", "kubernetes"]},
        },
    )
    disabled = client.post(
        "/api/registered-agents",
        json={
            "agent_id": "agent-disabled",
            "name": "Disabled agent",
            "card_url": "http://127.0.0.1:9002/.well-known/agent-card.json",
            "enabled": False,
        },
    )
    listed = client.get("/api/registered-agents")
    enabled_only = client.get("/api/registered-agents", params={"enabled_only": "true"})
    fetched = client.get("/api/registered-agents/agent-child-1")
    deleted = client.delete("/api/registered-agents/agent-child-1")
    missing = client.get("/api/registered-agents/agent-child-1")

    assert created.status_code == 200
    assert created.json()["agent_id"] == "agent-child-1"
    assert created.json()["card_json"] == {"name": "Child agent"}
    assert created.json()["metadata"] == {"team": "local", "keywords": ["sqlite", "kubernetes"]}
    assert disabled.status_code == 200
    assert listed.status_code == 200
    assert {agent["agent_id"] for agent in listed.json()} == {"agent-child-1", "agent-disabled"}
    assert enabled_only.status_code == 200
    assert [agent["agent_id"] for agent in enabled_only.json()] == ["agent-child-1"]
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Child agent"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    agent_store.close()


def test_main_agent_registered_agent_refresh_card(tmp_path, monkeypatch):
    client, agent_store, core = make_client(tmp_path)
    core.store.upsert_registered_agent(
        agent_id="agent-sql",
        name="SQL Agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        card_json={"name": "stale"},
        metadata={"keywords": ["manual"]},
    )

    monkeypatch.setattr(
        "vermay.api.app.fetch_agent_card",
        lambda card_url: {
            "name": "SQL Agent",
            "skills": [{"id": "sqlite-debug", "tags": ["sqlite", "database"]}],
            "url": card_url,
        },
    )

    refreshed = client.post("/api/registered-agents/agent-sql/refresh-card")

    assert refreshed.status_code == 200
    assert refreshed.json()["card_json"] == {
        "name": "SQL Agent",
        "skills": [{"id": "sqlite-debug", "tags": ["sqlite", "database"]}],
        "url": "http://127.0.0.1:9001/.well-known/agent-card.json",
    }
    assert refreshed.json()["metadata"] == {"keywords": ["manual"]}
    agent_store.close()


def test_main_agent_registered_agent_refresh_card_missing_agent(tmp_path):
    client, agent_store, _core = make_client(tmp_path)

    missing = client.post("/api/registered-agents/missing-agent/refresh-card")

    assert missing.status_code == 404
    assert missing.json()["code"] == "registered_agent_not_found"
    agent_store.close()

from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from vermay_agent.api.app import _router_model_name, create_app
from vermay_agent.main_agent import MainAgentCore, MainAgentStore, MessageRole
from vermay_agent.storage import AgentStore


class FakeResponder:
    def respond(self, messages):
        return [{"kind": "text", "text": "not used"}]


@dataclass
class ReconciliationSpy:
    calls: int = 0

    def reconcile_startup(self) -> None:
        self.calls += 1


@dataclass
class CloseSpy:
    close_calls: int = 0

    def close(self) -> None:
        self.close_calls += 1


@dataclass
class ShutdownSpy:
    shutdown_calls: int = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def make_client(tmp_path):
    store = AgentStore(tmp_path / "agent.sqlite")
    core = MainAgentCore(store=MainAgentStore(store), local_message_responder=FakeResponder())
    return TestClient(create_app(main_agent_core=core)), store


def test_api_health(tmp_path):
    client, store = make_client(tmp_path)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    store.close()


def test_api_model_config_returns_primary_and_router_models(tmp_path, monkeypatch):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "main-model",
  "router_model": "router-model",
  "models": {
    "main-model": {
      "provider": "ollama",
      "options": {
        "model": "main:latest",
        "base_url": "http://127.0.0.1:11434",
        "timeout_seconds": 120
      }
    },
    "router-model": {
      "provider": "openai_compatible",
      "options": {
        "model": "router",
        "base_url": "http://localhost:8000/v1"
      }
    }
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("vermay_agent.api.app.DEFAULT_MODEL_CONFIG_PATH", config_path)
    client, store = make_client(tmp_path)

    response = client.get("/api/model-config")

    assert response.status_code == 200
    assert response.json() == {
        "primary_model": {
            "name": "main-model",
            "provider": "ollama",
            "model": "main:latest",
            "base_url": "http://127.0.0.1:11434",
            "timeout_seconds": 120,
        },
        "router_model": {
            "name": "router-model",
            "provider": "openai_compatible",
            "model": "router",
            "base_url": "http://localhost:8000/v1",
            "timeout_seconds": None,
        },
        "router_model_overridden": False,
        "config_path": str(config_path),
    }
    store.close()


def test_api_contexts_fall_back_to_first_user_message_as_title(tmp_path):
    main_store_backend = AgentStore(tmp_path / "main.sqlite")
    main_store = MainAgentStore(main_store_backend)
    main_store.create_context(context_id="ctx-1")
    main_store.append_message(
        message_id="msg-user-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "  First   question\nwith spaces "}],
    )
    core = MainAgentCore(store=main_store, local_message_responder=FakeResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.get("/api/contexts")

    assert response.status_code == 200
    assert response.json()[0]["title"] == "First question with spaces"
    main_store_backend.close()


def test_api_lists_task_tool_invocation_facts(tmp_path):
    main_store_backend = AgentStore(tmp_path / "main.sqlite")
    main_store = MainAgentStore(main_store_backend)
    context = main_store.create_context(context_id="ctx-1")
    message = main_store.append_message(
        message_id="msg-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "delete pod-a"}],
    )
    task = main_store.create_local_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
    )
    invocation = main_store.create_or_get_tool_invocation(
        invocation_id="inv-1",
        task_id=task.task_id,
        context_id=context.context_id,
        runtime_thread_id=task.runtime_thread_id,
        loop_index=1,
        tool_call_id="call-1",
        tool_name="delete_resource",
        normalized_arguments={"resource": "pod-a"},
        arguments_digest="digest-1",
        capability={"readOnly": False, "sideEffectLevel": "destructive"},
        side_effect_level="destructive",
        idempotency_key=None,
        approval_required=True,
    )
    core = MainAgentCore(store=main_store, local_message_responder=FakeResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.get(f"/api/tasks/{task.task_id}/tool-invocations")

    assert response.status_code == 200
    assert response.json() == [
        {
            "invocation_id": invocation.invocation_id,
            "task_id": task.task_id,
            "context_id": context.context_id,
            "runtime_thread_id": task.runtime_thread_id,
            "loop_index": 1,
            "tool_call_id": "call-1",
            "tool_name": "delete_resource",
            "normalized_arguments": {"resource": "pod-a"},
            "arguments_digest": "digest-1",
            "capability": {"readOnly": False, "sideEffectLevel": "destructive"},
            "side_effect_level": "destructive",
            "idempotency_key": None,
            "approval_required": True,
            "approval_status": "pending",
            "approval_reason": None,
            "status": "prepared",
            "result_artifact_id": None,
            "error": None,
            "created_at": invocation.created_at,
            "started_at": None,
            "completed_at": None,
            "updated_at": invocation.updated_at,
        }
    ]
    assert client.get("/api/tasks/missing/tool-invocations").status_code == 404
    main_store_backend.close()


def test_api_reads_normalized_task_observations(tmp_path):
    main_store_backend = AgentStore(tmp_path / "main.sqlite")
    main_store = MainAgentStore(main_store_backend)
    context = main_store.create_context(context_id="ctx-1")
    message = main_store.append_message(
        message_id="msg-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "inspect"}],
    )
    task = main_store.create_local_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
    )
    main_store.upsert_artifact(
        artifact_id=f"{task.task_id}:tool_observations",
        task_id=task.task_id,
        context_id=context.context_id,
        parts=[
            {
                "kind": "data",
                "data": {"observations": [{"tool_call_id": "call-1", "tool_name": "inspect", "ok": True}]},
            }
        ],
        metadata={"kind": "tool_observations", "execution": {"stop_reason": "completed"}},
    )
    core = MainAgentCore(store=main_store, local_message_responder=FakeResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.get(f"/api/tasks/{task.task_id}/observations")

    assert response.status_code == 200
    assert response.json() == {
        "task_id": task.task_id,
        "observations": [{"tool_call_id": "call-1", "tool_name": "inspect", "ok": True}],
        "artifact_id": f"{task.task_id}:tool_observations",
        "execution": {"stop_reason": "completed"},
        "updated_at": main_store.get_artifact(f"{task.task_id}:tool_observations").updated_at,
    }
    assert client.get("/api/tasks/missing/observations").status_code == 404
    main_store_backend.close()


def test_api_updates_context_title(tmp_path):
    main_store_backend = AgentStore(tmp_path / "main.sqlite")
    main_store = MainAgentStore(main_store_backend)
    context = main_store.create_context(context_id="ctx-1", title="Original")
    core = MainAgentCore(store=main_store, local_message_responder=FakeResponder())
    client = TestClient(create_app(main_agent_core=core))

    response = client.patch("/api/contexts/ctx-1", json={"title": "  Renamed   session  "})

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed session"
    assert response.json()["updated_at"] == context.updated_at
    main_store_backend.close()


def test_api_context_messages_project_failed_direct_message_ingress(tmp_path):
    main_store_backend = AgentStore(tmp_path / "main.sqlite")
    main_store = MainAgentStore(main_store_backend)
    main_store.create_context(context_id="ctx-1")
    main_store.append_message(
        message_id="msg-user-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )
    main_store.reserve_message_ingress(
        message_id="msg-user-1",
        context_id="ctx-1",
        request_fingerprint="fingerprint",
    )
    main_store.fail_message_ingress(
        "msg-user-1",
        error_code="model_error",
        error_message="Model request failed.",
        error_http_status=503,
        retryable=True,
    )
    core = MainAgentCore(store=main_store, local_message_responder=FakeResponder())
    client = TestClient(create_app(main_agent_core=core))

    messages_response = client.get("/api/contexts/ctx-1/messages")
    assert messages_response.status_code == 200
    assert messages_response.json() == [
        {
            "message_id": "msg-user-1",
            "context_id": "ctx-1",
            "role": "user",
            "parts": [{"kind": "text", "text": "hello"}],
            "task_id": None,
            "metadata": {},
            "created_at": main_store.get_message("msg-user-1").created_at,
            "failure": {
                "code": "model_error",
                "message": "Model request failed.",
                "retryable": True,
            },
        }
    ]

    ingress_response = client.get("/api/message-ingress/msg-user-1")
    assert ingress_response.status_code == 200
    assert ingress_response.json()["message_id"] == "msg-user-1"
    assert ingress_response.json()["context_id"] == "ctx-1"
    assert ingress_response.json()["state"] == "failed"
    assert ingress_response.json()["failure"] == {
        "code": "model_error",
        "message": "Model request failed.",
        "retryable": True,
    }

    main_store_backend.close()


def test_router_model_name_loads_env_local(tmp_path, monkeypatch):
    monkeypatch.setattr("vermay_agent.env_config.ROOT", tmp_path)
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "router-config",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    },
    "router-config": {
      "provider": "ollama",
      "options": {}
    },
    "router-small": {
      "provider": "ollama",
      "options": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        "VERMAY_AGENT_ROUTER_MODEL=router-small\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VERMAY_AGENT_ROUTER_MODEL", raising=False)

    assert _router_model_name(config_path=config_path) == "router-small"


def test_router_model_name_loads_config_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("vermay_agent.env_config.ROOT", tmp_path)
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "primary_model": "local_ollama",
  "router_model": "router-config",
  "models": {
    "local_ollama": {
      "provider": "ollama",
      "options": {}
    },
    "router-config": {
      "provider": "ollama",
      "options": {}
    }
  }
}
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("VERMAY_AGENT_ROUTER_MODEL", raising=False)

    assert _router_model_name(config_path=config_path) == "router-config"


def test_legacy_local_rest_routes_are_not_exposed(tmp_path):
    client, store = make_client(tmp_path)

    legacy_requests = [
        ("post", "/api/sessions", {"json": {"session_id": "session-1"}}),
        ("get", "/api/sessions", {}),
        ("get", "/api/sessions/session-1", {}),
        ("delete", "/api/sessions/session-1", {}),
        ("post", "/api/sessions/session-1/tasks", {"json": {"input": "hello"}}),
        ("get", "/api/tasks/task-1", {}),
        ("get", "/api/tasks/task-1/events", {}),
        ("get", "/api/tasks/task-1/artifacts", {}),
        ("get", "/api/tasks/task-1/artifacts/task-1:final_answer", {}),
        ("get", "/api/tasks/task-1/stream", {}),
        ("post", "/api/tasks/task-1/resume", {"json": {"approved": True}}),
        ("post", "/api/tasks/task-1/cancel", {"json": {"reason": "operator"}}),
        ("post", "/api/tasks/task-1/retry", {"json": {"reason": "try again"}}),
    ]

    for method, path, kwargs in legacy_requests:
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404, path
        assert response.json() == {"detail": "Not Found"}

    store.close()


def test_unprefixed_local_rest_routes_are_not_exposed(tmp_path):
    client, store = make_client(tmp_path)

    assert client.get("/sessions").status_code == 404
    assert client.get("/sessions").json() == {"detail": "Not Found"}
    assert client.get("/tasks/task-1").status_code == 404

    store.close()


def test_create_app_has_no_legacy_agent_service_state():
    core = ReconciliationSpy()

    with TestClient(create_app(main_agent_core=core)) as client:
        assert client.get("/health").status_code == 200
        assert not hasattr(client.app.state, "agent_service")


def test_create_app_reconciles_main_agent_core_on_startup():
    core = ReconciliationSpy()

    with TestClient(create_app(main_agent_core=core)) as client:
        assert client.get("/health").status_code == 200

    assert core.calls == 1


def test_default_app_composition_owns_only_resources_it_creates(monkeypatch):
    core = ReconciliationSpy()
    store = CloseSpy()
    runner = CloseSpy()
    executor = ShutdownSpy()
    monkeypatch.setattr(
        "vermay_agent.api.app._build_default_main_agent_core",
        lambda: (core, store, runner, executor),
    )

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200
        assert client.app.state.main_agent_core is core
        assert not hasattr(client.app.state, "agent_service")

    assert core.calls == 1
    assert executor.shutdown_calls == 1
    assert runner.close_calls == 1
    assert store.close_calls == 1

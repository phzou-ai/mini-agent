from __future__ import annotations

import time
from urllib.error import URLError

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import Field

from vermay_agent.checkpointing import build_sqlite_checkpointer
from vermay_agent.execution_context import ExecutionContextRegistry, current_execution_context
from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.model_clients import OllamaModelClient, OpenAICompatibleModelClient
from vermay_agent.permission import PermissionGate
from vermay_agent.progress import ProgressReporter
from vermay_agent.langgraph_runtime import (
    ExecutionPolicy,
    ModelInvocation,
    OllamaModelAdapter,
    OpenAICompatibleModelAdapter,
)
from vermay_agent.langgraph_runtime.graph import build_graph
from vermay_agent.langgraph_runtime.model_factory import ModelProviderConfig, build_model_client
from vermay_agent.langgraph_runtime.nodes import GraphComponents
from vermay_agent.langgraph_runtime.execution import model_call_limit, policy_from_state
from vermay_agent.langgraph_runtime.observations import normalize_tool_observation
from vermay_agent.langgraph_runtime.routing import (
    latest_ai_message,
    route_after_approval,
    route_after_model,
    route_after_permission,
    route_loop_limit,
)
from vermay_agent.langgraph_runtime.runner import LangGraphAgentRuntime
from vermay_agent.langgraph_runtime.state import build_initial_state
from vermay_agent.tooling import ToolArgs, structured_tool
from vermay_agent.tool_schema import tool_schemas_from_tools
from vermay_agent.tool_registry import ToolRegistry
from vermay_agent.tools.user_input import register_user_input_tool
from vermay_agent.trace import TraceLogger
from vermay_agent.types import ModelResponse, ToolCall
from vermay_agent.storage import SQLITE_BUSY_TIMEOUT_MS


class EchoArgs(ToolArgs):
    value: str = Field(description="Value to echo.")


class EmptyArgs(ToolArgs):
    pass


class FakeModel:
    def __init__(self, responses: AIMessage | list[AIMessage]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.calls = []

    def invoke(self, messages, tools):
        self.calls.append((messages, tools))
        return ModelInvocation(message=self.responses.pop(0))


class FakeProjectModelClient:
    def __init__(self, response) -> None:
        self.response = response
        self.calls = []

    def invoke(self, messages, tools, *, timeout_seconds=None):
        self.calls.append((messages, tools, timeout_seconds))
        return self.response


def make_echo_tool():
    return structured_tool(
        func=lambda value: {"value": value},
        name="echo",
        description="Echo a value.",
        args_schema=EchoArgs,
        dangerous=False,
    )


def make_dangerous_tool(executed: dict[str, bool]):
    return structured_tool(
        func=lambda: executed.__setitem__("value", True) or {"executed": True},
        name="dangerous",
        description="Dangerous tool.",
        args_schema=EmptyArgs,
        dangerous=True,
    )


def make_named_dangerous_tool(name: str, executed: list[str]):
    return structured_tool(
        func=lambda: executed.append(name) or {"executed": name},
        name=name,
        description=f"Dangerous tool {name}.",
        args_schema=EmptyArgs,
        dangerous=True,
    )


def make_failing_tool():
    def fail() -> dict:
        raise RuntimeError("upstream service unavailable")

    return structured_tool(
        func=fail,
        name="failing_tool",
        description="Always fails.",
        args_schema=EmptyArgs,
        dangerous=False,
    )


def make_context_observing_tool(observed_contexts: list[dict[str, str | None]]):
    def observe() -> dict:
        context = current_execution_context()
        observed_contexts.append(
            {
                "runtime_thread_id": context.runtime_thread_id if context is not None else None,
                "invocation_id": context.invocation_id if context is not None else None,
            }
        )
        return {"ok": True}

    return structured_tool(
        func=observe,
        name="observe_execution_context",
        description="Observe the current execution context.",
        args_schema=EmptyArgs,
        dangerous=False,
    )


def test_langgraph_initial_state_uses_langchain_messages():
    state = build_initial_state("hello", system_prompt="system prompt", max_loops=3)

    assert isinstance(state["messages"][0], SystemMessage)
    assert isinstance(state["messages"][1], HumanMessage)
    assert state["messages"][0].content == "system prompt"
    assert state["messages"][1].content == "hello"
    assert state["loop_index"] == 1
    assert state["max_loops"] == 3
    assert state["final_answer"] is None


def test_langgraph_routing_detects_ai_message_tool_calls():
    state = build_initial_state("weather")
    state["messages"].append(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "weather_forecast",
                    "args": {"location": "Shanghai"},
                    "id": "call-1",
                    "type": "tool_call",
                }
            ],
        )
    )

    assert latest_ai_message(state["messages"]) is state["messages"][-1]
    assert route_after_model(state) == "tool_calls"


def test_langgraph_routing_detects_final_answer():
    state = build_initial_state("hello")
    state["messages"].append(AIMessage(content="final answer"))

    assert route_after_model(state) == "final"


def test_langgraph_loop_limit_uses_loop_index():
    assert route_loop_limit({**build_initial_state("hello", max_loops=2), "loop_index": 2}) == "continue"
    assert route_loop_limit({**build_initial_state("hello", max_loops=2), "loop_index": 3}) == "max_loops"


def test_langgraph_permission_routing():
    assert route_after_permission({**build_initial_state("hello"), "permission": {"status": "allowed"}}) == "allowed"
    assert (
        route_after_permission({**build_initial_state("hello"), "permission": {"status": "approval_required"}})
        == "approval_required"
    )
    assert (
        route_after_permission({**build_initial_state("hello"), "permission": {"status": "input_required"}})
        == "input_required"
    )
    assert route_after_permission({**build_initial_state("hello"), "permission": {"status": "denied"}}) == "denied"


def test_langgraph_approval_routing():
    assert route_after_approval({**build_initial_state("hello"), "approval": {"approved": True}}) == "approved"
    assert route_after_approval({**build_initial_state("hello"), "approval": {"approved": False}}) == "rejected"
    assert route_after_approval(build_initial_state("hello")) == "rejected"


def test_langgraph_graph_appends_ai_message_with_add_messages():
    model = FakeModel(AIMessage(content="final answer"))
    graph = build_graph(GraphComponents(model=model, tools=[]))

    output = graph.invoke(build_initial_state("hello", system_prompt="system prompt"))

    assert output["final_answer"] == "final answer"
    assert len(output["messages"]) == 3
    assert isinstance(output["messages"][-1], AIMessage)
    assert model.calls[0][0][0].content == "system prompt"
    assert model.calls[0][0][1].content == "hello"


def test_langgraph_runtime_returns_run_result_for_final_answer():
    model = FakeModel(AIMessage(content="final answer"))
    runtime = LangGraphAgentRuntime(model=model, system_prompt="system prompt", max_loops=3)

    result = runtime.start("hello", thread_id="thread-test")

    assert result.thread_id == "thread-test"
    assert result.status == "completed"
    assert result.final_answer == "final answer"
    assert result.to_output() == "final answer"
    assert result.execution["completion"] == {
        "claimed": True,
        "evidence_count": 0,
        "residual_risk_count": 1,
    }
    assert result.execution["residual_risks"] == [
        {
            "category": "no_tool_evidence",
            "summary": "The final answer is model-generated; no tool-backed observation was produced.",
            "retryable": False,
        }
    ]
    assert len(result.state["messages"]) == 3
    assert model.calls[0][0][0].content == "system prompt"
    assert model.calls[0][0][1].content == "hello"


def test_langgraph_runtime_run_returns_final_answer():
    runtime = LangGraphAgentRuntime(model=FakeModel(AIMessage(content="final answer")))

    assert runtime.run("hello") == "final answer"


def test_langgraph_runtime_rejects_final_answer_that_declares_an_unexecuted_tool():
    executed = {"value": False}

    def should_not_run(value: str) -> dict:
        executed["value"] = True
        return {"value": value}

    tool = structured_tool(
        func=should_not_run,
        name="echo",
        description="Echo a value.",
        args_schema=EchoArgs,
        dangerous=False,
    )
    runtime = LangGraphAgentRuntime(
        model=FakeModel(AIMessage(content="Let me check that. Calling tool echo.")),
        tools=[tool],
    )

    with pytest.raises(ModelProtocolError, match="without emitting a structured tool call"):
        runtime.start("echo hello", thread_id="thread-unexecuted-tool")

    assert executed["value"] is False


def test_langgraph_runtime_rejects_dsml_tool_call_emitted_as_text():
    executed = {"value": False}

    def should_not_run(value: str) -> dict:
        executed["value"] = True
        return {"value": value}

    tool = structured_tool(
        func=should_not_run,
        name="echo",
        description="Echo a value.",
        args_schema=EchoArgs,
        dangerous=False,
    )
    runtime = LangGraphAgentRuntime(
        model=FakeModel(
            AIMessage(
                content=(
                    "Let me check.\n"
                    '<tool_calls><｜DSML｜invoke name="echo">'
                    '<｜DSML｜parameter name="value">hello</｜DSML｜parameter>'
                    "</｜DSML｜invoke></tool_calls>"
                )
            )
        ),
        tools=[tool],
    )

    with pytest.raises(ModelProtocolError, match="without emitting a structured tool call") as raised:
        runtime.start("echo hello", thread_id="thread-dsml-tool-text")

    assert raised.value.reason == "invalid_model_output"
    assert executed["value"] is False


def test_langgraph_runtime_allows_tool_call_markup_without_a_registered_tool():
    content = '<tool_calls><｜DSML｜invoke name="unavailable_tool"></｜DSML｜invoke></tool_calls>'
    runtime = LangGraphAgentRuntime(
        model=FakeModel(AIMessage(content=content)),
        tools=[make_echo_tool()],
    )

    result = runtime.start("show an example", thread_id="thread-unregistered-tool-markup")

    assert result.status == "completed"
    assert result.final_answer == content


def test_langgraph_runtime_allows_a_final_answer_that_only_mentions_a_tool():
    runtime = LangGraphAgentRuntime(
        model=FakeModel(AIMessage(content="You can call tool echo to inspect a value.")),
        tools=[make_echo_tool()],
    )

    result = runtime.start("how can I inspect this?", thread_id="thread-tool-mention")

    assert result.status == "completed"
    assert result.final_answer == "You can call tool echo to inspect a value."


def test_langgraph_runtime_resumes_model_requested_user_input_on_same_thread():
    registry = ToolRegistry()
    register_user_input_tool(registry)
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_user_input",
                        "args": {"prompt": "Which environment?", "choices": ["staging", "production"]},
                        "id": "call-input",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Checking staging."),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=registry.tools_for_model(),
        permission_gate=PermissionGate(registry),
    )

    interrupted = runtime.start("Check the deployment", thread_id="thread-input")

    assert interrupted.status == "interrupted"
    assert interrupted.thread_id == "thread-input"
    assert interrupted.interrupt["kind"] == "user_input_required"
    assert interrupted.interrupt["prompt"] == "Which environment?"
    assert interrupted.interrupt["choices"] == ["staging", "production"]

    completed = runtime.resume_input(
        "thread-input",
        parts=[{"kind": "text", "text": "staging"}],
    )

    assert completed.status == "completed"
    assert completed.thread_id == "thread-input"
    assert completed.final_answer == "Checking staging."
    resumed_messages = model.calls[1][0]
    assert isinstance(resumed_messages[-1], ToolMessage)
    assert resumed_messages[-1].tool_call_id == "call-input"
    assert resumed_messages[-1].content == "staging"


def test_langgraph_runtime_propagates_model_provider_errors(monkeypatch):
    def fake_urlopen(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    runtime = LangGraphAgentRuntime(
        model=OpenAICompatibleModelAdapter(
            client=OpenAICompatibleModelClient(
                model="gpt-4o",
                base_url="https://api.openai.com/v1",
            )
        )
    )

    with pytest.raises(ModelProviderError, match="connection refused"):
        runtime.start("hello", thread_id="thread-model-failure")


def test_langgraph_runtime_close_runs_callbacks_once():
    calls = []
    runtime = LangGraphAgentRuntime(
        model=FakeModel(AIMessage(content="final answer")),
        close_callbacks=[lambda: calls.append("closed")],
    )

    runtime.close()
    runtime.close()

    assert calls == ["closed"]
    assert runtime.close_callbacks == []


def test_langgraph_runtime_resume_requires_thread_id():
    runtime = LangGraphAgentRuntime(model=FakeModel(AIMessage(content="final answer")))

    try:
        runtime.resume("", approved=True)
    except ValueError as exc:
        assert str(exc) == "thread_id is required to resume an approval interrupt"
    else:
        raise AssertionError("expected missing thread_id to fail")


def test_langgraph_graph_executes_safe_tool_with_toolnode_then_calls_model_again():
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="tool completed"),
        ]
    )
    tool = make_echo_tool()
    graph = build_graph(GraphComponents(model=model, tools=[tool]))

    output = graph.invoke(build_initial_state("echo hello"))

    assert output["final_answer"] == "tool completed"
    assert len(model.calls) == 2
    assert any(isinstance(message, ToolMessage) for message in output["messages"])
    tool_message = next(message for message in output["messages"] if isinstance(message, ToolMessage))
    assert tool_message.name == "echo"
    assert tool_message.tool_call_id == "call-echo"
    assert tool_message.status == "success"
    assert tool_message.content == '{"value": "hello"}'
    assert isinstance(model.calls[1][0][-1], ToolMessage)


def test_langgraph_runtime_executes_safe_tool_with_toolnode():
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="tool completed"),
        ]
    )
    tool = make_echo_tool()
    runtime = LangGraphAgentRuntime(model=model, tools=[tool])

    result = runtime.start("echo hello", thread_id="thread-safe-tool")

    assert result.thread_id == "thread-safe-tool"
    assert result.status == "completed"
    assert result.final_answer == "tool completed"
    assert result.execution["completion"] == {
        "claimed": True,
        "evidence_count": 1,
        "residual_risk_count": 0,
    }
    assert result.execution["evidence"][0]["tool_call_id"] == "call-echo"
    assert any(isinstance(message, ToolMessage) for message in result.state["messages"])


def test_langgraph_tool_node_binds_the_active_runtime_execution_context():
    observed_contexts: list[dict[str, str | None]] = []
    registry = ExecutionContextRegistry()
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "observe_execution_context",
                        "args": {},
                        "id": "call-context",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="context observed"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[make_context_observing_tool(observed_contexts)],
        execution_context_registry=registry,
    )

    with registry.activate("thread-r3"):
        result = runtime.start("observe", thread_id="thread-r3")

    assert result.status == "completed"
    assert observed_contexts == [{"runtime_thread_id": "thread-r3", "invocation_id": None}]


def test_model_adapter_caps_an_active_task_call_by_its_remaining_deadline():
    project_client = FakeProjectModelClient(ModelResponse(content="ok"))
    adapter = OllamaModelAdapter(client=project_client)
    registry = ExecutionContextRegistry()

    with registry.activate("thread-model-deadline"):
        with registry.bind_model_context(
            runtime_thread_id="thread-model-deadline",
            deadline_monotonic=time.monotonic() + 5,
        ):
            response = adapter.invoke([HumanMessage(content="hello")], tools=[])

    assert response.message.content == "ok"
    timeout_seconds = project_client.calls[0][2]
    assert timeout_seconds is not None
    assert 0 < timeout_seconds <= 5


def test_langgraph_runtime_stops_when_a_slow_model_exhausts_task_elapsed_budget():
    class SlowModel:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, messages, tools):
            self.calls += 1
            time.sleep(0.03)
            return ModelInvocation(message=AIMessage(content="late answer"))

    model = SlowModel()
    runtime = LangGraphAgentRuntime(
        model=model,
        execution_policy=ExecutionPolicy(
            max_model_calls=2,
            max_tool_calls=2,
            max_failures=2,
            max_loop_steps=2,
            max_elapsed_seconds=0.01,
        ),
    )

    result = runtime.start("slow model", thread_id="thread-model-deadline")

    assert result.status == "stopped"
    assert result.stop_reason == "budget_exhausted"
    assert result.final_answer is None
    assert result.execution["stop_detail"]["limit"] == "max_elapsed_seconds"
    assert model.calls == 1


def test_langgraph_runtime_stops_before_executing_tools_after_cancellation_during_model_call():
    registry = ExecutionContextRegistry()
    executed = {"value": False}

    class CancelDuringModelCall:
        def invoke(self, messages, tools):
            assert registry.request_cancellation(
                "thread-cancel-after-model",
                reason="operator requested",
            )
            return ModelInvocation(
                message=AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "dangerous",
                            "args": {},
                            "id": "call-dangerous",
                            "type": "tool_call",
                        }
                    ],
                )
            )

    runtime = LangGraphAgentRuntime(
        model=CancelDuringModelCall(),
        tools=[make_dangerous_tool(executed)],
        execution_context_registry=registry,
    )

    with registry.activate("thread-cancel-after-model"):
        result = runtime.start("delete resource", thread_id="thread-cancel-after-model")

    assert result.status == "stopped"
    assert result.stop_reason == "canceled"
    assert result.execution["stop_detail"] == {"source": "control_plane_cancellation"}
    assert executed["value"] is False


def test_langgraph_runtime_stops_before_exceeding_model_call_budget():
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="this response must not be invoked"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[make_echo_tool()],
        execution_policy=ExecutionPolicy(
            max_model_calls=1,
            max_tool_calls=2,
            max_failures=2,
            max_loop_steps=3,
        ),
    )

    result = runtime.start("echo hello", thread_id="thread-model-budget")

    assert result.status == "stopped"
    assert result.stop_reason == "budget_exhausted"
    assert result.execution["stop_detail"]["limit"] == "max_model_calls"
    assert result.execution["metrics"]["model_calls"] == 1
    assert result.execution["completion"]["claimed"] is False
    assert len(model.calls) == 1


def test_langgraph_runtime_stops_before_exceeding_tool_call_budget():
    executed = {"count": 0}

    def side_effect() -> dict:
        executed["count"] += 1
        return {"executed": True}

    tool = structured_tool(
        func=side_effect,
        name="side_effect",
        description="Records an execution.",
        args_schema=EmptyArgs,
        dangerous=False,
    )
    runtime = LangGraphAgentRuntime(
        model=FakeModel(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "side_effect", "args": {}, "id": "call-effect", "type": "tool_call"}
                ],
            )
        ),
        tools=[tool],
        execution_policy=ExecutionPolicy(
            max_model_calls=2,
            max_tool_calls=0,
            max_failures=2,
            max_loop_steps=2,
        ),
    )

    result = runtime.start("perform work", thread_id="thread-tool-budget")

    assert result.status == "stopped"
    assert result.stop_reason == "budget_exhausted"
    assert result.execution["stop_detail"]["limit"] == "max_tool_calls"
    assert executed["count"] == 0


def test_langgraph_runtime_stops_after_repeated_tool_failure_and_keeps_observation():
    runtime = LangGraphAgentRuntime(
        model=FakeModel(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "failing_tool", "args": {}, "id": "call-fail", "type": "tool_call"}
                ],
            )
        ),
        tools=[make_failing_tool()],
        execution_policy=ExecutionPolicy(
            max_model_calls=2,
            max_tool_calls=2,
            max_failures=1,
            max_loop_steps=2,
        ),
    )

    result = runtime.start("run a failing tool", thread_id="thread-failure-budget")

    assert result.status == "stopped"
    assert result.stop_reason == "repeated_failure"
    assert result.execution["metrics"]["failure_count"] == 1
    assert result.observations[0]["tool_call_id"] == "call-fail"
    assert result.observations[0]["ok"] is False
    assert result.observations[0]["error_category"] == "tool_execution_error"
    assert result.observations[0]["retryable"] is False
    assert result.execution["completion"] == {
        "claimed": False,
        "evidence_count": 0,
        "residual_risk_count": 2,
    }


def test_normalized_tool_observation_uses_structured_error_fields_without_text_inference():
    observation = normalize_tool_observation(
        ToolMessage(
            content=(
                '{"error_code":"network_unavailable","retryable":true,'
                '"changed_resources":[{"kind":"pod","name":"api-1"}],'
                '"artifact_refs":["artifact-1"]}'
            ),
            name="inspect_cluster",
            tool_call_id="call-structured-error",
            status="error",
        ),
        loop_index=2,
    )

    assert observation["error_category"] == "network_unavailable"
    assert observation["retryable"] is True
    assert observation["changed_resources"] == [{"kind": "pod", "name": "api-1"}]
    assert observation["artifact_refs"] == ["artifact-1"]


def test_normalized_tool_observation_treats_structured_ok_false_as_a_failure():
    observation = normalize_tool_observation(
        ToolMessage(
            content='{"ok":false,"error_code":"execution_timeout","retryable":true}',
            name="inspect_cluster",
            tool_call_id="call-structured-failure",
        ),
        loop_index=2,
    )

    assert observation["ok"] is False
    assert observation["error_category"] == "execution_timeout"
    assert observation["retryable"] is True


def test_execution_policy_preserves_a_pre_r2_checkpoint_loop_limit(monkeypatch):
    state = build_initial_state("resume legacy task", max_loops=3)
    state.pop("execution_policy")
    state["execution_started_at"] = 0.0
    state["model_calls"] = 3
    monkeypatch.setattr("vermay_agent.langgraph_runtime.execution.time.time", lambda: 10.0)

    policy = policy_from_state(state)
    limit = model_call_limit(state)

    assert policy.max_loop_steps == 3
    assert policy.max_model_calls == 3
    assert limit is not None
    assert limit.detail["limit"] == "max_model_calls"


def test_execution_policy_stops_when_elapsed_time_is_exhausted(monkeypatch):
    state = build_initial_state(
        "time-bound task",
        execution_policy=ExecutionPolicy(
            max_model_calls=3,
            max_tool_calls=3,
            max_failures=2,
            max_loop_steps=3,
            max_elapsed_seconds=2.0,
        ),
    )
    state["execution_started_at"] = 10.0
    monkeypatch.setattr("vermay_agent.langgraph_runtime.execution.time.time", lambda: 13.0)

    limit = model_call_limit(state)

    assert limit is not None
    assert limit.detail == {
        "limit": "max_elapsed_seconds",
        "limit_value": 2.0,
        "observed_value": 3.0,
    }


def test_langgraph_runtime_interrupts_dangerous_tool_before_toolnode():
    executed = {"value": False}
    registry = ToolRegistry()
    tool = make_dangerous_tool(executed)
    registry.register(tool)
    model = FakeModel(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "dangerous",
                    "args": {},
                    "id": "call-dangerous",
                    "type": "tool_call",
                }
            ],
        )
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[tool],
        permission_gate=PermissionGate(registry),
    )

    result = runtime.start("run dangerous", thread_id="thread-dangerous")

    assert result.status == "interrupted"
    assert result.final_answer is None
    assert result.interrupt["kind"] == "approval_required"
    assert result.interrupt["permission"]["reason"] == "tool 'dangerous' is marked dangerous"
    assert result.interrupt["permission"]["approval_summary"] == "Approve tool call: dangerous"
    assert result.interrupt["permission"]["safe_argument_preview"] == {}
    assert result.interrupt["permission"]["policy_tags"] == ["unknown", "approval_required"]
    assert result.interrupt_message.startswith("Approval required for tool call")
    assert executed["value"] is False


def test_langgraph_runtime_resumes_approved_dangerous_tool():
    executed = {"value": False}
    registry = ToolRegistry()
    tool = make_dangerous_tool(executed)
    registry.register(tool)
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dangerous",
                        "args": {},
                        "id": "call-dangerous",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="dangerous completed"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[tool],
        permission_gate=PermissionGate(registry),
    )

    interrupted = runtime.start("run dangerous", thread_id="thread-dangerous-approved")
    result = runtime.resume(interrupted.thread_id, approved=True, reason="approved for test")

    assert result.status == "completed"
    assert result.final_answer == "dangerous completed"
    assert executed["value"] is True
    assert any(isinstance(message, ToolMessage) for message in result.state["messages"])
    assert result.state["approval"] == {"approved": True, "reason": "approved for test"}


def test_langgraph_runtime_checks_every_tool_call_before_requesting_approval():
    executed = {"value": False}
    registry = ToolRegistry()
    dangerous_tool = make_dangerous_tool(executed)
    registry.register(dangerous_tool)
    model = FakeModel(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "dangerous",
                    "args": {},
                    "id": "call-dangerous",
                    "type": "tool_call",
                },
                {
                    "name": "unknown_tool",
                    "args": {},
                    "id": "call-unknown",
                    "type": "tool_call",
                },
            ],
        )
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[dangerous_tool],
        permission_gate=PermissionGate(registry),
    )

    result = runtime.start("run tools", thread_id="thread-multiple-tool-denied")

    assert result.status == "completed"
    assert result.final_answer == "Tool call rejected: unknown tool: unknown_tool"
    assert result.state["permission"]["status"] == "denied"
    assert [decision["status"] for decision in result.state["permission"]["decisions"]] == [
        "approval_required",
        "denied",
    ]
    assert executed["value"] is False
    assert not any(isinstance(message, ToolMessage) for message in result.state["messages"])


def test_langgraph_runtime_executes_checked_batch_only_after_approval():
    executed = {"value": False}
    registry = ToolRegistry()
    echo_tool = make_echo_tool()
    dangerous_tool = make_dangerous_tool(executed)
    registry.register(echo_tool)
    registry.register(dangerous_tool)
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    },
                    {
                        "name": "dangerous",
                        "args": {},
                        "id": "call-dangerous",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="tools completed"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[echo_tool, dangerous_tool],
        permission_gate=PermissionGate(registry),
    )

    interrupted = runtime.start("run tools", thread_id="thread-multiple-tool-approved")

    assert interrupted.status == "interrupted"
    assert [decision["status"] for decision in interrupted.interrupt["permission"]["decisions"]] == [
        "allowed",
        "approval_required",
    ]
    assert executed["value"] is False

    result = runtime.resume(interrupted.thread_id, approved=True, reason="approved batch")

    assert result.status == "completed"
    assert result.final_answer == "tools completed"
    assert executed["value"] is True
    assert {
        message.tool_call_id for message in result.state["messages"] if isinstance(message, ToolMessage)
    } == {"call-echo", "call-dangerous"}


def test_langgraph_runtime_requires_separate_approval_for_each_dangerous_tool_call():
    executed: list[str] = []
    registry = ToolRegistry()
    first_tool = make_named_dangerous_tool("dangerous_first", executed)
    second_tool = make_named_dangerous_tool("dangerous_second", executed)
    registry.register(first_tool)
    registry.register(second_tool)
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dangerous_first",
                        "args": {},
                        "id": "call-dangerous-first",
                        "type": "tool_call",
                    },
                    {
                        "name": "dangerous_second",
                        "args": {},
                        "id": "call-dangerous-second",
                        "type": "tool_call",
                    },
                ],
            ),
            AIMessage(content="tools completed"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[first_tool, second_tool],
        permission_gate=PermissionGate(registry),
    )

    first_interrupt = runtime.start("run tools", thread_id="thread-multiple-approvals")

    assert first_interrupt.status == "interrupted"
    assert first_interrupt.interrupt["tool_call"]["id"] == "call-dangerous-first"
    assert first_interrupt.interrupt["approval_index"] == 1
    assert first_interrupt.interrupt["approval_count"] == 2

    second_interrupt = runtime.resume(first_interrupt.thread_id, approved=True, reason="approve first")

    assert second_interrupt.status == "interrupted"
    assert second_interrupt.interrupt["tool_call"]["id"] == "call-dangerous-second"
    assert second_interrupt.interrupt["approval_index"] == 2
    assert executed == []

    result = runtime.resume(second_interrupt.thread_id, approved=True, reason="approve second")

    assert result.status == "completed"
    assert result.final_answer == "tools completed"
    assert executed == ["dangerous_first", "dangerous_second"]


def test_langgraph_runtime_resumes_approval_from_sqlite_checkpoint_across_runtime_instances(tmp_path):
    checkpoint_path = tmp_path / "langgraph.sqlite"
    executed = {"value": False}
    registry = ToolRegistry()
    tool = make_dangerous_tool(executed)
    registry.register(tool)
    first_checkpointer = build_sqlite_checkpointer(checkpoint_path)
    first_runtime = LangGraphAgentRuntime(
        model=FakeModel(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "dangerous",
                        "args": {},
                        "id": "call-dangerous",
                        "type": "tool_call",
                    }
                ],
            )
        ),
        tools=[tool],
        permission_gate=PermissionGate(registry),
        checkpointer=first_checkpointer,
        close_callbacks=[first_checkpointer.conn.close],
    )

    interrupted = first_runtime.start("run dangerous", thread_id="durable-thread")
    first_runtime.close()
    second_checkpointer = build_sqlite_checkpointer(checkpoint_path)
    second_runtime = LangGraphAgentRuntime(
        model=FakeModel(AIMessage(content="dangerous completed")),
        tools=[tool],
        permission_gate=PermissionGate(registry),
        checkpointer=second_checkpointer,
        close_callbacks=[second_checkpointer.conn.close],
    )

    result = second_runtime.resume(interrupted.thread_id, approved=True, reason="approved from second runtime")

    assert result.status == "completed"
    assert result.final_answer == "dangerous completed"
    assert executed["value"] is True
    assert result.state["approval"] == {"approved": True, "reason": "approved from second runtime"}
    second_runtime.close()


def test_sqlite_checkpointer_uses_runtime_connection_contract(tmp_path):
    checkpointer = build_sqlite_checkpointer(tmp_path / "langgraph.sqlite")

    try:
        assert checkpointer.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert checkpointer.conn.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_TIMEOUT_MS
        assert checkpointer.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        checkpointer.conn.close()


def test_langgraph_runtime_resumes_rejected_dangerous_tool_without_execution():
    executed = {"value": False}
    registry = ToolRegistry()
    tool = make_dangerous_tool(executed)
    registry.register(tool)
    model = FakeModel(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "dangerous",
                    "args": {},
                    "id": "call-dangerous",
                    "type": "tool_call",
                }
            ],
        )
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[tool],
        permission_gate=PermissionGate(registry),
    )

    interrupted = runtime.start("run dangerous", thread_id="thread-dangerous-rejected")
    result = runtime.resume(interrupted.thread_id, approved=False, reason="not allowed")

    assert result.status == "completed"
    assert result.final_answer == "Tool call rejected by approval: not allowed"
    assert executed["value"] is False
    assert not any(isinstance(message, ToolMessage) for message in result.state["messages"])
    assert result.state["approval"] == {"approved": False, "reason": "not allowed"}


def test_langgraph_runtime_progress_uses_langgraph_messages(capsys):
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="tool completed"),
        ]
    )
    tool = make_echo_tool()
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[tool],
        progress=ProgressReporter(enabled=True),
    )

    result = runtime.start("echo hello", thread_id="thread-progress")

    assert result.final_answer == "tool completed"
    output = capsys.readouterr().err
    assert "loop 1" in output
    assert "context" in output
    assert "tool_call" in output
    assert "echo" in output
    assert "result" in output
    assert "observation" in output
    assert "loop 2" in output
    assert "done" in output


def test_langgraph_runtime_trace_uses_langgraph_messages(tmp_path):
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "echo",
                        "args": {"value": "hello"},
                        "id": "call-echo",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="tool completed"),
        ]
    )
    tool = make_echo_tool()
    trace_path = tmp_path / "trace.jsonl"
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=[tool],
        trace=TraceLogger(trace_path),
    )

    result = runtime.start("echo hello", thread_id="thread-trace")

    assert result.final_answer == "tool completed"
    trace = trace_path.read_text(encoding="utf-8")
    assert '"type": "langgraph_run_started"' in trace
    assert '"type": "langgraph_context_built"' in trace
    assert '"type": "langgraph_model_response"' in trace
    assert '"type": "langgraph_tool_execute_start"' in trace
    assert '"type": "langgraph_tool_message"' in trace
    assert '"type": "langgraph_run_finished"' in trace


def test_ollama_adapter_returns_thin_ai_message_wrapper():
    project_client = FakeProjectModelClient(
        OllamaModelClient()._parse_content(
            '{"action":"tool_call","name":"echo","arguments":{"value":"hello"}}'
        )
    )
    adapter = OllamaModelAdapter(client=project_client)
    tool = make_echo_tool()

    response = adapter.invoke([SystemMessage(content="system"), HumanMessage(content="hello")], tools=[tool])

    assert isinstance(response, ModelInvocation)
    assert isinstance(response.message, AIMessage)
    assert response.message.tool_calls[0]["name"] == "echo"
    assert response.message.tool_calls[0]["args"] == {"value": "hello"}
    assert project_client.calls[0][1] == tool_schemas_from_tools([tool])


def test_openai_adapter_preserves_multiple_tool_calls_and_ids():
    project_client = FakeProjectModelClient(
        ModelResponse(
            content="Calling tools.",
            tool_calls=[
                ToolCall(name="echo", arguments={"value": "first"}, id="call-1"),
                ToolCall(name="echo", arguments={"value": "second"}, id="call-2"),
            ],
        )
    )
    adapter = OpenAICompatibleModelAdapter(client=project_client)
    tool = make_echo_tool()

    response = adapter.invoke([HumanMessage(content="hello")], tools=[tool])

    assert [tool_call["id"] for tool_call in response.message.tool_calls] == ["call-1", "call-2"]
    assert [tool_call["args"] for tool_call in response.message.tool_calls] == [
        {"value": "first"},
        {"value": "second"},
    ]


def test_ollama_adapter_extracts_embedded_json_tool_call():
    project_client = FakeProjectModelClient(
        OllamaModelClient()._parse_content(
            'Let me use a tool.\n\n{"action":"tool_call","name":"echo","arguments":{"value":"hello"}}'
        )
    )
    adapter = OllamaModelAdapter(client=project_client)
    tool = make_echo_tool()

    response = adapter.invoke([SystemMessage(content="system"), HumanMessage(content="hello")], tools=[tool])

    assert response.message.content == "Calling tool echo."
    assert response.message.tool_calls[0]["name"] == "echo"
    assert response.message.tool_calls[0]["args"] == {"value": "hello"}
    assert project_client.calls[0][1] == tool_schemas_from_tools([tool])


def test_ollama_adapter_uses_tools_argument_for_each_invocation():
    project_client = FakeProjectModelClient(OllamaModelClient()._parse_content('{"action":"final","content":"ok"}'))
    adapter = OllamaModelAdapter(client=project_client)
    echo_tool = make_echo_tool()
    dangerous_tool = make_dangerous_tool({"value": False})

    adapter.invoke([HumanMessage(content="first")], tools=[echo_tool])
    adapter.invoke([HumanMessage(content="second")], tools=[dangerous_tool])

    assert project_client.calls[0][1] == tool_schemas_from_tools([echo_tool])
    assert project_client.calls[1][1] == tool_schemas_from_tools([dangerous_tool])


def test_model_factory_builds_default_provider_adapter():
    model = build_model_client(ModelProviderConfig(provider="ollama"))

    assert isinstance(model, OllamaModelAdapter)

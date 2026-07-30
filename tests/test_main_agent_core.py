from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage

from vermay_agent.errors import ModelProviderError
from vermay_agent.main_agent import (
    DefaultMainAgentRouter,
    DirectModelRouterModelClient,
    LocalMessageResult,
    LocalTaskResult,
    LocalTaskRunResult,
    MainAgentCore,
    MainAgentRequest,
    MainAgentStore,
    MessageRecord,
    MessageRole,
    RemoteAgentResult,
    RemoteAgentSendResult,
    RouteDecisionKind,
    RouterModelDecision,
    TaskStatus,
)
from vermay_agent.langgraph_runtime.model_adapters import ModelInvocation
from vermay_agent.storage import AgentStore


@dataclass
class FakeResponder:
    calls: list[list[MessageRecord]] = field(default_factory=list)

    def respond(self, messages: list[MessageRecord]) -> list[dict]:
        self.calls.append(messages)
        return [{"kind": "text", "text": "model answer"}]


@dataclass
class FakeTaskRunner:
    calls: list[tuple[list[MessageRecord], str]] = field(default_factory=list)
    resume_calls: list[tuple[str, bool, str | None]] = field(default_factory=list)
    resume_input_calls: list[tuple[str, list[dict], dict | None]] = field(default_factory=list)

    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        self.calls.append((messages, thread_id))
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "task answer"}],
        )

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        self.resume_calls.append((thread_id, approved, reason))
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "resumed task answer"}],
        )

    def resume_input(
        self,
        *,
        thread_id: str,
        parts: list[dict],
        metadata: dict | None = None,
    ) -> LocalTaskRunResult:
        self.resume_input_calls.append((thread_id, parts, metadata))
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=[{"kind": "text", "text": "input resumed answer"}],
        )


def test_main_agent_core_submits_requested_input_without_rerouting(tmp_path):
    class InputRunner(FakeTaskRunner):
        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            self.calls.append((messages, thread_id))
            return LocalTaskRunResult(
                status=TaskStatus.INPUT_REQUIRED,
                parts=[{"kind": "text", "text": "Which environment?"}],
                input_request={
                    "kind": "user_input_required",
                    "prompt": "Which environment?",
                    "choices": ["staging", "production"],
                    "inputSchema": {"type": "string", "enum": ["staging", "production"]},
                },
            )

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = InputRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
    )
    started = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "check deployment"}],
            metadata={"executionMode": "task"},
        )
    )
    task = store.get_task(started.task_id)
    assert task is not None
    assert task.status == TaskStatus.INPUT_REQUIRED

    resumed = core.submit_task_input(
        task.task_id,
        MainAgentRequest(
            context_id=task.context_id,
            message_id="msg-user-input",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "staging"}],
            metadata={"source": "test"},
        ),
    )

    assert resumed.status == TaskStatus.COMPLETED
    assert runner.resume_input_calls == [
        (task.runtime_thread_id, [{"kind": "text", "text": "staging"}], {"source": "test"})
    ]
    submitted = store.get_message("msg-user-input")
    assert submitted is not None
    assert submitted.task_id == task.task_id
    assert store.get_pending_input_request(task.task_id) is None
    assert [event.type for event in store.list_task_events(task.task_id)] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_input_submitted",
        "task_resumed",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]


@dataclass
class DeferredTaskSubmitter:
    pending: list[tuple[object, tuple[object, ...]]] = field(default_factory=list)

    def submit(self, func, *args):
        self.pending.append((func, args))

    def run_next(self) -> None:
        func, args = self.pending.pop(0)
        func(*args)


@dataclass
class FakeRouterModel:
    decisions: list[RouterModelDecision]
    calls: list[list[MessageRecord]] = field(default_factory=list)

    def classify(self, *, request, messages, registered_agents):
        self.calls.append(messages)
        return self.decisions.pop(0)


@dataclass
class FakeLangGraphModelClient:
    contents: list[str]
    calls: list[list] = field(default_factory=list)

    def invoke(self, messages: list, tools: list) -> ModelInvocation:
        self.calls.append(messages)
        return ModelInvocation(message=AIMessage(content=self.contents.pop(0)))


@dataclass
class FakeRouterRawJsonClient:
    contents: list[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def invoke_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.contents.pop(0)


@dataclass
class FakeRemoteAgentClient:
    responses: list[RemoteAgentSendResult]
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    def send_message(self, *, agent, request, context_id: str, message_id: str) -> RemoteAgentSendResult:
        self.calls.append((agent.agent_id, context_id, message_id))
        return self.responses.pop(0)


def test_main_agent_core_local_message_persists_messages_without_task(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    responder = FakeResponder()
    core = MainAgentCore(store=store, local_message_responder=responder)

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "hello"}],
            metadata={"executionMode": "message"},
        )
    )

    assert isinstance(result, LocalMessageResult)
    assert result.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert result.input_message_id == "msg-user-1"
    assert result.parts == [{"kind": "text", "text": "model answer"}]
    messages = store.list_context_messages(result.context_id)
    assert [message.role for message in messages] == [MessageRole.USER, MessageRole.AGENT]
    assert [message.message_id for message in messages] == ["msg-user-1", result.message_id]
    assert store.list_context_tasks(result.context_id) == []
    assert len(responder.calls) == 1
    assert [message.message_id for message in responder.calls[0]] == ["msg-user-1"]


def test_main_agent_core_new_context_title_uses_first_user_input(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(store=store, local_message_responder=FakeResponder())

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "  Check   k8s status\nagain  "}],
            metadata={"executionMode": "message"},
        )
    )

    context = store.get_context(result.context_id)
    assert context is not None
    assert context.title == "Check k8s status again"


def test_main_agent_core_existing_context_keeps_original_title(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(store=store, local_message_responder=FakeResponder())

    first = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "first question"}],
            metadata={"executionMode": "message"},
        )
    )
    core.handle_message(
        MainAgentRequest(
            context_id=first.context_id,
            message_id="msg-user-2",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "second question"}],
            metadata={"executionMode": "message"},
        )
    )

    context = store.get_context(first.context_id)
    assert context is not None
    assert context.title == "first question"


def test_main_agent_core_local_message_receives_same_context_history(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    responder = FakeResponder()
    core = MainAgentCore(store=store, local_message_responder=responder)

    first = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "my name is Ada"}],
            metadata={"executionMode": "message"},
        )
    )
    second = core.handle_message(
        MainAgentRequest(
            context_id=first.context_id,
            message_id="msg-user-2",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "what is my name?"}],
            metadata={"executionMode": "message"},
        )
    )

    assert isinstance(second, LocalMessageResult)
    assert len(responder.calls) == 2
    assert [message.message_id for message in responder.calls[1]] == [
        "msg-user-1",
        first.message_id,
        "msg-user-2",
    ]
    assert [message.role for message in responder.calls[1]] == [
        MessageRole.USER,
        MessageRole.AGENT,
        MessageRole.USER,
    ]


def test_main_agent_core_local_task_creates_task_without_responder_call(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    responder = FakeResponder()
    core = MainAgentCore(store=store, local_message_responder=responder)
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run"}],
            metadata={"executionMode": "task"},
        )
    )

    assert isinstance(result, LocalTaskResult)
    assert result.kind == RouteDecisionKind.LOCAL_TASK
    assert result.context_id == "ctx-1"
    assert store.get_task(result.task_id) is not None
    assert responder.calls == []


def test_main_agent_core_local_task_runner_receives_same_context_history(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = FakeTaskRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
    )
    context = store.create_context(context_id="ctx-1")
    store.append_message(
        message_id="msg-user-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "remember project alpha"}],
    )
    store.append_message(
        message_id="msg-agent-1",
        context_id=context.context_id,
        role=MessageRole.AGENT,
        parts=[{"kind": "text", "text": "project alpha noted"}],
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-2",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run a task for that project"}],
            metadata={"executionMode": "task"},
        )
    )

    assert isinstance(result, LocalTaskResult)
    assert len(runner.calls) == 1
    assert [message.message_id for message in runner.calls[0][0]] == [
        "msg-user-1",
        "msg-agent-1",
        "msg-user-2",
    ]
    assert [message.role for message in runner.calls[0][0]] == [
        MessageRole.USER,
        MessageRole.AGENT,
        MessageRole.USER,
    ]


def test_main_agent_core_context_window_is_bounded_to_recent_ten_messages(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    responder = FakeResponder()
    core = MainAgentCore(store=store, local_message_responder=responder)
    context = store.create_context(context_id="ctx-1")
    for index in range(12):
        store.append_message(
            message_id=f"msg-history-{index}",
            context_id=context.context_id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.AGENT,
            parts=[{"kind": "text", "text": f"history {index}"}],
        )

    core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-current",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "current"}],
            metadata={"executionMode": "message"},
        )
    )

    assert [message.message_id for message in responder.calls[0]] == [
        "msg-history-3",
        "msg-history-4",
        "msg-history-5",
        "msg-history-6",
        "msg-history-7",
        "msg-history-8",
        "msg-history-9",
        "msg-history-10",
        "msg-history-11",
        "msg-user-current",
    ]


def test_main_agent_core_local_task_runner_persists_output_message_artifact_and_events(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = FakeTaskRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run"}],
            metadata={"executionMode": "task"},
        )
    )

    assert isinstance(result, LocalTaskResult)
    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert task.output_message_id is not None
    assert [message.role for message in store.list_context_messages("ctx-1")] == [
        MessageRole.USER,
        MessageRole.AGENT,
    ]
    assert store.get_message(task.output_message_id).parts == [{"kind": "text", "text": "task answer"}]
    artifacts = store.list_task_artifacts(result.task_id)
    assert len(artifacts) == 1
    assert artifacts[0].parts == [{"kind": "text", "text": "task answer"}]
    assert artifacts[0].metadata["kind"] == "final_answer"
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]
    assert len(runner.calls) == 1
    assert [message.message_id for message in runner.calls[0][0]] == ["msg-user-1"]
    assert runner.calls[0][1] == task.runtime_thread_id


def test_main_agent_core_background_task_returns_while_execution_is_queued(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = FakeTaskRunner()
    submitter = DeferredTaskSubmitter()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
        task_submitter=submitter,
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run in background"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.QUEUED
    assert runner.calls == []
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_queued",
    ]

    submitter.run_next()

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.COMPLETED
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]


def test_main_agent_core_running_background_task_honors_cancel_at_safe_boundary(tmp_path):
    class BlockingRunner:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()

        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            self.started.set()
            assert self.release.wait(timeout=2)
            return LocalTaskRunResult(
                status=TaskStatus.COMPLETED,
                parts=[{"kind": "text", "text": "late answer"}],
            )

    class ThreadTaskSubmitter:
        def __init__(self) -> None:
            self.threads: list[threading.Thread] = []

        def submit(self, func, *args):
            thread = threading.Thread(target=func, args=args)
            self.threads.append(thread)
            thread.start()
            return thread

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = BlockingRunner()
    submitter = ThreadTaskSubmitter()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
        task_submitter=submitter,
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run then cancel"}],
            metadata={"executionMode": "task"},
        )
    )
    assert runner.started.wait(timeout=2)

    cancel_requested = core.cancel_task(result.task_id, reason="operator requested")
    assert cancel_requested.status == TaskStatus.CANCEL_REQUESTED

    runner.release.set()
    for thread in submitter.threads:
        thread.join(timeout=2)
        assert not thread.is_alive()

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.CANCELED
    assert task.output_message_id is None
    assert store.list_task_artifacts(result.task_id) == []
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_queued",
        "task_started",
        "task_cancel_requested",
        "task_cancelled",
    ]


def test_main_agent_core_background_resume_rejects_duplicate_request(tmp_path):
    class ApprovalRunner(FakeTaskRunner):
        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            return LocalTaskRunResult(status=TaskStatus.INPUT_REQUIRED)

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    submitter = DeferredTaskSubmitter()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=ApprovalRunner(),
        task_submitter=submitter,
    )
    context = store.create_context(context_id="ctx-1")
    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "approve"}],
            metadata={"executionMode": "task"},
        )
    )
    submitter.run_next()

    resumed = core.resume_task(result.task_id, approved=True)

    assert resumed.status == TaskStatus.QUEUED
    with pytest.raises(ValueError, match="task is not waiting for approval"):
        core.resume_task(result.task_id, approved=True)
    assert len(submitter.pending) == 1


def test_main_agent_core_does_not_overwrite_terminal_task_status_after_runner_returns(tmp_path):
    class CancelBeforeCompleteRunner:
        def __init__(self, store: MainAgentStore) -> None:
            self.store = store

        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            task = next(
                task
                for task in self.store.list_context_tasks(messages[-1].context_id)
                if task.runtime_thread_id == thread_id
            )
            self.store.update_task_status(task.task_id, TaskStatus.CANCELED)
            self.store.append_task_event(task_id=task.task_id, type="task_cancelled", status=TaskStatus.CANCELED)
            return LocalTaskRunResult(
                status=TaskStatus.COMPLETED,
                parts=[{"kind": "text", "text": "late completed answer"}],
            )

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=CancelBeforeCompleteRunner(store),
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run then cancel"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.CANCELED
    assert task.output_message_id is None
    assert [message.role for message in store.list_context_messages("ctx-1")] == [MessageRole.USER]
    assert store.list_task_artifacts(result.task_id) == []
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_started",
        "task_cancelled",
    ]


def test_main_agent_core_rolls_back_partial_completed_task_result(tmp_path):
    class FailingArtifactStore(MainAgentStore):
        def upsert_artifact(self, **kwargs):
            raise RuntimeError("artifact write failed")

    store = FailingArtifactStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=FakeTaskRunner(),
    )
    context = store.create_context(context_id="ctx-1")

    try:
        core.handle_message(
            MainAgentRequest(
                context_id=context.context_id,
                message_id="msg-user-1",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "run"}],
                metadata={"executionMode": "task"},
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "artifact write failed"
    else:
        raise AssertionError("expected artifact write failure")

    tasks = store.list_context_tasks("ctx-1")
    assert len(tasks) == 1
    assert tasks[0].status == TaskStatus.RUNNING
    assert tasks[0].output_message_id is None
    assert [message.role for message in store.list_context_messages("ctx-1")] == [MessageRole.USER]
    assert store.list_task_artifacts(tasks[0].task_id) == []
    assert [event.type for event in store.list_task_events(tasks[0].task_id)] == [
        "task_created",
        "task_started",
    ]


def test_main_agent_core_local_task_runner_failure_marks_task_failed(tmp_path):
    class FailingRunner:
        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            raise RuntimeError("runtime failed")

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=FailingRunner(),
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.error_code == "runtime_error"
    assert task.error_message == "Agent execution failed."
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_started",
        "task_failed",
    ]


def test_main_agent_core_model_failure_uses_model_error_code_and_retryability(tmp_path):
    class FailingRunner:
        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            raise ModelProviderError(
                "Ollama request failed: connection refused",
                provider="ollama",
                retryable=True,
            )

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=FailingRunner(),
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.error_code == "model_error"
    assert task.error_message == "Model request failed."
    assert store.list_task_events(result.task_id)[-1].payload == {
        "error_code": "model_error",
        "error_message": "Model request failed.",
        "retryable": True,
    }


def test_main_agent_core_local_task_runner_can_leave_task_running(tmp_path):
    class RunningRunner:
        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            return LocalTaskRunResult(status=TaskStatus.RUNNING)

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=RunningRunner(),
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "run and hold"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.RUNNING
    assert task.output_message_id is None
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_started",
    ]


def test_main_agent_core_local_task_runner_can_resume_input_required_task(tmp_path):
    class ApprovalRunner:
        resume_calls: list[tuple[str, bool, str | None]]

        def __init__(self) -> None:
            self.resume_calls = []

        def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
            return LocalTaskRunResult(
                status=TaskStatus.INPUT_REQUIRED,
                parts=[{"kind": "text", "text": "approval required"}],
                error_code="input_required",
                error_message="approval required",
            )

        def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
            self.resume_calls.append((thread_id, approved, reason))
            return LocalTaskRunResult(
                status=TaskStatus.COMPLETED,
                parts=[{"kind": "text", "text": "approved answer"}],
            )

    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = ApprovalRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
    )
    context = store.create_context(context_id="ctx-1")

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "delete pod"}],
            metadata={"executionMode": "task"},
        )
    )

    task = store.get_task(result.task_id)
    assert task is not None
    assert task.status == TaskStatus.INPUT_REQUIRED
    assert task.output_message_id is None

    resumed = core.resume_task(result.task_id, approved=True, reason="operator approved")

    assert resumed.status == TaskStatus.COMPLETED
    assert resumed.output_message_id is not None
    assert runner.resume_calls == [(task.runtime_thread_id, True, "operator approved")]
    assert store.get_message(resumed.output_message_id).parts == [{"kind": "text", "text": "approved answer"}]
    assert [event.type for event in store.list_task_events(result.task_id)] == [
        "task_created",
        "task_started",
        "task_interrupted",
        "task_resumed",
        "task_started",
        "task_artifact_created",
        "task_completed",
    ]


def test_main_agent_core_unknown_context_is_rejected(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(store=store, local_message_responder=FakeResponder())

    try:
        core.handle_message(
            MainAgentRequest(
                context_id="ctx-missing",
                message_id="msg-user-1",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "hello"}],
                metadata={"executionMode": "message"},
            )
        )
    except ValueError as exc:
        assert str(exc) == "unknown context: ctx-missing"
    else:
        raise AssertionError("expected unknown context to fail")


def test_main_agent_core_remote_message_records_delegation_and_assistant_message(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        responses=[
            RemoteAgentSendResult(
                kind="message",
                context_id="remote-ctx-1",
                message_id="remote-msg-1",
                parts=[{"kind": "text", "text": "remote answer"}],
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        remote_agent_client=remote_client,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "delegate"}],
            metadata={"route": "remote_agent", "targetAgentId": "agent-child-1"},
        )
    )

    assert isinstance(result, RemoteAgentResult)
    assert result.target_agent_id == "agent-child-1"
    assert result.message_id is not None
    assert result.parts == [{"kind": "text", "text": "remote answer"}]
    assert remote_client.calls == [("agent-child-1", result.context_id, "msg-user-1")]
    messages = store.list_context_messages(result.context_id)
    assert [message.message_id for message in messages] == ["msg-user-1", result.message_id]
    assert messages[-1].metadata["remoteMessageId"] == "remote-msg-1"
    delegation = store.get_delegated_task(result.delegation_id)
    assert delegation is not None
    assert delegation.result_kind == "message"
    assert delegation.remote_agent_id == "agent-child-1"
    assert delegation.remote_context_id == "remote-ctx-1"
    assert delegation.remote_message_id == "remote-msg-1"


def test_main_agent_core_remote_task_records_proxy_task_and_delegation(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    remote_client = FakeRemoteAgentClient(
        responses=[
            RemoteAgentSendResult(
                kind="task",
                context_id="remote-ctx-1",
                task_id="remote-task-1",
                status="working",
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        remote_agent_client=remote_client,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "delegate"}],
            metadata={"route": "remote_agent", "targetAgentId": "agent-child-1"},
        )
    )

    assert isinstance(result, RemoteAgentResult)
    assert result.task_id is not None
    task = store.get_task(result.task_id)
    assert task is not None
    assert task.assigned_agent_id == "agent-child-1"
    assert task.status == TaskStatus.RUNNING
    events = store.list_task_events(task.task_id)
    assert [event.type for event in events] == ["task_delegated"]
    assert events[0].payload["remote_task_id"] == "remote-task-1"
    delegation = store.get_delegated_task(result.delegation_id)
    assert delegation is not None
    assert delegation.result_kind == "task"
    assert delegation.local_task_id == task.task_id
    assert delegation.remote_task_id == "remote-task-1"


def test_main_agent_core_auto_routes_to_registered_agent_by_keyword(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.upsert_registered_agent(
        agent_id="agent-k8s",
        name="Kubernetes agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        metadata={"keywords": ["kubernetes"]},
    )
    remote_client = FakeRemoteAgentClient(
        responses=[
            RemoteAgentSendResult(
                kind="message",
                context_id="remote-ctx-1",
                message_id="remote-msg-1",
                parts=[{"kind": "text", "text": "k8s answer"}],
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        remote_agent_client=remote_client,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "check kubernetes pods"}],
            metadata={"executionMode": "auto"},
        )
    )

    assert isinstance(result, RemoteAgentResult)
    assert result.target_agent_id == "agent-k8s"
    assert remote_client.calls == [("agent-k8s", result.context_id, "msg-user-1")]
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.kind == RouteDecisionKind.REMOTE_AGENT
    assert decision.reason == "auto route matched registered agent keyword: kubernetes"
    assert decision.metadata == {
        "source": "guardrail",
        "executionMode": "auto",
        "keyword": "kubernetes",
        "legacySource": "keyword_match",
    }


def test_main_agent_core_auto_routes_to_registered_agent_by_skill_tag(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.upsert_registered_agent(
        agent_id="agent-sql",
        name="SQL agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        card_json={"skills": [{"id": "sqlite-debug", "tags": ["sqlite", "database"]}]},
    )
    remote_client = FakeRemoteAgentClient(
        responses=[
            RemoteAgentSendResult(
                kind="message",
                context_id="remote-ctx-1",
                message_id="remote-msg-1",
                parts=[{"kind": "text", "text": "sql answer"}],
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        remote_agent_client=remote_client,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "debug sqlite trace events"}],
            metadata={"executionMode": "auto"},
        )
    )

    assert isinstance(result, RemoteAgentResult)
    assert result.target_agent_id == "agent-sql"
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.metadata == {
        "source": "guardrail",
        "executionMode": "auto",
        "keyword": "sqlite",
        "legacySource": "keyword_match",
    }


def test_main_agent_core_auto_fallback_routes_to_local_message_without_task(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    responder = FakeResponder()
    runner = FakeTaskRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=responder,
        local_task_runner=runner,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "tell me a joke"}],
            metadata={"executionMode": "auto"},
        )
    )

    assert isinstance(result, LocalMessageResult)
    assert result.parts == [{"kind": "text", "text": "model answer"}]
    assert store.list_context_tasks(result.context_id) == []
    assert runner.calls == []
    assert len(responder.calls) == 1
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decision.reason == "auto fallback to local message"
    assert decision.metadata == {"source": "fallback", "executionMode": "auto"}


def test_main_agent_core_auto_hard_signal_continues_active_task(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = FakeTaskRunner()
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
    )
    context = store.create_context(context_id="ctx-1")
    store.append_message(
        message_id="msg-original",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s status"}],
    )
    active_task = store.create_task(
        task_id="task-active",
        context_id=context.context_id,
        input_message_id="msg-original",
        runtime_thread_id="thread-active",
        status=TaskStatus.RUNNING,
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=context.context_id,
            message_id="msg-user-2",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "continue"}],
            metadata={"executionMode": "auto", "taskId": active_task.task_id},
        )
    )

    assert isinstance(result, LocalTaskResult)
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.confidence == 1.0
    assert decision.metadata == {
        "source": "hard_signal",
        "executionMode": "auto",
        "taskId": "task-active",
        "signal": "active_task",
    }


def test_main_agent_core_auto_uses_router_model_for_local_task(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    runner = FakeTaskRunner()
    router_model = FakeRouterModel(
        decisions=[
            RouterModelDecision(
                kind=RouteDecisionKind.LOCAL_TASK,
                reason="Needs Kubernetes inspection through tools.",
                confidence=0.91,
                metadata={"modelReason": "Needs Kubernetes inspection through tools."},
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=runner,
        router=DefaultMainAgentRouter(router_model=router_model),
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "检查 k8s 状态"}],
            metadata={"executionMode": "auto"},
        )
    )

    assert isinstance(result, LocalTaskResult)
    assert len(runner.calls) == 1
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.confidence == 0.91
    assert decision.metadata == {
        "source": "model",
        "executionMode": "auto",
        "modelReason": "Needs Kubernetes inspection through tools.",
    }


def test_main_agent_core_auto_router_model_low_confidence_falls_back_to_message(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    router_model = FakeRouterModel(
        decisions=[
            RouterModelDecision(
                kind=RouteDecisionKind.LOCAL_TASK,
                reason="Possibly needs tools.",
                confidence=0.4,
                metadata={
                    "source": "fallback",
                    "fallbackReason": "low_confidence",
                    "modelRoute": "local_task",
                    "modelReason": "Possibly needs tools.",
                    "confidenceThreshold": 0.65,
                },
            )
        ]
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=FakeResponder(),
        local_task_runner=FakeTaskRunner(),
        router=DefaultMainAgentRouter(router_model=router_model),
    )

    result = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-user-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "maybe check something"}],
            metadata={"executionMode": "auto"},
        )
    )

    assert isinstance(result, LocalMessageResult)
    decision = store.get_route_decision(result.route_decision_id)
    assert decision.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decision.metadata["source"] == "fallback"
    assert decision.metadata["fallbackReason"] == "low_confidence"


def test_direct_model_router_model_parses_json_and_validates_remote_agent(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.upsert_registered_agent(
        agent_id="agent-k8s",
        name="Kubernetes agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
    )
    model = FakeLangGraphModelClient(
        contents=[
            '{"route":"remote_agent","confidence":0.88,"reason":"Kubernetes specialist owns this.","targetAgentId":"agent-k8s"}'
        ]
    )
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=store.list_registered_agents(enabled_only=True),
    )

    assert decision.kind == RouteDecisionKind.REMOTE_AGENT
    assert decision.target_agent_id == "agent-k8s"
    assert decision.confidence == 0.88
    assert decision.metadata == {
        "source": "model",
        "model": "router-small",
        "modelReason": "Kubernetes specialist owns this.",
    }


def test_direct_model_router_model_uses_raw_json_client_without_agent_action_parser():
    raw_client = FakeRouterRawJsonClient(
        contents=[
            '{"route":"local_message","confidence":0.99,"reason":"Simple chat request.","targetAgentId":null}'
        ]
    )
    router_model = DirectModelRouterModelClient(raw_json_client=raw_client, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "tell me a joke"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decision.confidence == 0.99
    assert decision.reason == "Simple chat request."
    assert decision.metadata == {
        "source": "model",
        "model": "router-small",
        "modelReason": "Simple chat request.",
    }
    assert raw_client.calls


def test_direct_model_router_model_repairs_classifier_payload_with_tool_requirement():
    model = FakeLangGraphModelClient(
        contents=[
            '{"classification":"infrastructure_monitoring","intent":"check_kubernetes_status","requires_tool":true}'
        ]
    )
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s status"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.confidence == 0.75
    assert decision.metadata["schemaRepair"] == "classifier_payload"
    assert decision.metadata["model"] == "router-small"


def test_direct_model_router_model_repairs_classifier_payload_with_tool_access_alias():
    model = FakeLangGraphModelClient(
        contents=['{"classification":"infrastructure_monitoring","requires_tool_access":true}']
    )
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s status"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.metadata["schemaRepair"] == "classifier_payload"


def test_direct_model_router_model_repairs_classifier_payload_with_tool_name():
    model = FakeLangGraphModelClient(
        contents=['{"classification":"infrastructure_monitoring","requires_tool":"k8s_status_checker"}']
    )
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s status"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.metadata["schemaRepair"] == "classifier_payload"


def test_direct_model_router_model_repairs_classifier_payload_for_joke_request():
    model = FakeLangGraphModelClient(
        contents=['{"classification":"entertainment_request","intent":"joke_request","category":"humor"}']
    )
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "tell me a joke"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decision.confidence == 0.75
    assert decision.metadata["schemaRepair"] == "classifier_payload"


def test_direct_model_router_model_repairs_plain_classifier_label_for_joke_request():
    model = FakeLangGraphModelClient(contents=["user_request_joke"])
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "tell me a joke"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert decision.metadata["schemaRepair"] == "classifier_payload"


def test_direct_model_router_model_repairs_plain_route_label():
    model = FakeLangGraphModelClient(contents=["local_task"])
    router_model = DirectModelRouterModelClient(model, model_name="router-small")
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "check k8s status"}],
        metadata={"executionMode": "auto"},
    )

    decision = router_model.classify(
        request=request,
        messages=[
            MessageRecord(
                message_id="msg-user-1",
                context_id="ctx-1",
                role=MessageRole.USER,
                parts=request.parts,
                task_id=None,
                metadata={},
                created_at="2026-06-08T00:00:00Z",
            )
        ],
        registered_agents=[],
    )

    assert decision.kind == RouteDecisionKind.LOCAL_TASK
    assert decision.metadata["schemaRepair"] == "classifier_payload"


def test_main_agent_core_remote_route_requires_registered_enabled_agent_and_client(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(store=store, local_message_responder=FakeResponder())

    try:
        core.handle_message(
            MainAgentRequest(
                context_id=None,
                message_id="msg-user-1",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "delegate"}],
                metadata={"route": "remote_agent"},
            )
        )
    except ValueError as exc:
        assert str(exc) == "remote_agent route requires metadata.targetAgentId"
    else:
        raise AssertionError("expected missing target to fail")
    assert store.list_contexts() == []

    store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        enabled=False,
    )
    try:
        core.handle_message(
            MainAgentRequest(
                context_id=None,
                message_id="msg-user-2",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "delegate"}],
                metadata={"route": "remote_agent", "targetAgentId": "agent-child-1"},
            )
        )
    except ValueError as exc:
        assert str(exc) == "registered agent is disabled: agent-child-1"
    else:
        raise AssertionError("expected disabled agent to fail")
    assert store.list_contexts() == []


def test_main_agent_core_invalid_explicit_execution_mode_does_not_append_message(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    core = MainAgentCore(store=store, local_message_responder=FakeResponder())

    try:
        core.handle_message(
            MainAgentRequest(
                context_id=None,
                message_id="msg-user-1",
                role=MessageRole.USER,
                parts=[{"kind": "text", "text": "hello"}],
                metadata={"executionMode": "invalid"},
            )
        )
    except ValueError as exc:
        assert str(exc) == "unsupported executionMode: invalid"
    else:
        raise AssertionError("expected unsupported execution mode to fail")

    assert store.list_contexts() == []

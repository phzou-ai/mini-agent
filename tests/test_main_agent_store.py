from __future__ import annotations

import threading

import pytest

from vermay.main_agent import (
    InvalidLocalTaskTransitionError,
    LocalMessageResult,
    LocalTaskResult,
    MainAgentRequest,
    MainAgentStore,
    MessageIngressOutcomeKind,
    MessageIngressState,
    MessageRole,
    QueuedTaskExecutionKind,
    RemoteAgentResult,
    RouteDecisionKind,
    TaskStatus,
)
from vermay.main_agent.projection import A2ATaskState, task_status_to_a2a_state
from vermay.main_agent.context import recent_messages
from vermay.storage import AgentStore


def test_main_agent_store_persists_context_message_route_task_event_artifact(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))

    context = store.create_context(context_id="ctx-1", title="Agent Workbench")
    user_message = store.append_message(
        message_id="msg-user-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run diagnostics"}],
        metadata={"source": "test"},
    )
    decision = store.record_route_decision(
        decision_id="route-1",
        context_id=context.context_id,
        message_id=user_message.message_id,
        kind=RouteDecisionKind.LOCAL_TASK,
        reason="metadata requested local task",
    )
    task = store.create_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=user_message.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.QUEUED,
        model={"provider": "fake"},
    )
    event = store.append_task_event(
        task_id=task.task_id,
        type="task_queued",
        status=TaskStatus.QUEUED,
        payload={"channel": "a2a"},
    )
    assistant_message = store.append_message(
        message_id="msg-agent-1",
        context_id=context.context_id,
        role=MessageRole.AGENT,
        parts=[{"kind": "text", "text": "done"}],
        task_id=task.task_id,
    )
    updated_task = store.set_task_output_message(task.task_id, assistant_message.message_id)
    artifact = store.upsert_artifact(
        artifact_id="artifact-1",
        task_id=task.task_id,
        context_id=context.context_id,
        parts=[{"kind": "text", "text": "done"}],
        metadata={"kind": "final_answer"},
    )

    reloaded_context = store.get_context("ctx-1")
    assert reloaded_context is not None
    assert reloaded_context.context_id == context.context_id
    assert reloaded_context.title == context.title
    assert store.get_message("msg-user-1") == user_message
    assert store.get_route_decision("route-1") == decision
    assert updated_task.output_message_id == "msg-agent-1"
    assert store.list_task_events("task-1") == [event]
    assert store.list_task_artifacts("task-1") == [artifact]
    assert [message.message_id for message in store.list_context_messages("ctx-1")] == [
        "msg-user-1",
        "msg-agent-1",
    ]


def test_main_agent_store_validates_and_records_local_task_transitions_atomically(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    context = store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-user-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run diagnostics"}],
    )
    task = store.create_local_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
    )

    queued = store.transition_local_task(task.task_id, TaskStatus.QUEUED)
    duplicate = store.transition_local_task(task.task_id, TaskStatus.QUEUED)

    assert queued.status == TaskStatus.QUEUED
    assert duplicate == queued
    assert [(event.type, event.status) for event in store.list_task_events(task.task_id)] == [
        ("task_created", TaskStatus.CREATED),
        ("task_queued", TaskStatus.QUEUED),
    ]

    with pytest.raises(InvalidLocalTaskTransitionError, match="queued -> completed"):
        store.transition_local_task(task.task_id, TaskStatus.COMPLETED)

    unchanged = store.get_task(task.task_id)
    assert unchanged is not None
    assert unchanged.status == TaskStatus.QUEUED
    assert [(event.type, event.status) for event in store.list_task_events(task.task_id)] == [
        ("task_created", TaskStatus.CREATED),
        ("task_queued", TaskStatus.QUEUED),
    ]


def test_main_agent_store_claims_one_durable_queued_execution_with_worker_start(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    context = store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-user-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run diagnostics"}],
    )
    task = store.create_local_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
    )
    queued = store.transition_local_task(task.task_id, TaskStatus.QUEUED)
    command = store.enqueue_task_execution(
        task.task_id,
        kind=QueuedTaskExecutionKind.INITIAL,
        runtime_thread_id=queued.runtime_thread_id,
    )

    claimed = store.claim_queued_task_execution(task.task_id)

    assert claimed is not None
    running, claimed_command = claimed
    assert running.status == TaskStatus.RUNNING
    assert claimed_command == command
    assert store.get_queued_task_execution(task.task_id) is None
    assert store.claim_queued_task_execution(task.task_id) is None
    assert [(event.type, event.status) for event in store.list_task_events(task.task_id)] == [
        ("task_created", TaskStatus.CREATED),
        ("task_queued", TaskStatus.QUEUED),
        ("task_started", TaskStatus.RUNNING),
    ]

def test_main_agent_store_wait_for_task_events_is_notified_by_new_event(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run"}],
    )
    task = store.create_task(
        task_id="task-1",
        context_id="ctx-1",
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.QUEUED,
    )
    waiting = threading.Event()
    received = []

    def wait_for_event() -> None:
        waiting.set()
        received.extend(
            store.wait_for_task_events(
                task.task_id,
                after_event_id=0,
                timeout_seconds=2,
            )
        )

    thread = threading.Thread(target=wait_for_event)
    thread.start()
    assert waiting.wait(timeout=2)
    event = store.append_task_event(
        task_id=task.task_id,
        type="task_started",
        status=TaskStatus.RUNNING,
    )
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert received == [event]


def test_main_agent_store_message_idempotency_and_conflict(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    first = store.append_message(
        message_id="msg-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    duplicate = store.append_message(
        message_id="msg-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
    )

    assert duplicate == first
    with pytest.raises(ValueError, match="message conflict"):
        store.append_message(
            message_id="msg-1",
            context_id="ctx-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "different"}],
        )


def test_main_agent_store_reserves_and_resolves_message_ingress_once(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    context = store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
        metadata={"executionMode": "message"},
    )

    first, created = store.reserve_message_ingress(
        message_id=message.message_id,
        context_id=context.context_id,
        request_fingerprint="fingerprint-1",
    )
    duplicate, duplicate_created = store.reserve_message_ingress(
        message_id=message.message_id,
        context_id=context.context_id,
        request_fingerprint="fingerprint-1",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate == first
    assert first.state == MessageIngressState.IN_PROGRESS

    decision = store.record_route_decision(
        decision_id="route-1",
        context_id=context.context_id,
        message_id=message.message_id,
        kind=RouteDecisionKind.LOCAL_MESSAGE,
        reason="test",
    )
    store.set_message_ingress_route_decision(message.message_id, route_decision_id=decision.decision_id)
    output = store.append_message(
        message_id="msg-agent-1",
        context_id=context.context_id,
        role=MessageRole.AGENT,
        parts=[{"kind": "text", "text": "done"}],
    )
    resolved = store.resolve_message_ingress(
        message.message_id,
        outcome_kind=MessageIngressOutcomeKind.MESSAGE,
        outcome_id=output.message_id,
    )

    assert resolved.state == MessageIngressState.RESOLVED
    assert resolved.route_decision_id == decision.decision_id
    assert resolved.outcome_kind == MessageIngressOutcomeKind.MESSAGE
    assert resolved.outcome_id == output.message_id


def test_main_agent_store_pending_continuation_is_not_derived_from_events(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    context = store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-user-1",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "delete pod"}],
    )
    task = store.create_task(
        task_id="task-1",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.AUTH_REQUIRED,
    )
    input_request = {"kind": "approval_required", "prompt": "Approve deletion?"}
    store.set_pending_continuation(
        task.task_id,
        kind="approval_required",
        input_request=input_request,
    )
    store.append_task_event(
        task_id=task.task_id,
        type="task_interrupted",
        status=TaskStatus.AUTH_REQUIRED,
        payload={"input_request": input_request},
    )
    store.append_task_event(
        task_id=task.task_id,
        type="task_resumed",
        status=TaskStatus.AUTH_REQUIRED,
        payload={"approved": True},
    )

    assert store.get_pending_input_request(task.task_id) == input_request
    consumed = store.consume_pending_continuation(task.task_id, expected_kind="approval_required")

    assert consumed.input_request == input_request
    assert store.get_pending_input_request(task.task_id) is None


def test_main_agent_store_recent_messages_are_bounded_and_ordered(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    for index in range(5):
        store.append_message(
            message_id=f"msg-{index}",
            context_id="ctx-1",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": str(index)}],
        )

    assert [message.message_id for message in store.list_context_messages("ctx-1", limit=3)] == [
        "msg-2",
        "msg-3",
        "msg-4",
    ]


def test_main_agent_store_persists_context_sequence_and_task_input_cut(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    history = store.append_message(
        message_id="msg-history",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "history"}],
    )
    task_input = store.append_message(
        message_id="msg-task-input",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run task"}],
    )
    task = store.create_task(
        task_id="task-1",
        context_id="ctx-1",
        input_message_id=task_input.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.QUEUED,
    )
    later = store.append_message(
        message_id="msg-later",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "later input"}],
    )

    assert [history.context_sequence, task_input.context_sequence, later.context_sequence] == [1, 2, 3]
    assert task.input_context_sequence == task_input.context_sequence
    assert [message.message_id for message in store.list_task_input_messages(task.task_id)] == [
        "msg-history",
        "msg-task-input",
    ]

    store.store.execute("UPDATE messages SET created_at=? WHERE message_id=?", ("2099-01-01T00:00:00+00:00", "msg-history"))
    assert [message.message_id for message in store.list_context_messages("ctx-1")] == [
        "msg-history",
        "msg-task-input",
        "msg-later",
    ]


def test_recent_messages_ignores_task_events(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    user = store.append_message(
        message_id="msg-user-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run"}],
    )
    task = store.create_task(
        task_id="task-1",
        context_id="ctx-1",
        input_message_id=user.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.RUNNING,
    )
    store.append_task_event(
        task_id=task.task_id,
        type="tool_output",
        status=TaskStatus.RUNNING,
        payload={"text": "raw tool trace"},
    )
    store.append_message(
        message_id="msg-agent-1",
        context_id="ctx-1",
        role=MessageRole.AGENT,
        parts=[{"kind": "text", "text": "answer"}],
        task_id=task.task_id,
    )

    assert [message.message_id for message in recent_messages(store, "ctx-1", limit=10)] == [
        "msg-user-1",
        "msg-agent-1",
    ]


def test_main_agent_store_delete_terminal_context_rejects_active_tasks(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-user-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "run"}],
    )
    store.record_route_decision(
        decision_id="route-1",
        context_id="ctx-1",
        message_id=message.message_id,
        kind=RouteDecisionKind.LOCAL_TASK,
        reason="test",
    )
    store.create_task(
        task_id="task-1",
        context_id="ctx-1",
        input_message_id=message.message_id,
        runtime_thread_id="thread-1",
        status=TaskStatus.RUNNING,
    )
    store.append_task_event(task_id="task-1", type="task_started", status=TaskStatus.RUNNING)

    with pytest.raises(ValueError, match="non-terminal"):
        store.delete_terminal_context("ctx-1")

    still_running = store.get_task("task-1")
    assert still_running is not None
    assert still_running.status == TaskStatus.RUNNING

    store.transition_local_task("task-1", TaskStatus.COMPLETED)
    result = store.delete_terminal_context("ctx-1")

    assert result.deleted_tasks == 1
    assert result.deleted_messages == 1
    assert result.deleted_task_events == 2
    assert result.deleted_route_decisions == 1
    assert store.get_context("ctx-1") is None


def test_main_agent_store_updates_context_title_without_reordering_timestamp(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    context = store.create_context(context_id="ctx-1", title="Original")

    updated = store.update_context_title("ctx-1", title="Renamed")

    assert updated is not None
    assert updated.title == "Renamed"
    assert updated.updated_at == context.updated_at


def test_main_agent_store_registered_agents_and_delegations(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    registered = store.upsert_registered_agent(
        agent_id="agent-child-1",
        name="Child Agent",
        card_url="http://127.0.0.1:9001/.well-known/agent-card.json",
        card_json={"name": "Child Agent"},
        enabled=True,
        metadata={"role": "research"},
    )
    store.upsert_registered_agent(
        agent_id="agent-disabled",
        name="Disabled Agent",
        card_url="http://127.0.0.1:9002/.well-known/agent-card.json",
        enabled=False,
    )
    store.create_context(context_id="ctx-1")
    message = store.append_message(
        message_id="msg-user-1",
        context_id="ctx-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "delegate"}],
    )
    decision = store.record_route_decision(
        decision_id="route-1",
        context_id="ctx-1",
        message_id=message.message_id,
        kind=RouteDecisionKind.REMOTE_AGENT,
        target_agent_id=registered.agent_id,
        reason="explicit route",
    )
    task = store.create_task(
        task_id="task-proxy-1",
        context_id="ctx-1",
        input_message_id=message.message_id,
        runtime_thread_id="thread-proxy-1",
        assigned_agent_id=registered.agent_id,
        status=TaskStatus.RUNNING,
    )

    delegation = store.create_delegated_task(
        delegation_id="delegate-1",
        context_id="ctx-1",
        input_message_id=message.message_id,
        route_decision_id=decision.decision_id,
        remote_agent_id=registered.agent_id,
        local_task_id=task.task_id,
        remote_task_id="remote-task-1",
        remote_context_id="remote-ctx-1",
        result_kind="task",
        status="working",
        metadata={"source": "test"},
    )

    assert store.get_registered_agent("agent-child-1") == registered
    assert [agent.agent_id for agent in store.list_registered_agents(enabled_only=True)] == [
        "agent-child-1",
    ]
    assert store.get_delegated_task("delegate-1") == delegation
    assert store.get_delegated_task_by_local_task_id("task-proxy-1") == delegation
    assert store.list_context_delegations("ctx-1") == [delegation]
    updated_delegation = store.update_delegated_task_status(
        "delegate-1",
        status="completed",
        metadata={"source": "test", "remoteStatus": "completed"},
    )
    assert updated_delegation.status == "completed"
    assert updated_delegation.metadata["remoteStatus"] == "completed"


def test_main_agent_task_status_projection_uses_a2a_names():
    assert task_status_to_a2a_state(TaskStatus.CREATED) == A2ATaskState.SUBMITTED
    assert task_status_to_a2a_state(TaskStatus.QUEUED) == A2ATaskState.SUBMITTED
    assert task_status_to_a2a_state(TaskStatus.RUNNING) == A2ATaskState.WORKING
    assert task_status_to_a2a_state(TaskStatus.CANCEL_REQUESTED) == A2ATaskState.WORKING
    assert task_status_to_a2a_state(TaskStatus.INPUT_REQUIRED) == A2ATaskState.INPUT_REQUIRED
    assert task_status_to_a2a_state(TaskStatus.AUTH_REQUIRED) == A2ATaskState.AUTH_REQUIRED
    assert task_status_to_a2a_state(TaskStatus.COMPLETED) == A2ATaskState.COMPLETED
    assert task_status_to_a2a_state(TaskStatus.CANCELED) == A2ATaskState.CANCELED
    assert task_status_to_a2a_state(TaskStatus.FAILED) == A2ATaskState.FAILED


def test_main_agent_request_and_result_types_are_protocol_independent():
    request = MainAgentRequest(
        context_id=None,
        message_id="msg-user-1",
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "hello"}],
        metadata={"executionMode": "message"},
    )
    message_result = LocalMessageResult(
        kind=RouteDecisionKind.LOCAL_MESSAGE,
        context_id="ctx-1",
        message_id="msg-agent-1",
        input_message_id="msg-user-1",
        route_decision_id="route-1",
        parts=[{"kind": "text", "text": "hi"}],
    )
    task_result = LocalTaskResult(
        kind=RouteDecisionKind.LOCAL_TASK,
        context_id="ctx-1",
        task_id="task-1",
        input_message_id="msg-user-1",
        route_decision_id="route-1",
    )
    remote_result = RemoteAgentResult(
        kind=RouteDecisionKind.REMOTE_AGENT,
        context_id="ctx-1",
        input_message_id="msg-user-1",
        target_agent_id="agent-child-1",
        route_decision_id="route-2",
        delegation_id="delegate-1",
    )

    assert request.role == MessageRole.USER
    assert message_result.kind == RouteDecisionKind.LOCAL_MESSAGE
    assert task_result.kind == RouteDecisionKind.LOCAL_TASK
    assert remote_result.kind == RouteDecisionKind.REMOTE_AGENT

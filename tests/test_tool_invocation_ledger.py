from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import Field

from vermay_agent.langgraph_runtime import LangGraphAgentRuntime, ModelInvocation
from vermay_agent.main_agent import (
    DirectLangGraphLocalTaskRunner,
    MainAgentCore,
    MainAgentRequest,
    MainAgentStore,
    MessageRole,
    TaskStatus,
    ToolInvocationApprovalStatus,
    ToolInvocationStatus,
)
from vermay_agent.main_agent.invocation_ledger import MainAgentToolInvocationLedger
from vermay_agent.permission import PermissionGate
from vermay_agent.storage import AgentStore
from vermay_agent.tool_metadata import ApprovalPolicy, ExecutionScope, SideEffectLevel, ToolCategory
from vermay_agent.tool_registry import ToolRegistry
from vermay_agent.tooling import ToolArgs, structured_tool


class WriteArgs(ToolArgs):
    target: str = Field(description="Target to update.")


@dataclass
class ScriptedModel:
    responses: list[AIMessage]
    calls: list[list] = field(default_factory=list)

    def invoke(self, messages, tools):
        self.calls.append(messages)
        return ModelInvocation(message=self.responses.pop(0))


@dataclass
class NoopResponder:
    def respond(self, messages):
        del messages
        return [{"kind": "text", "text": "unused"}]


def _write_tool(calls: list[str], *, approval_required: bool = False):
    return structured_tool(
        func=lambda target: calls.append(target) or {"updated": target},
        name="apply_change",
        description="Apply a remote change.",
        args_schema=WriteArgs,
        dangerous=approval_required,
        category=ToolCategory.KUBERNETES,
        execution_scope=ExecutionScope.REMOTE,
        read_only=False,
        side_effect_level=SideEffectLevel.DESTRUCTIVE if approval_required else SideEffectLevel.REMOTE,
        approval_policy=(
            ApprovalPolicy.APPROVAL_REQUIRED if approval_required else ApprovalPolicy.AUTO
        ),
        destructive=approval_required,
    )


def _create_running_task(store: MainAgentStore, *, thread_id: str = "thread-ledger"):
    context = store.create_context(context_id="ctx-ledger")
    message = store.append_message(
        message_id="msg-ledger",
        context_id=context.context_id,
        role=MessageRole.USER,
        parts=[{"kind": "text", "text": "apply change"}],
    )
    task = store.create_local_task(
        task_id="task-ledger",
        context_id=context.context_id,
        input_message_id=message.message_id,
        runtime_thread_id=thread_id,
    )
    store.transition_local_task(task.task_id, TaskStatus.QUEUED)
    return store.transition_local_task(task.task_id, TaskStatus.RUNNING)


def _create_prepared_invocation(
    store: MainAgentStore,
    *,
    approval_required: bool = False,
):
    task = _create_running_task(store)
    return store.create_or_get_tool_invocation(
        invocation_id="inv-ledger",
        task_id=task.task_id,
        context_id=task.context_id,
        runtime_thread_id=task.runtime_thread_id,
        loop_index=1,
        tool_call_id="call-ledger",
        tool_name="apply_change",
        normalized_arguments={"target": "pod-a"},
        arguments_digest="digest-ledger",
        capability={"sideEffectLevel": "remote"},
        side_effect_level="remote",
        idempotency_key=None,
        approval_required=approval_required,
    )


def test_tool_invocation_store_binds_approval_and_result_artifact(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    prepared = _create_prepared_invocation(store, approval_required=True)

    assert prepared.status == ToolInvocationStatus.PREPARED
    assert prepared.approval_status == ToolInvocationApprovalStatus.PENDING

    with pytest.raises(ValueError, match="approval does not match"):
        store.resolve_tool_invocation_approval(
            task_id=prepared.task_id,
            invocation_id=prepared.invocation_id,
            tool_name="other_tool",
            arguments_digest=prepared.arguments_digest,
            approved=True,
            reason="operator approved",
        )

    approved = store.resolve_tool_invocation_approval(
        task_id=prepared.task_id,
        invocation_id=prepared.invocation_id,
        tool_name=prepared.tool_name,
        arguments_digest=prepared.arguments_digest,
        approved=True,
        reason="operator approved",
    )
    running = store.begin_tool_invocation(approved.invocation_id)
    completed = store.complete_tool_invocation_success(
        invocation_id=running.invocation_id,
        artifact_parts=[{"kind": "text", "text": "updated pod-a"}],
        artifact_metadata={"kind": "tool_invocation_result"},
    )

    assert completed.status == ToolInvocationStatus.SUCCEEDED
    assert completed.approval_status == ToolInvocationApprovalStatus.APPROVED
    assert completed.result_artifact_id == "inv-ledger:result"
    assert store.list_task_artifacts(prepared.task_id)[0].artifact_id == completed.result_artifact_id
    assert store.list_task_events(prepared.task_id)[-1].payload["invocation_id"] == prepared.invocation_id


def test_ledger_blocks_a_duplicate_succeeded_effect_within_one_task(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    task = _create_running_task(store)
    ledger = MainAgentToolInvocationLedger(store)
    metadata = _write_tool([], approval_required=False).metadata
    first = ledger.prepare(
        runtime_thread_id=task.runtime_thread_id,
        loop_index=1,
        tool_call={"name": "apply_change", "args": {"target": "pod-a"}, "id": "call-1"},
        tool_metadata=metadata,
        approval_required=False,
    )
    assert first is not None
    assert ledger.begin_execution(first.invocation_id).execute is True
    ledger.finish_execution(
        first.invocation_id,
        response=ToolMessage(content="updated", name="apply_change", tool_call_id="call-1"),
    )

    duplicate = ledger.prepare(
        runtime_thread_id=task.runtime_thread_id,
        loop_index=2,
        tool_call={"name": "apply_change", "args": {"target": "pod-a"}, "id": "call-2"},
        tool_metadata=metadata,
        approval_required=False,
    )

    assert duplicate is not None
    assert duplicate.execution_blocked is True
    assert duplicate.status == ToolInvocationStatus.SUCCEEDED.value


def test_ledger_marks_a_structured_failed_write_result_uncertain(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    prepared = _create_prepared_invocation(store)
    ledger = MainAgentToolInvocationLedger(store)

    assert ledger.begin_execution(prepared.invocation_id).execute is True
    ledger.finish_execution(
        prepared.invocation_id,
        response=ToolMessage(
            content=(
                '{"ok":false,"error_code":"execution_canceled",'
                '"error_category":"execution_canceled","stderr":"SSH command canceled"}'
            ),
            name="apply_change",
            tool_call_id="call-ledger",
        ),
    )

    invocation = store.get_tool_invocation(prepared.invocation_id)
    assert invocation is not None
    assert invocation.status == ToolInvocationStatus.UNCERTAIN
    assert invocation.error_code == "execution_canceled"
    assert invocation.result_artifact_id is None


def test_langgraph_runtime_records_non_read_only_tool_execution(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    task = _create_running_task(store)
    calls: list[str] = []
    tool = _write_tool(calls)
    registry = ToolRegistry()
    registry.register(tool)
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_change",
                        "args": {"target": "pod-a"},
                        "id": "call-write",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="change applied"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=registry.tools_for_model(),
        permission_gate=PermissionGate(registry),
        tool_invocation_recorder=MainAgentToolInvocationLedger(store),
    )

    result = runtime.start("apply change", thread_id=task.runtime_thread_id)

    assert result.status == "completed"
    assert result.final_answer == "change applied"
    assert calls == ["pod-a"]
    invocations = store.list_task_tool_invocations(task.task_id)
    assert len(invocations) == 1
    assert invocations[0].status == ToolInvocationStatus.SUCCEEDED
    assert invocations[0].result_artifact_id is not None


def test_core_binds_approval_resume_to_the_interrupted_invocation(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    calls: list[str] = []
    tool = _write_tool(calls, approval_required=True)
    registry = ToolRegistry()
    registry.register(tool)
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "apply_change",
                        "args": {"target": "pod-a"},
                        "id": "call-approval",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="approved change applied"),
        ]
    )
    runtime = LangGraphAgentRuntime(
        model=model,
        tools=registry.tools_for_model(),
        permission_gate=PermissionGate(registry),
        tool_invocation_recorder=MainAgentToolInvocationLedger(store),
    )
    core = MainAgentCore(
        store=store,
        local_message_responder=NoopResponder(),
        local_task_runner=DirectLangGraphLocalTaskRunner(runtime),
    )

    started = core.handle_message(
        MainAgentRequest(
            context_id=None,
            message_id="msg-approval",
            role=MessageRole.USER,
            parts=[{"kind": "text", "text": "apply the change"}],
            metadata={"executionMode": "task"},
        )
    )
    pending = store.get_pending_continuation(started.task_id)
    assert pending is not None
    assert pending.kind == "approval_required"
    assert pending.input_request["invocationId"].startswith("inv-")
    assert pending.input_request["toolName"] == "apply_change"
    assert pending.input_request["argumentsDigest"]

    completed = core.resume_task(started.task_id, approved=True, reason="operator approved")

    assert completed.status == TaskStatus.COMPLETED
    assert calls == ["pod-a"]
    invocation = store.list_task_tool_invocations(started.task_id)[0]
    assert invocation.approval_status == ToolInvocationApprovalStatus.APPROVED
    assert invocation.status == ToolInvocationStatus.SUCCEEDED


def test_startup_reconciliation_marks_running_tool_invocations_uncertain(tmp_path):
    store = MainAgentStore(AgentStore(tmp_path / "agent.sqlite"))
    prepared = _create_prepared_invocation(store)
    running = store.begin_tool_invocation(prepared.invocation_id)
    assert running.status == ToolInvocationStatus.RUNNING

    core = MainAgentCore(store=store, local_message_responder=NoopResponder())
    recovery = core.reconcile_startup()

    assert recovery.failed_task_ids == (prepared.task_id,)
    task = store.get_task(prepared.task_id)
    invocation = store.get_tool_invocation(prepared.invocation_id)
    assert task is not None and task.status == TaskStatus.FAILED
    assert invocation is not None and invocation.status == ToolInvocationStatus.UNCERTAIN
    assert invocation.error_code == "runtime_restart_interrupted"

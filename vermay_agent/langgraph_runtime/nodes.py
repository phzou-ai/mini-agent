from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import interrupt

from vermay_agent.errors import ModelProtocolError, ModelProviderError
from vermay_agent.execution_context import ExecutionContextRegistry
from vermay_agent.permission import PermissionGate
from vermay_agent.progress import ProgressReporter
from vermay_agent.trace import TraceLogger
from vermay_agent.types import ToolCall
from vermay_agent.tools.user_input import REQUEST_USER_INPUT_TOOL_NAME

from .execution import (
    ExecutionLimit,
    ExecutionStopReason,
    elapsed_seconds,
    loop_step_limit,
    model_call_limit,
    policy_from_state,
    tool_call_limit,
)
from .invocations import ToolInvocationRecorder, ToolInvocationReference
from .model_adapters import ModelInvocation
from .observations import normalize_tool_observation, observation_artifact_refs_for_tool_message
from .routing import latest_ai_message
from .state import AgentState


class ModelClient(Protocol):
    def invoke(self, messages: list[BaseMessage], tools: list[BaseTool]) -> ModelInvocation: ...


@dataclass
class GraphComponents:
    model: ModelClient
    tools: list[BaseTool]
    permission_gate: PermissionGate | None = None
    progress: ProgressReporter | None = None
    trace: TraceLogger | None = None
    tool_invocation_recorder: ToolInvocationRecorder | None = None
    execution_context_registry: ExecutionContextRegistry | None = None


def tool_call_wrapper(components: GraphComponents):
    """Bind one ToolNode call to execution controls and the effect ledger.

    The wrapper does not own process lifecycle. It gives a concrete capability
    adapter the current cancellation signal and the remaining execution budget,
    then lets the R1 ledger handle non-read-only effect identity and outcome.
    """

    def wrapper(request, execute):
        state = request.state if isinstance(request.state, dict) else {}
        tool_call = dict(request.tool_call)
        recorder = components.tool_invocation_recorder
        reference = None
        if recorder is None:
            cancellation = _cancellation_limit(components, state)
            if cancellation is not None:
                return _blocked_tool_message(tool_call, cancellation.message)
            return _execute_tool_with_context(components, state, None, request, execute)

        reference = _invocation_reference_for_tool_call(state, tool_call)
        cancellation = _cancellation_limit(components, state)
        if cancellation is not None:
            if reference is not None:
                recorder.cancel(reference.invocation_id, reason=cancellation.message)
            return _blocked_tool_message(tool_call, cancellation.message)
        if reference is None:
            reference = recorder.prepare(
                runtime_thread_id=state.get("runtime_thread_id"),
                loop_index=int(state.get("loop_index") or 1),
                tool_call=tool_call,
                tool_metadata=_tool_metadata_for_call(components, request.tool, tool_call),
                approval_required=False,
            )
        if reference is None:
            return _execute_tool_with_context(components, state, None, request, execute)
        if reference.execution_blocked:
            return _blocked_tool_message(tool_call, reference.blocked_reason)

        cancellation = _cancellation_limit(components, state)
        if cancellation is not None:
            recorder.cancel(reference.invocation_id, reason=cancellation.message)
            return _blocked_tool_message(tool_call, cancellation.message)
        execution = recorder.begin_execution(reference.invocation_id)
        if not execution.execute:
            return _blocked_tool_message(tool_call, execution.message)

        try:
            response = _execute_tool_with_context(components, state, reference, request, execute)
        except Exception as exc:
            recorder.mark_execution_uncertain(
                reference.invocation_id,
                error_code="tool_execution_exception",
                error_message=str(exc),
            )
            raise

        try:
            recorder.finish_execution(reference.invocation_id, response=response)
        except Exception as exc:
            recorder.mark_execution_uncertain(
                reference.invocation_id,
                error_code="tool_execution_recording_failed",
                error_message=str(exc),
            )
            raise
        return response

    return wrapper


def _execute_tool_with_context(
    components: GraphComponents,
    state: dict[str, Any],
    reference: ToolInvocationReference | None,
    request,
    execute,
):
    registry = components.execution_context_registry
    if registry is None:
        return execute(request)
    with registry.bind_tool_context(
        runtime_thread_id=_optional_string(state.get("runtime_thread_id")),
        invocation_id=reference.invocation_id if reference is not None else None,
        deadline_monotonic=_tool_execution_deadline(state),
    ):
        return execute(request)


def _tool_execution_deadline(state: dict[str, Any]) -> float | None:
    return _execution_deadline(state)


def _model_execution_deadline(state: dict[str, Any]) -> float | None:
    return _execution_deadline(state)


def _execution_deadline(state: dict[str, Any]) -> float | None:
    policy = policy_from_state(state)
    if policy.max_elapsed_seconds is None:
        return None
    remaining = policy.max_elapsed_seconds - elapsed_seconds(state)
    return time.monotonic() + max(0.0, remaining)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def call_model_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        limit = _cancellation_limit(components, state) or model_call_limit(state)
        if limit is not None:
            return _execution_stop_updates(components, state, limit)
        _emit_context_built(components, loop_index, state)
        _emit_progress(
            components,
            loop_index,
            "model_call_start",
        )
        try:
            invocation = _invoke_model_with_context(components, state)
        except ModelProviderError:
            # A task deadline can expire while its bounded HTTP request is in
            # flight. Preserve the execution-policy outcome when that is what
            # actually ended the call; otherwise propagate the provider error.
            limit = _cancellation_limit(components, state) or model_call_limit(state)
            if limit is not None:
                return _execution_stop_updates(components, state, limit)
            raise
        limit = _cancellation_limit(components, state) or model_call_limit(state)
        if limit is not None:
            return _execution_stop_updates(components, state, limit)
        response = invocation.message
        tool_calls = [_tool_call_payload(tool_call) for tool_call in response.tool_calls]
        _log_trace(
            components,
            "langgraph_model_response",
            {
                "loop": loop_index,
                "content": response.content,
                "tool_calls": tool_calls,
            },
        )

        declared_tool_name = _declared_unexecuted_tool_call(response, components.tools)
        if declared_tool_name is not None:
            raise ModelProtocolError(
                "Invalid model final answer: declared a call to tool "
                f"{declared_tool_name!r} without emitting a structured tool call.",
                provider="runtime",
            )

        updates: dict = {
            "messages": [response],
            "model_calls": int(state.get("model_calls") or 0) + 1,
        }
        if response.tool_calls:
            first_tool = tool_calls[0]["name"] if tool_calls else None
            _emit_progress(
                components,
                loop_index,
                "model_response",
                content=response.content,
                tool=first_tool,
            )
            for tool_call in tool_calls:
                _emit_progress(components, loop_index, "tool_call", payload=tool_call)
        else:
            updates["final_answer"] = str(response.content)
            updates["stop_reason"] = ExecutionStopReason.COMPLETED.value
            updates["stop_detail"] = {"source": "model_final_answer"}
            updates["stop_message"] = None
            _emit_progress(
                components,
                loop_index,
                "model_response",
                content=response.content,
                tool=None,
            )
            _emit_progress(components, loop_index, "final_answer")
        return updates

    return node


def _declared_unexecuted_tool_call(response: AIMessage, tools: list[BaseTool]) -> str | None:
    """Detect the narrow case where a final answer narrates a pending tool call.

    A final answer is allowed to mention a tool, for example when explaining
    that a capability is unavailable. It must not claim that it is *calling*
    a registered tool while returning no structured ``tool_calls``: ToolNode
    would never receive that call, yet the task would otherwise be completed.
    """

    if response.tool_calls or not isinstance(response.content, str):
        return None

    for tool in tools:
        if _final_answer_declares_tool_call(response.content, tool.name):
            return tool.name
    return None


def _final_answer_declares_tool_call(content: str, tool_name: str) -> bool:
    escaped_name = re.escape(tool_name)
    patterns = (
        rf"\bcalling\s+(?:the\s+)?tools?\s*:?\s*[`'\"“”]?{escaped_name}\b",
        rf"(?:正在)?调用\s*(?:工具\s*)?[`'\"“”]?{escaped_name}\b",
        # Some models leak their internal tool template into message content
        # instead of using the provider's structured tool-call channel. Treat
        # the envelope as an invalid final answer, but never parse or execute it.
        rf"<tool_calls?\b[^>]*>[\s\S]*?\binvoke\s+name\s*=\s*['\"]{escaped_name}['\"]",
    )
    return any(re.search(pattern, content, flags=re.IGNORECASE) is not None for pattern in patterns)


def check_permission_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        cancellation = _cancellation_limit(components, state)
        if cancellation is not None:
            return _execution_stop_updates(components, state, cancellation)
        ai_message = latest_ai_message(state["messages"])
        tool_calls = ai_message.tool_calls if ai_message else []
        if not tool_calls:
            permission = {"status": "denied", "reason": "missing tool call"}
            _emit_permission(components, loop_index, permission)
            return {"permission": permission}

        input_request = next(
            (
                raw_tool_call
                for raw_tool_call in tool_calls
                if raw_tool_call.get("name") == REQUEST_USER_INPUT_TOOL_NAME
            ),
            None,
        )
        if input_request is not None:
            permission = {
                "status": "input_required",
                "reason": "the model requires additional user input",
                "tool_call": input_request,
                "tool_calls": tool_calls,
            }
            _emit_permission(components, loop_index, permission)
            return {
                "permission": permission,
                **_pause_updates(
                    ExecutionStopReason.INPUT_REQUIRED,
                    message=str(permission["reason"]),
                    detail={"tool_call_id": str(input_request.get("id") or "")},
                ),
            }

        limit = tool_call_limit(state, requested_calls=len(tool_calls))
        if limit is not None:
            permission = {
                "status": "budget_exhausted",
                "reason": limit.message,
                "tool_calls": tool_calls,
            }
            _emit_permission(components, loop_index, permission)
            return {"permission": permission, **_execution_stop_updates(components, state, limit)}

        if components.permission_gate is None:
            decisions = []
            for raw_tool_call in tool_calls:
                decision = {
                    "status": "allowed",
                    "reason": "no permission gate configured",
                    "tool_call": raw_tool_call,
                }
                _attach_invocation_reference(
                    components,
                    state,
                    decision,
                    approval_required=False,
                )
                decisions.append(decision)
            permission = {
                "status": "allowed",
                "reason": "no permission gate configured",
                "tool_calls": tool_calls,
                "decisions": decisions,
            }
            _emit_permission(components, loop_index, permission)
            _emit_tool_execute_start(components, loop_index, tool_calls)
            return {"permission": permission}

        decisions: list[dict[str, Any]] = []
        for raw_tool_call in tool_calls:
            tool_call = _to_project_tool_call(raw_tool_call)
            decision = components.permission_gate.check(tool_call)
            status = "approval_required" if decision.requires_approval else "allowed" if decision.allowed else "denied"
            decisions.append(
                {
                    "status": status,
                    "reason": decision.reason,
                    "tool_call": raw_tool_call,
                    "decision": decision.decision,
                    "risk_level": decision.risk_level,
                    "approval_summary": decision.approval_summary,
                    "safe_argument_preview": decision.safe_argument_preview,
                    "policy_tags": decision.policy_tags,
                }
            )
            _attach_invocation_reference(
                components,
                state,
                decisions[-1],
                approval_required=decision.requires_approval,
            )

        denied = [decision for decision in decisions if decision["status"] == "denied"]
        if denied:
            first = denied[0]
            permission = {
                **first,
                "status": "denied",
                "tool_calls": tool_calls,
                "decisions": decisions,
            }
            _emit_permission(components, loop_index, permission)
            return {"permission": permission}

        approval_required = [decision for decision in decisions if decision["status"] == "approval_required"]
        if approval_required:
            first = approval_required[0]
            permission = {
                **first,
                "status": "approval_required",
                "tool_calls": tool_calls,
                "approval_required_tool_calls": [decision["tool_call"] for decision in approval_required],
                "decisions": decisions,
            }
            _emit_permission(components, loop_index, permission)
            return {
                "permission": permission,
                **_pause_updates(
                    ExecutionStopReason.APPROVAL_REQUIRED,
                    message=str(first.get("reason") or "approval required"),
                    detail={"tool_call_id": str(first["tool_call"].get("id") or "")},
                ),
            }

        permission = {
            "status": "allowed",
            "reason": "all tool calls allowed",
            "tool_calls": tool_calls,
            "decisions": decisions,
        }
        _emit_permission(components, loop_index, permission)
        _emit_tool_execute_start(components, loop_index, tool_calls)
        return {"permission": permission}

    return node


def _invoke_model_with_context(
    components: GraphComponents,
    state: AgentState,
) -> ModelInvocation:
    registry = components.execution_context_registry
    if registry is None:
        return components.model.invoke(messages=state["messages"], tools=components.tools)
    with registry.bind_model_context(
        runtime_thread_id=_optional_string(state.get("runtime_thread_id")),
        deadline_monotonic=_model_execution_deadline(state),
    ):
        return components.model.invoke(messages=state["messages"], tools=components.tools)


def _cancellation_limit(
    components: GraphComponents,
    state: dict[str, Any],
) -> ExecutionLimit | None:
    registry = components.execution_context_registry
    runtime_thread_id = _optional_string(state.get("runtime_thread_id"))
    if registry is None or not registry.cancellation_requested(runtime_thread_id):
        return None
    return ExecutionLimit(
        reason=ExecutionStopReason.CANCELED,
        message="Task cancellation was requested. Execution stopped at a safe boundary.",
        detail={"source": "control_plane_cancellation"},
    )


def reject_tool_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        approval = state.get("approval") or {}
        if approval.get("approved") is False:
            reason = approval.get("reason") or "approval rejected"
            _cancel_prepared_invocations(components, state, reason=reason)
            final_answer = f"Tool call rejected by approval: {reason}"
            _log_trace(components, "langgraph_tool_rejected", {"loop": loop_index, "reason": reason})
            _emit_progress(components, loop_index, "final_answer")
            return {
                "final_answer": final_answer,
                "stop_reason": ExecutionStopReason.POLICY_BLOCKED.value,
                "stop_detail": {"reason": reason, "source": "approval_rejected"},
                "stop_message": None,
            }

        permission = state.get("permission") or {}
        status = permission.get("status")
        reason = permission.get("reason") or "tool call rejected"
        _cancel_prepared_invocations(components, state, reason=reason)
        if status == "approval_required":
            final_answer = f"Tool call requires approval: {reason}"
        else:
            final_answer = f"Tool call rejected: {reason}"
        _log_trace(components, "langgraph_tool_rejected", {"loop": loop_index, "reason": reason, "status": status})
        _emit_progress(components, loop_index, "final_answer")
        return {
            "final_answer": final_answer,
            "stop_reason": ExecutionStopReason.POLICY_BLOCKED.value,
            "stop_detail": {"reason": reason, "source": "permission_denied"},
            "stop_message": None,
        }

    return node


def approval_required_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        permission = state.get("permission") or {}
        approval_decisions = [
            decision
            for decision in permission.get("decisions", [])
            if isinstance(decision, dict)
            and decision.get("status") == "approval_required"
            and isinstance(decision.get("tool_call"), dict)
        ]
        if not approval_decisions and isinstance(permission.get("tool_call"), dict):
            approval_decisions = [permission]

        approval = {"approved": False, "reason": "approval rejected"}
        for index, decision in enumerate(approval_decisions, start=1):
            tool_call = decision["tool_call"]
            reason = decision.get("reason") or "approval required"
            message = f"Approval required for tool call: {reason}"
            tool_name = tool_call.get("name")

            _emit_progress(components, loop_index, "approval_required", tool=tool_name)
            _log_trace(
                components,
                "langgraph_approval_required",
                {
                    "loop": loop_index,
                    "tool_call": _tool_call_payload(tool_call),
                    "permission": _permission_payload(permission),
                    "approval_index": index,
                    "approval_count": len(approval_decisions),
                    "message": message,
                },
            )

            resume = interrupt(
                {
                    "kind": "approval_required",
                    "tool_call": tool_call,
                    "tool_calls": [tool_call],
                    "permission": permission,
                    "approval_index": index,
                    "approval_count": len(approval_decisions),
                    "message": message,
                    **(
                        {
                            "invocationId": decision["invocation_id"],
                            "argumentsDigest": decision["arguments_digest"],
                            "toolName": str(tool_name or ""),
                        }
                        if isinstance(decision.get("invocation_id"), str)
                        and isinstance(decision.get("arguments_digest"), str)
                        else {}
                    ),
                }
            )
            if isinstance(resume, dict):
                approved = bool(resume.get("approved"))
                approval_reason = str(resume.get("reason") or ("approved" if approved else "approval rejected"))
            else:
                approved = bool(resume)
                approval_reason = "approved" if approved else "approval rejected"

            approval = {"approved": approved, "reason": approval_reason}
            _emit_progress(components, loop_index, "approval_resumed", tool=tool_name)
            _log_trace(
                components,
                "langgraph_approval_resumed",
                {
                    "loop": loop_index,
                    "approval": approval,
                    "approval_index": index,
                    "approval_count": len(approval_decisions),
                },
            )
            if not approved:
                return {"approval": approval, **_clear_pause_updates()}

        executable_tool_calls = permission.get("tool_calls")
        if approval["approved"] and isinstance(executable_tool_calls, list):
            _emit_tool_execute_start(components, loop_index, executable_tool_calls)
        return {"approval": approval, **_clear_pause_updates()}

    return node


def user_input_required_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        permission = state.get("permission") or {}
        tool_call = permission.get("tool_call") or {}
        arguments = dict(tool_call.get("args") or {})
        prompt = str(arguments.get("prompt") or "Please provide the information required to continue.")
        raw_choices = arguments.get("choices") or []
        choices = [str(choice) for choice in raw_choices if str(choice).strip()]
        tool_call_id = str(tool_call.get("id") or "request-user-input")

        request = {
            "kind": "user_input_required",
            "message": prompt,
            "prompt": prompt,
            "choices": choices,
            "inputSchema": {
                "type": "string",
                **({"enum": choices} if choices else {}),
            },
            "toolCallId": tool_call_id,
            "toolName": REQUEST_USER_INPUT_TOOL_NAME,
        }
        _emit_progress(components, loop_index, "user_input_required", tool=REQUEST_USER_INPUT_TOOL_NAME)
        _log_trace(components, "langgraph_user_input_required", {"loop": loop_index, **request})

        resume = interrupt(request)
        parts = resume.get("parts") if isinstance(resume, dict) else None
        text = _text_from_input_parts(parts)
        if not text and isinstance(resume, dict):
            text = str(resume.get("text") or "")
        if not text:
            text = str(resume or "")

        _emit_progress(components, loop_index, "user_input_resumed", tool=REQUEST_USER_INPUT_TOOL_NAME)
        _log_trace(
            components,
            "langgraph_user_input_resumed",
            {"loop": loop_index, "tool_call_id": tool_call_id, "text": text},
        )
        tool_messages: list[ToolMessage] = []
        for pending_tool_call in permission.get("tool_calls") or [tool_call]:
            pending_name = str(pending_tool_call.get("name") or "tool")
            pending_id = str(pending_tool_call.get("id") or pending_name)
            pending_content = (
                text
                if pending_name == REQUEST_USER_INPUT_TOOL_NAME
                else "Tool call deferred because additional user input was required."
            )
            tool_messages.append(
                ToolMessage(
                    content=pending_content,
                    tool_call_id=pending_id,
                    name=pending_name,
                )
            )
        return {"messages": tool_messages, **_clear_pause_updates()}

    return node


def record_tool_messages_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        tool_messages = _latest_tool_messages(state["messages"])
        observations: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for message in tool_messages:
            payload = _tool_message_payload(message)
            observation = normalize_tool_observation(
                message,
                loop_index=loop_index,
                artifact_refs=observation_artifact_refs_for_tool_message(state, message),
            )
            observations.append(observation)
            if not observation["ok"]:
                errors.append(
                    {
                        "tool_call_id": observation["tool_call_id"],
                        "tool_name": observation["tool_name"],
                        "category": observation["error_category"],
                        "retryable": observation["retryable"],
                    }
                )
            _emit_progress(
                components,
                loop_index,
                "tool_result",
                tool=payload["name"],
                ok=payload["ok"],
                exit_code=None,
                command_summary=None,
            )
            _emit_progress(
                components,
                loop_index,
                "observation",
                tool=payload["name"],
                ok=payload["ok"],
                summary=observation["summary"],
            )
            _log_trace(
                components,
                "langgraph_tool_message",
                {"loop": loop_index, **payload, "observation": observation},
            )

        updates: dict[str, Any] = {
            "observations": [*state.get("observations", []), *observations],
            "tool_calls": int(state.get("tool_calls") or 0) + len(observations),
            "failure_count": int(state.get("failure_count") or 0) + len(errors),
            "errors": [*state.get("errors", []), *errors],
        }
        policy = policy_from_state(state)
        failure_count = int(updates["failure_count"])
        if failure_count >= policy.max_failures:
            limit = ExecutionLimit(
                reason=ExecutionStopReason.REPEATED_FAILURE,
                message=f"Execution stopped after {failure_count} tool failure(s).",
                detail={
                    "limit": "max_failures",
                    "limit_value": policy.max_failures,
                    "observed_value": failure_count,
                },
            )
            updates.update(_execution_stop_updates(components, state, limit))
        return updates

    return node


def increment_loop_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        next_loop_index = state["loop_index"] + 1
        updates: dict[str, Any] = {
            "loop_index": next_loop_index,
            "permission": None,
        }
        limit = loop_step_limit(state, next_loop_index=next_loop_index)
        if limit is not None:
            updates.update(_execution_stop_updates(components, state, limit))
        return updates

    return node


def max_loops_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        limit = ExecutionLimit(
            reason=ExecutionStopReason.BUDGET_EXHAUSTED,
            message=f"Execution budget exhausted: maximum loop steps ({state['max_loops']}) reached.",
            detail={
                "limit": "max_loop_steps",
                "limit_value": state["max_loops"],
                "observed_value": max(0, state["loop_index"] - 1),
            },
        )
        _emit_progress(components, state["loop_index"], "max_steps_reached", max_steps=state["max_loops"])
        return _execution_stop_updates(components, state, limit)

    return node


def _execution_stop_updates(
    components: GraphComponents,
    state: AgentState,
    limit: ExecutionLimit,
) -> dict[str, Any]:
    loop_index = int(state.get("loop_index") or 1)
    _emit_progress(
        components,
        loop_index,
        "execution_stopped",
        reason=limit.reason.value,
        detail=limit.detail,
    )
    _log_trace(
        components,
        "langgraph_execution_stopped",
        {
            "loop": loop_index,
            "reason": limit.reason.value,
            "message": limit.message,
            "detail": limit.detail,
        },
    )
    return {
        "stop_reason": limit.reason.value,
        "stop_detail": limit.detail,
        "stop_message": limit.message,
    }


def _pause_updates(
    reason: ExecutionStopReason,
    *,
    message: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "stop_reason": reason.value,
        "stop_detail": detail,
        "stop_message": message,
    }


def _clear_pause_updates() -> dict[str, Any]:
    return {
        "stop_reason": None,
        "stop_detail": None,
        "stop_message": None,
    }


def _attach_invocation_reference(
    components: GraphComponents,
    state: AgentState,
    decision: dict[str, Any],
    *,
    approval_required: bool,
) -> None:
    recorder = components.tool_invocation_recorder
    tool_call = decision.get("tool_call")
    if recorder is None or not isinstance(tool_call, dict):
        return
    reference = recorder.prepare(
        runtime_thread_id=state.get("runtime_thread_id"),
        loop_index=int(state.get("loop_index") or 1),
        tool_call=tool_call,
        tool_metadata=_tool_metadata_for_call(components, None, tool_call),
        approval_required=approval_required,
    )
    if reference is None:
        return
    decision.update(
        {
            "invocation_id": reference.invocation_id,
            "arguments_digest": reference.arguments_digest,
            "invocation_status": reference.status,
            "invocation_execution_blocked": reference.execution_blocked,
        }
    )
    if reference.execution_blocked:
        decision["status"] = "denied"
        decision["reason"] = reference.blocked_reason or "Tool invocation will not be replayed automatically."
        decision["decision"] = "deny_replay"


def _invocation_reference_for_tool_call(
    state: dict[str, Any],
    tool_call: dict[str, Any],
) -> ToolInvocationReference | None:
    permission = state.get("permission")
    if not isinstance(permission, dict):
        return None
    decisions = permission.get("decisions")
    if not isinstance(decisions, list):
        return None
    for decision in decisions:
        if not isinstance(decision, dict) or not _same_tool_call(decision.get("tool_call"), tool_call):
            continue
        invocation_id = decision.get("invocation_id")
        arguments_digest = decision.get("arguments_digest")
        if not isinstance(invocation_id, str) or not isinstance(arguments_digest, str):
            continue
        return ToolInvocationReference(
            invocation_id=invocation_id,
            arguments_digest=arguments_digest,
            status=str(decision.get("invocation_status") or "prepared"),
            execution_blocked=bool(decision.get("invocation_execution_blocked")),
            blocked_reason=(str(decision["reason"]) if decision.get("invocation_execution_blocked") else None),
        )
    return None


def _same_tool_call(candidate: object, tool_call: dict[str, Any]) -> bool:
    if not isinstance(candidate, dict):
        return False
    candidate_id = candidate.get("id")
    tool_call_id = tool_call.get("id")
    if candidate_id is not None and tool_call_id is not None:
        return str(candidate_id) == str(tool_call_id)
    return (
        candidate.get("name") == tool_call.get("name")
        and dict(candidate.get("args") or {}) == dict(tool_call.get("args") or {})
    )


def _tool_metadata_for_call(
    components: GraphComponents,
    tool: BaseTool | None,
    tool_call: dict[str, Any],
) -> dict[str, Any] | None:
    tool_name = str(tool_call.get("name") or "")
    if components.permission_gate is not None:
        try:
            return components.permission_gate.registry.tool_metadata(tool_name).to_metadata()
        except KeyError:
            pass
    candidate = tool
    if candidate is None:
        candidate = next((item for item in components.tools if item.name == tool_name), None)
    metadata = getattr(candidate, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else None


def _blocked_tool_message(tool_call: dict[str, Any], message: str | None) -> ToolMessage:
    tool_name = str(tool_call.get("name") or "tool")
    tool_call_id = str(tool_call.get("id") or f"blocked:{tool_name}")
    return ToolMessage(
        content=message or "Tool invocation was blocked.",
        name=tool_name,
        tool_call_id=tool_call_id,
        status="error",
    )


def _cancel_prepared_invocations(components: GraphComponents, state: AgentState, *, reason: str) -> None:
    recorder = components.tool_invocation_recorder
    permission = state.get("permission")
    if recorder is None or not isinstance(permission, dict):
        return
    decisions = permission.get("decisions")
    if not isinstance(decisions, list):
        return
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        invocation_id = decision.get("invocation_id")
        if isinstance(invocation_id, str) and invocation_id:
            recorder.cancel(invocation_id, reason=reason)


def _to_project_tool_call(raw_tool_call: dict[str, Any]) -> ToolCall:
    return ToolCall(
        name=str(raw_tool_call.get("name")),
        arguments=dict(raw_tool_call.get("args") or {}),
    )


def _latest_tool_messages(messages: list[BaseMessage]) -> list[ToolMessage]:
    tool_messages: list[ToolMessage] = []
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            tool_messages.append(message)
            continue
        if tool_messages:
            break
    return list(reversed(tool_messages))


def _emit_permission(
    components: GraphComponents,
    loop_index: int,
    permission: dict[str, Any],
) -> None:
    status = permission.get("status")
    _emit_progress(
        components,
        loop_index,
        "permission",
        allowed=status == "allowed",
        approval=status == "approval_required",
        reason=permission.get("reason") or "",
    )
    _log_trace(
        components,
        "langgraph_permission_checked",
        {
            "loop": loop_index,
            "permission": _permission_payload(permission),
        },
    )


def _emit_context_built(
    components: GraphComponents,
    loop_index: int,
    state: AgentState,
) -> None:
    messages = state["messages"]
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    _emit_progress(
        components,
        loop_index,
        "context_built",
        messages=len(messages),
        observations=len(tool_messages),
        message_preview=[_message_preview(message) for message in messages],
    )
    _log_trace(
        components,
        "langgraph_context_built",
        {
            "loop": loop_index,
            "messages": len(messages),
            "observations": len(tool_messages),
            "roles": [_message_preview(message) for message in messages],
        },
    )


def _emit_tool_execute_start(
    components: GraphComponents,
    loop_index: int,
    tool_calls: list[dict[str, Any]],
) -> None:
    for raw_tool_call in tool_calls:
        payload = _tool_call_payload(raw_tool_call)
        _emit_progress(components, loop_index, "tool_execute_start", tool=payload["name"])
        _log_trace(components, "langgraph_tool_execute_start", {"loop": loop_index, "tool_call": payload})


def _emit_progress(
    components: GraphComponents,
    loop_index: int | None,
    event: str,
    **fields: Any,
) -> None:
    if components.progress is not None:
        components.progress.event(loop_index, event, **fields)


def _log_trace(components: GraphComponents, event_type: str, payload: dict[str, Any]) -> None:
    if components.trace is not None:
        components.trace.log_event(event_type, payload)


def _permission_payload(permission: dict[str, Any]) -> dict[str, Any]:
    payload = dict(permission)
    tool_call = payload.get("tool_call")
    if isinstance(tool_call, dict):
        payload["tool_call"] = _tool_call_payload(tool_call)
    for key in ("tool_calls", "approval_required_tool_calls"):
        tool_calls = payload.get(key)
        if isinstance(tool_calls, list):
            payload[key] = [
                _tool_call_payload(raw_tool_call) if isinstance(raw_tool_call, dict) else raw_tool_call
                for raw_tool_call in tool_calls
            ]
    decisions = payload.get("decisions")
    if isinstance(decisions, list):
        payload["decisions"] = [
            _permission_payload(decision) if isinstance(decision, dict) else decision for decision in decisions
        ]
    return payload


def _tool_call_payload(raw_tool_call: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": raw_tool_call.get("name"),
        "arguments": dict(raw_tool_call.get("args") or {}),
        "id": raw_tool_call.get("id"),
    }


def _tool_message_payload(message: ToolMessage) -> dict[str, Any]:
    status = getattr(message, "status", "success")
    content = message.content
    structured = _tool_message_structured_data(content)
    return {
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "status": status,
        "ok": status != "error" and not (
            isinstance(structured, dict) and structured.get("ok") is False
        ),
        "content": content,
    }


def _tool_message_structured_data(content: object) -> object:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _message_preview(message: BaseMessage) -> dict[str, str | None]:
    role = getattr(message, "type", message.__class__.__name__)
    if role == "human":
        role = "user"
    elif role == "ai":
        role = "assistant"
    return {
        "role": role,
        "name": getattr(message, "name", None),
        "content": str(message.content),
    }


def _text_from_input_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        str(part.get("text"))
        for part in parts
        if isinstance(part, dict) and part.get("text") is not None
    )

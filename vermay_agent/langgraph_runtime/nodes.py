from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import interrupt

from vermay_agent.permission import PermissionGate
from vermay_agent.progress import ProgressReporter
from vermay_agent.trace import TraceLogger
from vermay_agent.types import ToolCall
from vermay_agent.tools.user_input import REQUEST_USER_INPUT_TOOL_NAME

from .model_adapters import ModelInvocation
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


def call_model_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        _emit_context_built(components, loop_index, state)
        _emit_progress(
            components,
            loop_index,
            "model_call_start",
        )
        invocation = components.model.invoke(messages=state["messages"], tools=components.tools)
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

        updates: dict = {"messages": [response]}
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


def check_permission_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
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
            return {"permission": permission}

        if components.permission_gate is None:
            permission = {
                "status": "allowed",
                "reason": "no permission gate configured",
                "tool_calls": tool_calls,
                "decisions": [
                    {
                        "status": "allowed",
                        "reason": "no permission gate configured",
                        "tool_call": raw_tool_call,
                    }
                    for raw_tool_call in tool_calls
                ],
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
            return {"permission": permission}

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


def reject_tool_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        approval = state.get("approval") or {}
        if approval.get("approved") is False:
            reason = approval.get("reason") or "approval rejected"
            final_answer = f"Tool call rejected by approval: {reason}"
            _log_trace(components, "langgraph_tool_rejected", {"loop": loop_index, "reason": reason})
            _emit_progress(components, loop_index, "final_answer")
            return {"final_answer": final_answer}

        permission = state.get("permission") or {}
        status = permission.get("status")
        reason = permission.get("reason") or "tool call rejected"
        if status == "approval_required":
            final_answer = f"Tool call requires approval: {reason}"
        else:
            final_answer = f"Tool call rejected: {reason}"
        _log_trace(components, "langgraph_tool_rejected", {"loop": loop_index, "reason": reason, "status": status})
        _emit_progress(components, loop_index, "final_answer")
        return {"final_answer": final_answer}

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
                return {"approval": approval}

        executable_tool_calls = permission.get("tool_calls")
        if approval["approved"] and isinstance(executable_tool_calls, list):
            _emit_tool_execute_start(components, loop_index, executable_tool_calls)
        return {"approval": approval}

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
        return {"messages": tool_messages}

    return node


def record_tool_messages_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        loop_index = state["loop_index"]
        tool_messages = _latest_tool_messages(state["messages"])
        for message in tool_messages:
            payload = _tool_message_payload(message)
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
                summary=payload["content"],
            )
            _log_trace(components, "langgraph_tool_message", {"loop": loop_index, **payload})
        return {}

    return node


def increment_loop_node(_: GraphComponents):
    def node(state: AgentState) -> dict:
        return {
            "loop_index": state["loop_index"] + 1,
            "permission": None,
        }

    return node


def max_loops_node(components: GraphComponents):
    def node(state: AgentState) -> dict:
        final_answer = f"Stopped after reaching max_loops={state['max_loops']}."
        _emit_progress(components, state["loop_index"], "max_steps_reached", max_steps=state["max_loops"])
        _log_trace(
            components,
            "langgraph_max_loops_reached",
            {"loop": state["loop_index"], "max_loops": state["max_loops"]},
        )
        return {"final_answer": final_answer}

    return node


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
    return {
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "status": status,
        "ok": status != "error",
        "content": message.content,
    }


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

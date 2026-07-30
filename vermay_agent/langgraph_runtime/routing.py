from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage

from .state import AgentState


def latest_ai_message(messages: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def route_after_model(state: AgentState) -> str:
    if state.get("final_answer") is not None:
        return "final"

    ai_message = latest_ai_message(state.get("messages", []))
    if ai_message is not None and ai_message.tool_calls:
        return "tool_calls"

    return "final"


def route_after_permission(state: AgentState) -> str:
    permission = state.get("permission") or {}
    if permission.get("status") == "allowed":
        return "allowed"
    if permission.get("status") == "input_required":
        return "input_required"
    if permission.get("status") == "approval_required":
        return "approval_required"
    return "denied"


def route_after_approval(state: AgentState) -> str:
    approval = state.get("approval") or {}
    if approval.get("approved") is True:
        return "approved"
    return "rejected"


def route_loop_limit(state: AgentState) -> str:
    if state["loop_index"] > state["max_loops"]:
        return "max_loops"
    return "continue"

from __future__ import annotations

import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph.message import add_messages

from .execution import ExecutionPolicy


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    permission: dict[str, Any] | None
    approval: dict[str, Any] | None
    final_answer: str | None
    loop_index: int
    max_loops: int
    errors: list[dict[str, Any]]
    runtime_thread_id: str | None
    execution_policy: dict[str, Any]
    execution_started_at: float
    model_calls: int
    tool_calls: int
    failure_count: int
    observations: list[dict[str, Any]]
    stop_reason: str | None
    stop_detail: dict[str, Any] | None
    stop_message: str | None


def build_initial_state(
    user_input: str,
    *,
    system_prompt: str | None = None,
    context_messages: list[BaseMessage] | None = None,
    history_messages: list[BaseMessage] | None = None,
    max_loops: int = 5,
    execution_policy: ExecutionPolicy | None = None,
    runtime_thread_id: str | None = None,
) -> AgentState:
    messages: list[BaseMessage] = []
    if system_prompt:
        messages.append(SystemMessage(content=system_prompt))
    if context_messages:
        messages.extend(context_messages)
    if history_messages:
        messages.extend(history_messages)
    messages.append(HumanMessage(content=user_input))
    policy = execution_policy or ExecutionPolicy.from_max_loops(max_loops)
    return {
        "messages": messages,
        "permission": None,
        "approval": None,
        "final_answer": None,
        "loop_index": 1,
        "max_loops": policy.max_loop_steps,
        "errors": [],
        "runtime_thread_id": runtime_thread_id,
        "execution_policy": policy.to_dict(),
        "execution_started_at": time.time(),
        "model_calls": 0,
        "tool_calls": 0,
        "failure_count": 0,
        "observations": [],
        "stop_reason": None,
        "stop_detail": None,
        "stop_message": None,
    }

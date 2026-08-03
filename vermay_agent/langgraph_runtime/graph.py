from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .nodes import (
    GraphComponents,
    approval_required_node,
    call_model_node,
    check_permission_node,
    increment_loop_node,
    max_loops_node,
    record_tool_messages_node,
    reject_tool_node,
    tool_call_wrapper,
    user_input_required_node,
)
from .routing import route_after_approval, route_after_model, route_after_permission, route_loop_limit
from .state import AgentState


def build_graph(components: GraphComponents, checkpointer=None):
    graph = StateGraph(AgentState)
    graph.add_node("call_model", call_model_node(components))
    graph.add_node("check_permission", check_permission_node(components))
    graph.add_node("approval_required", approval_required_node(components))
    graph.add_node("user_input_required", user_input_required_node(components))
    graph.add_node("reject_tool", reject_tool_node(components))
    graph.add_node(
        "tools",
        ToolNode(
            components.tools,
            handle_tool_errors=True,
            wrap_tool_call=tool_call_wrapper(components),
        ),
    )
    graph.add_node("record_tool_messages", record_tool_messages_node(components))
    graph.add_node("increment_loop", increment_loop_node(components))
    graph.add_node("max_loops", max_loops_node(components))

    graph.add_edge(START, "call_model")
    graph.add_conditional_edges(
        "call_model",
        route_after_model,
        {
            "final": END,
            "tool_calls": "check_permission",
            "stopped": END,
        },
    )
    graph.add_conditional_edges(
        "check_permission",
        route_after_permission,
        {
            "allowed": "tools",
            "input_required": "user_input_required",
            "approval_required": "approval_required",
            "denied": "reject_tool",
            "stopped": END,
        },
    )
    graph.add_conditional_edges(
        "approval_required",
        route_after_approval,
        {
            "approved": "tools",
            "rejected": "reject_tool",
        },
    )
    graph.add_edge("user_input_required", "increment_loop")
    graph.add_edge("reject_tool", END)
    graph.add_edge("tools", "record_tool_messages")
    graph.add_edge("record_tool_messages", "increment_loop")
    graph.add_conditional_edges(
        "increment_loop",
        route_loop_limit,
        {
            "continue": "call_model",
            "max_loops": "max_loops",
            "stopped": END,
        },
    )
    graph.add_edge("max_loops", END)

    return graph.compile(checkpointer=checkpointer)

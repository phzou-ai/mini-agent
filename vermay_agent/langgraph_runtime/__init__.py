"""LangGraph runtime.

This package is the default production-oriented runtime. It uses LangChain /
LangGraph standard message types and ToolNode-backed tool execution.
"""

from .graph import build_graph
from .execution import ExecutionPolicy, ExecutionStopReason
from .invocations import ToolInvocationExecution, ToolInvocationRecorder, ToolInvocationReference
from .model_adapters import ModelInvocation, OllamaModelAdapter, OpenAICompatibleModelAdapter
from .model_factory import ModelProviderConfig, build_graph_model_client
from .nodes import GraphComponents, GraphModelClient
from .runner import LangGraphAgentRuntime
from .state import AgentState, build_initial_state

__all__ = [
    "LangGraphAgentRuntime",
    "ExecutionPolicy",
    "ExecutionStopReason",
    "AgentState",
    "GraphComponents",
    "GraphModelClient",
    "ModelInvocation",
    "ModelProviderConfig",
    "OllamaModelAdapter",
    "OpenAICompatibleModelAdapter",
    "build_graph_model_client",
    "build_initial_state",
    "build_graph",
    "ToolInvocationExecution",
    "ToolInvocationRecorder",
    "ToolInvocationReference",
]

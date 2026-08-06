from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from vermay_agent.langgraph_runtime import (
    ExecutionPolicy,
    LangGraphAgentRuntime,
    ModelProviderConfig,
    ToolInvocationRecorder,
    build_graph_model_client,
)
from vermay_agent.model_selection import resolve_model_selection

from .checkpointing import build_sqlite_checkpointer
from .mcp.client import MCPClientManager
from .mcp.prompts import MCPPromptProvider
from .mcp.resources import MCPResourceProvider
from .memory import SQLiteMemoryStore
from .permission import PermissionGate
from .progress import ProgressReporter
from .runtime_context import RuntimeContextProvider
from .skills import SkillStore
from .storage import AgentStore
from .system_prompt import default_system_prompt
from .tool_registry import ToolRegistry
from .tools.devops import register_devops_tools
from .tools.user_input import register_user_input_tool
from .tools.weather import register_weather_tools
from .trace import TraceLogger


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_PATH = ROOT / "traces" / "latest.jsonl"
DEFAULT_CHECKPOINT_PATH = ROOT / "data" / "checkpoints" / "langgraph.sqlite"
DEFAULT_AGENT_STORE_PATH = ROOT / "data" / "agent.sqlite"
DEFAULT_SKILLS_PATH = ROOT / "skills"
DEFAULT_SKILL_PROPOSALS_PATH = ROOT / "data" / "skill_proposals"
DEFAULT_MCP_CONFIG_PATH = ROOT / "config" / "mcp_servers.json"
DEFAULT_MODEL_CONFIG_PATH = ROOT / "config" / "models.json"


@dataclass(frozen=True)
class RuntimeFactoryConfig:
    model: ModelProviderConfig | None = None
    model_config_path: Path = DEFAULT_MODEL_CONFIG_PATH
    max_loops: int = 5
    max_tool_calls: int | None = None
    max_failures: int = 2
    max_elapsed_seconds: float | None = None
    show_progress: bool = True
    trace_path: Path = DEFAULT_TRACE_PATH
    checkpoint_path: Path = DEFAULT_CHECKPOINT_PATH
    agent_store_path: Path = DEFAULT_AGENT_STORE_PATH
    skills_path: Path = DEFAULT_SKILLS_PATH
    skill_proposals_path: Path = DEFAULT_SKILL_PROPOSALS_PATH
    mcp_config_path: Path = DEFAULT_MCP_CONFIG_PATH
    mcp_servers: tuple[str, ...] = field(default_factory=tuple)
    mcp_prompts: tuple[str, ...] = field(default_factory=tuple)
    mcp_resources: tuple[str, ...] = field(default_factory=tuple)


def build_runtime(
    config: RuntimeFactoryConfig | None = None,
    *,
    tool_invocation_recorder: ToolInvocationRecorder | None = None,
) -> LangGraphAgentRuntime:
    active_config = config or RuntimeFactoryConfig()
    active_model = active_config.model or resolve_model_selection(config_path=active_config.model_config_path)
    registry = ToolRegistry()
    register_devops_tools(registry)
    register_user_input_tool(registry)
    register_weather_tools(registry)
    progress = ProgressReporter(enabled=active_config.show_progress)
    trace = TraceLogger(active_config.trace_path)
    mcp_tools = MCPClientManager(active_config.mcp_config_path, selected_servers=active_config.mcp_servers).load_tools()
    if active_config.mcp_servers and not mcp_tools:
        payload = {"servers": list(active_config.mcp_servers), "eligible_tools": 0}
        trace.log_event("mcp_selection_no_eligible_tools", payload)
        progress.event(None, "mcp_selection", **payload)
    for tool in mcp_tools:
        registry.register(tool)
    mcp_prompt_provider = None
    if active_config.mcp_prompts:
        mcp_prompt_provider = MCPPromptProvider(
            config_path=active_config.mcp_config_path,
            selected_servers=active_config.mcp_servers,
            selected_prompts=active_config.mcp_prompts,
            trace=trace,
            progress=progress,
        )
    mcp_resource_provider = None
    if active_config.mcp_resources:
        mcp_resource_provider = MCPResourceProvider(
            config_path=active_config.mcp_config_path,
            selected_servers=active_config.mcp_servers,
            selected_resources=active_config.mcp_resources,
            trace=trace,
            progress=progress,
        )
    checkpointer = build_sqlite_checkpointer(active_config.checkpoint_path)
    agent_store = AgentStore(active_config.agent_store_path)
    memory_store = SQLiteMemoryStore(agent_store)
    skill_store = SkillStore(
        authored_dir=active_config.skills_path,
        proposals_dir=active_config.skill_proposals_path,
        store=agent_store,
    )

    return LangGraphAgentRuntime(
        model=build_graph_model_client(active_model),
        tools=registry.tools_for_model(),
        permission_gate=PermissionGate(registry),
        system_prompt=default_system_prompt(),
        trace=trace,
        max_loops=active_config.max_loops,
        execution_policy=ExecutionPolicy.from_max_loops(
            active_config.max_loops,
            max_tool_calls=active_config.max_tool_calls,
            max_failures=active_config.max_failures,
            max_elapsed_seconds=active_config.max_elapsed_seconds,
        ),
        checkpointer=checkpointer,
        progress=progress,
        context_provider=RuntimeContextProvider(
            mcp_prompts=mcp_prompt_provider,
            skills=skill_store,
            memory=memory_store,
            mcp_resources=mcp_resource_provider,
        ),
        tool_invocation_recorder=tool_invocation_recorder,
        close_callbacks=[checkpointer.conn.close, agent_store.close],
    )

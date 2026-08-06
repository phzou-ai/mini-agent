# Key Modules

## Entry Point

`vermay_agent/main.py`

- Defines the `vermay-agent` console entry point and keeps `mini-agent` as a compatibility alias.
- Dispatches prompt execution or named subcommands.
- Re-exports a small set of CLI helpers for compatibility with existing tests.

`vermay_agent/cli/prompt.py`

- Parses prompt-run CLI arguments.
- Maps provider-specific flags and `--model-option key=value` into model provider options.
- Resolves trace paths.
- Handles approval resume CLI options.
- Owns terminal-only interactive approval prompting.

`vermay_agent/cli/subcommands.py`

- Dispatches subcommands for `serve`, memory, skills, eval replay, and MCP inspection.
- Owns subcommand-specific argument parsing.
- Keeps local SQLite store lifecycle scoped to each command invocation.

## API

`vermay_agent/api/`

- `app.py`: FastAPI app factory and HTTP route definitions.
- `a2a/`: A2A JSON-RPC/SSE binding and projection package over `MainAgentCore`.
- `management_models.py`: request and response models for the first-party Context, registered-agent, and model-configuration read-model endpoints.

`create_app()` builds one default `MainAgentCore` composition. It owns no
business lifecycle decisions itself: FastAPI only starts reconciliation, exposes
the bindings/read models, and closes the resources it created during shutdown.
A2A remains an API-edge binding and must not introduce A2A concepts into
`vermay_agent/langgraph_runtime/`.

The API package contains no alternate service/session lifecycle. All supported
agent operations enter through the A2A adapter and delegate to
`MainAgentCore`.

## Main Agent

`vermay_agent/main_agent/`

- `core.py`: the application lifecycle owner for direct Messages, local Tasks,
  continuations, cancellation, remote child-task proxies, and durable ingress.
- `store.py`: Main Agent persistence adapter over the SQLite store.
- `router.py`, `router_classifier.py`, and `router_json_client.py`: explicit
  route selection, model-backed classification, and model-provider transport.
- `context.py`: causal Context cuts, role-preserving conversion, and
  route-specific character-bounded history policies.
- `responder.py`: direct model-backed Message response and direct SSE text
  streaming.
- `task_runner.py`: local LangGraph Task execution and per-runtime-thread
  serialization.
- `executor.py`: application-owned in-process Task executor.
- `remote_agent.py`: child A2A client and remote Task snapshot validation.
- `projection.py`: protocol-facing projection of durable main-agent records.

`MainAgentCore` owns public lifecycle facts; `LangGraphAgentRuntime` owns only
local graph execution and checkpoint continuation.

## Runtime Factory

`vermay_agent/app_factory.py`

- Defines `RuntimeFactoryConfig`.
- Builds the active LangGraph runtime through `build_runtime()`.
- Wires model adapters, tools, permission checks, trace logging, progress reporting, memory, skills, and approval handling.
- Injects the CLI SQLite checkpointer.
- Registers runtime close callbacks for owned resources such as SQLite connections.
- Owns factory-level paths such as `trace_path`, `checkpoint_path`, `agent_store_path`, `skills_path`, and `mcp_config_path`.

## LangGraph Runtime

`vermay_agent/langgraph_runtime/`

- `state.py`: standard LangGraph state using `messages: Annotated[list[BaseMessage], add_messages]`.
- `nodes.py`: model, permission, approval, tool-message recording, and loop-control nodes.
- `routing.py`: message routing helpers based on `AIMessage.tool_calls`.
- `graph.py`: graph topology using `ToolNode` after permission and approval checks.
- `runner.py`: runtime wrapper around the compiled graph.
- `results.py`: structured runtime result type and API-facing result payload helpers.
- `model_adapters.py`: adapters from project model clients to a thin `AIMessage` wrapper.
- `model_factory.py`: provider factory for constructing runtime model adapters.

This package is the only active runtime path. It is the production-oriented path and uses LangChain / LangGraph standard message and tool execution types.

## Shared Runtime Components

`vermay_agent/`

- `system_prompt.py`: owns the default agent system prompt used by runtime assembly and direct Message responses.
- `checkpointing.py`: builds SQLite checkpointers for durable CLI approval resume.
- `tooling.py`: helper for creating `StructuredTool` objects with Pydantic `args_schema` and project metadata.
- `tool_schema.py`: converts active `StructuredTool` objects into model-facing schemas.
- `tool_registry.py`: stores `StructuredTool` objects and exposes schema inspection over the same tool objects.
- `permission.py`: blocks dangerous tools before execution.
- `result_summary.py`: shared summary helpers for terminal progress output.
- `trace.py`: writes JSONL runtime events.
- `progress.py`: renders the default human-readable harness progress transcript.
- `errors.py`: shared project error taxonomy for API response mapping and failed-task persistence.
- `storage.py`: local SQLite metadata store with the
  `main_agent_clean_slate_v1` schema-family marker and a forward-only baseline.
- `memory.py`: SQLite-backed explicit-write memory.
- `skills.py`: authored skill parser, retrieval, proposal generation, and approval.
- `runtime_context.py`: injects selected MCP prompts, authored skills, memory, and selected MCP resources as initial system context.
- `evaluation.py`: offline trace/scenario replay reporting without live model or live tool execution.
- `mcp/`: MCP client integration package for config parsing, dataclasses, transport, tool wrapping, prompt/resource providers, and structured selection payloads.
- `types.py`: active bridge dataclasses used by model clients, tool-call normalization, and permission checks.

The active tool schema source is each tool's Pydantic `args_schema`. Model adapters and `ToolRegistry.schemas()` both derive schemas from the same `StructuredTool` objects that `ToolNode` executes.

## Model Adapters

`vermay_agent/model_clients/ollama.py`

- Calls Ollama `/api/chat`.
- Supports explicit `native`, `prompt_json`, and `none` tool-calling modes.
- Uses native `tools` and `message.tool_calls` for the active Task path; the
  JSON action format is an explicit compatibility mode only.
- Reads model configuration from `config/models.json` or explicit runtime overrides.

`vermay_agent/model_clients/openai_compatible.py`

- Calls OpenAI-style `{base_url}/chat/completions` endpoints.
- Sends Bearer authentication when `api_key` or `api_key_env` is configured.
- Uses standard Chat Completions `tools`, `tool_choice`, assistant `tool_calls`, and `role: tool` messages with `tool_call_id`.
- Omits `tools` and `tool_choice` when no tools are available.
- Supports `native` and `none` tool-calling modes; it does not interpret the
  project JSON action compatibility format.

`vermay_agent/langgraph_runtime/model_factory.py`

- Builds provider-specific model adapters for the active runtime.
- Accepts `ModelProviderConfig(provider, options)`.
- Validates provider-specific options.
- Supports configured `ollama` and `openai_compatible` model providers.
- `openai_compatible` targets OpenAI-style `/chat/completions` endpoints such as vLLM.
- `config/models.json` defines named model provider configs and the primary model.

## Tool Domains

`vermay_agent/tools/devops/`

- Local file and log inspection tools.
- Local sample Kubernetes data tools.
- SSH-backed read-only Kubernetes tools.
- Dangerous tool placeholders that require approval.

`vermay_agent/tools/weather/`

- `weather_forecast` read-only external data tool backed by `wttr.in`.

Configured MCP servers are inactive by default. Runtime construction loads MCP tools only from explicitly selected servers, such as `--mcp-server k8s`. Eligible discovered MCP tools are wrapped as `StructuredTool` objects with namespaced model-facing names and registered through the same `ToolRegistry` path as built-in tools.

Explicitly selected MCP prompts and resources are read once at run start. `RuntimeContextProvider` injects them in this order: MCP prompts, local authored skills, explicit memory, MCP resources. Prompts are treated as external workflow guidance; resources are treated as untrusted external data. Prompt selections can carry explicit string arguments, which are passed to the MCP server when retrieving the prompt.

`vermay_agent/mcp/`

- `client.py`: high-level MCP client manager and compatibility aliases.
- `config.py`: MCP server config parsing and exposure policy constants.
- `models.py`: MCP server, tool, resource, prompt, and report dataclasses.
- `tool_adapter.py`: MCP tool exposure policy, namespacing, reports, and `StructuredTool` conversion.
- `transport.py`: stdio MCP transport calls, bounded operation timeouts, transport errors, and result serialization.
- `selection.py`: structured MCP selection model used by the API service and runtime factory wiring.
- `prompts.py`: selected MCP prompt retrieval, truncation, and context injection.
- `resources.py`: selected MCP resource retrieval, truncation, and context injection.

`examples/mcp_servers/k8s/`

- Read-only MCP stdio server for Kubernetes inspection.
- Reuses the existing SSH/microk8s backend.
- Exposes read-only tools, resources, and prompts without adding Kubernetes-specific logic to the runtime core.

## Infrastructure

`vermay_agent/infra/ssh.py`

- Builds strict SSH commands from environment configuration.
- Enforces host key checking and known hosts usage.
- Redacts identity file path in returned command traces.

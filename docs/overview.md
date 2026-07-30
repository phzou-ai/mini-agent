# Project Overview

## Purpose

Vermay Agent is an A2A-native main-agent runtime and inspectable process host for direct answers, durable local execution, and delegation to external child agents. It also provides command-line and Web UI surfaces for validating agent-system behavior.

The current implementation focuses on:

- LangGraph-based orchestration.
- LangChain / LangGraph standard message types.
- Tool registration with LangGraph `ToolNode` execution.
- Tool schemas defined once through Pydantic `args_schema` on `StructuredTool`.
- Permission checks before dangerous operations.
- Approval interrupt and SQLite-backed resume in the CLI runtime.
- Human-readable progress output.
- Machine-readable JSONL trace output.
- Local SQLite metadata for memory, skills, eval runs, and runtime metadata.
- Explicit-write memory injection.
- Authored markdown skills and generated skill proposals.
- Evaluation replay from traces or scenario fixtures without live tool execution.
- Ollama and OpenAI-compatible model adapters with named model selection.
- MCP client-side tool discovery for configured servers.
- Local FastAPI server for agent session and task lifecycle.
- Compact API lifecycle events for local operation monitoring.
- Optional local A2A routes over existing session, task, event, and artifact records.
- SSH-backed read-only Kubernetes inspection.
- External read-only data tools such as weather forecast.

## Current Runtime Position

The primary service path is the A2A main-agent boundary implemented by `vermay_agent/api/a2a/` and `vermay_agent/main_agent/`. It classifies incoming Messages as direct replies, locally owned durable Tasks, or delegated child-agent work.

The LangGraph executor is implemented by `vermay_agent/langgraph_runtime/`. The CLI calls this layer directly as a development and operations harness; it does not represent the complete A2A main-agent lifecycle.

The earlier hands-on runtime has been archived under `archive/hands_on_langgraph_runtime/`. It remains useful as historical reference material for explicit harness mechanics, but it is not an active runtime path.

The architectural direction for separating A2A protocol resources, durable local process lifecycle, LangGraph continuation, and direct Message execution is documented in [agent-os-architecture.md](agent-os-architecture.md). The Agent OS model is a responsibility and lifecycle abstraction; it does not replace A2A or LangGraph and does not require an immediate code or database rename.

## Primary Service Flow

```text
A2A Message
  -> persist Context input
  -> route to direct Message, local Task, or child A2A agent
  -> execute or delegate
  -> persist Message / Task / event / artifact state
  -> project A2A response and stream updates
```

## LangGraph Harness Flow

```text
CLI input
  -> build runtime
  -> build initial graph state
  -> call model
  -> route final answer or tool call
  -> check permission
  -> execute safe tool or interrupt for approval
  -> record tool message
  -> continue or finish
```

## Runtime Guarantees

- Tool execution goes through LangGraph `ToolNode` after project permission checks.
- Model-facing tool schemas are derived from the same `StructuredTool` objects that `ToolNode` executes.
- Dangerous tools are intercepted by `PermissionGate`.
- Real cluster operations are limited to allowlisted read-only Kubernetes commands.
- SSH identity file paths are redacted in command traces.
- LangGraph checkpoint files are stored under `data/checkpoints/` and are not intended for Git.
- API session, task, and task-event metadata is stored in `data/agent.sqlite`.
- Local API lifecycle endpoints use `/api/...` and separate long-lived sessions from per-input tasks.
- API task start/resume supports `wait=false` for background execution with queued/running/completed task inspection.
- API task cancellation is cooperative: queued/interrupted tasks cancel immediately; running tasks move through `cancel_requested`.
- API task retry creates a new task row for each retry and records lineage through `root_task_id`, `retry_of_task_id`, and `attempt`.
- Completed API tasks persist a default final-answer artifact under `task_artifacts`.
- API background execution, per-task execution locks, and task-event wait notification are isolated in API execution helper modules while `AgentService` remains the public service facade.
- API task lifecycle events can be streamed through a local SSE endpoint backed by persisted `task_events`.
- API artifact events are compact references and do not include final answer text.
- API lifecycle events are compact service-level records written through a lifecycle observer; they do not include raw user input, model output, graph state, final answer text, or full tool output.
- API task/status/artifact metadata has local A2A projection helpers. `vermay-agent serve` exposes the A2A-first service boundary by default.
- A2A routes remain an API-edge adapter and do not alter LangGraph runtime internals. Use `vermay-agent serve --disable-a2a` only when you explicitly need management APIs without public A2A routes.
- The local metadata schema currently records version `6` through ordered schema migrations.
- API lifecycle errors are classified through a shared project error taxonomy before response mapping and failed-task persistence.
- Local trace outputs are not intended for Git.
- Evaluation replay defaults to recorded trace/scenario data and does not execute a live model or live tools.
- Memory writes are explicit CLI operations only.
- MCP servers are inactive by default and must be selected per run; selected MCP tools require approval unless marked read-only in configuration.
- MCP prompts and resources are injected only when explicitly requested; prompts are workflow guidance, resources are external data.
- The Kubernetes MCP server under `examples/mcp_servers/k8s/` is a local read-only test example.

## MCP v1 Status

MCP v1 is feature-frozen for the current project scope. The implemented boundary is a client-side MCP integration baseline:

- Configured MCP servers are inactive unless explicitly selected per run.
- Selected MCP tools are discovered and wrapped as LangChain `StructuredTool` instances.
- MCP tool names are namespaced before they are exposed to the model.
- MCP tools are approval-required by default unless the server or tool is explicitly marked read-only.
- Selected MCP prompts are read once at run start and injected as bounded workflow guidance.
- MCP prompt selections support explicit string arguments.
- Selected MCP resources are read once at run start and injected as bounded external data.
- MCP discovery, tool calls, prompt reads, and resource reads use configured operation timeouts.
- MCP transport errors are surfaced through a dedicated transport error boundary.
- CLI and API session metadata preserve selected MCP servers, prompts, prompt arguments, and resources.
- The Kubernetes MCP test example under `examples/mcp_servers/k8s/` demonstrates read-only tools, resources, and prompts.

The current MCP implementation is sufficient for validating the runtime integration pattern. Further MCP work should be treated as production hardening rather than feature completion.

Production-complete MCP todo list:

- Replace per-operation stdio process startup with a managed MCP client lifecycle where appropriate.
- Add retry, backoff, and circuit-breaker policy for unavailable MCP servers.
- Add stronger auth, trust, and capability review for non-local MCP servers.
- Add support for additional MCP transports only when a real deployment needs them.
- Add UI or API discovery endpoints for browsing selected MCP tools, resources, and prompts.
- Add redaction policy for sensitive MCP tool/resource outputs before trace or session persistence.
- Add configurable limits for MCP output size, argument size, and prompt/resource injection budgets.
- Add production observability around MCP latency, timeout rate, error rate, and approval rate.

## Local Storage

The project uses SQLite for metadata and files for larger artifacts:

- `data/agent.sqlite`: memory items, skill index, eval run metadata, model profile metadata, API session/task metadata, task events, and task artifacts.
- `data/checkpoints/langgraph.sqlite`: LangGraph checkpoint state for interrupt/resume.
- `skills/*.md`: authored skills tracked with the project.
- `data/skill_proposals/*.md`: generated skill proposals, local-only by default.
- `evals/scenarios/*.json`: replay scenario fixtures tracked with the project.
- `data/eval_runs/*.json`: generated replay reports, local-only by default.
- `config/mcp_servers.json`: configured MCP clients.
- `config/models.json`: configured models and the primary model.
- `examples/mcp_servers/k8s/`: local read-only Kubernetes MCP test example.

## Current Non-Goals

- Arbitrary SSH command execution.
- Unreviewed execution of dangerous tools.
- Production deployment packaging.
- Public unauthenticated API exposure.
- Public unauthenticated A2A exposure.
- Automatic long-term memory writes.
- Vector search or embedding-backed memory retrieval.
- Dynamic MCP server installation.
- Self-evolving skill publication without review.

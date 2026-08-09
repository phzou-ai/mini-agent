# Project Overview

## Purpose

Vermay is an A2A-native main-agent runtime and inspectable process host for direct answers, durable local execution, and delegation to external child agents. It also provides command-line and Web UI surfaces for validating agent-system behavior.

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
- Local FastAPI host for A2A ingress and first-party management/read-model endpoints.
- Durable Context, Message, Task, lifecycle-event, artifact, and route-decision inspection.
- A2A JSON-RPC and SSE routes over the same `MainAgentCore` lifecycle owner.
- SSH-backed read-only Kubernetes inspection.
- External read-only data tools such as weather forecast.

## Current Runtime Position

The primary service path is the A2A main-agent boundary implemented by `vermay/api/a2a/` and `vermay/main_agent/`. It classifies incoming Messages as direct replies, locally owned durable Tasks, or delegated child-agent work.

The LangGraph executor is implemented by `vermay/langgraph_runtime/`. The CLI calls this layer directly as a development and operations harness; it does not represent the complete A2A main-agent lifecycle.

The repository contains one runtime implementation. Historical experimental
runtime code that no longer imported cleanly or participated in product tests
has been removed, so the active package does not carry compatibility bridges
for an unsupported execution path.

The implemented full-stack architecture, package boundaries, state ownership,
and request flows are documented in
[current-system.md](../architecture/current-system.md). The longer-term direction
for separating A2A protocol resources, durable local process lifecycle,
LangGraph continuation, and direct Message execution is documented in
[agent-os-evolution.md](../architecture/agent-os-evolution.md). The Agent OS model is a
responsibility and lifecycle abstraction; it does not replace A2A or LangGraph
and does not require an immediate code or database rename.

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
- Context, Message, Task, event, artifact, route-decision, delegation, and ingress metadata is stored in `data/agent.sqlite`.
- `MainAgentCore` is the product lifecycle owner for direct Messages, local Tasks, and remote child-task proxies.
- Direct Messages can stream token deltas over SSE; local Tasks expose durable lifecycle events and a final artifact.
- Local Task cancellation is cooperative, and approval or user-input continuation uses a durable pending-continuation record.
- Repeated top-level A2A `messageId` values reuse one durable ingress outcome rather than routing or executing twice.
- A local Task captures a causal Context input cut; later independent Messages cannot change its initial prompt.
- A2A routes are API-edge bindings and do not alter LangGraph graph topology or checkpoint semantics.
- The local metadata store starts from the `main_agent_clean_slate_v1` baseline
  at schema version `1`. Historical service/session databases are intentionally
  discarded rather than migrated.
- Public errors use a stable `{ code, message, retryable }` contract where a structured error applies.
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

- `data/agent.sqlite`: memory items, skill index, eval metadata, model profile metadata, and durable main-agent Context/Message/Task lifecycle records.
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

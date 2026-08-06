# Operations

The supported process topology, secret handling, persistence boundary, and release checklist are defined in [Runtime and Release Boundary](runtime-and-release.md).

## Install

```bash
cd <repo-root>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## Run

```bash
vermay-agent "weather forecast for Beijing"
```

The CLI uses `vermay_agent/langgraph_runtime/`. No alternate runtime is exposed through the active CLI.

## API Server

Start the local FastAPI server:

```bash
vermay-agent serve
```

Default bind address:

```text
127.0.0.1:8000
```

Use a different port:

```bash
vermay-agent serve --host 127.0.0.1 --port 9000
```

The local server exposes the A2A main-agent service surface together with the
first-party management and diagnostic APIs used by the Web UI:

```bash
vermay-agent serve
```

Current public A2A service boundary:

```text
GET  /health
GET  /.well-known/agent-card.json
POST /rpc
```

Agent-to-agent operations use A2A JSON-RPC methods through `/rpc`. The `/api` prefix is reserved for first-party Web UI management and diagnostics and is not the external A2A integration contract.

Run the default smoke against one local main-agent server:

```bash
scripts/a2a_dev_smoke.sh
```

To include registered child-agent delegation, start a second local A2A server on another port and pass it as the child endpoint:

```bash
BASE_URL=http://127.0.0.1:8000 \
CHILD_AGENT_A2A_BASE_URL=http://127.0.0.1:8001 \
scripts/a2a_dev_smoke.sh
```

Use separate main and child processes for this check; the main agent synchronously calls the child agent over HTTP.

Send a model-backed local message:

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "ops-message-1",
    "method": "SendMessage",
    "params": {
      "message": {
        "kind": "message",
        "role": "user",
        "messageId": "msg-ops-message-1",
        "parts": [{"kind": "text", "text": "summarize current agent status"}]
      },
      "metadata": {"executionMode": "message"}
    }
  }'
```

Run a task:

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "ops-task-1",
    "method": "SendMessage",
    "params": {
      "message": {
        "kind": "message",
        "role": "user",
        "messageId": "msg-ops-task-1",
        "parts": [{"kind": "text", "text": "debug service health"}]
      },
      "metadata": {"executionMode": "task"}
    }
  }'
```

Inspect a task:

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"ops-get-1","method":"GetTask","params":{"id":"<task-id>"}}'
```

Subscribe to task events:

```bash
curl -N -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"ops-subscribe-1","method":"SubscribeToTask","params":{"id":"<task-id>","afterEventId":0}}'
```

The `/api` prefix is reserved for Web UI management and diagnostics:

```text
GET    /api/contexts
GET    /api/contexts/{context_id}
GET    /api/contexts/{context_id}/messages
GET    /api/contexts/{context_id}/tasks
GET    /api/contexts/{context_id}/route-decisions
GET    /api/contexts/{context_id}/delegations
DELETE /api/contexts/{context_id}?force=true
GET    /api/registered-agents
POST   /api/registered-agents
GET    /api/registered-agents/{agent_id}
POST   /api/registered-agents/{agent_id}/refresh-card
DELETE /api/registered-agents/{agent_id}
```

Context deletion is a core-owned lifecycle operation. A Context containing a
live local or remote Task returns a conflict; `force=true` is accepted for API
compatibility but does not erase live work. Registered-agent deletion is
allowed only when the registration has no retained delegation history; disable
an agent with `POST /api/registered-agents` instead when history must remain.

The server is local-only by default and has no authentication. Keep the default bind address unless an access-control layer is added.

## Local Storage

Runtime metadata and generated artifacts are local by default:

```text
data/agent.sqlite
data/checkpoints/langgraph.sqlite
data/eval_runs/*.json
data/skill_proposals/*.md
traces/*.jsonl
```

The tracked configuration and scenario locations are:

```text
config/models.json
config/mcp_servers.json
evals/scenarios/*.json
skills/*.md
```

## Model Configuration

The runtime selects configured models from `config/models.json` by default. The config defines a `primary_model`, an optional `router_model`, and a map of named model provider configurations.

Ollama model settings live in `config/models.json` under the selected model's `options`.

`primary_model` is used for normal local message and task execution. `router_model` is used by the main-agent auto router. If `router_model` is omitted, the router falls back to `primary_model`. `VERMAY_AGENT_ROUTER_MODEL` can temporarily override `router_model` without changing the file.

Use the primary model:

```bash
vermay-agent "weather forecast for Beijing"
```

Use another configured model:

```bash
vermay-agent "weather forecast for Beijing" --model local_ollama
```

Provider-specific CLI override example:

```bash
vermay-agent "weather forecast for Beijing" \
  --model-provider ollama \
  --ollama-model qwen3.6:27b \
  --ollama-base-url http://127.0.0.1:11434 \
  --ollama-timeout-seconds 120
```

Advanced model provider options can be passed as repeated flat `key=value` pairs:

```bash
vermay-agent "weather forecast for Beijing" \
  --model-provider ollama \
  --model-option model=deepseek-v4-flash:cloud \
  --model-option tool_calling=native \
  --model-option timeout_seconds=120
```

`--model-option` has higher priority than provider-specific flags. It is intended as a generic escape hatch for provider options; nested JSON values are not supported.

`--ollama-*` flags are valid only with `--model-provider ollama`. Other providers should use `--model-option` until provider-specific flags are added for them.

`timeout_seconds` must be a positive integer.

The CLI maps configured model selections or provider override flags into `ModelProviderConfig(provider, options)`. Runtime assembly lives in `vermay_agent/app_factory.py`; provider-specific model construction lives in `vermay_agent/langgraph_runtime/model_factory.py`.

OpenAI-compatible endpoint example:

```bash
vermay-agent "weather forecast for Beijing" \
  --model-provider openai_compatible \
  --model-option model=qwen \
  --model-option base_url=http://localhost:8000/v1
```

The OpenAI-compatible adapter uses Chat Completions request semantics: `{base_url}/chat/completions`, Bearer authentication when an API key is configured, standard `tools` with `tool_choice: auto` when tools are present, and standard assistant `tool_calls` plus `role: tool` messages with `tool_call_id` for tool results. When no tools are present, `tools` and `tool_choice` are omitted.

`tool_calling` selects a provider's explicit tool-call strategy. Ollama supports
`native`, `prompt_json`, and `none`; the checked-in primary model uses
`native`. OpenAI-compatible endpoints support `native` and `none`. The runtime
does not silently retry a request with another strategy. See
[model-tool-calling.md](runtime-refinement/model-tool-calling.md) for the
supported matrix and safety behavior.

## Memory

Memory writes are explicit:

```bash
vermay-agent memory add "Prefer read-only Kubernetes inspection first." --tag k8s --tag preference
vermay-agent memory list
vermay-agent memory disable 1
```

Enabled memory is selected by deterministic keyword, tag, and latest-item matching and injected as system context before the user message.

## Skills

Authored skills are markdown files under `skills/` with front matter fields `name`, `description`, `triggers`, and `version`.

```bash
vermay-agent skills list
vermay-agent skills show kubernetes-readonly-debug
vermay-agent skills propose-from-trace --trace traces/latest.jsonl
vermay-agent skills approve <proposal-id>
```

Generated skills remain proposals under `data/skill_proposals/` until approved.

## Offline Evaluation Replay

Replay uses recorded trace or scenario data only. It does not execute a live model, live SSH, MCP, or dangerous tools.

```bash
vermay-agent eval replay --trace traces/latest.jsonl
vermay-agent eval replay --scenario evals/scenarios/weather.json
vermay-agent eval list-runs
```

Eval metadata is stored in `data/agent.sqlite`; full reports are written under `data/eval_runs/`.

## MCP Client

MCP client configuration lives in `config/mcp_servers.json`.

```bash
vermay-agent mcp list-servers
vermay-agent mcp list-tools
vermay-agent mcp list-tools --server k8s
vermay-agent mcp list-resources --server k8s
vermay-agent mcp list-prompts --server k8s
```

Configured MCP servers are inactive during normal agent runs until selected with `--mcp-server`. MCP tools are approval-required by default. A server or individual tool must be explicitly marked read-only in config to bypass approval.

Selected MCP prompts and resources can be injected as bounded context:

```bash
vermay-agent "debug service health" --mcp-server k8s --mcp-prompt k8s-service-health-check
vermay-agent "debug phzou-core service" --mcp-server k8s --mcp-prompt 'k8s-service-health-check?service=phzou-core&namespace=default'
vermay-agent "check service status" --mcp-server k8s --mcp-resource k8s://cluster/services
```

Prompts and resources are read once at run start. Prompts are injected as external workflow guidance before local skills, memory, and resources. Resources are injected as external data after local memory. Prompt arguments use query-string syntax after the prompt name. When multiple MCP servers are selected, use qualified forms such as `--mcp-prompt 'k8s:k8s-service-health-check?service=phzou-core'` and `--mcp-resource k8s:k8s://cluster/services`.

A local `k8s` MCP test example lives under `examples/mcp_servers/k8s/` and exposes read-only Kubernetes tools, resources, and prompts. It uses the existing SSH/microk8s backend, so live tool/resource reads require the preferred `VERMAY_AGENT_SSH_*` environment configuration. The deprecated `MINI_AGENT_SSH_*` prefix is still accepted as a compatibility fallback during migration. The config starts it with `.venv/bin/python` and applies `timeout_seconds` to MCP discovery, tool calls, resources, and prompts. Update `config/mcp_servers.json` if the project is run from another Python environment.

## Trace Path

`--trace` accepts a filename or relative subpath under `traces/`:

```bash
vermay-agent "weather forecast for Beijing" --trace runs/latest.jsonl
```

Absolute paths are allowed for debugging and tests. Relative paths cannot escape `traces/`.

## Approval Resume

Dangerous tools require approval and pause the graph through LangGraph interrupt/resume.

In an interactive terminal, the default command prompts for approval and resumes automatically:

```bash
vermay-agent "run a dangerous operation"
```

The CLI runtime stores LangGraph checkpoints in:

```text
data/checkpoints/langgraph.sqlite
```

This makes manual resume durable across CLI processes:

```bash
vermay-agent "run a dangerous operation" --thread-id approval-session
vermay-agent --thread-id approval-session --resume-approval true --approval-reason "approved by operator"
```

Interactive approval asks at most once per run by default. If the model requests another dangerous tool after approval, the run stops instead of repeatedly prompting.

Detailed interrupt, checkpoint, and resume mechanics are documented in [langgraph-interrupt-resume.md](langgraph-interrupt-resume.md).

## Terminal Progress

`ProgressReporter` is enabled by default and writes a compact harness transcript to stderr. It should stay concise and describe harness-level behavior:

```text
loop 1
  context ...
  model_call ...
  model_decision ...
```

The terminal transcript is for scanability. It is not the durable audit log and should not try to expose full tool payloads.

Disable progress output:

```bash
vermay-agent "weather forecast for Beijing" --no-progress
```

## JSONL Traces

Machine-readable traces are written to:

```text
traces/*.jsonl
```

`TraceLogger` is the durable audit log. It can store fuller payloads than terminal output, including tool messages, observations, permission decisions, and raw model responses.

## Tests

```bash
.venv/bin/python -m pytest
```

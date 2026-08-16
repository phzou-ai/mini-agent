# API Boundary

## Current Position

The server exposes an A2A service boundary for agent operations and a separate
first-party `/api` boundary for Web UI management and diagnostics.

Start the server:

```bash
vermay serve
```

`serve` always exposes the A2A service boundary. The same process also hosts
the first-party management and diagnostic APIs used by the Web UI.

Default bind address:

```text
127.0.0.1:8000
```

The server is local-only by default and does not add authentication. Do not expose it outside a trusted environment without an access-control layer.

## Public Service Boundary

Canonical A2A integration routes:

```text
GET  /health
GET  /.well-known/agent-card.json
POST /rpc
```

Agent operations use A2A JSON-RPC methods through `/rpc`. Child-agent delegation in `vermay/main_agent/remote_agent.py` uses the same boundary.

Mutating A2A bindings construct immutable lifecycle commands and consume typed
outcomes from `MainAgentCore.execute()` or `MainAgentCore.stream()`. The API
adapter does not mutate lifecycle storage directly. First-party management
queries remain bounded read-model calls; startup reconciliation and failed-Task
retry use the same command surface as A2A lifecycle mutations.

The service implements the A2A `0.3.0` JSON-RPC binding. Unadvertised
path-style agent routes are not part of the service surface.

## JSON-RPC Methods

`POST /rpc` supports one JSON-RPC request object per HTTP request.

Supported canonical methods:

```text
message/send
message/stream
tasks/get
tasks/cancel
tasks/resubscribe
tasks/resume
```

`tasks/resume` is a Vermay extension for explicit approval continuation. The
Agent Card advertises it under `capabilities.extensions` with URI
`urn:vermay:a2a:task-approval-resume:0.1`. The other methods above are the A2A
`0.3.0` JSON-RPC method names.

Batch arrays are intentionally rejected until single-request usage has completed one review and burn-in pass.

## Identity Model

The main-agent service separates conversation context from execution lifecycle:

```text
contextId
  long-lived conversation/context container

taskId
  single task execution started by one user input

thread_id
  internal LangGraph checkpoint key for local task execution
```

`thread_id` is runtime state. It must not become the public task identity.

## Send Message

Local message response:

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-message-1",
    "method": "message/send",
    "params": {
      "message": {
        "kind": "message",
        "role": "user",
        "messageId": "msg-message-1",
        "parts": [{"kind": "text", "text": "summarize current status"}]
      },
      "metadata": {"executionMode": "message"}
    }
  }'
```

Task response:

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-task-1",
    "method": "message/send",
    "params": {
      "message": {
        "kind": "message",
        "role": "user",
        "messageId": "msg-task-1",
        "parts": [{"kind": "text", "text": "debug service health"}]
      },
      "metadata": {"executionMode": "task"}
    }
  }'
```

Route mode is controlled by `metadata.executionMode`:

```text
auto
message
task
```

Registered child-agent routing can be requested with route metadata such as `targetAgentId`.

If a task is in `input-required` because the model requested missing information, continue it with another `message/send`. Put the existing `taskId` on the user message; `contextId` may be omitted and will be inferred from the task.

```json
{
  "jsonrpc": "2.0",
  "id": "req-input-1",
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "messageId": "msg-input-1",
      "taskId": "<task-id>",
      "parts": [{"kind": "text", "text": "staging"}]
    }
  }
}
```

This resumes the existing LangGraph checkpoint and bypasses routing. A supplied `contextId` must match the task context.

## Task Get

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"req-get-1","method":"tasks/get","params":{"id":"<task-id>"}}'
```

## Task Events

Subscribe through `/rpc`:

```bash
curl -N -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"req-subscribe-1","method":"tasks/resubscribe","params":{"id":"<task-id>","afterEventId":0}}'
```

SSE streams replay persisted task events and then stop at a terminal, `input-required`, or `auth-required` task state.

Expected SSE event names:

```text
task
status-update
artifact-update
error
```

Streams do not expose raw graph state, raw prompts, raw model output, or full tool output.

## Task Cancel

```bash
curl -X POST http://127.0.0.1:8000/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"req-cancel-1","method":"tasks/cancel","params":{"id":"<task-id>","reason":"operator requested"}}'
```

Terminal tasks return `invalid_session_state` when cancellation is no longer allowed.

## Main-Agent Management API

The `/api` prefix is reserved for Web UI management and diagnostics:

```text
GET    /api/contexts?limit=100&offset=0
GET    /api/model-config
GET    /api/contexts/{context_id}
PATCH  /api/contexts/{context_id}
GET    /api/contexts/{context_id}/messages
GET    /api/contexts/{context_id}/tasks
GET    /api/contexts/{context_id}/route-decisions
GET    /api/contexts/{context_id}/delegations
GET    /api/message-ingress/{message_id}
POST   /api/management/tasks/{task_id}/retry
GET    /api/tasks/{task_id}/tool-invocations
GET    /api/tasks/{task_id}/observations
DELETE /api/contexts/{context_id}?force=true
GET    /api/registered-agents
POST   /api/registered-agents
GET    /api/registered-agents/{agent_id}
POST   /api/registered-agents/{agent_id}/refresh-card
DELETE /api/registered-agents/{agent_id}
```

Context deletion is core-owned and refuses live local or remote Tasks.

The Context list is a bounded first-party read model. `limit` defaults to
`100`, is capped at `200`, and `offset` defaults to `0`. The Web console
currently requests the first 100 most recently updated Contexts; incremental
UI loading remains deferred until retained data demonstrates that it is
needed.

New Contexts normally persist their title from the first admitted user
Message. For older untitled records, the list endpoint resolves first-user
Message titles in one bulk query for the current page. It must not issue one
message-list query per Context.

The four Context detail endpoints for Messages, Tasks, route decisions, and
delegations also accept `limit` and `offset`. Their default `limit` is `200`,
the maximum is `500`, and the default `offset` is `0`. Each endpoint returns
the selected latest window in chronological order. This is a management read
contract only; lifecycle operations that require complete Context state do not
reuse a truncated page.

`force=true` does not bypass that safeguard. A registered agent can be
hard-deleted only if it has no delegation history; otherwise update it with
`enabled: false` to prevent future delegation while preserving audit facts.

These routes are not the public A2A service boundary. Browser clients should access them through the Next.js BFF.

## Error Mapping

JSON-RPC errors preserve the caller-provided `id` and expose local error codes in `error.data.localCode`.

The response also includes `error.data.errorInfo` as a bridge toward A2A / google.rpc.ErrorInfo-style details:

```json
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "error": {
    "code": -32602,
    "message": "JSON-RPC params.message.role must be 'user'.",
    "data": {
      "localCode": "invalid_request",
      "errorInfo": {
        "reason": "invalid_request",
        "domain": "vermay",
        "metadata": {
          "localCode": "invalid_request"
        }
      }
    }
  }
}
```

Current mapping:

```text
invalid_request        -> 400 / -32602
session_not_found      -> 404 / -32004
task_not_found         -> 404 / -32004
artifact_not_found     -> 404 / -32004
permission_error       -> 403 / -32003
invalid_session_state  -> 409 / -32009
other agent error      -> mapped by local error info
```

## Projection Boundaries

A2A projections include public task/message/status/artifact data only.

Every A2A Task snapshot exposes its durable Task projection version as
`metadata.lifecycleRevision`. Status and artifact events expose the revision
current when their durable event was inserted. The first-party management API
uses the snake-case field `lifecycle_revision` for the same value.

The revision versions current Task state; it is not an event cursor. SSE replay
and reconnect continue to use durable `event_id`. Several additive events may
share one lifecycle revision.

They must not expose:

```text
raw LangGraph state
raw prompts
raw model output
full tool output
internal checkpoint payloads
private trace details
```

Local artifact and output metadata determines whether artifacts are projectable to A2A.

## Verification

Backend gate:

```bash
.venv/bin/python -m pytest -q
```

Deterministic smoke gate:

```bash
BFF_URL=http://localhost:3000 scripts/a2a_dev_smoke.sh
```

The smoke script validates the configured A2A server through `/rpc`.

Optional child-agent delegation smoke:

```bash
BASE_URL=http://127.0.0.1:8000 \
CHILD_AGENT_A2A_BASE_URL=http://127.0.0.1:8001 \
scripts/a2a_dev_smoke.sh
```

Use a separate child-agent process/port for this check. Do not point `CHILD_AGENT_A2A_BASE_URL` at the same single-process server as `BASE_URL`; the main-agent request synchronously calls the child agent over HTTP.

## Current Boundaries

- `/rpc` supports single-request JSON-RPC only.
- JSON-RPC batch requests are rejected.
- Task approval resume is exposed as the Agent Card-declared Vermay extension
  method `tasks/resume`.
- The local default server has no authentication.

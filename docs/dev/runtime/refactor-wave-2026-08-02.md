# Runtime Refactor Wave: Single Ownership And Bounded Context

**Status:** implemented for the product path on 2026-08-02, including the
intentional clean-slate retirement of the old service/session stack.

## Why This Refactor Exists

The project is still in rapid development, so this wave removes misleading
runtime behavior rather than preserving every historical abstraction. The goal
is not to introduce a new framework. It is to make one execution path easy to
name, trace, and extend:

```text
A2A / management ingress
  -> MainAgentCore
     -> route decision
        -> direct model response
        -> local Task -> LangGraph slice
        -> remote child Task proxy
```

The critical distinction is ownership:

- `MainAgentCore` owns public A2A lifecycle decisions and durable process
  records.
- `LangGraphAgentRuntime` owns local graph execution and checkpoint
  continuation only.
- FastAPI owns resources it constructs for the default application.
- SQLite owns durable records and idempotency, not in-memory callbacks.

## Changes Made

### 1. Router uses model judgment, not keyword delegation

The router now applies decisions in this order:

```text
explicit route/mode/target
  -> hard protocol facts (continuation or existing lifecycle)
  -> router model
  -> safe local-message fallback
```

Registered child-agent skills and request words are evidence in the router
model prompt. They are not a substring rule that can silently delegate a
request to an unrelated child agent. This keeps multilingual and indirect
intent interpretation model-backed while preserving deterministic control for
an explicit user selection or an existing Task continuation.

### 2. Default tool registry exposes only real capabilities

The default DevOps registry no longer advertises placeholder `exec_shell` or
`kubectl_apply` tools. A listed capability must either do the described work
under its approval boundary or be unavailable. The registry keeps real
SSH-backed read-only inspection tools and the explicitly approval-gated
`delete_resource` capability.

This prevents a model from receiving a fake write capability and then
producing a misleading successful-looking result.

### 3. One bounded context policy for each execution path

Persisted conversation history now has explicit policies:

| Path | Messages | Historical Characters | Per Historical Message |
| --- | ---: | ---: | ---: |
| Router | 8 | 6,000 | 1,500 |
| Direct message | 12 | 14,000 | 4,000 |
| Initial local Task | 16 | 18,000 | 5,000 |

The current input is never truncated. Only earlier history is bounded, newest
first, and any trimmed record is a transient model-facing copy. The durable
transcript remains unchanged.

Dynamic runtime context has a separate, deliberately smaller contract:

```text
MCP prompts -> skills -> memory -> MCP resources
```

Each injected section has a 5,000-character cap and their total is capped at
16,000 characters. These are character caps, not token budgets. The current
implementation does not claim exact prompt replay, model-token accuracy, or a
global tool-output budget.

### 4. Default application composition has one lifecycle owner

`create_app()` constructs exactly one product composition when no core is
injected: storage, local LangGraph Task runner, in-process executor, model
router, and `MainAgentCore`. Its lifespan reconciles startup and closes only
the resources it created:

```text
FastAPI lifespan
  -> MainAgentCore.reconcile_startup()
  -> shutdown executor
  -> close task runner
  -> close SQLite store
```

An injected core remains caller-owned. The default app does not create a second
lifecycle service or expose one through `app.state`.

### 5. Remote child-task snapshots are identity-checked

A remote snapshot is accepted only when its non-empty remote A2A `taskId`
matches the durable delegation's `remoteTaskId`. If both sides provide a
context ID, they must also match. Validation happens before the transaction
that persists proxy status, delegation diagnostics, lifecycle events, or a
final artifact.

This is separate from the monotonic-state rule: a mismatched identity is a
protocol error, while a stale state for the same remote Task is ignored.

## What Is Deliberately Not Done

### Clean-slate storage cut

The project chose to discard existing developer-local records. The old
`AgentService` / session modules, their tests, A2A projection adapter, and
historical SQLite schema migrations have been removed. There is no compatibility
reader or export path.

`AgentStore` now creates one active `main_agent_clean_slate_v1` baseline. A
retired local database is cleared before this baseline is installed; an unknown
schema family fails explicitly. LangGraph checkpoints are reset alongside the
metadata store, so no retired task identity is resumable. See
[clean-slate-storage.md](clean-slate-storage.md).

### Token-aware context, full prompt snapshots, and task token streaming

Those features add model-specific accounting, replay semantics, storage cost,
and additional stream recovery contracts. The current character-bounded policy
solves the immediate risk of unbounded and inconsistent input without claiming
more than it implements.

### Distributed execution

The current single-host SQLite plus in-process executor shape remains
appropriate. A queue, lease protocol, Temporal, Redis, or a distributed
scheduler needs concrete deployment pressure, not just architectural analogy.

## Regression Contracts Added

- The default FastAPI composition reconciles its `MainAgentCore` on startup.
- The default app shuts down the executor, runner, and store that it creates.
- An injected core is not replaced by a hidden second lifecycle service.
- Router fallback does not automatically delegate because an input happens to
  contain a child-agent skill word.
- Remote results cannot be persisted under a different remote task or context
  identity.
- Dynamic runtime context is bounded both per section and in total.

## Follow-Up Status

The direct-message ingress recovery item identified by this review was
subsequently completed. Stale `in_progress` ingress is now reconciled to an
explicit terminal failure without replaying routing, Task creation, or tool
execution for the same `messageId`.

See [Durable Message Ingress](../../components/backend/runtime/message-ingress.md) and
[direct-message-failures.md](direct-message-failures.md) for the implemented
contract. Current priorities are maintained only in
[roadmap.md](roadmap.md#current-phase-gate); this dated review is historical.

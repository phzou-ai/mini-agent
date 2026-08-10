# Runtime Composition and Remote Proxy Synchronization

## Status

**Implemented, 2026-08-02.**

This P1 increment removes two remaining lifecycle ambiguities without adding a
queue, broker, worker lease, or distributed scheduler.

## Problem

The normal local A2A path already uses `MainAgentCore`, but two details still
blur the intended ownership boundary:

1. The default application composition previously supplied the core with a
   second service-owned thread pool.
2. `A2AAdapter` previously fetched, cancelled, and persisted remote child-agent Task
   snapshots itself.

The first makes a second lifecycle resource part of the new runtime's execution
path. The second lets a transport adapter mutate process state, and its raw
updates can accept an old remote snapshot after a newer one.

## Composition Contract

- The application lifespan owns the in-process executor used by a default
  `MainAgentCore` composition.
- `MainAgentCore` receives a submitter as a dependency; it owns scheduling
  decisions, while the application owns startup and shutdown of a submitter it
  creates.
- An injected core or injected submitter remains caller-owned and is never
  shut down by `create_app()`.
- There is no alternate service/session lifecycle path in the active codebase.

## Remote Proxy Contract

`A2AAdapter` is a binding and projection layer. It delegates all remote proxy
operations to `MainAgentCore`:

- refresh a remote Task snapshot before projecting a Task or its events;
- forward a cancellation request to the registered child agent;
- persist the accepted remote snapshot, its local Task projection, final
  artifact, and lifecycle event.

The core is therefore the only lifecycle owner for both locally executed Tasks
and locally persisted proxies for child-agent Tasks.

Before a remote snapshot can be persisted, the core also validates its
identity against the durable delegation record:

- a remote snapshot must contain a non-empty remote A2A `taskId`;
- that identifier must exactly equal the delegation's `remoteTaskId`;
- when both sides provide a context identifier, it must equal the delegation's
  `remoteContextId`.

An identity mismatch is a protocol error, not a stale snapshot. It is rejected
before the transaction that could update a local proxy, delegation, event, or
artifact. This prevents a compliant-looking response for the wrong child Task
from being attached to the local process.

## Monotonic Snapshot Rule

The remote protocol currently supplies an A2A state snapshot, not a durable
per-Task revision or ordering token. The local proxy consequently applies a
conservative state rule:

```text
queued -> running -> input_required | auth_required -> terminal
queued -> terminal
running -> terminal
input_required | auth_required -> terminal
terminal -> same terminal state only
```

An equal-state snapshot may refresh delegation diagnostics or fill a missing
final artifact. A snapshot that would move a proxy backwards, leave a terminal
state, or exchange one terminal outcome for another is ignored completely:
it cannot change the local Task, delegation status, events, or artifact.

Remote continuation is not supported yet. If it becomes supported later, the
contract must add a remote revision/timestamp rule before permitting a blocked
proxy to re-enter `queued` or `running`.

## Atomicity

For an accepted snapshot, the core performs the delegated-task update, local
proxy status update, and status synchronization event under the same SQLite
transaction. A completed snapshot may materialize its final answer as a
separate artifact fact before the terminal status event is projected.

## Acceptance Criteria

- A default `MainAgentCore` does not receive a second lifecycle executor.
- App shutdown waits for core-owned in-process work before closing the local
  LangGraph runner.
- A2A binding methods do not contain remote state mutation or remote client
  orchestration.
- A stale `queued` snapshot cannot regress a `running` proxy.
- A stale nonterminal or alternate-terminal snapshot cannot regress a terminal
  proxy or duplicate its final artifact/event.
- Existing remote Task retrieval, cancellation, final artifact projection, and
  A2A JSON-RPC/SSE compatibility remain covered by focused tests.

## Non-Goals

- Remote Task continuation or `SubmitTaskInput` forwarding.
- Polling workers, push notifications, remote stream subscriptions, or a
  snapshot version database column.
- Distributed executor ownership, leasing, or failover.

## Implementation

- `create_app()` creates an application-owned `InProcessTaskExecutor` for its
  default `MainAgentCore`. The FastAPI lifespan waits for that executor before
  closing the local LangGraph runner.
- `MainAgentCore` now owns remote proxy refresh, cancellation forwarding,
  snapshot persistence, final-answer artifact materialization, and status
  event creation. `A2AAdapter` only translates protocol calls and projects the
  resulting records.
- `accepts_remote_proxy_snapshot()` defines the separate remote-proxy state
  policy. Regressive and terminal-conflicting snapshots return the current
  record without changing the delegation, artifact, or event history.
- Remote `resume` and `SubmitTaskInput` remain explicitly unsupported. They
  fail before entering the local continuation path.
- A remote snapshot with a missing or mismatched Task/context identity is
  rejected before it can mutate persisted local state.

## Verification

- Focused remote-proxy contract suite: `5 passed`.
- Core, adapter, and application-lifecycle suite: `120 passed`.
- Full Python regression passes after the composition and remote-identity
  contracts are applied.

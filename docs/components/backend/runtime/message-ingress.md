# Durable Message Ingress

> Status: Stable
> Implemented and hardened: 2026-08-02

This document defines the durable ownership boundary for a top-level A2A
`message/send` or `message/stream` request handled by `MainAgentCore`.
It applies when the incoming Message does **not** carry an existing `taskId`.
Messages that carry a `taskId` are task-continuation input and continue through
the separate pending-continuation contract.

## Problem

Persisting a user Message first is not enough to make delivery idempotent. A
duplicate request could find that Message but still run the router again, create
another Task, invoke a model again, or call a child agent again. Reconstructing
an old result by scanning route decisions, Tasks, and delegations is also not a
durable execution outcome.

## Record

The store owns one `main_agent_message_ingress` record per top-level
`messageId`.

```text
messageId (primary key)
  -> contextId
  -> requestFingerprint
  -> state: in_progress | resolved | failed
  -> routeDecisionId (once selected)
  -> outcome: message | task | delegation (once resolved)
  -> structured error (when failed)
```

`requestFingerprint` covers the user-controlled request body relevant to
execution: role, parts, and metadata. A duplicate `messageId` with another
context, role, parts, or execution metadata is a conflict rather than a retry.

An outcome reference is deliberately narrow:

| Route | Outcome kind | Reference |
| --- | --- | --- |
| Local direct Message | `message` | locally persisted agent `messageId` |
| Local Task | `task` | local A2A `taskId` |
| Remote child-agent result | `delegation` | local `delegationId` |

The delegation record still owns remote task/message identifiers. The ingress
record only owns the fact that this A2A Message has already produced that
delegation outcome.

## Reservation and Completion

For a new top-level Message, `MainAgentCore` performs this order:

1. Resolve or create the Context.
2. Persist the user Message and reserve its ingress record in one SQLite
   transaction.
3. Only the caller that created the ingress record may invoke the router.
4. Persist the route decision and `routeDecisionId` atomically.
5. Persist the route-specific outcome and mark the ingress record `resolved`
   in the same transaction.
6. If routing or dispatch raises before an outcome is persisted, record the
   structured public error and mark the ingress `failed`.

For a local Task, `resolved` means the message has durably produced the Task
identity. It does **not** mean the Task process has completed. Task lifecycle
state remains separately owned by `main_agent_tasks`.

## Duplicate Delivery Contract

| Existing ingress state | Behavior |
| --- | --- |
| `resolved` | Rebuild the result from the stored outcome reference. Do not route or execute again. |
| `in_progress` | Return a retryable `message_in_progress` error while the owning process is alive. Do not route or execute again. |
| `failed` | Return the stored structured failure. Do not route or execute again. |

The SQLite primary-key constraint and durable ingress record are the
correctness boundary across processes and restart. There is no process-local
per-message lock in the active runtime.

## Streaming Semantics

Direct-message token deltas remain ephemeral SSE output. The ingress record is
resolved only when the final agent Message is persisted.

If the direct stream is closed before that final outcome is committed, the core
marks the ingress `failed` with the retryable `message_stream_aborted` error.
At application startup, reconciliation marks any residual `in_progress`
ingress from a previous process as `failed` with retryable
`message_ingress_stale`. In both cases, the original `messageId` replays its
stored failure and cannot route or execute again; a caller must send a new
`messageId` to explicitly retry.

## Scope and Non-Goals

- The active clean-slate SQLite baseline always includes ingress records.
  Historical local data is intentionally discarded rather than backfilled or
  replayed; see
  [Clean-Slate Storage](../../../dev/runtime/clean-slate-storage.md).
- This milestone does not persist a direct-message failure as a conversational
  agent Message. It persists the ingress failure needed for idempotency. The
  separate first-party read-model presentation is implemented in
  [Direct Message Failures](../../../dev/runtime/direct-message-failures.md).
- This milestone does not add final-answer token streaming for LangGraph Tasks,
  worker leases, or automatic replay of uncertain direct-message work.

## Acceptance Checks

- Replaying a resolved local Message, local Task, or remote delegation returns
  the same result without a second router, model, Task, tool, or remote call.
- A concurrent duplicate sees `message_in_progress`, never a second execution.
- An abandoned stream or post-restart residual ingress becomes one persisted,
  retryable failure without a second execution.
- A duplicate with conflicting request content is rejected.
- A failed direct Message replay returns the recorded `{ code, message,
  retryable }` failure rather than invoking the model again.

## Verification

- A resolved direct Message is replayed after reopening the SQLite store without
  another model call.
- An in-progress duplicate through a separate SQLite connection returns the
  retryable `message_in_progress` error and cannot create a second route.
- A stream closed before final persistence records `message_stream_aborted`.
- Startup reconciliation records `message_ingress_stale` for residual
  in-progress ingress and returns its message IDs for inspection.
- A persisted direct-message failure is replayed without a second provider
  call.
- Storage baseline coverage validates creation of the ingress table.
- Full repository regression suite after direct-message failure presentation:
  `492 passed`.

# M3 Transaction And Post-Commit Boundary Handoff

> Status: implemented and validated on 2026-08-16  
> Scope: current single-host SQLite lifecycle and process-local side effects  
> Authority: M3 implementation boundary, evidence, and preserved limits

## Purpose

M3 makes an existing but previously scattered rule explicit: durable lifecycle
state must commit before a local worker, cancellation signal, subscriber, or
child-agent call can act on it. It does not change A2A identity, replace
SQLite, introduce a scheduler, or add a generic infrastructure abstraction.

The implementation introduces `LifecycleTransactionRunner` and named
`LifecyclePostCommitAction` values. A lifecycle workflow returns its committed
result from the existing `AgentStore.transaction()` boundary; only after that
context commits does the runner execute its process-local action.

## Snapshot Model

Vermay persists related facts for different purposes:

| Fact | Role | Key contract |
| --- | --- | --- |
| A2A Task row and management projection | Current public lifecycle snapshot. It is read directly rather than reconstructed from events. | A newer `lifecycle_revision` supersedes an older snapshot. |
| Task event rows | Append-only audit and SSE replay history. | `event_id` is the durable replay cursor; notification only asks subscribers to re-read it. |
| LangGraph checkpoint | Internal execution snapshot used to continue one graph thread. | `thread_id` addresses runtime state and is not a public Task identity. |
| Queued-execution row | Durable intent to execute the next local Task slice. | It must commit before a worker can claim or run it. |

This is not Event Sourcing. The event log does not rebuild the Task row, and a
LangGraph checkpoint does not determine the public A2A state. The lifecycle
transaction coordinates their accepted application facts without merging
their ownership.

## Implemented Contract

```text
typed lifecycle command
  -> validate transition and idempotency
  -> write accepted lifecycle facts in AgentStore.transaction()
  -> commit SQLite transaction
  -> execute one named post-commit action, when required
```

The current post-commit action kinds are intentionally bounded:

- `START_LOCAL_EXECUTION` wakes committed local Task work or continuation;
- `SIGNAL_LOCAL_CANCELLATION` requests cooperative cancellation after the
  durable lifecycle transition; and
- `SEND_REMOTE_MESSAGE` starts child-agent delivery only after Message ingress
  and its route decision commit.

Task-event subscriber wake-up stays in the storage boundary through
`AgentStore.register_after_commit()`. Nested transaction scopes join the outer
SQLite transaction. Their callbacks run only after the outer commit and are
discarded by rollback.

## Migrated Workflows

| Workflow | Durable transaction | Post-commit action |
| --- | --- | --- |
| New local Task admission | Message ingress, route decision, Task/process state, initial events, and queued execution | Wake the local worker |
| Approval continuation | Accepted continuation state and queued next slice | Wake the same local Task |
| Ordinary input continuation | Accepted input state and queued next slice | Wake the same local Task |
| Safe failed-Task retry | One lineage-linked child Task attempt and its queued execution | Wake only the newly created retry |
| Task cancellation | Durable cancel or cancel-requested transition and queue cleanup | Signal cooperative runtime cancellation when active |
| Remote route acceptance | Message ingress and remote route decision | Call the selected child agent |
| Task event publication | Durable event row | Notify subscribers to re-read by `event_id` |

Execution outcomes still return to the lifecycle command surface and are
persisted as accepted outcomes. M3 does not let scheduler, LangGraph, or remote
clients mutate public Task state directly.

## Failure Semantics

- A transaction failure or rollback prevents its post-commit action.
- A post-commit local action failure does not undo already committed lifecycle
  facts. The error is surfaced to the caller, and durable state remains
  available for bounded recovery or inspection.
- Duplicate subscriber wake-ups do not create Task events and cannot duplicate
  replay history; subscribers re-read durable events by cursor.
- A crash after a committed remote route but before or during child delivery
  remains ambiguous. M3 deliberately does not claim exactly-once remote
  delivery and does not add blind automatic replay.
- M3 does not make an uncertain non-read-only tool invocation safe to retry.
  The Tool Invocation Ledger remains the external-effect authority.

## Validation Evidence

- Focused M3 transaction and lifecycle suites passed 171 tests.
- The single-host reliability gate passed 223 backend tests and 18 Playwright
  tests.
- The full-stack gate passed 495 Python tests, frontend type checking, the
  Next.js production build, and the same 18 Playwright tests.
- Coverage includes rollback suppression, commit-before-local-wake,
  commit-before-continuation, commit-before-cancellation signal,
  commit-before-remote-call, and duplicate notification without duplicate
  durable events.

The canonical current-checkout evidence is maintained in the
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md).

## Preserved Limits

- `MainAgentCore` remains the single A2A lifecycle owner and composition point.
- `AgentStore` remains the single SQLite transaction owner;
  `MainAgentStore` remains the production lifecycle adapter.
- The local execution queue and thread pool are unchanged.
- There is no generic repository, outbox, event bus, distributed transaction,
  lease, heartbeat, Redis, PostgreSQL, or Temporal integration.
- Some store methods retain nested transaction scopes because they are shared
  by independently accepted workflows. They join the outer transaction rather
  than opening a second commit boundary.

## Phase Gate

M3 is closed. At M3 close, M4 required explicit selection; M4 was subsequently
selected and completed. The M3 contract remains unchanged: committed
queued-execution intent precedes worker wake-up, and scheduler mechanics remain
separate from A2A lifecycle policy.

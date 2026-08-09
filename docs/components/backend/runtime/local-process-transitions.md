# Local Process Transition Governance

> Status: Stable
> Implemented: 2026-08-01

## Purpose

`main_agent_tasks.status` is the authoritative lifecycle state for a locally
owned Agent Process. M2 makes a state change a validated operation rather than
an unconstrained database update.

The goal is not to introduce another status model. It keeps the existing
`TaskStatus` values and establishes one rule:

> Every post-creation local-process status change is validated and persisted
> with its lifecycle event in one SQLite transaction.

## Scope

This contract applies to a Task that is locally executed by `MainAgentCore`.
It covers queueing, worker start, interruption, continuation queueing,
completion, failure, cancellation, and cancellation after a safe boundary.

It does not yet govern:

- remote-process proxy synchronization, whose transition input is a child A2A
  Task snapshot;
- retired local data outside the active clean-slate storage boundary.

Remote proxy monotonicity remains a separate follow-up because a valid remote
snapshot may arrive later or out of order.

Context deletion is now a separate core-owned control operation: it rejects
live work rather than forcing a lifecycle transition or deleting durable
records under an active process. See
[Lifecycle And State Ownership](../../../architecture/lifecycle-and-state.md#destructive-management).

## State Machine

```text
created -> queued -> running -> completed
                |       |  \
                |       |   +-> input_required -> queued
                |       |   +-> auth_required  -> queued
                |       |   +-> cancel_requested -> canceled | failed
                |       +-> failed
                +-> canceled | failed

created -> running | canceled | failed
input_required -> canceled | failed
auth_required  -> canceled | failed
```

`completed`, `canceled`, and `failed` are terminal. No transition leaves a
terminal state. `cancel_requested` is a short-lived local state used only while
a worker is active; it resolves to `canceled` at the next safe boundary. If a
process restart removes that worker, M4 resolves it to a retryable `failed`
state rather than pretending cancellation completed.

## Transition Events

The transition API derives the event type from the target state:

| Target local status | Lifecycle event |
| --- | --- |
| `queued` | `task_queued` |
| `running` | `task_started` |
| `input_required`, `auth_required` | `task_interrupted` |
| `cancel_requested` | `task_cancel_requested` |
| `completed` | `task_completed` |
| `canceled` | `task_cancelled` |
| `failed` | `task_failed` |

`task_created` is written with the initial Task record. It is not a transition
from another local state.

The following are facts or control-plane audit events, not status transitions:

- `task_resumed`;
- `task_input_submitted`;
- `task_artifact_created` and `task_artifact_updated`;
- `task_delegated` and remote snapshot synchronization.

They do not carry a target local status. A2A status updates are emitted only
from status-bearing lifecycle events; artifact updates use their own A2A
projection.

## Atomicity and Idempotency

For a real transition, the API:

1. reads the current Task record;
2. verifies that it is locally owned and that the requested transition is in
   the table above;
3. updates the Task status and error fields;
4. appends the derived lifecycle event with the target status;
5. commits both operations together.

A request to the already-current target state is a no-op and emits no duplicate
event. Invalid transitions fail before either status or event is persisted.

M4 now uses this transition policy for startup recovery. It does not introduce
distributed worker leases or replay ambiguous execution; see
[startup-reconciliation.md](startup-reconciliation.md).

## Relationship to Other Contracts

- `messageId` ingress decides whether a top-level request may route or execute.
- `input_context_sequence` decides which stored Messages form a local Task's
  initial causal input.
- a pending continuation decides whether a blocked Task may accept approval or
  user input.
- this M2 transition policy decides whether the local Agent Process may change
  lifecycle state.
- A2A TaskState is a projection of the resulting local state; LangGraph emits
  an execution-slice outcome that the local lifecycle layer translates.

## Acceptance

- Complete. `MainAgentCore` uses `create_local_task()` and
  `transition_local_task()` for every locally owned lifecycle mutation.
- Complete. `MainAgentStore.transition_local_task()` validates the current and
  target state, updates the Task, and appends its matching status-bearing event
  inside one SQLite transaction.
- Complete. Duplicate targets are no-ops; invalid and terminal-state
  transitions do not mutate the Task or append a lifecycle event.
- Complete. Audit and artifact events carry no status, so they cannot project
  as misleading A2A status updates.
- Deliberate boundary. Remote proxy synchronization retains its explicit,
  separate path and now applies the monotonic policy documented in
  [Runtime Composition And Remote Proxy](../../../dev/runtime/runtime-composition-and-remote-proxy.md).

## Implementation

- `vermay/main_agent/lifecycle.py` owns the allowed local transition
  table and target-state-to-event mapping.
- `vermay/main_agent/store.py` provides the atomic local-process creation
  and transition operations.
- `vermay/main_agent/core.py` owns the local execution decisions and uses
  those operations for queueing, execution, interruption, continuation,
  completion, failure, and cancellation.

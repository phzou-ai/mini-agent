# Runtime State Ownership

## Purpose

This document defines which layer owns each identity and status. It prevents A2A protocol state, local process state, and LangGraph execution state from being stored as if they were the same thing.

## Identifier Ownership

| Identifier | Owner | Meaning | Lifecycle |
| --- | --- | --- | --- |
| `contextId` | A2A/application | Conversation namespace containing Messages and Tasks. | Long-lived conversation scope. |
| `messageId` | A2A/application | One user or agent Message. | Immutable interaction record. |
| `contextSequence` | Local persistence | Immutable causal order of a Message within one Context. | Allocated once and never reused in that Context. |
| `taskId` | A2A/application | Public identity of one durable Agent Process. | Stable across execution slices and resume. |
| `inputContextSequence` | Local persistence | Input cut copied from the Task's input Message. | Stable for the Task's first execution slice. |
| `runtimeThreadId` | LangGraph runtime | Checkpoint continuation key for one local process execution. | Stable for the process; not a public task identity. |
| `invocationId` | Tool Invocation Ledger | Durable identity of one local non-read-only tool attempt. | Stable for the attempt; never a public Task identity. |
| `remoteTaskId` | Child A2A agent | Task identity owned by a delegated child agent. | Never replaced by the local proxy task id. |
| `eventId` | Local persistence | Monotonic identifier for a persisted local task event. | Used for replay and subscription offsets. |

There is no need for a separate `processId` in the current version. The local `taskId` is the durable process identity exposed through A2A, while `runtimeThreadId` identifies its LangGraph continuation.

## Ingress Idempotency and Outcome Ownership

`messageId` is both the immutable identity of an A2A Message and the idempotency key for its ingress operation. It must not be treated as proof that routing or execution finished.

The application should maintain one durable message-execution record keyed by `messageId`:

```text
messageId
  -> contextId
  -> ingress state
  -> routeDecisionId
  -> outcome reference: agentMessageId | taskId | delegationId | error
```

A duplicate `messageId` loads this record. `resolved` returns the established
outcome; a live `in_progress` record returns retryable `message_in_progress`;
and `failed` returns the stored structured error. Startup reconciliation turns
a previous-process `in_progress` record into retryable
`message_ingress_stale`; an abandoned direct stream becomes
`message_stream_aborted`. None of these duplicate paths may invoke the router,
model, tools, or child agent again. SQLite ownership is the complete
idempotency authority; the active runtime has no process-local message lock.
The detailed record and recovery contract is in
[Durable Message Ingress](../components/backend/runtime/message-ingress.md).

## Status Ownership

### Internal Agent Process Status

The local main-agent store owns the process status:

```text
created
queued
running
input_required
auth_required
cancel_requested
canceled
completed
failed
```

Only the process lifecycle owner may persist or transition these values.

`input_required` and `auth_required` are resumable states. They indicate that the current execution slice ended and the process is waiting for an external continuation.

### Pending Continuation

The blocked process owns a durable pending-continuation record. It is not inferred by scanning lifecycle events.

```text
taskId
  -> kind: user_input_required | approval_required
  -> prompt and optional input schema
  -> capability/tool binding when approval is required
  -> createdAt
```

The current implementation stores this record in `main_agent_pending_continuations`. `approval_required` maps to `auth_required` and is resumed only through the approval operation. `user_input_required` maps to `input_required` and is resumed only through A2A message input carrying the existing `taskId`. Accepting either continuation validates its kind and deletes the pending record atomically before an execution slice is queued; `task_resumed` remains the durable audit fact for that acceptance.

### Queued Execution Command

`queued` is a process state, not proof that an in-process worker still owns the
next execution slice. Before a local Task is submitted, the core persists one
durable queued-execution command in `main_agent_queued_executions`:

```text
taskId
  -> kind: initial | approval | user_input
  -> runtimeThreadId
  -> frozen accepted payload for approval or user input
  -> createdAt
```

The command is created atomically with the `queued` transition. A worker claims
it by atomically deleting it and changing the local Task to `running`. This
separates “safe to submit” from “possibly executing” without introducing a
third public status model. It is also the evidence used by startup
reconciliation: a remaining valid command may be resubmitted, while a claimed
`running` slice is treated as ambiguous after restart. The detailed contract is
in [Startup Reconciliation](../components/backend/runtime/startup-reconciliation.md).

### Tool Invocation Ledger

The Tool Invocation Ledger owns the external-effect attempt state for local
non-read-only ToolNode calls. It is deliberately separate from both the Agent
Process state and the LangGraph checkpoint state:

```text
prepared -> running -> succeeded
                    -> uncertain
prepared -> canceled
```

`failed` remains reserved for a future provably-not-executed result; current
transport and tool-result errors are represented as `uncertain` because the
external outcome cannot be proven. Approval is a separate field:

```text
not_required | pending | approved | rejected
```

The ledger records the Task and runtime-thread binding, tool call identity,
redacted normalized arguments, a digest of the original arguments, capability
metadata, result artifact reference, timestamps, and structured error. It is
prepared before the ToolNode call, starts immediately before the external call,
and becomes succeeded only when the tool result and artifact have been stored.

An approval continuation carries the exact `invocationId`, tool name, and
arguments digest. `MainAgentCore` validates that binding before it queues the
resume. A matching previous `running`, `succeeded`, or `uncertain` effect in
the same Task is blocked from automatic replay. See
[Tool Invocation Ledger](../components/backend/runtime/tool-invocation-ledger.md)
for the full contract.

### Ephemeral Execution Context

R3.1 adds an in-memory `ExecutionContext` only for the period in which a
local execution slice is active. It is not persisted in the main-agent store
or in a LangGraph checkpoint, and it adds no new A2A or Agent Process status.

```text
runtimeThreadId
  -> optional invocationId
  -> optional monotonic deadline
  -> active cancellation signal
  -> optional workspaceId
```

`MainAgentCore` first records `cancel_requested` in the durable process
lifecycle. The process-local `ExecutionContextRegistry` then exposes that
request only to an already active local execution with the matching
`runtimeThreadId`. The LangGraph ToolNode wrapper binds it around one tool
call, and the SSH adapter owns termination of its local child process.

This bridge cannot prove whether a remote write completed after the SSH
connection is terminated. A started non-read-only invocation therefore remains
`uncertain`, never implicitly successful or automatically replayable. The
current SSH/Kubernetes adapters do not have a shared workspace and leave
`workspaceId` unset. See
[workspace-and-isolation-boundary.md](../dev/runtime/workspace-and-isolation-boundary.md).

### A2A Task State

A2A exposes a projection of the internal process status:

| Internal status | A2A state |
| --- | --- |
| `created`, `queued` | `submitted` |
| `running`, `cancel_requested` | `working` |
| `input_required` | `input-required` |
| `auth_required` | `auth-required` |
| `completed` | `completed` |
| `canceled` | `canceled` |
| `failed` | `failed` |

A2A state is not persisted as a competing source of truth. It is generated by the A2A adapter from the local process record and event history.

### Inspector Presentation

The Web Inspector is a read-only presentation layer. For a local Task, it
must keep the following values visibly separate:

| Inspector field | Source | Meaning |
| --- | --- | --- |
| A2A Task | A2A adapter projection | The public protocol state, such as `submitted`, `working`, or `completed`. |
| Local process | Main-agent Task record / A2A metadata | The durable local lifecycle state, such as `queued`, `running`, `input_required`, or `cancel_requested`. |
| LangGraph thread | `runtimeThreadId` | The private checkpoint continuation key for that local process. |

A status-update event may carry the first two values. An artifact event records
output and normally does not change either Task state; a missing status on that
event is therefore expected, not an unknown Task state. The full normalized
event record and its A2A payload remain available as collapsed diagnostics.
The Inspector creates no fourth persisted state model.

### Destructive Management

`MainAgentCore` owns Context deletion and registered-agent removal as control
operations, rather than allowing management routes to delete rows directly.

- A Context is deletable only when every local or remote Task is terminal and
  no active/scheduled local callback still owns it. `force` does not bypass
  this rule; it is reserved for a future explicit cancel-and-wait workflow.
- Before deleting a terminal local Task's application records, the core asks
  the configured local runner to discard its LangGraph checkpoint when that
  runner supports the operation.
- A registered agent is hard-deletable only with no active delegation and no
  retained delegation history. Disabling an existing registration is the
  supported way to stop future delegation while preserving audit facts.

### LangGraph Runtime Outcome

LangGraph reports execution outcomes for one slice:

```text
completed
interrupted with a structured kind
stopped
unknown/error
```

These are not public task states. The local task runner translates them into process transitions. A LangGraph interruption ends a slice; it does not automatically end the Agent Process.

### Governed Execution Summary

The LangGraph checkpoint also retains execution-kernel facts for one local
runtime thread: an immutable `ExecutionPolicy`, model/tool/failure counters,
normalized tool observations, and a typed `stop_reason`. These facts do not
form a third process-state model. The runner returns them to `MainAgentCore`,
which persists an inspectable summary on Task events and artifacts while it
alone performs the local-process transition.

See [governed-execution-kernel.md](../dev/runtime/governed-execution-kernel.md) for policy
limits, stop-reason mapping, and observation persistence.

## Message and Task Relationship

```text
Context
  -> Message
     -> direct answer
        -> ephemeral model invocation
     -> local task route
        -> one Agent Process / A2A Task
           -> multiple LangGraph execution slices
```

A direct Message does not require a durable Task. A Task begins only when the request needs durable lifecycle, tool execution, approval, interruption, artifact output, cancellation, or delegation.

For a local Task, `inputContextSequence` is copied from `inputMessageId` when
the Task is created. Its first worker slice loads only Messages at or before
that cut. Later top-level Messages do not change the Task's initial prompt;
the detailed contract is in [context-input-cut.md](../dev/runtime/context-input-cut.md).

When a Task is waiting for input, the next user Message must carry the same `taskId`. It continues the existing process and `runtimeThreadId`; it must not create a new route decision or a new task.

When a Task is retried, a new `taskId` is created with lineage to the previous task. Retry is a new process attempt, not another execution slice of the original process.

## Event Rules

- Events describe facts about a local process transition or output.
- An event status uses the internal process status vocabulary.
- A2A status updates are projections of events, not separate lifecycle records.
- Artifact events describe output availability; they are not process completion by themselves.
- `task_resumed` describes an accepted continuation action. The surrounding atomic transition consumes the pending-continuation record; the event itself is not used to reconstruct that control state.
- Approval and user-input events must carry an explicit interruption kind.

## Open Boundaries

- The retired service/session lifecycle and its second `TaskStatus` vocabulary
  have been removed. Historical SQLite records are intentionally outside the
  active runtime boundary; see [clean-slate-storage.md](../dev/runtime/clean-slate-storage.md).
- Worker execution and active-task ownership are process-local. M4 now
  conservatively reconciles queued commands after restart, while ambiguous
  claimed work becomes a retryable failure rather than being replayed.
- Remote proxy synchronization retains a separate raw-update path because its
  input is a child-agent snapshot rather than a local transition. It is still
  core-owned and applies a monotonic policy; remote continuation remains
  intentionally absent.
- M6 persists Context order and the initial Task input cut. Router,
  direct-message, and local-Task history now use route-specific character
  limits, while injected runtime context has per-section and total character
  caps. Token-aware budgets, rendered-prompt snapshots, and global tool-output
  caps remain future work.

These are implementation gaps, not reasons to introduce a new process abstraction or distributed infrastructure immediately.

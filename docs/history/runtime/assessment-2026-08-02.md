# Runtime Architecture Assessment - 2026-08-02

> Status: Historical assessment
> Authority: Dated evidence only; not current architecture or active priority

## Scope

This assessment describes the implemented runtime as observed on 2026-08-02.
It is not a target-state design or current architecture authority. Use
[Current System Architecture](../../architecture/current-system.md) for the
implemented system, [Backend Runtime Contracts](../../components/backend/runtime/README.md)
for settled runtime behavior, and [roadmap.md](../../dev/runtime/roadmap.md) for active priority.

```text
A2A Message ingress
  -> MainAgentCore
     -> direct Message result
     -> local Agent Process / A2A Task
        -> in-process worker
           -> LangGraph execution slice and checkpoint
     -> remote child-agent delegation / local proxy
```

SQLite persists Contexts, Messages, durable local Task records, events,
artifacts, pending continuations, route decisions, delegations, and Message
ingress outcomes. The Web UI is an inspection and control surface over those
records; it is not a second lifecycle owner.

## Current Guarantees and Boundaries

The current implementation has a useful, deliberately narrow durability
contract:

| Concern | Current guarantee | Deliberate boundary |
| --- | --- | --- |
| Top-level Message delivery | One `messageId` has one durable ingress owner and cannot route or execute twice; a terminal failure is projected as a distinct UI activity. | An abandoned stream or prior-process `in_progress` ingress becomes an explicit retryable failure; it is never automatically replayed. |
| Context ordering | Every stored Message has a Context-local `contextSequence`. | The sequence orders persisted Messages only; it does not serialize all work in a Context. |
| Local Task initial input | A Task persists the sequence of its input Message and reads history only through that cut. | This is a causal history cut, not a serialized full prompt snapshot. |
| Blocked continuation | Approval and user-input requests are durable, distinct, and consumed atomically by their matching operation. | A post-acceptance worker command is durable; a claimed slice is not automatically replayed after restart. |
| Local worker restart | A remaining queued command is resubmitted once on startup; `running` and `cancel_requested` work becomes an explicit retryable failure. | No leases, heartbeats, distributed workers, or automatic replay of ambiguous execution. |
| A2A Task state | Locally owned state changes are validated and written with their matching lifecycle event atomically. Remote proxy refresh/cancel/snapshot persistence is core-owned and monotonic. | Remote snapshots have no durable ordering token, and remote continuation is not implemented. |

This resolves the most important ordering and lifecycle hazards: a later
independent Message cannot change the initial prompt of an already-created
local Task, and a local Task cannot take an invalid state transition. It does
not claim deterministic replay of every model input.

## Strengths

### Clear identity and ownership boundaries

- A2A `messageId` identifies one inbound or outbound Message and is the
  idempotency key for a top-level ingress operation.
- A2A `taskId` identifies a durable local Agent Process exposed publicly as a
  Task.
- LangGraph `runtimeThreadId` is only the checkpoint continuation key for that
  local process.
- `MainAgentCore` owns normal A2A routing, local lifecycle decisions, and
  locally persisted child-agent proxy lifecycle; LangGraph owns local execution
  and checkpoint continuation only.

This prevents the public protocol, durable process lifecycle, and graph
runtime from becoming one overloaded `task` abstraction.

### Safe duplicate delivery handling

`main_agent_message_ingress` provides one durable record per top-level
`messageId`. SQLite uniqueness and the durable ingress record, rather than an
in-memory lock, decide who may route and execute an inbound request.

As a result, a duplicate cannot create another route decision, model call,
local Task, delegation, or tool call. A resolved request replays its stored
outcome; a live unresolved request returns retryable `message_in_progress`; a
failed request replays its structured failure. Startup converts a residual
previous-process ingress to `message_ingress_stale`, and an abandoned direct
stream becomes `message_stream_aborted`. This is especially important before
dangerous write-capable tools become real.

### Durable causal task input

The Task record stores both its input Message identity and the corresponding
Context sequence. The router and direct responder also read through the input
Message boundary. A queued worker receives only `taskId` and reconstructs its
initial history from SQLite, rather than receiving a mutable Python list.

This is a strong fit for the current runtime: it fixes a real async race
without introducing a separate prompt-snapshot service or a workflow engine.

### Correct interruption control-plane direction

Approval and ordinary user-input interrupts have separate continuation kinds.
The pending continuation is stored independently from the event history and is
consumed atomically when the corresponding public operation is accepted. A
background worker receives a frozen accepted command rather than inferring
control state from lifecycle events.

### Pragmatic current deployment shape

SQLite plus an in-process worker remains appropriate for local development and
the current single-host service. It keeps the system debuggable, avoids an
unnecessary scheduler or broker, and permits lifecycle, route, event, and
artifact inspection through one persistence boundary.

### Strong inspection foundation

Contexts, Messages, route decisions, Tasks, task events, artifacts, pending
continuations, and remote delegations are inspectable records. The Web UI can
therefore present a conversation transcript alongside process diagnostics
without reconstructing lifecycle facts from presentation state. For local
Tasks, the Inspector separately presents the A2A public state, the durable
local process state, and the LangGraph checkpoint thread. Artifact events are
shown as output facts rather than falsely implying a Task state transition;
their raw records remain collapsed diagnostics.

## Limitations and Tradeoffs

### Safety is ahead of liveness

An interrupted direct-message stream or a process crash never causes silent
re-execution under the same `messageId`. The stream closes as retryable
`message_stream_aborted`; startup changes residual ingress to retryable
`message_ingress_stale`. A caller must create a new Message for a deliberate
retry.

This is a deliberate safety-first tradeoff. Local Task startup reconciliation
can resubmit only an unclaimed queued command, while ambiguous work becomes a
visible failure. The runtime does not claim automatic recovery of uncertain
model or tool work.

### Lifecycle integrity is closed for the single-host boundary

`MainAgentCore` owns destructive Context and registered-agent management.
Deleting a Context with live local or remote work returns a conflict, including
when the API receives `force=true`; terminal local checkpoints are discarded
before their application records are removed. A registered child agent with
delegation history is retained and can be disabled rather than erased.

Initial asynchronous Task acceptance is one SQLite transaction: route
decision, immutable input cut, Task creation, ingress resolution, transition
to `queued`, and initial durable execution command commit together. The
executor is scheduled only after that transaction returns. A failed queue write
rolls the complete acceptance back rather than leaving an orphaned `created`
Task.

### Task input causality and current prompt bounds are durable

Each Context Message now has a persisted sequence and each local Task captures
the sequence of its input Message. A worker receives only `taskId` and loads
initial history through that durable cut, so later Messages cannot enter a
queued Task's initial prompt and a restart does not lose an in-memory history
list.

The current prompt policy is explicit but intentionally modest. Router,
direct-message, and local-Task paths have different character-bounded history
policies, and injected MCP prompts, skills, memory, and MCP resources have
separate per-section and total character caps. The runtime does not yet budget
model tokens, summarize old history, globally cap tool output, or persist a
complete rendered prompt. A changed system prompt, model selection, skill set,
MCP resource, tool catalog, or memory result can still change an execution
after it was queued or recovered. The implemented contract is documented in
[Durable Context Input Cut](../../components/backend/runtime/context-input-cut.md).

That tradeoff is appropriate now. Persist a full prompt or capability snapshot
only when reproducible execution, audit requirements, or recovery behavior
needs it; do not mislabel the current causal cut as deterministic replay.

### Local lifecycle governance is centralized; remote proxy policy is separate

Locally owned Agent Processes now use one transition table. A real change
validates the previous status, updates the Task, and appends the matching
status-bearing event within one SQLite transaction. Duplicate target states are
no-ops, while invalid and terminal-state transitions fail without writing a
partial event trail. `MainAgentCore` uses that path for creation, queueing,
execution, interruption, continuation, completion, failure, and cancellation.

`MainAgentStore.update_task_status()` remains only for core-owned remote proxy
snapshot synchronization. It is not the normal
local lifecycle API. Remote snapshots deliberately use their own monotonic
policy rather than being forced into the local state machine: a stale snapshot
cannot move a proxy backward, leave its first observed terminal state, or
replace one terminal outcome with another.

### The execution boundary is single-host

The message-ingress guarantee applies to callers sharing the same SQLite
database. The current in-process thread pool has no durable lease, worker
heartbeat, distributed queue, failover, or cross-node scheduler. Separate
database replicas would not share the same idempotency boundary.

This is an explicit deployment limit, not a reason to add Temporal, Redis, or
a distributed queue before recovery requirements are demonstrated.

### Top-level messages and Task continuations are intentionally different

A new top-level user Message uses `messageId` ingress and may route to a direct
answer, local Task, or child agent. A user response to an existing blocked Task
carries its `taskId` and uses the pending-continuation contract instead.

The distinction is semantically correct, but the API and UI must continue to
make it clear that an approval or `SubmitTaskInput` action resumes a process;
it is not a new top-level request.

### Streaming is asymmetric by design

Direct Messages can stream token deltas over SSE and commit the final Message
when complete. Local LangGraph Tasks currently emit lifecycle events and a
final artifact rather than final-answer token deltas. Task final-answer
streaming remains deferred until observed latency justifies its added contract
and recovery complexity.

## Architecture Verdict

The current runtime is a sound **single-host A2A main-agent foundation**. Its
strongest property is its explicit separation of A2A identity, application
process lifecycle, and LangGraph continuation. Uncertain claimed execution is
held in an inspectable state rather than silently repeated, and local queued
Task slices have conservative startup reconciliation.

The lifecycle integrity boundary is now closed for the intended single-host
deployment: destructive management is core-owned, initial asynchronous Task
acceptance is atomic, abandoned direct ingress has an explicit failure outcome,
and local non-read-only tool attempts have a durable invocation and uncertainty
record. The remaining limitation is that `uncertain` effects require operator
reconciliation rather than automatic replay; distributed worker ownership
remains a deliberate deployment boundary.

Its second tradeoff is reproducibility: it now preserves the causal set of
conversation Messages, but not the full dynamic execution environment. That is
the right level of complexity for the current development stage, provided the
boundary remains explicit in code and documentation.

It should not yet be described as a distributed Agent OS. It is better
understood as a compact, inspectable runtime that can evolve toward that role
once restart recovery and deployment coordination are reliable.

## Recommended Evolution Order

1. **Stabilize bounded execution and evidence-based verification.** Exercise
   the R0-R3.1 single-host baseline through real workflows and correct issues
   within its existing ownership boundaries.
2. **Expand only with evidence.** Activate a workspace, persistent plan, or
   distributed scheduling only when its documented workload signal exists and
   a bounded extension is justified.

The feasibility and entry criteria for these stages are defined in
[runtime-evolution-path.md](../../dev/runtime/runtime-evolution-path.md). The active backlog and
acceptance criteria remain in [roadmap.md](../../dev/runtime/roadmap.md).

The retired service/session stack and its historical local databases were
explicitly removed in the clean-slate storage cut. The current baseline and
reset boundary are documented in
[Clean-Slate Storage Baseline](../../components/backend/runtime/clean-slate-storage.md).

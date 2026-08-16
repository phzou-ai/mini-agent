# M4 Bounded Local Execution Handoff

> Status: implemented and validated on 2026-08-16  
> Scope: current single-host SQLite queue, process-local scheduling, and LangGraph execution slices  
> Authority: M4 implementation boundary, evidence, and preserved limits

## Purpose

M4 separates committed lifecycle intent from process-local execution mechanics
without replacing the current single-host mechanism. The SQLite queue remains
the durable authority for pending local work, while `MainAgentCore` remains the
only owner of public A2A Task and durable local-process lifecycle state.

The new boundary is intentionally narrow. It makes the current thread-pool
implementation replaceable in tests and keeps scheduler mechanics out of the
lifecycle facade, but it does not introduce a scheduler framework, workflow
engine abstraction, lease, heartbeat, or distributed worker protocol.

## Ownership Boundary

| Owner | Responsibility | Explicitly does not own |
| --- | --- | --- |
| `MainAgentCore` | Accept lifecycle commands, atomically claim queued work, capture the initial causal input cut, and persist typed outcomes. | Thread-pool bookkeeping or runner dispatch details. |
| `MainAgentStore` | Persist versioned queued commands and atomically delete one command while transitioning its Task to `running`. | Process-local wake-up or execution. |
| `InProcessLocalExecutionAdapter` | Wake committed work, deduplicate scheduled/active Task IDs in one process, dispatch the correct runner method, forward cancellation/checkpoint operations, and return a typed outcome. | Task state, events, artifacts, Messages, retry policy, or A2A projection. |
| `MainAgentTaskRunner` / LangGraph | Execute one bounded initial or continuation slice using the supplied `thread_id`. | Public Task identity or lifecycle persistence. |
| `InProcessTaskExecutor` | Provide current thread-pool capacity. | Queue correctness, lifecycle policy, or durable recovery. |

## Durable Command Contract

`main_agent_queued_executions` now stores a versioned execution command:

```text
task_id
kind = initial | approval | user_input
runtime_thread_id
command_version = 1
payload = kind-specific JSON
```

The in-memory command payload is an immutable typed value:

- `InitialTaskExecutionPayload` has no continuation data;
- `ApprovalTaskExecutionPayload` carries the accepted decision and optional
  reason; and
- `UserInputTaskExecutionPayload` carries an immutable copy of accepted A2A
  parts and metadata.

Serialization and deserialization validate the command version, kind, and
payload shape. Unsupported versions and malformed payloads fail explicitly
during durable read or startup reconciliation. Runner code never receives the
mutable dictionary originally supplied by a caller.

## Unified Execution Flow

```text
typed lifecycle command
  -> transaction persists Task/process state/events/queued command
  -> commit
  -> post-commit adapter wake
  -> Core callback atomically claims command and transitions Task to running
  -> adapter dispatches run/resume/resume_input
  -> adapter returns LocalExecutionSucceeded or LocalExecutionFailed
  -> Core records the accepted outcome in a new lifecycle transaction
```

Initial Tasks, approval continuation, and ordinary input continuation all use
this same durable queue, claim, dispatch, and outcome path. The previous direct
runner path has been removed. When no submitter is injected, the adapter runs
the same path synchronously; this is a deterministic test/composition mode, not
a second product lifecycle implementation.

The initial execution input is captured only after the command is claimed. It
uses the Task's persisted `input_context_sequence`, so Messages accepted while
the Task waits in the queue cannot change that Task's original causal input.

## Failure, Recovery, And Effect Safety

- A runner exception becomes a typed failed outcome and is persisted by the
  Core-owned outcome path.
- Failure while persisting a nominally successful result is converted into one
  durable Task failure; partial output artifacts or Messages roll back.
- Startup reconciliation resubmits only a valid unclaimed queued command.
  Claimed `running` work is not replayed after process loss.
- Process-local scheduled and active sets are wake-up optimizations only.
  SQLite atomic claim remains the correctness boundary.
- Cooperative cancellation and checkpoint deletion are forwarded by the
  adapter, but their lifecycle decision remains in `MainAgentCore`.
- A started non-read-only tool effect with an unknown outcome remains
  `uncertain` in the Tool Invocation Ledger and is never made replayable by
  queue recovery.

## Validation Evidence

- The focused store and Core suite passed 85 tests.
- `scripts/check_single_host_reliability.sh` passed 225 backend tests and 18
  deterministic Playwright tests.
- `scripts/check_full_stack_regression.sh` passed 497 Python tests, frontend
  type checking, the Next.js production build, and the same 18 Playwright
  tests.
- `scripts/a2a_dev_smoke.sh` passed against an isolated current-code server
  using the configured real model. It exercised direct Message, local Task,
  `message/stream`, `tasks/resubscribe`, and the late-cancel boundary without
  using a destructive tool.

The canonical current-checkout evidence is maintained in the
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md).

## Preserved Limits

- This remains a one-process, one-host execution mechanism.
- There is no durable lease, heartbeat, worker ownership record, backpressure
  policy, distributed scheduler, Redis, PostgreSQL, or Temporal integration.
- Process termination after queue claim remains a conservative retryable Task
  failure, not automatic execution replay.
- Final-answer Task token streaming remains deferred.
- The adapter is not a generic workflow interface and is not shaped around a
  hypothetical future middleware API.

## Phase Gate

M4 is closed. At M4 close, M5 required explicit selection; M5 was subsequently
selected and completed. The versioned command, atomic claim, typed outcome,
and single lifecycle-owner contracts established here remain prerequisites for
M6 or any later work.

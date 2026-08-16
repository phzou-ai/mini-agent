# Startup Reconciliation

> Status: Stable
> Implemented and extended for direct-message ingress: 2026-08-02

## Purpose

SQLite preserves local Agent Process records and LangGraph checkpoints, but the
current worker is an in-process thread pool. A process restart therefore loses
the in-memory executor queue, active-task set, and worker callback. This
contract defines the conservative recovery policy used when the FastAPI
application starts.

The policy favors correctness over automatic liveness: it resubmits only work
that is proven not to have started. It never guesses whether a model or tool
call may already have run.

## Durable Queued Execution Command

A locally owned Task that is ready for an in-process worker has two durable
records:

```text
main_agent_tasks
  status = queued
  runtime_thread_id = <LangGraph checkpoint key>

main_agent_queued_executions
  task_id = <same Task>
  command_version = 1
  kind = initial | approval | user_input
  runtime_thread_id = <same checkpoint key>
  payload = immutable typed execution data
```

The command is created in the same SQLite transaction as the transition to
`queued`. A worker claims it by deleting the command and transitioning the
Task to `running` in one transaction. Thus:

- a remaining command proves that no worker has claimed that execution slice;
- a `running` Task proves only that a worker claimed the slice, not that it
  completed safely;
- continuation payload is not reconstructed from audit events.

Queue deserialization is strict. The store rejects unsupported command
versions, mismatched kind/payload combinations, malformed continuation data,
and runtime-thread mismatches. This keeps startup recovery from guessing how
to execute an older or invalid command after code changes.

The command types have explicit meaning:

| Command | Worker action |
| --- | --- |
| `initial` | Build the Task's stored initial input cut and call `LocalTaskRunner.run()`. |
| `approval` | Resume the same `runtimeThreadId` with the accepted approval decision. |
| `user_input` | Resume the same `runtimeThreadId` with the accepted user input. |

## Startup Algorithm

`MainAgentCore.reconcile_startup()` is called from the FastAPI lifespan before
the application begins serving requests. It first reconciles top-level direct
Message ingress, then processes locally owned Tasks:

| Persisted direct-message ingress | Recovery action |
| --- | --- |
| `in_progress` from the previous process | Mark `failed` with retryable `message_ingress_stale`. The original `messageId` remains a replay of that failure, not a new execution. |
| `resolved`, `failed` | Do nothing. |

For locally owned Tasks:

| Persisted state | Recovery action |
| --- | --- |
| `queued` with a valid matching command and available worker | Resubmit once to the in-process executor. |
| `queued` without a command, with invalid payload, mismatched thread, unavailable worker, or failed submission | Transition to `failed` with a typed retryable recovery error. |
| `running` | Transition to `failed` with `runtime_restart_interrupted`. |
| `cancel_requested` | Transition to `failed` with `runtime_restart_interrupted`; there is no worker left to reach a cancellation safe boundary. |
| `input_required`, `auth_required` | Retain unchanged with their pending continuation record. |
| terminal states and remote proxies | Do nothing. |

When a local Task is failed during reconciliation, its invocation ledger is
reconciled in the same transaction: `running` non-read-only invocations become
`uncertain` with the task recovery error, and `prepared` invocations become
`canceled`. The runtime never assumes that an interrupted external call did not
reach the external system.

Recovery submission is idempotent within one process: the local execution
adapter tracks scheduled and active Task IDs as disposable optimizations.
Across processes, the Core-owned SQLite claim remains the correctness
boundary; at most one worker can delete the command and transition the Task to
`running`.

## Failure Semantics

Recovery failures use the normal local lifecycle transition and append a
`task_failed` event. Their payload includes:

```json
{
  "error_code": "runtime_restart_interrupted",
  "error_message": "Local task execution was interrupted by a runtime restart.",
  "retryable": true
}
```

The error is retryable because an operator may create a new Task attempt after
checking its original intent and side-effect risk. It does **not** mean the
runtime automatically replays ambiguous work.

## Boundaries

- This is a single-host recovery policy. It does not introduce worker leases,
  heartbeats, distributed scheduling, or cross-node failover.
- It does not replay a top-level Message ingress left `in_progress`; that
  ephemeral invocation becomes a visible retryable failure because its
  execution outcome is uncertain.
- It does not use a LangGraph checkpoint to replay a claimed `running` slice.
  Safe checkpoint recovery requires explicit idempotency and capability policy
  beyond the current runtime.
- A blocked Task retains its `runtimeThreadId`; a later valid approval or input
  resumes it through the normal continuation contract.

## Verification

Focused tests cover:

- initial queued work after a store restart;
- durable approval and ordinary-input continuation commands after restart;
- rejection of unsupported queue-command versions and invalid typed payloads;
- failure of `running`, `cancel_requested`, and malformed/missing queued work;
- retention of `input_required` and `auth_required` Tasks;
- conversion of a `running` non-read-only invocation to `uncertain` when its
  Task is failed after a restart;
- conversion of residual direct-message ingress to
  `message_ingress_stale` without rerouting it;
- active storage-baseline coverage for `main_agent_queued_executions`.

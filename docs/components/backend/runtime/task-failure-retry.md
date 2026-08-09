# Task Failure Projection and Safe Retry

> Status: Stable
> Implemented: 2026-08-04

## Purpose

This document closes two current single-host reliability gaps without adding a
new scheduler, replay mechanism, or A2A lifecycle owner:

1. a local Task failure must remain understandable after the original SSE
   connection ends or the browser reloads; and
2. an operator must be able to make a safe, explicit second attempt when the
   failed work has not reached a potentially side-effecting tool boundary.

This is a reliability refinement of the existing local Task path. It is not
automatic recovery and it does not change the public A2A Task lifecycle.

## Durable Failure Fact

`main_agent_tasks` persists three safe fields for a terminal local Task
failure:

```text
error_code
error_message
error_retryable
```

They are written in the same local-process transition that writes the failed
Task lifecycle event. The A2A snapshot and terminal `status-update` project
the same values as `localErrorCode`, `localErrorMessage`, and
`localErrorRetryable`. The first-party management/read model exposes the
stored fields directly.

This avoids treating a single historical event payload as the authoritative
answer to whether a Task can be retried. A page reload, event selection change,
or new SSE subscription sees the same Task failure fact.

Provider/transport failures may be retryable. A model protocol failure is not:
the model returned an invalid Task action rather than being temporarily
unavailable. Public error text remains sanitized; raw provider diagnostics stay
in local logs only.

## Canonical Conversation Answer

Task artifact SSE can arrive before the durable assistant Message is fetched
from the Context read model. The browser therefore creates a temporary
`pending:*` assistant item while the stream is live.

On a terminal Task `status-update`, the console refreshes the durable Task
snapshot and Context messages. A durable assistant Message with the same
`taskId` replaces that temporary item. The conversation has exactly one answer
for that Task, regardless of whether the durable read or a late stream error
arrives first.

The browser no longer ends a Task event stream after an arbitrary short client
timeout. Slow-model bounds belong to the provider and optional Task execution
budget in the backend. A completed Task is still recovered from durable
Context/Task records when a stream ends ambiguously.

## Manual Retry Contract

The first-party control-plane endpoint is:

```text
POST /api/management/tasks/{taskId}/retry
```

The Web UI uses its BFF proxy for this endpoint. It is not an A2A method and
does not create an alternate A2A lifecycle path.

The core accepts a retry only when all conditions hold:

1. the source Task exists and is locally owned;
2. it is terminally `failed`;
3. its persisted `error_retryable` flag is true; and
4. its Tool Invocation Ledger contains no invocation with a side-effect level
   other than `none`.

If accepted, the source Task remains immutable. The core creates, in the same
Context:

```text
new user Message
-> new durable message ingress
-> new local-task route decision
-> new Task (attempt + 1, retry_of_task_id = source task)
-> new LangGraph runtime thread
```

It then schedules the new Task through the normal local execution path. The
source records `task_retry_requested`; the child records `task_retried`.

A partial unique index on `retry_of_task_id` is the concurrency boundary. Two
simultaneous operator clicks return the same child Task rather than starting
two new attempts.

## Non-Goals and Safety Limits

- No automatic retry of any Task.
- No reuse of the source `taskId`, ingress ID, or LangGraph thread.
- No generic retry for remote/proxy Tasks.
- No replay of a Task with recorded potentially side-effecting tool work,
  including uncertain work after a failure or restart.
- No attempt to reconcile an external system before retrying. That requires a
  capability-specific workflow, not the generic retry control.

The distinction is deliberate: retryability describes whether an operator may
create a new attempt, while the Tool Invocation Ledger decides whether that
new attempt would be unsafe to create generically.

## Deterministic Evidence

- Python core tests verify durable failure retryability, a new Task/thread
  lineage, idempotent repeated retry, and rejection after a potentially
  side-effecting invocation.
- API tests verify the management retry endpoint and its duplicate-request
  convergence.
- Browser reliability tests verify the Retry control and that a transient Task
  artifact is replaced by exactly one durable assistant answer.

## Cleanup Scope

The stabilization work also removed two proven-redundant pieces of historical
overhead:

- `vermay serve` no longer accepts `--enable-a2a`: A2A routes are the
  normal serve mode, so the flag could not change runtime behavior.
- A newly created SQLite database now creates the final partial unique retry
  lineage index directly. Migration v3 still upgrades existing databases that
  carry the former non-unique index.

Archived hands-on runtime material and its narrow harness modules remain
outside the product execution path. They are intentionally retained as
historical/reference material rather than being treated as active runtime
compatibility code.

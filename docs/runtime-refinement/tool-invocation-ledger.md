# Tool Invocation Ledger

**Status:** implemented for local non-read-only ToolNode calls, 2026-08-02.

## Purpose

LangGraph checkpoints record graph continuation, not whether an external
system received a tool call. The Tool Invocation Ledger is the minimal durable
record for that missing fact. It prevents the runtime from treating a graph
replay as proof that an external side effect is safe to repeat.

It is not a second A2A Task implementation and it is not an execution
scheduler. `MainAgentCore` still owns Agent Process lifecycle, while LangGraph
still owns one execution slice and checkpoint continuation.

## Scope

The initial boundary applies only to locally owned, non-read-only tools invoked
through `ToolNode`. Read-only tools keep their existing direct path. Remote
child-agent calls and general MCP replay policy remain outside this increment.

## Record And State

Each record contains:

```text
invocationId
taskId, contextId, runtimeThreadId
loop index, ToolNode call id, tool name
redacted normalized arguments and digest of the original arguments
capability and side-effect metadata
approval requirement and approval decision
effect status, result artifact reference, timestamps, and structured error
```

Effect status is independent from both local Agent Process status and the A2A
Task state:

| Status | Meaning |
| --- | --- |
| `prepared` | The effect has a durable identity but has not started. |
| `running` | The runtime has crossed the boundary immediately before external execution. |
| `succeeded` | The tool result and its Task artifact were persisted. |
| `uncertain` | The external effect may have happened, but the runtime cannot prove its outcome. |
| `canceled` | A prepared call was rejected, canceled, or invalidated before execution. |
| `failed` | Reserved for a future outcome proven not to have executed. |

Approval is separate from effect status: `not_required`, `pending`, `approved`,
or `rejected`.

## Lifecycle

1. Permission evaluation identifies a non-read-only call and creates or reloads
   a deterministic ledger record before `ToolNode` can execute it.
2. If approval is required, the LangGraph interruption carries the exact
   `invocationId`, tool name, and arguments digest. `MainAgentCore` validates
   that binding when it accepts the approval continuation.
3. Immediately before calling the external capability, the adapter transitions
   the record from `prepared` to `running`.
4. A normal ToolNode result becomes `succeeded` and creates a result artifact.
   A tool error or an exception at the boundary becomes `uncertain`.
5. Task rejection, cancellation, failure before the call, or restart recovery
   cancels records still `prepared`. A running record becomes `uncertain` when
   the Task ends without a durable tool outcome.

## Replay And Recovery

R1 does not claim exactly-once external execution. It makes ambiguity visible
and blocks automatic repetition. A matching previous non-read-only call in the
same Task with `running`, `succeeded`, or `uncertain` status is denied before
ToolNode execution. A deliberate retry is a new Task attempt and therefore a
new effect identity.

On startup reconciliation, a local Task that had a claimed `running` worker
slice is failed conservatively. Its `running` ledger entries become
`uncertain`; prepared entries become `canceled`. An operator can inspect the
facts and reconcile the external system before choosing a new action.

## Inspection

The first-party read model is:

```text
GET /api/tasks/{task_id}/tool-invocations
```

It exposes ledger facts, including the redacted normalized arguments, approval
state, effect status, structured error, and result artifact id. This endpoint
does not create a competing A2A lifecycle API; it is an application inspection
surface for the local Agent Process.

## Deliberate Boundaries

- Argument digests are computed from the original structured arguments so that
  redaction does not collapse different sensitive calls into one identity.
- The result artifact records the ToolNode response. It is not proof that the
  external system committed a write beyond what the tool itself reports.
- There is no automatic external reconciliation or retry of `uncertain`
  effects.
- No distributed lock, worker lease, planner, or workspace abstraction is
  introduced by this milestone.

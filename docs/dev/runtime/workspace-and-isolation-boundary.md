# Workspace And Isolation Boundary

## Status

**R3.1 implemented, 2026-08-02.** The first concrete execution boundary covers
the existing SSH-backed Kubernetes capabilities. It adds bounded local-process
control without claiming that the project now has a general workspace,
sandbox, or distributed worker system.

## Purpose

The current runtime has real host-reaching capabilities: SSH-backed Kubernetes
read operations and an approval-gated destructive delete operation. R1 gives a
non-read-only operation a durable effect identity; R2 gives the enclosing
LangGraph slice a budget and evidence model. R3.1 connects those facts to the
actual local `ssh` child process.

It answers three concrete questions:

1. How does a durable `cancel_requested` Task reach an already running local
   capability adapter?
2. How does the adapter respect the remaining execution-slice deadline?
3. What outcome is recorded when local cancellation or timeout cannot prove
   whether a remote write was already applied?

## Implemented Boundary

```text
MainAgentCore
  -> durable Task cancel_requested
  -> DirectLangGraphLocalTaskRunner
  -> ExecutionContextRegistry (in-memory active thread signal)
  -> LangGraph ToolNode wrapper
  -> SshClient local ssh subprocess
  -> remote Kubernetes command
```

### Ownership

| Layer | Owns | Does not own |
| --- | --- | --- |
| `MainAgentCore` | Durable A2A Task cancellation request and public lifecycle projection. | Local process handles or a second execution state machine. |
| `ExecutionContextRegistry` | Process-local cancellation signal for an active `runtimeThreadId`. | Persisted Task state, restart recovery, or public identity. |
| LangGraph ToolNode wrapper | Binds the active signal, remaining R2 elapsed-time budget, and R1 `invocationId` around one tool call. | SSH process management or A2A status changes. |
| `SshClient` | Local `ssh` subprocess start, polling, timeout, terminate/kill, and structured result metadata. | Remote task lifecycle or proof of remote write completion after disconnection. |
| Tool Invocation Ledger | Conservative durable outcome for a started non-read-only effect. | General execution scheduling. |

`ExecutionContext` is deliberately ephemeral. It contains an optional
`runtimeThreadId`, `invocationId`, monotonic deadline, cancellation signal, and
opaque `workspaceId`. It is never stored in SQLite and disappears when the
current execution slice ends or the process restarts.

### Cancellation And Timeout Semantics

| Situation | Adapter behavior | Durable consequence for non-read-only tool calls |
| --- | --- | --- |
| Cancellation arrives before `ssh` starts. | No local process is created; the adapter returns structured `execution_canceled`. | The current ledger conservatively marks an already-started non-read-only invocation `uncertain`, even though this adapter can report that no local SSH process was launched. |
| Cancellation arrives while local `ssh` is running. | The adapter terminates the local child, then kills it if it does not exit promptly. | The started invocation becomes `uncertain`: the remote command may already have reached the host. |
| The R2 deadline or SSH timeout expires while local `ssh` is running. | The same terminate/kill sequence runs and returns structured `execution_timeout`. | The started invocation becomes `uncertain`; it is never automatically replayed. |
| SSH exits nonzero. | The adapter returns `ok: false`, an error code/category, retryability, and execution metadata. | A started non-read-only invocation is conservatively `uncertain`, not `succeeded`. |

The R2 observation normalizer treats a structured `{ "ok": false }` tool
result as a failed observation even when LangGraph transports it in a nominally
successful `ToolMessage`. This prevents a failed adapter call from becoming
evidence for a completed task.

## Current Non-Goals

R3.1 intentionally does **not** add:

- arbitrary host shell execution;
- a generic local working directory or repository workspace;
- file snapshots, rollback, or workspace cleanup;
- Docker, microVM, or container isolation;
- a worker scheduler, leases, remote worker control, or distributed recovery;
- a claim that every MCP capability belongs to a workspace.

The current SSH/Kubernetes tools leave `workspaceId` unset. They are fixed
capability adapters, not a generic shell exposed to the model.

## Activation Criteria For Broader R3

Add a real workspace adapter only after a concrete workload needs a shared
filesystem or command namespace, such as repository inspection, multi-step
file changes, generated artifacts, or cleanup/rollback of local work.

Add stronger isolation only when the workload executes arbitrary commands,
handles untrusted repositories, accepts untrusted artifacts, or needs a trust
boundary that cannot be supplied by the current host process. Choose Docker,
a remote worker, or a microVM from that workload's isolation and operations
requirements; do not select the technology merely because a generic execution
abstraction exists.

## Verification

Focused regression tests cover:

- active-context registration and cancellation visibility;
- ToolNode binding of the `runtimeThreadId` to a capability invocation;
- SSH success metadata, pre-start cancellation, in-flight cancellation, and
  timeout termination;
- conservative Tool Invocation Ledger handling for structured failed write
  results;
- R2 observation classification for `{ "ok": false }` results; and
- `MainAgentCore` forwarding durable cancellation to a runner that supports
  the active execution signal.

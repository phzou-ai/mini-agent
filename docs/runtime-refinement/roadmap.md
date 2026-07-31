# Runtime Refinement Roadmap

## Scope

This roadmap addresses the current runtime review findings:

1. multiple production ingress and lifecycle paths;
2. duplicate task status models and projection logic;
3. in-process execution without restart reconciliation;
4. ambiguous approval and user-input interruption semantics;
5. registered dangerous tools that are still placeholders;
6. duplicated and weakly bounded context assembly.

It does not add a new product feature. It makes the current A2A, LangGraph, SQLite, MCP, approval, and Web UI behavior easier to reason about and safer to extend.

## Recommended Order

### M0. Freeze the Runtime Boundary

**Purpose:** establish the baseline before changing ownership.

**Work:**

- document all active A2A bindings and which implementation they call;
- identify every current task table, task status enum, event projection, and checkpoint path;
- add no new behavior in this milestone;
- record the current full-stack regression baseline.

**Acceptance:** the repository has one inventory of active and compatibility paths, and future changes can identify the authoritative owner of each record.

### M1. Make `MainAgentCore` the A2A Lifecycle Owner

**Priority:** P0

**Status:** Implemented. The A2A adapter now requires `MainAgentCore` for lifecycle operations, and path-style bindings delegate to the same core as JSON-RPC. The legacy service remains available to `/api/*` management code but is no longer an A2A lifecycle fallback.

**Problem:** JSON-RPC requests currently use `MainAgentCore`, while some path-style A2A bindings still call the legacy `AgentService`. The same logical A2A operation can therefore create different records and statuses.

**Work:**

- route `/rpc` and supported A2A message/task operations through `MainAgentCore`;
- make the path-style bindings thin compatibility adapters to the same core, or explicitly remove them after the Web UI no longer needs them;
- keep `/api/*` as a first-party management/read-model surface, not a second public agent lifecycle;
- ensure `A2AAdapter` does not select a lifecycle owner based on request shape;
- preserve response and event compatibility through projection helpers rather than duplicate execution code.

**Acceptance:** the same A2A Message produces the same context, task, event, artifact, and status behavior regardless of whether it arrives through the supported binding or a temporary compatibility binding.

### M2. Establish One Internal Process Status Model

**Priority:** P0

**Problem:** `main_agent` and the legacy API define different task status sets, while A2A has a third vocabulary.

**Work:**

- keep one authoritative internal status model for locally owned Agent Processes;
- define explicit projection tables to A2A TaskState and legacy compatibility status;
- centralize allowed transitions and event creation;
- clarify that `queued` and `running` are process states, while `submitted` and `working` are A2A projections;
- remove status decisions from individual routes and background callbacks;
- keep retry as a new task with lineage, not a status mutation of the old task.

**Recommended internal model:**

```text
created -> queued -> running
                     |\
                     | +-> input_required
                     | +-> auth_required
                     | +-> completed
                     | +-> canceled
                     | +-> failed
cancel_requested ----+
```

`input_required` and `auth_required` are resumable process states, not terminal states. An execution slice may end while the process remains resumable.

**Acceptance:** every local task transition is validated in one place, every persisted event has a consistent status meaning, and A2A projection tests cover every internal state.

### M3. Separate Interrupt Kinds from Execution Outcomes

**Priority:** P0

**Problem:** LangGraph currently reports approval and user-input interrupts through the same `interrupted` result shape, and the task layer maps both to `input_required`.

**Work:**

- add an explicit interruption kind to the runtime result, such as `approval_required` or `input_required`;
- keep the LangGraph runtime protocol-neutral by returning structured interruption data rather than A2A state;
- map approval to the internal authorization state and user input to the internal input state;
- make resume operations validate the expected interruption kind;
- expose the appropriate A2A state and input message through the adapter.

**Acceptance:** an approval request cannot be resumed through a user-input operation, and a user-input request cannot be approved through an approval operation.

### M4. Add Restart Reconciliation for the Current Worker Model

**Priority:** P1

**Problem:** SQLite records and LangGraph checkpoints can survive a process restart, but the in-process worker, active-task set, and notifier do not.

**Work:**

- add a startup reconciliation step for persisted local tasks;
- requeue tasks that were durably queued but never started;
- mark tasks that were running when the process stopped with an explicit retryable runtime-restart failure, unless a valid continuation policy is implemented;
- leave `input_required` and `auth_required` tasks resumable;
- verify that `runtimeThreadId` points to the same task input and checkpoint lineage before resuming;
- make task submission and recovery idempotent.

The first implementation should not introduce a new `orphaned` or `lease` state unless real deployment requirements make the existing failure metadata insufficient.

**Acceptance:** a controlled backend restart leaves no task silently stuck in `queued` or `running`, and resumable tasks retain their task and runtime identities.

### M5. Close the Dangerous-Tool Boundary

**Priority:** P1

**Problem:** `exec_shell` and `kubectl_apply` are registered as tools but do not execute the requested operation.

**Work:**

- remove placeholder tools from the default production registry, or implement them behind a separately reviewed execution boundary;
- keep approval checks before any destructive or remote write operation;
- make unavailable capabilities visible in the Agent Card and diagnostics;
- add explicit tests that a placeholder cannot be presented as a successful operation.

**Acceptance:** every registered dangerous tool either performs a real, tested operation under an explicit trust boundary or is unavailable to the production model.

### M6. Consolidate Context Assembly

**Priority:** P1

**Problem:** direct messages, local tasks, skills, memory, MCP prompts/resources, and recent history are assembled through several paths with different formatting rules.

**Work:**

- define one context-assembly policy for each route type;
- keep direct-message history lightweight and bounded;
- create an immutable input snapshot for a local task at task creation time;
- preserve role and provenance when converting stored messages to model input;
- define size limits for recent messages, skills, memory, MCP prompts, and resources;
- keep task continuation on the checkpointed LangGraph state instead of rebuilding the original task from changing conversation history.

**Acceptance:** the same task can be resumed deterministically after later messages are added to the context, and prompt composition is inspectable and bounded.

### M7. Retire the Compatibility Lifecycle Path

**Priority:** P2, after M1-M6 and Web UI stability

**Work:**

- remove legacy A2A execution fallback from `A2AAdapter`;
- remove obsolete session/task lifecycle code only after the UI and regression baseline no longer depend on it;
- retain read-only migration or export utilities if existing local data needs to be preserved;
- update runtime and release documentation.

**Acceptance:** one production lifecycle owner remains, and deleting the compatibility path does not change the supported A2A contract.

## Validation Strategy

Each milestone should include:

- focused unit tests for the changed contract;
- existing backend and frontend type/build checks;
- deterministic A2A JSON-RPC and SSE regression coverage;
- one approval flow and one user-input flow;
- one restart/reconciliation scenario once M4 begins;
- no dependency additions unless a milestone proves they are required.

## Deferred Work

The following are explicitly outside this refinement sequence:

- final-answer token streaming for task/LangGraph execution;
- Temporal, Redis, Redpanda, or a distributed scheduler;
- multi-process worker leasing and horizontal task scheduling;
- a separate Agent OS service or package;
- in-process multi-profile agent hosting;
- broad renaming of `TaskRecord`, `AgentService`, or `runtimeThreadId`.

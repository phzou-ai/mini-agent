# Runtime Refinement Roadmap Through R3

> Status: Historical
> Snapshot date: 2026-08-09
> Authority: Historical rationale and completed milestone evidence only

This snapshot preserves the detailed M0 through R3 milestone record that led
to the current runtime. It is not the active implementation queue. Current
priority and handoff are owned by [roadmap.md](../../dev/runtime/roadmap.md).

## Scope

This roadmap addresses the current runtime review findings:

1. multiple production ingress and lifecycle paths;
2. duplicate task status models and projection logic;
3. in-process execution without restart reconciliation;
4. ambiguous approval and user-input interruption semantics;
5. registered dangerous tools that are still placeholders;
6. duplicated and weakly bounded context assembly.

The supporting dated review is recorded in
[review-2026-08-01.md](review-2026-08-01.md). It confirms the normal A2A
lifecycle path and records concrete risks for message idempotency, task input
causality, direct-message failures, lifecycle ownership, and remote proxy
ordering.

The staged path beyond the correctness work at the time was defined in
[runtime-evolution-path.md](../../dev/runtime/runtime-evolution-path.md). That document provides
feasibility and activation criteria. Current implementation priority has since
moved to [roadmap.md](../../dev/runtime/roadmap.md).

It does not add a new product feature. It makes the current A2A, LangGraph, SQLite, MCP, approval, and Web UI behavior easier to reason about and safer to extend.

## Execution Status At Snapshot

This table records completed work and the active priority at the snapshot date.
Detailed milestone rationale and acceptance criteria remain below.

| Milestone | Status | Implemented boundary or remaining scope |
| --- | --- | --- |
| M1, A2A lifecycle owner | Complete | `MainAgentCore` owns A2A execution and destructive Context/registered-agent management. |
| M1.5, durable message ingress | Complete | SQLite-backed `messageId` reservation and outcome ownership prevent duplicate routing or execution. |
| M2, local process transitions | Complete | Local status transitions and their lifecycle events are validated and atomic. |
| M3, continuation kinds | Complete for local processes | Approval and ordinary input are distinct pending-continuation operations. |
| M6, bounded context assembly | Complete for the current character-bounded scope | Router, direct-message, and local-Task paths use explicit causal, route-specific history policies; dynamic injected context has separate section and total caps. |
| M4, startup reconciliation | Complete | Durable queued execution commands are safely resubmitted; ambiguous claimed work fails explicitly and blocked Tasks remain resumable. |
| P1, direct-message failure presentation | Complete | Failed ingress records project to distinct Context UI activities with `{ code, message, retryable }`; they are not assistant Messages or Tasks. |
| P0/P1, Task terminal projection and safe retry | Complete, 2026-08-04 | Task failure code/message/retryability is durable and projected consistently; terminal Context reconciliation leaves one canonical answer; an eligible manual retry creates one new lineage-linked local Task attempt. |
| P1, runtime composition and remote proxies | Complete | The default core owns a dedicated executor; remote proxy refresh/cancel/snapshot persistence live in the core and reject stale state regressions. |
| R0, runtime integrity closure | Complete, 2026-08-02 | Destructive management is core-owned, asynchronous Task acceptance is atomic, direct ingress recovery is explicit, and SQLite is hardened for the single-host runtime. |
| R1, side-effect execution boundary | Complete, 2026-08-02 | Local non-read-only tool calls receive durable invocation identities, exact approval bindings, artifact-backed results, and conservative uncertain recovery. |
| R2, governed execution kernel | Complete, 2026-08-02 | Local Tasks have task-scoped execution limits, typed stop reasons, normalized tool observations, and artifact-backed evidence/risk summaries. |
| R3.1, SSH execution control | Complete, 2026-08-02 | The existing SSH/Kubernetes adapters receive an ephemeral active-execution context, bounded local subprocess control, and conservative non-read-only outcome handling. This is not a generic workspace or sandbox. |
| R3.2, model execution control | Complete, 2026-08-02 | When an optional local Task elapsed-time budget is configured, its remaining time caps each provider HTTP model call; cancellation prevents later model/tool work at the next safe boundary and is projected honestly in the Web console. |
| S0, single-host reliability matrix | Implemented, 2026-08-02 | A deterministic focused command verifies ingress, public errors, Task lifecycle and terminal projection, continuation, cancellation, restart, side-effect evidence, and browser recovery after a late stream error. |
| S1, Inspector state presentation | Implemented, 2026-08-02 | The web Inspector presents public A2A state, local durable process state, and the LangGraph checkpoint thread separately. Artifact events explicitly indicate that they do not change Task state; raw records are collapsed diagnostics. |
| S2, release baseline refresh | Active at snapshot | Re-run the deterministic single-host and full-stack gates, preserve a clean worktree, and record any dependency-backed live-check failure as an explicit defect or external blocker. |

## Phase Gate At Snapshot

**Priority recorded at this snapshot: complete S2, the single-host release
baseline refresh.**

R3.1 and R3.2 close the narrow execution-control gaps in the current
single-host path: durable cancellation reaches active SSH/Kubernetes adapters
and model calls; SSH/Kubernetes adapters and model calls observe the remaining
R2 deadline when it is configured; and uncertain side-effect outcomes remain
visible. MCP and other independently timed HTTP tools retain their own
capability-level timeouts rather than a generic Task-deadline wrapper. The
capability boundary is in
[Workspace And Isolation Boundary](../../components/backend/runtime/workspace-and-isolation-boundary.md),
while the model-call and cancellation semantics are in
[Governed Execution Kernel](../../components/backend/runtime/governed-execution-kernel.md).
The deterministic verification matrix is in
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md).

No broader R3, R4, or R5 implementation milestone is active. The default work
is to validate the existing direct Message, local Task, continuation,
cancellation, and SSH/Kubernetes flows; fix observed defects; and preserve
focused regression coverage. Do not select a future capability merely because
it appears later in this document.

Before expanding the architecture, record a concrete current workload, the
missing boundary it exposes, and the smallest contract that closes it. A
general workspace, sandbox, planner, scheduler, or distributed runtime is not
the default response to a hypothetical future need.

### Work Item Recorded At Snapshot: S2 Release Baseline Refresh

**Scope:** verification and defect closure only. Do not add a product feature
or a new runtime owner.

**Work:**

1. Run `scripts/check_single_host_reliability.sh`.
2. Run `scripts/check_full_stack_regression.sh` and verify that it does not
   modify tracked files.
3. When operator-configured model, MCP, SSH/Kubernetes, or child-agent
   dependencies are available, exercise one representative direct Message and
   one local Task workflow.
4. Record every reproducible failure as a bounded defect at its current owner;
   record unavailable external dependencies as blockers rather than weakening
   the deterministic gate.

**Acceptance:** both deterministic gates pass, the tracked worktree remains
unchanged by the gates, and each attempted live workflow either passes or has
one explicit defect/blocker record with reproduction evidence.

### Handoff At Snapshot

> Status: Historical snapshot
> Scope: Documentation authority closure and S2 verification handoff
> Last reviewed: 2026-08-09

**Objective:** leave the reorganized documentation with one clear API owner,
one active runtime priority, and enough validation state for the next
contributor to continue without reconstructing this iteration from chat.

**Completed:**

- Reorganized project documentation into stable overview, architecture,
  component, operations, active-development, and maintenance domains.
- Established `docs/README.md` as the project documentation entry point and
  `docs/AI-collaboration-guide.md` as the canonical collaboration convention.
- Made [API Boundary](../../components/backend/api-boundary.md) the only
  endpoint inventory; operational guidance now links to it instead of
  maintaining a second list.
- Documented canonical `/rpc` integration routes, supported path-style A2A
  bindings, the complete first-party management surface, the real
  approval-gated Kubernetes delete capability, and the Web `_components/`
  location.
- Marked completed roadmap milestone detail as historical implementation
  evidence and created S2 as the only active work item.

**Decisions to preserve:**

- `/rpc` is the canonical A2A integration endpoint. Supported path-style A2A
  bindings remain tested adapters over `MainAgentCore`, not a second lifecycle
  owner.
- `docs/components/backend/api-boundary.md` owns the endpoint catalog.
  Operations documents explain usage and link to that catalog.
- No product feature, distributed runtime, scheduler, workspace, or sandbox is
  authorized by this handoff. S2 is verification and defect closure only.

**Validation performed:**

- The documentation audit passed for all 45 Markdown files after the boundary
  corrections.
- `git diff --check` passed before the documentation correction commit.
- Documentation reorganization is recorded by commit `a22753b`; the semantic
  audit corrections are recorded by commit `44af5b4`.

**Not yet validated:**

- `scripts/check_single_host_reliability.sh` was not run during this
  documentation-only iteration.
- `scripts/check_full_stack_regression.sh` was not run during this
  documentation-only iteration.
- No live model, MCP, SSH/Kubernetes, or child-agent workflow was exercised.

**Next task recorded at this snapshot:** execute S2 in its documented order. Run the focused
single-host gate, then the full-stack gate, verify that tracked files remain
unchanged, and record any live dependency check as passed, a bounded defect, or
an explicit external blocker.

**Repository checkpoint:** documentation reorganization is recorded by commit
`a22753b`, and the semantic audit corrections are recorded by commit
`44af5b4`. Treat branch position and working-tree contents as live session
state; inspect them directly before continuing instead of relying on this
handoff.

The retired service/session stack and its historical SQLite support have been
removed by an explicit clean-slate decision. See
[Clean-Slate Storage Baseline](../../components/backend/runtime/clean-slate-storage.md).

## Priority Order At Snapshot

The following order was recorded after R3.2:

1. Stabilize the current single-host runtime through real workflows and
   focused correctness, reliability, inspection, and UX fixes.
2. When a current workflow proves one missing boundary, define and implement
   one narrow extension with an explicit contract and acceptance criteria.
3. Reassess broader R3, R4, or R5 work only when its documented evidence
   persists after the current boundary has been exercised.

This sequence deliberately keeps SQLite and the current in-process worker. It
does not justify Temporal, Redis, a persistent plan DAG, or a new scheduler.

## Milestone Detail

The sections below retain the problem statements, scope, and acceptance
criteria for each milestone. The table above records execution status at the
snapshot date; current status is owned by [roadmap.md](../../dev/runtime/roadmap.md).

> Status: Historical implementation evidence unless the status table above
> explicitly marks a milestone Active. The `Work` and `Acceptance` sections
> below explain how completed boundaries were established; they are not an
> implementation queue.

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

**Status:** Implemented. The A2A adapter and management/read-model endpoints
use `MainAgentCore`. `create_app()` constructs no alternate lifecycle service
or application-state lifecycle owner.

**Historical problem:** JSON-RPC requests used `MainAgentCore`, while some
path-style A2A bindings used a separate lifecycle implementation. The same
logical A2A operation could therefore create different records and statuses.

**Work:**

- route `/rpc` and supported A2A message/task operations through `MainAgentCore`;
- make the path-style bindings thin compatibility adapters to the same core, or explicitly remove them after the Web UI no longer needs them;
- keep `/api/*` as a first-party management/read-model surface, not a second public agent lifecycle;
- ensure `A2AAdapter` does not select a lifecycle owner based on request shape;
- preserve response and event compatibility through projection helpers rather than duplicate execution code.

**Acceptance:** the same A2A Message produces the same context, task, event, artifact, and status behavior regardless of whether it arrives through the supported binding or a temporary compatibility binding.

**Composition completion:** P1 gives the default core an application-owned
executor. There is no alternate service executor or compatibility lifecycle
path in the active codebase.

### M1.5. Make A2A Message Ingress Durable and Idempotent

**Priority:** P0

**Status:** Implemented and hardened, 2026-08-02. The active SQLite baseline
contains one durable ingress record keyed by `messageId`; no process-local
message lock participates in delivery.

**Problem:** a stored `messageId` alone is not a durable execution outcome. A
crash, concurrent process, or partially persisted outcome must not route or
execute the request again.

**Work:**

- create one durable message-execution/outcome record keyed by `messageId`;
- reserve or load that record before routing, task creation, delegation, model invocation, or tool execution;
- persist the selected route and result reference (`agentMessageId`, `taskId`, or `delegationId`) on the same record;
- return the prior outcome, or an explicit in-progress result, for a duplicate request;
- use a database uniqueness constraint and durable ingress record as the
  correctness boundary; do not depend on an in-memory lock.

**Acceptance:** complete. A duplicate `messageId` cannot create a second route
decision, Task, delegation, model invocation, or tool execution across restart
or concurrent ingress. The focused ingress contract is covered by the current
Python regression suite.

### M2. Establish One Internal Process Status Model

**Priority:** P0

**Status:** Implemented, 2026-08-01. The local transition contract is in
[Local Process Transition Governance](../../components/backend/runtime/local-process-transitions.md).

**Problem:** local process state and A2A Task state use different vocabularies
and must not become competing lifecycle owners.

**Work:**

- keep one authoritative internal status model for locally owned Agent Processes;
- define explicit projections to A2A TaskState;
- centralize allowed transitions and event creation;
- clarify that `queued` and `running` are process states, while `submitted` and `working` are A2A projections;
- remove status decisions from individual routes and background callbacks;
- keep retry as a new task with lineage, not a status mutation of the old task.

**Recommended internal model:**

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

`input_required` and `auth_required` are resumable process states, not terminal states. An execution slice may end while the process remains resumable.

**Acceptance:** complete for locally owned processes. Every local task
transition is validated in one place, every real transition writes one
status-bearing lifecycle event atomically, and A2A projection tests cover every
internal state. Remote proxies remain a separate synchronization policy.

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

**Review status:** implemented for the local process path. The local runner maps
`approval_required` to `auth_required` and `user_input_required` to
`input_required`; `MainAgentCore` persists a durable pending continuation,
validates the public continuation interface, consumes the record atomically,
and passes a frozen command to asynchronous workers. Startup reconciliation is
now complete; later remote continuation support remains separate work.

### M4. Add Restart Reconciliation for the Current Worker Model

**Priority:** P1

**Problem:** SQLite records and LangGraph checkpoints can survive a process restart, but the in-process worker, active-task set, and notifier do not.

**Work:**

- add a startup reconciliation step for persisted local tasks;
- requeue only tasks that were durably queued and never claimed or started;
- mark tasks that were running or cancellation-pending when the process stopped with an explicit retryable runtime-restart failure, unless a valid continuation policy is implemented;
- leave `input_required` and `auth_required` tasks resumable;
- verify that `runtimeThreadId` points to the same task input and checkpoint lineage before resuming;
- make task submission and recovery idempotent.

The first implementation should not introduce a new `orphaned` or `lease` state unless real deployment requirements make the existing failure metadata insufficient.

**Acceptance:** a controlled backend restart leaves no task silently stuck in
`queued`, `running`, or `cancel_requested`, and resumable tasks retain their
task and runtime identities.

**Review status:** implemented, 2026-08-01. The active storage baseline
contains a durable queued-execution command. Application startup invokes
`MainAgentCore.reconcile_startup()`: unclaimed valid commands are submitted
once, `running` and `cancel_requested` local Tasks become structured retryable
failures, and blocked Tasks are retained. The focused contract is in
[Startup Reconciliation](../../components/backend/runtime/startup-reconciliation.md).

### M5. Close the Dangerous-Tool Boundary

**Priority:** P1

**Problem:** `exec_shell` and `kubectl_apply` are registered as tools but do not execute the requested operation.

**Work:**

- remove placeholder tools from the default production registry, or implement them behind a separately reviewed execution boundary;
- keep approval checks before any destructive or remote write operation;
- make unavailable capabilities visible in the Agent Card and diagnostics;
- add explicit tests that a placeholder cannot be presented as a successful operation.

**Acceptance:** every registered dangerous tool either performs a real, tested operation under an explicit trust boundary or is unavailable to the production model.

**Review status:** implemented. The default DevOps registry contains only real
SSH-backed read-only inspection tools and the explicitly approval-gated
`delete_resource` capability. Placeholder `exec_shell` and `kubectl_apply`
tools are not registered.

### M6. Consolidate Context Assembly

**Priority:** P1

**Problem:** direct messages, local tasks, skills, memory, MCP prompts/resources, and recent history are assembled through several paths with different formatting rules.

**Work:**

- define one context-assembly policy for each route type;
- keep direct-message history lightweight and bounded;
- capture a durable causal input boundary for a local task at task creation time;
- preserve role and provenance when converting stored messages to model input;
- define size limits for recent messages, skills, memory, MCP prompts, and resources;
- keep task continuation on the checkpointed LangGraph state instead of rebuilding the original task from changing conversation history.

**Acceptance:** later Context Messages cannot change a Task's initial stored
Message history, and prompt composition has an explicit, inspectable,
character-bounded policy. This does not require full prompt replay unless a
later audit or recovery requirement establishes that need.

**Review status:** implemented for the current scope. Messages have stable
Context-local sequence numbers, a local Task persists its input cut, and the
worker reads initial history only through that cut. The router, direct
responder, and local-task runner share route-specific persisted-history
policies. `RuntimeContextProvider` caps injected MCP prompts, skills, memory,
and resources by section and in total. These are character caps, not a
model-token guarantee. See
[Durable Context Input Cut](../../components/backend/runtime/context-input-cut.md).

### M7. Retire the Compatibility Lifecycle Path

**Priority:** P2

**Status:** Implemented, 2026-08-02. The project chose an intentional
clean-slate cut: obsolete service/session modules, their tests, the retired A2A
projection adapter, and historical SQLite migrations have been deleted. The
active store has one `main_agent_clean_slate_v1` baseline and does not read,
export, or migrate retired local data. See
[Clean-Slate Storage Baseline](../../components/backend/runtime/clean-slate-storage.md).

**Acceptance:** complete. `MainAgentCore` is the only product lifecycle owner,
and existing local records are discarded by an explicit documented decision.

### R0. Close Remaining Runtime Integrity Gaps

**Priority:** P0

**Status:** Complete, 2026-08-02. The detailed staged contract and exit
criteria are in
[runtime-evolution-path.md](../../dev/runtime/runtime-evolution-path.md#r0-close-current-runtime-integrity-gaps).

**Work:**

- make Context deletion and registered-agent decommissioning core-owned control
  operations;
- prevent physical deletion while a worker or remote Task can still execute;
- clean up LangGraph checkpoints only after execution ownership is resolved;
- make initial asynchronous Task acceptance and durable command creation one
  transaction;
- define stale direct-message ingress as a visible non-replayable failure;
- remove legacy ingress materialization and unbounded local Message locks;
- configure the single-host SQLite boundary consistently and remove
  request-time skill-index writes.

**Acceptance:** complete. Destructive management cannot detach live work from
its records; every accepted asynchronous Task is atomically recoverably queued
or rolls back; stale direct invocations have a visible retryable outcome; and
the clean-slate runtime no longer infers outcomes for impossible record shapes.

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
- a generic workspace lifecycle, arbitrary command execution, or sandbox;
- Temporal, Redis, Redpanda, or a distributed scheduler;
- multi-process worker leasing and horizontal task scheduling;
- a separate Agent OS service or package;
- in-process multi-profile agent hosting;
- broad renaming of `TaskRecord` or `runtimeThreadId`.

Persistent planning, workspace isolation, bounded autonomous execution, and
distributed scheduling are governed by the activation criteria in
[runtime-evolution-path.md](../../dev/runtime/runtime-evolution-path.md); they are not implied by
the use of LangGraph or the Agent OS vocabulary.

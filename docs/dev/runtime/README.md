# Runtime Refinement

## Purpose

This folder contains the implementation-facing roadmap and contracts for
refining the current Vermay runtime. It is not a proposal to introduce a
new framework or a second runtime.

The plan is intentionally incremental. The current project is still in rapid development, so the objective is to remove conflicting ownership and clarify contracts before adding broader infrastructure or new user-facing features.

## Operating Model

Converge the runtime on the following shape:

```text
A2A ingress
  -> MainAgentCore
     -> direct Message
     -> local Agent Process / A2A Task
     -> remote child-agent proxy
        -> LangGraph execution slice
           -> model, permission, tools, checkpoint
```

The target is an A2A-native main-agent runtime and inspectable process host. LangGraph remains the local execution engine; it does not own public task identity, routing, queueing, or delegation.

## Documentation Roles

| Document | Role |
| --- | --- |
| [roadmap.md](roadmap.md) | Authoritative active priority, milestone scope, and acceptance criteria. |
| [roadmap-history-through-r3.md](roadmap-history-through-r3.md) | Historical detailed M0-R3 milestone rationale and evidence; not an active queue. |
| [runtime-evolution-path.md](runtime-evolution-path.md) | Feasibility, stage gates, and strategic sequence from runtime integrity through governed execution, optional planning, and optional distribution. |
| [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md) | Normative ownership of identifiers, states, interruptions, and projections. |
| [Backend Runtime Contracts](../../components/backend/runtime/README.md) | Stable implemented contracts for message ingress, local process transitions, startup reconciliation, Task failure/retry, and non-read-only tool effects. |
| [direct-message-failures.md](direct-message-failures.md) | Implemented direct-Message failure persistence and browser presentation contract. |
| [context-input-cut.md](context-input-cut.md) | Implemented Context ordering and local-Task initial-input contract. |
| [runtime-composition-and-remote-proxy.md](runtime-composition-and-remote-proxy.md) | Implemented executor-composition and monotonic remote-proxy contract. |
| [refactor-wave-2026-08-02.md](refactor-wave-2026-08-02.md) | Implemented cleanup decisions: product-path ownership, bounded context, tool registry, and remote identity validation. |
| [governed-execution-kernel.md](governed-execution-kernel.md) | Implemented R2 policy limits, stop-reason projection, normalized tool observations, and evidence/risk summaries. |
| [model-tool-calling.md](model-tool-calling.md) | Implemented model-provider tool-calling strategy, canonical tool-call normalization, and fail-closed compatibility boundary. |
| [workspace-and-isolation-boundary.md](workspace-and-isolation-boundary.md) | Implemented R3.1 SSH/Kubernetes execution-control boundary and the explicit non-goals before a real workspace or sandbox is needed. |
| [clean-slate-storage.md](clean-slate-storage.md) | Intentional retirement of the old service/session stack and the active SQLite baseline. |
| [current-architecture-assessment.md](current-architecture-assessment.md) | Current strengths, tradeoffs, deployment limits, and rationale for the active order. |
| [single-host-reliability-matrix.md](single-host-reliability-matrix.md) | Active P0 deterministic verification matrix for ingress, Tasks, continuation, cancellation, restart, and browser recovery. |
| [review-2026-08-01.md](review-2026-08-01.md) | Dated review evidence and historical findings; not a second roadmap. |

When these documents differ in emphasis, use the roadmap for current priority,
the evolution path for later-stage activation criteria, the focused contract
for runtime behavior, and the dated review only for the reasoning and evidence
recorded at that time.

## Current Status

| Area | Status | Primary reference |
| --- | --- | --- |
| A2A lifecycle ownership | Implemented for the current single-host path; the default core receives an application-owned executor. | [runtime-composition-and-remote-proxy.md](runtime-composition-and-remote-proxy.md) |
| Durable message ingress | Implemented. Repeated top-level `messageId` values do not route or execute twice. | [Durable Message Ingress](../../components/backend/runtime/message-ingress.md) |
| Direct-message failure presentation | Implemented. Failed ingress records project to distinct UI activities, not assistant answers. | [direct-message-failures.md](direct-message-failures.md) |
| Task failure projection and retry | Implemented. Task failures persist safe `code`, `message`, and retryability; eligible manual retry creates one new lineage-linked Task attempt, never a replay. | [Task Failure Projection And Safe Retry](../../components/backend/runtime/task-failure-retry.md) |
| Continuation handoff | Implemented for local approval and user-input continuations. | [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md#pending-continuation) |
| Local process transitions | Implemented. Owned-process transitions and lifecycle events are atomic and validated. | [Local Process Transition Governance](../../components/backend/runtime/local-process-transitions.md) |
| Causal task input and prompt bounds | Implemented for current character-bounded history and injected runtime context. | [context-input-cut.md](context-input-cut.md) |
| Startup reconciliation | Implemented for locally owned queued worker commands; ambiguous claimed work fails explicitly. | [Startup Reconciliation](../../components/backend/runtime/startup-reconciliation.md) |
| Side-effect execution boundary | Implemented for local non-read-only ToolNode calls. Each attempt has a durable invocation record, exact approval binding, conservative replay blocking, and result artifact reference. | [Tool Invocation Ledger](../../components/backend/runtime/tool-invocation-ledger.md) |
| Remote proxy synchronization | Implemented; refresh, cancellation, accepted snapshots, artifact materialization, and monotonic status projection are core-owned. | [runtime-composition-and-remote-proxy.md](runtime-composition-and-remote-proxy.md) |
| Storage baseline | Implemented. Retired service/session data is discarded; new stores use `main_agent_clean_slate_v1`. | [clean-slate-storage.md](clean-slate-storage.md) |
| R0 runtime integrity closure | Complete, 2026-08-02. Destructive cleanup is core-owned, asynchronous Task acceptance is atomic, direct ingress recovery is explicit, and SQLite uses one single-host connection contract. | [runtime-evolution-path.md](runtime-evolution-path.md#r0-close-current-runtime-integrity-gaps) |
| R1 side-effect execution boundary | Complete, 2026-08-02. The Tool Invocation Ledger makes non-read-only local effects durable and conservative across approval, completion, cancellation, and restart recovery. | [Tool Invocation Ledger](../../components/backend/runtime/tool-invocation-ledger.md) |
| R2 governed execution kernel | Complete, 2026-08-02. Local LangGraph Tasks have immutable per-process limits, typed stop reasons, normalized tool observations, and deterministic execution evidence. | [governed-execution-kernel.md](governed-execution-kernel.md) |
| R3.1 SSH execution control | Complete, 2026-08-02. Active SSH/Kubernetes capability calls receive a bounded ephemeral execution context, local subprocess cancellation, timeout metadata, and conservative write uncertainty. | [workspace-and-isolation-boundary.md](workspace-and-isolation-boundary.md) |
| R3.2 model execution control | Complete, 2026-08-02. Active model calls honor configured provider and optional Task-budget limits, while cancellation is projected at the next safe boundary. | [governed-execution-kernel.md](governed-execution-kernel.md) |
| Model tool-calling boundary | Implemented. The active Ollama primary model uses native function calls for Tasks; provider-specific responses normalize into project-owned tool calls before the existing permission and ToolNode path. | [model-tool-calling.md](model-tool-calling.md) |
| Single-host reliability matrix | Implemented. Deterministic backend and browser checks exercise the P0 failure, continuation, cancellation, restart, and terminal-projection contract. | [single-host-reliability-matrix.md](single-host-reliability-matrix.md) |
| Inspector state presentation | Implemented. The web Inspector separately presents public A2A Task state, durable local process state, and LangGraph checkpoint thread; raw event diagnostics are collapsed by default. | [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md#inspector-presentation) |

## Current Phase: Stabilize The Single-Host Runtime

R0 through R3.2 close the currently demonstrated correctness and
execution-control gaps. There is no active expansion milestone after R3.2.
The current bounded verification task is
[S2, Release Baseline Refresh](roadmap.md#active-work-item-s2-release-baseline-refresh).
The roadmap also owns the
[latest durable handoff](roadmap.md#current-handoff); this index intentionally
does not duplicate its status or validation record.
The default work for this phase is deliberately conservative:

- validate direct Messages, local Tasks, continuation, cancellation, and the
  existing SSH/Kubernetes capability path against real user workflows;
- fix observed correctness, reliability, inspection, and UX issues at the
  existing ownership boundaries; and
- add focused regression coverage and documentation for those boundaries.

A proposed architectural change must first show a real current workflow, a
specific missing ownership or safety boundary, and why a narrow extension of
the existing runtime cannot address it. Generic workspaces, arbitrary command
execution, sandboxes, persistent plan DAGs, distributed scheduling, new Agent
OS services, and task final-answer streaming are not active work merely
because they are described elsewhere in these documents.

R3, R4, and R5 are conditional capability maps. They become implementation
work only when their documented workload signals are present and a bounded
scope is recorded before code changes begin.

## Current Decisions

- `MainAgentCore` is the target owner for A2A message routing and local task lifecycle.
- Destructive Context cleanup and registered-agent decommissioning are
  lifecycle operations through `MainAgentCore`; direct storage deletion is not
  an ownership exception. Live work conflicts with deletion, and `force` does
  not erase it.
- `A2AAdapter` is a binding adapter only. Every A2A message, task, cancel,
  resume, subscription, and remote-proxy synchronization operation delegates to
  `MainAgentCore`.
- A repeated A2A `messageId` is an idempotent replay request, not a new routing
  or execution request. The durable ingress record, rather than an in-memory
  lock or inferred result, is the authority for that decision. Abandoned direct
  ingress becomes an explicit retryable failure and needs a new `messageId` for
  a deliberate retry.
- `LangGraphAgentRuntime` owns graph execution and checkpoint continuation only.
- LangGraph is the replaceable inner execution kernel. It must not become a second owner of A2A ingress, public Task state, Context deletion, queue ownership, or delegation.
- The Tool Invocation Ledger owns the effect-attempt facts for local
  non-read-only calls. It is neither a second Task lifecycle nor an A2A state
  projection.
- The governed execution kernel owns bounded model/tool progression and
  normalized observations for one local runtime thread. Its stop reason is
  projected by the core; it is not an additional process or A2A status model.
- Provider adapters own only request/response translation. They do not own
  execution: the active primary model uses native tool calls, while
  `prompt_json` is an explicit Ollama compatibility strategy and never a
  silent runtime fallback.
- The R3.1 execution context is an in-memory bridge from a durable
  cancellation request to an active SSH capability call. It is not persisted,
  not a workspace, and not an additional lifecycle owner.
- SQLite remains the local persistence boundary for the current version.
- The existing in-process thread pool remains the worker mechanism during this refinement. Distributed scheduling is out of scope.
- Approval is authorization. It is not a sandbox or an execution-isolation boundary.

## Guardrails

- Prefer contract clarification and small adapters over broad renaming.
- Do not introduce Temporal, Redis, a distributed scheduler, or a separate Agent OS service in this phase.
- Do not add final-answer token streaming as part of this work.
- Preserve A2A JSON-RPC/SSE as the public agent boundary.
- Do not introduce a second lifecycle owner or a compatibility store beside `MainAgentCore`.
- Every lifecycle change must preserve task idempotency, checkpoint continuity, and inspectable events.
- Activate planning, workspace isolation, and distributed scheduling only when the entry conditions in [runtime-evolution-path.md](runtime-evolution-path.md) are demonstrated by real workloads.

## Completion Definition

This refinement is complete when:

- all public A2A bindings use the same `MainAgentCore` lifecycle;
- destructive management cannot detach live local or remote execution from its durable records;
- every accepted asynchronous Task has a recoverable execution owner in the same committed unit;
- one internal process status model is authoritative for local tasks;
- A2A states and LangGraph outcomes are projections, not competing sources of truth;
- approval and user-input interruptions are distinguishable;
- restart behavior for queued, running, and interrupted tasks is explicit and verified;
- local non-read-only tool attempts have a durable execution, approval, and
  uncertainty boundary;
- placeholder dangerous tools are removed or implemented;
- context assembly has one documented character-bounded policy and bounded inputs;
- retired local data is explicitly outside the active runtime boundary.

# Runtime Refinement

## Purpose

This folder contains the active runtime roadmap and staged evolution criteria.
Stable implemented runtime contracts live
under [Backend Runtime Contracts](../../components/backend/runtime/README.md),
and completed implementation evidence lives under
[Runtime History](../../history/runtime/README.md), not in this development
directory.

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
| [runtime-evolution-path.md](runtime-evolution-path.md) | Feasibility, stage gates, and strategic sequence from runtime integrity through governed execution, optional planning, and optional distribution. |
| [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md) | Normative ownership of identifiers, states, interruptions, and projections. |
| [Backend Runtime Contracts](../../components/backend/runtime/README.md) | Stable implemented runtime and persistence contracts. |
| [Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md) | Stable deterministic verification coverage used by the active release-baseline refresh. |
| [Runtime History](../../history/runtime/README.md) | Completed milestones, dated assessments, and refactor evidence; not an active queue. |

When these documents differ in emphasis, use the roadmap for current priority,
the evolution path for later-stage activation criteria, the focused contract
for runtime behavior, and the dated review only for the reasoning and evidence
recorded at that time.

## Current Phase: Stabilize The Single-Host Runtime

R0 through R3.2 close the currently demonstrated correctness and
execution-control gaps. There is no active expansion milestone after R3.2.
The current bounded verification task is
[S2, Release Baseline Refresh](roadmap.md#active-work-item-s2-release-baseline-refresh).
The roadmap also owns the
[latest durable handoff](roadmap.md#current-handoff). This index intentionally
does not maintain another implementation-status table or validation record.
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

## Guardrails

- Prefer contract clarification and small adapters over broad renaming.
- Do not introduce Temporal, Redis, a distributed scheduler, or a separate Agent OS service in this phase.
- Do not add final-answer token streaming as part of this work.
- Preserve A2A JSON-RPC/SSE as the public agent boundary.
- Do not introduce a second lifecycle owner or a compatibility store beside `MainAgentCore`.
- Every lifecycle change must preserve task idempotency, checkpoint continuity, and inspectable events.
- Activate planning, workspace isolation, and distributed scheduling only when the entry conditions in [runtime-evolution-path.md](runtime-evolution-path.md) are demonstrated by real workloads.

Stable lifecycle decisions and completed-contract acceptance conditions are
owned by [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md)
and [Backend Runtime Contracts](../../components/backend/runtime/README.md).

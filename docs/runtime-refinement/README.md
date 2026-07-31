# Runtime Refinement

## Status

This folder contains the implementation plan for refining the current Vermay Agent runtime. It is an execution plan, not a proposal to introduce a new framework or a second runtime.

The plan is intentionally incremental. The current project is still in rapid development, so the objective is to remove conflicting ownership and clarify contracts before adding broader infrastructure or new user-facing features.

## Goal

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

## Reading Order

1. [roadmap.md](roadmap.md) - ordered milestones, scope, acceptance criteria, and sequencing.
2. [state-ownership.md](state-ownership.md) - ownership of identifiers, lifecycle states, interruptions, and projections.

## Current Decisions

- `MainAgentCore` is the target owner for A2A message routing and local task lifecycle.
- `AgentService` and the old session/task tables remain temporarily for compatibility and Web UI stabilization, but they must not become a second long-term A2A lifecycle owner.
- `A2AAdapter` is a binding adapter only. It does not create or mutate legacy service sessions/tasks; every A2A message, task, cancel, resume, and subscription operation delegates to `MainAgentCore`.
- `LangGraphAgentRuntime` owns graph execution and checkpoint continuation only.
- SQLite remains the local persistence boundary for the current version.
- The existing in-process thread pool remains the worker mechanism during this refinement. Distributed scheduling is out of scope.
- Approval is authorization. It is not a sandbox or an execution-isolation boundary.

## Guardrails

- Prefer contract clarification and small adapters over broad renaming.
- Do not introduce Temporal, Redis, a distributed scheduler, or a separate Agent OS service in this phase.
- Do not add final-answer token streaming as part of this work.
- Preserve A2A JSON-RPC/SSE as the public agent boundary.
- Keep the legacy API only while the Web UI still requires it; mark compatibility paths explicitly.
- Every lifecycle change must preserve task idempotency, checkpoint continuity, and inspectable events.

## Completion Definition

This refinement is complete when:

- all public A2A bindings use the same `MainAgentCore` lifecycle;
- one internal process status model is authoritative for local tasks;
- A2A states and LangGraph outcomes are projections, not competing sources of truth;
- approval and user-input interruptions are distinguishable;
- restart behavior for queued, running, and interrupted tasks is explicit and verified;
- placeholder dangerous tools are removed or implemented;
- context assembly has one documented policy and bounded inputs;
- the legacy lifecycle path can be removed without changing the A2A contract.

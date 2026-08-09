# Runtime Refinement Roadmap

> Status: Active
> Last reviewed: 2026-08-09
> Authority: Current runtime priority, phase gate, and handoff

## Purpose

This document answers three questions only:

1. What runtime work is authorized now?
2. What evidence is required to finish it?
3. What should the next contributor preserve?

Completed milestone detail is retained in the
[Runtime Roadmap Through R3](roadmap-history-through-r3.md). That historical
record explains how the present boundaries were reached; it is not an active
implementation queue.

## Current Phase Gate

**Current priority: complete S2, the single-host release baseline refresh.**

The current runtime has closed the demonstrated ownership and execution-control
gaps through R3.2. No broader planner, scheduler, workspace, sandbox,
distributed runtime, or final-answer streaming milestone is active.

Before expanding the architecture, record:

1. one concrete current workload;
2. the missing ownership, correctness, or safety boundary it exposes; and
3. the smallest contract that closes that boundary.

A future capability described in an architecture or evolution document is not
authorized work until those entry conditions are met.

## Active Work Item: S2 Release Baseline Refresh

**Scope:** verification and bounded defect closure only. Do not add a product
feature or a new runtime owner.

### Work

1. Run `scripts/check_single_host_reliability.sh`.
2. Run `scripts/check_full_stack_regression.sh`.
3. Verify that both gates leave tracked files unchanged.
4. When operator-configured dependencies are available, exercise one
   representative direct Message and one local Task workflow.
5. Record each reproducible failure at its existing owner. Record unavailable
   model, MCP, SSH/Kubernetes, or child-agent dependencies as explicit external
   blockers rather than weakening deterministic checks.

### Acceptance

- Both deterministic gates pass.
- The gates do not modify tracked files.
- Each attempted live workflow either passes or has one bounded defect or
  external-blocker record with reproduction evidence.
- No unrelated feature or infrastructure work is introduced.

## Current Handoff

**Objective:** continue S2 without reconstructing documentation authority or
runtime ownership from chat history.

### Completed Baseline

- `MainAgentCore` is the single A2A lifecycle owner.
- Durable Message Ingress prevents duplicate routing and execution for one
  top-level `messageId`.
- Local process transitions, continuation kinds, startup reconciliation, and
  bounded context assembly have explicit contracts.
- Task failures have durable public error projection and safe manual retry
  lineage.
- Local non-read-only effects use a durable Tool Invocation Ledger.
- Governed execution limits and cancellation reach the current model and
  SSH/Kubernetes capability paths.
- The Inspector separates public A2A state, durable local process state, and
  the LangGraph checkpoint thread.
- Documentation has stable overview, architecture, component, operations, and
  active-development domains with a compact AI collaboration entry point.

### Decisions To Preserve

- `/rpc` is the canonical A2A integration endpoint. Supported path-style A2A
  bindings are adapters over `MainAgentCore`, not another lifecycle owner.
- [API Boundary](../../components/backend/api-boundary.md) owns the endpoint
  catalog. Operations documents explain usage and link to it.
- LangGraph owns local graph execution and checkpoint continuation only.
- SQLite and the in-process worker remain the current single-host baseline.
- Approval is authorization, not execution isolation.
- Stable runtime contracts belong in architecture or backend component
  documentation; this development area owns current priority and unsettled
  work.

### Validation State

The documentation-only reorganization does not establish a new runtime test
result. Run the S2 commands above before claiming the release baseline is
current. Report branch position, commits, and working-tree state as live Git
state, not as durable content in this roadmap.

### Next Task

Execute S2 in the documented order. If it passes, record the dated validation
result and explicitly close the active item. If it fails, fix only the bounded
defect at its current owner and rerun the narrowest relevant gate before the
full stack gate.

## Guardrails

- Preserve A2A JSON-RPC and SSE as the public agent boundary.
- Do not introduce a second lifecycle owner or compatibility store.
- Keep A2A Task state, local process state, LangGraph continuation state, and
  tool-effect state distinct.
- Prefer a focused contract or adapter over broad abstraction.
- Do not activate Temporal, Redis, a distributed scheduler, a generic
  workspace, or a sandbox without demonstrated workload evidence.
- Do not add final-answer token streaming during S2.
- Preserve unrelated work and keep deterministic gates worktree-clean.

## Completed Baseline Summary

| Boundary | State | Durable reference |
| --- | --- | --- |
| A2A lifecycle and durable ingress | Implemented | [Backend Runtime Contracts](../../components/backend/runtime/README.md) |
| Local process and continuation governance | Implemented | [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md) |
| Startup reconciliation | Implemented | [Backend Runtime Contracts](../../components/backend/runtime/README.md) |
| Task failure projection and retry | Implemented | [Backend Runtime Contracts](../../components/backend/runtime/README.md) |
| Side-effect execution ledger | Implemented | [Backend Runtime Contracts](../../components/backend/runtime/README.md) |
| Governed model/tool execution | Implemented | [Governed Execution Kernel](governed-execution-kernel.md) |
| Single-host regression matrix | Implemented; refresh active | [Single-Host Reliability Matrix](single-host-reliability-matrix.md) |
| Detailed M0-R3 record | Historical | [Runtime Roadmap Through R3](roadmap-history-through-r3.md) |

## Activation Rule For Future Work

After S2 closes, leave the roadmap with either:

- one evidence-backed active item with scope and acceptance criteria; or
- an explicit statement that no runtime expansion is active.

Do not turn the completed milestone history back into a speculative backlog.

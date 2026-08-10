# Runtime Refinement Roadmap

> Status: Active
> Last reviewed: 2026-08-10
> Authority: Current runtime priority, phase gate, and handoff

## Purpose

This document answers three questions only:

1. What runtime work is authorized now?
2. What evidence is required to finish it?
3. What should the next contributor preserve?

Completed milestone detail is retained in the
[Runtime Roadmap Through R3](../../history/runtime/roadmap-through-r3.md). That historical
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

Deterministic S2 validation was refreshed on 2026-08-10:

- `scripts/check_single_host_reliability.sh` passed with 189 backend tests and
  10 Playwright tests.
- `scripts/check_full_stack_regression.sh` passed with 489 backend tests,
  frontend type checking, the Next.js production build, and 10 deterministic
  Playwright tests.
- Both gates left tracked files unchanged.

The suite still reports a non-blocking Starlette `TestClient` deprecation
warning. Live model, MCP, SSH/Kubernetes, and child-agent workflows remain
operator-dependent and were not exercised by this deterministic refresh.
Report branch position, commits, and working-tree state as live Git state, not
as durable content in this roadmap.

### Next Task

Complete the operator-dependent portion of S2 when configured dependencies are
available: exercise one representative direct Message and one local Task. For
each attempted workflow, record either a pass, one bounded defect at its current
owner, or an explicit external blocker. Do not weaken the deterministic gates
to accommodate unavailable external services.

### Known Improvement Points

The following issues were confirmed by the 2026-08-10 implementation review.
They are recorded here so that rapid development does not lose the findings,
but they are not all authorized work during S2:

1. The in-process Task executor limits active workers but does not bound its
   pending submission queue. Add admission control only when a current workload
   demonstrates queue pressure or unpredictable latency.
2. Context, Message, Task, route-decision, and delegation read models are not
   consistently paginated. Introduce bounded defaults before retained local
   data makes these endpoints or the Web UI materially slow.
3. The Next.js BFF does not yet define explicit deadlines for ordinary upstream
   requests or stream establishment, and malformed SSE payloads are currently
   ignored. Treat a reproduced stuck request or protocol-drift failure as the
   activation signal for this work.
4. `MainAgentCore`, `MainAgentStore`, and the Web console remain concentrated
   implementation units. Prefer small responsibility extractions when touching
   an existing workflow; do not start a broad rewrite solely to reduce file
   length.
5. Python release dependencies use broad version ranges, and static linting plus
   focused frontend projection tests remain possible release-quality
   improvements after the current behavior is stable.

Authentication, network policy, multi-process event notification, distributed
scheduling, and generalized resource governance remain deployment-stage or
workload-driven concerns. They are deliberately not prioritized for the current
local, rapid-development phase.

Bounded frontend extractions have moved the stateless welcome experience and
the Inspector timeline presentation out of `agent-console.tsx`. Task streams,
lifecycle actions, selected-event state, and session state remain in the
existing controller until a concrete change demonstrates another smaller
ownership boundary.

CLI model selection now has one path: the default or named model is resolved
from `config/models.json`, and `--model` is the only per-run selector. Provider
URLs, credentials, timeouts, and tool-calling strategies are configuration
concerns rather than parallel CLI overrides. Task approval resume likewise has
one JSON-RPC method, `tasks/resume`; the former method-name retry path has been
removed.

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
| Governed model/tool execution | Implemented | [Governed Execution Kernel](../../components/backend/runtime/governed-execution-kernel.md) |
| Single-host regression matrix | Implemented; refresh active | [Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md) |
| Detailed M0-R3 record | Historical | [Runtime Roadmap Through R3](../../history/runtime/roadmap-through-r3.md) |

## Activation Rule For Future Work

After S2 closes, leave the roadmap with either:

- one evidence-backed active item with scope and acceptance criteria; or
- an explicit statement that no runtime expansion is active.

Do not turn the completed milestone history back into a speculative backlog.

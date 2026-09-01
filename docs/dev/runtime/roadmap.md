# Runtime Refinement Roadmap

> Status: Active
> Last reviewed: 2026-08-16
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

**Current priority: preserve the closed S3 runtime baseline and the completed
Platform M1-M6 contracts; no runtime expansion or Platform M7 work is
authorized.**

The current runtime has closed the demonstrated ownership and execution-control
gaps through R3.2. No broader planner, scheduler, workspace, sandbox,
distributed runtime, or final-answer streaming milestone is active.

Before expanding the architecture, record:

1. one concrete current workload;
2. the missing ownership, correctness, or safety boundary it exposes; and
3. the smallest contract that closes that boundary.

A future capability described in an architecture or evolution document is not
authorized work until those entry conditions are met.

## Closed Work Item: S3 Protocol Surface Governance

**Scope:** make the supported A2A transport contract coherent without changing
`MainAgentCore` lifecycle behavior or adding another runtime owner.

### Work

1. Keep the declared A2A protocol version at `0.3` for this stage.
2. Use the A2A 0.3 slash-style JSON-RPC methods as the canonical `/rpc`
   contract.
3. Classify `tasks/resume` as a versioned Vermay extension for local approval
   continuation.
4. Remove unadvertised path-style bindings and PascalCase method aliases rather
   than maintaining a second transport surface.
5. Align the Agent Card, backend dispatch, first-party clients, tests, and API
   documentation with the same endpoint catalog.

### Acceptance

- `/rpc` exposes one documented A2A 0.3 method-name family.
- `tasks/resume` is visibly identified as a Vermay extension rather than an
  A2A standard method.
- Removed aliases and path bindings cannot create an alternate lifecycle path.
- Focused and full-stack regression gates pass without tracked-file pollution.

All acceptance conditions were met on 2026-08-14.

## Current Handoff

**Objective:** preserve the closed S3 boundary without reconstructing
documentation authority or runtime ownership from chat history.

### Completed Baseline

- `MainAgentCore` is the single A2A lifecycle owner.
- Every committed local execution slice uses one versioned immutable queue
  command. `InProcessLocalExecutionAdapter` owns process-local scheduling
  mechanics only; Core-owned callbacks claim work and persist outcomes.
- Typed lifecycle mutations enter `MainAgentCore.execute()` or
  `MainAgentCore.stream()`; `TaskOutcomeRecorder` remains a subordinate
  persistence boundary for accepted execution outcomes.
- Durable Message Ingress prevents duplicate routing and execution for one
  top-level `messageId`.
- Local process transitions, continuation kinds, startup reconciliation, and
  bounded context assembly have explicit contracts.
- Task failures have durable public error projection and safe manual retry
  lineage.
- Durable Task projections have a monotonic `lifecycle_revision`; replay order
  remains independently owned by Task-event `event_id`.
- The Task-event table is the replay authority. Process-local notification is
  post-commit and disposable; subscribers re-read by `event_id`, and an
  unprojectable durable event fails explicitly without advancing the cursor.
- The Web console reconciles hydration, SSE, continuation, retry, and recovery
  through one revision-aware Task reducer and derives Session lifecycle
  presentation from the accepted Task projection.
- Context detail management reads default to a bounded latest window. One
  Session read controller owns the four related requests, and one Task event
  controller owns replay, subscription, and recovery without becoming another
  lifecycle state writer.
- Local non-read-only effects use a durable Tool Invocation Ledger.
- Governed execution limits and cancellation reach the current model and
  SSH/Kubernetes capability paths.
- An opt-in read-only live Kubernetes workflow gate exercises the real A2A
  Task boundary and validates normalized tool observations without coupling
  external infrastructure to the deterministic release gates.
- The Inspector separates public A2A state, durable local process state, and
  the LangGraph checkpoint thread.
- Documentation has stable overview, architecture, component, operations, and
  active-development domains with a compact AI collaboration entry point.

### Decisions To Preserve

- `/rpc` is the canonical A2A integration endpoint. S3 removed unadvertised
  path-style bindings instead of preserving a second public transport surface.
- [API Boundary](../../components/backend/api-boundary.md) owns the endpoint
  catalog. Operations documents explain usage and link to it.
- LangGraph owns local graph execution and checkpoint continuation only.
- SQLite and the in-process worker remain the current single-host baseline.
- Approval is authorization, not execution isolation.
- Stable runtime contracts belong in architecture or backend component
  documentation; this development area owns current priority and unsettled
  work.
- Task revision and event replay cursor are separate contracts. New lifecycle
  paths must preserve both and must not write browser Task state outside the
  reducer.
- Initial Task execution, approval continuation, and ordinary-input
  continuation must not bypass the durable queue-command and Core-owned
  claim/outcome path.

### Validation State

Closed milestone counts below are immutable evidence from the date on which
that milestone was accepted. They are not the current checkout's test count.
The current regression baseline is owned by the
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md).

S2 closed on 2026-08-14 with deterministic and live-model evidence:

- `scripts/check_single_host_reliability.sh` passed with 189 backend tests and
  10 Playwright tests.
- `scripts/check_full_stack_regression.sh` passed with 489 backend tests,
  frontend type checking, the Next.js production build, and 10 deterministic
  Playwright tests.
- Both gates left tracked files unchanged.
- A direct Message completed through `/rpc`, produced the expected model-backed
  answer, persisted one user Message and one agent Message, and created no Task.
- A local Task completed through `/rpc` on attempt 1, preserved its A2A Task and
  LangGraph thread linkage, and returned the expected final answer artifact.

The suite still reports a non-blocking Starlette `TestClient` deprecation
warning. Live MCP, SSH/Kubernetes, and child-agent workflows were not part of
the S2 acceptance boundary and were not exercised by this refresh.

The later live Kubernetes gate implementation was verified independently from
that historical S2 evidence:

- `scripts/check_live_kubernetes_workflow.sh` passes shell syntax validation
  and refuses to contact external infrastructure unless `RUN_LIVE_K8S=1` is
  set explicitly.
- A controlled local A2A fixture verified successful list-and-describe
  observation validation.
- A controlled duplicate-observation fixture verified that repeated identical
  successful reads fail the gate.
- No operator-configured model, MCP/SSH transport, or Kubernetes target was
  exercised during this implementation pass. The script therefore provides a
  validation mechanism, not new live-environment evidence.

S3 closed on 2026-08-14 with protocol-focused, full-stack, and live-model
evidence:

- The focused A2A adapter, boundary, main-agent API, and remote-client suite
  passed with 77 tests.
- `scripts/check_single_host_reliability.sh` passed with 186 backend tests and
  10 Playwright tests.
- `scripts/check_full_stack_regression.sh` passed with 480 backend tests,
  frontend type checking, the Next.js production build, and 10 deterministic
  Playwright tests.
- `scripts/a2a_dev_smoke.sh` passed against a temporary local server and the
  configured Ollama model using only the canonical `/rpc` surface.
- The live smoke accepts asynchronous Task creation and polls `tasks/get` to a
  terminal state instead of assuming that `message/send` completes a Task
  synchronously.
- The Agent Card, backend dispatch, child-agent client, Web BFF envelopes,
  smoke script, tests, README examples, and API catalog now use the A2A `0.3.0`
  slash-style method family. Approval continuation is declared as the
  versioned Vermay `tasks/resume` extension.
- Removed path-style bindings return `404`, and removed PascalCase JSON-RPC
  aliases return `-32601`.

### Post-S3 Implementation Rescan

A static implementation and documentation rescan on 2026-08-14 did not
establish a new correctness defect or an entry condition for S4, S5, or S6.
The review covered the public route surface, lifecycle ownership, persistence
and read models, Web BFF and stream behavior, concentrated implementation
modules, tests, and release configuration.

The following boundaries remain coherent:

- FastAPI exposes the Agent Card and canonical `/rpc` A2A surface plus
  first-party `/api/*` management and read-model endpoints. Removed path-style
  A2A routes have not reappeared.
- `MainAgentCore` remains the only owner that admits a top-level Message,
  routes it, creates or resumes a Task, and projects the result. LangGraph,
  SQLite repositories, transport adapters, and the Web BFF retain subordinate
  execution, persistence, projection, and proxy roles.
- The backend A2A version and method catalog is centralized in
  `vermay/a2a_protocol.py`. The TypeScript client intentionally mirrors that
  wire catalog and is protected by boundary and full-stack tests; no generated
  cross-language schema is justified by current drift evidence.
- The Web console uses EventSource for active Task updates and a finite
  terminal reconciliation path. No periodic production polling loop was found.
- New Contexts receive their title from the first admitted user Message. The
  list endpoint's first-message title lookup is a fallback for untitled records,
  not the normal creation path.

The review also confirmed these accepted constraints and activation signals:

| Current constraint | Why it remains accepted | Activation signal |
| --- | --- | --- |
| Context list and detail reads use bounded `limit/offset` defaults, but the Web console has no older-history control. | The latest-window read closes the current payload risk without introducing cursor infrastructure or a generic query layer. | A real retained Context needs records older than the 200-record window, or concurrent writes make offset navigation observably unstable. |
| `MainAgentCore`, `MainAgentStore`, and `agent-console.tsx` remain concentrated modules. | Their ownership is explicit, and the scan found no competing state machine or concrete change-coupling defect. | A real change becomes unsafe or repeatedly crosses unrelated responsibilities, activating one focused S5 extraction. |
| Python and TypeScript maintain separate A2A wire constants. | Cross-language generation would add tooling and release work; current boundary tests catch known drift. | A protocol upgrade or reproduced constant mismatch authorizes a focused contract-generation decision. |
| Child-agent endpoint inference retains a tested Agent Card compatibility fallback. | It is isolated to remote interoperability and does not create a second local lifecycle path. | A real child-agent interoperability defect determines whether to narrow, replace, or remove it. |
| Python dependencies use lower bounds, and the Starlette `TestClient` warning remains. | Neither has broken the current source-development gates. | Release preparation or reproduced dependency drift activates S6. |

This was a static review and does not replace the S3 validation evidence above.
No product code was changed as part of the rescan.

Report branch position, commits, and working-tree state as live Git state, not
as durable content in this roadmap.

### Next Task

Do not activate S4, Platform M7, or any later milestone automatically. Preserve
the S3 release baseline and the Platform M1-M6 contracts. Collect measured
evidence of a deep-history workflow, execution-boundary defect, concrete
change-coupling defect, or release-reproducibility failure before authorizing
another runtime or platform milestone.

The operations layer now provides an opt-in, read-only live Kubernetes
workflow gate. It is evidence collection infrastructure, not a new runtime
milestone and not part of default CI. The next runtime decision should be based
on a dated run of that gate against an operator-configured target: a clean pass
preserves the current phase gate, while a reproduced duplicate call,
correction failure, or budget exhaustion authorizes only the narrowest
corresponding stabilization change.

### 2026-08-16 Stabilization Strategy

The current stabilization pass closes two demonstrated races and read-model
risks without expanding runtime capabilities:

- cancel and continuation HTTP responses initially used one timestamp-aware
  Task snapshot merge. Platform M1 superseded that partial fix with one
  revision-aware reducer for every durable Task source;
- `/api/contexts` defaults to the latest 100 records, supports bounded
  `limit/offset`, and resolves fallback titles with one bulk query per page;
- `max_elapsed_seconds` remains optional. Human approval/input waiting is part
  of its wall-clock age today, so setting an arbitrary short default would
  create false expirations rather than predictable execution control;
- validation counts in closed milestones remain historical. Current evidence
  is updated only in the operations reliability matrix after the gate runs.

This is a controlled subset of S4, not authorization for cursor pagination or
incremental loading across every management endpoint.

The later Platform M1 close-out on 2026-08-16 passed the 215-test focused
single-host backend gate and 18 deterministic Playwright tests. The full-stack
gate passed all 487 Python tests, frontend type checking, the Next.js
production build, and the same 18 Playwright tests. Current evidence is
maintained in the
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md)
and the
[M1 Task Projection Handoff](../platform/m1-task-projection.md).

Platform M2 closed later on 2026-08-16. Its focused command and A2A boundary
suite passed 137 tests; the single-host gate passed 217 backend and 18
Playwright tests; and the full-stack gate passed 489 Python tests, frontend
type checking, the Next.js production build, and the same 18 Playwright tests.
The command boundary and preserved limits are recorded in the
[M2 Lifecycle Command Surface Handoff](../platform/m2-lifecycle-command-surface.md).

Platform M3 closed later on 2026-08-16. Its focused lifecycle and transaction
suite passed 171 tests; the single-host gate passed 223 backend and 18
Playwright tests; and the full-stack gate passed 495 Python tests, frontend
type checking, the Next.js production build, and the same 18 Playwright tests.
The commit-before-effect contract and preserved limits are recorded in the
[M3 Transaction And Post-Commit Boundary Handoff](../platform/m3-transaction-post-commit-boundary.md).

Platform M4 closed later on 2026-08-16. Its focused store and Core suite passed
85 tests; the single-host gate passed 225 backend and 18 Playwright tests; and
the full-stack gate passed 497 Python tests, frontend type checking, the
Next.js production build, and the same 18 Playwright tests. An isolated server
using the current configured model also passed `scripts/a2a_dev_smoke.sh` with
temporary lifecycle and LangGraph databases. The bounded execution port,
versioned queue-command contract, and preserved single-host limits are recorded
in the [M4 Bounded Local Execution Handoff](../platform/m4-bounded-local-execution.md).

Platform M5 closed later on 2026-08-16. Its focused store and A2A replay suite
passed 82 tests; the single-host gate passed 228 backend and 19 Playwright
tests; and the full-stack gate passed 500 Python tests, frontend type checking,
the Next.js production build, and the same 19 Playwright tests. The durable
event-table authority, disposable post-commit notifier, reconnect cursor, and
explicit projection-error contract are recorded in the
[M5 Event Replay And Subscription Handoff](../platform/m5-event-replay-subscription.md).

## Evidence-Gated Follow-up Plan

The 2026-08-14 code and documentation review confirmed that the architecture
direction is stable. The remaining work is boundary consolidation and release
quality, not a new runtime design. S2 and S3 are closed. The remaining stages
are evidence-gated candidates, not an active implementation queue.

### S3: Protocol Surface Governance (Completed)

**Entry condition:** S2 has closed, and the supported A2A version plus the
first-party Web console contract can be verified against the same endpoint
catalog.

**Scope:** make the supported transport surface explicit without changing
`MainAgentCore` lifecycle behavior.

1. Choose one canonical JSON-RPC method-name family for `/rpc`.
2. Classify `tasks/resume` explicitly as a versioned Vermay extension unless a
   target A2A specification defines the required continuation operation.
3. Classify every path-style A2A binding as retained or removable and preserve
   only the tests required by that decision.
4. Keep all retained bindings as thin adapters over the same core and
   projection helpers.

**Acceptance:** completed. The endpoint catalog, backend dispatch, BFF calls,
tests, and Agent Card describe one coherent protocol surface; no compatibility
decision creates a second lifecycle path.

### S4: Bounded Read Models

**Entry condition:** retained local data or measured request size demonstrates
that full Context, Task, route-decision, or delegation reads are no longer a
safe default.

**Scope:** introduce bounded, cursor-based reads without changing lifecycle
storage or transcript semantics.

1. Define one cursor contract for first-party management/read-model endpoints.
2. Add bounded defaults to Context, Task, route-decision, and delegation reads.
3. Update the Web console to load incrementally while preserving selection and
   chronological conversation projection.
4. Add focused backend and Playwright coverage for page boundaries and empty
   or deleted selections.

**Acceptance:** the normal Web console path no longer requires unbounded table
reads, and pagination cannot duplicate or omit visible records.

### S5: Restrained Ownership Extraction

**Entry condition:** a concrete change in an existing workflow is materially
slowed or made unsafe by one of the current concentrated modules. File length
alone is not sufficient evidence.

**Scope:** reduce change coupling while preserving `MainAgentCore` as the
single lifecycle owner and one SQLite transaction boundary.

1. Extract one internal `MainAgentCore` collaborator at a time, starting with
   Message admission, local Task lifecycle, remote proxy, or startup
   reconciliation according to the active defect or change.
2. Split `MainAgentStore` behind the same store and transaction owner into
   narrowly named Context/Message, Task, invocation, or agent-registry
   repositories only as their workflows are touched.
3. In the Web console, extract one ownership-based controller hook for Task
   stream/reconciliation or session read models. Do not split stateless visual
   fragments solely to reduce the size of `agent-console.tsx`.
4. Add focused tests at each extracted boundary before moving another
   responsibility.

**Acceptance:** external A2A, management API, persistence, and Web console
behavior remain unchanged; no extraction introduces a second state machine,
database owner, or competing frontend source of truth.

### S6: Release Reproducibility

**Entry condition:** the project is preparing a repeatable `0.1.x` source
release or dependency drift causes a reproducible regression.

**Scope:** strengthen the maintained source-release boundary.

1. Add a documented Python dependency constraint or lock strategy compatible
   with editable development installs.
2. Add a minimal backend static-analysis gate and resolve the current Starlette
   `TestClient` deprecation warning.
3. Add fast focused tests for frontend conversation projection and task
   presentation logic where browser-only coverage is unnecessarily expensive.
4. Preserve the existing worktree-clean full-stack release gate.

**Acceptance:** a clean source checkout can reproduce the supported dependency
set and pass the deterministic backend, frontend, build, and browser checks.

### Deferred Workload-Driven Candidates

The following work remains deferred until a measured workload activates it:

- Task worker admission control requires demonstrated queue pressure or
  unpredictable queue latency.
- BFF request and stream-establishment deadlines require a reproduced stuck
  request; malformed SSE handling requires a protocol-drift failure fixture.
- Task final-answer streaming requires evidence that final-artifact latency is
  unacceptable.
- Remote continuation requires a real delegated workflow that cannot be
  completed through the current proxy boundary.
- Authentication, network policy, multi-process event notification,
  workspaces, sandboxes, planning, and distributed scheduling remain
  deployment- or workload-stage decisions.

CLI model selection continues to have one path: the default or named model is
resolved from `config/models.json`, and `--model` is the only per-run selector.
Provider URLs, credentials, timeouts, and tool-calling strategies are
configuration concerns rather than parallel CLI overrides. Task approval
resume likewise has one JSON-RPC method, `tasks/resume`; the former method-name
retry path has been removed.

## Guardrails

- Preserve A2A JSON-RPC and SSE as the public agent boundary.
- Do not introduce a second lifecycle owner or compatibility store.
- Keep A2A Task state, local process state, LangGraph continuation state, and
  tool-effect state distinct.
- Prefer a focused contract or adapter over broad abstraction.
- Do not activate Temporal, Redis, a distributed scheduler, a generic
  workspace, or a sandbox without demonstrated workload evidence.
- Do not add final-answer token streaming without measured latency evidence and
  an explicitly activated milestone.
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
| Single-host regression matrix | Implemented; S2 refresh closed | [Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md) |
| Detailed M0-R3 record | Historical | [Runtime Roadmap Through R3](../../history/runtime/roadmap-through-r3.md) |

## Activation Rule For Future Work

While no next stage is active, leave the roadmap with either:

- one evidence-backed active item with scope and acceptance criteria; or
- an explicit statement that no runtime expansion is active.

Do not turn the completed milestone history back into a speculative backlog.

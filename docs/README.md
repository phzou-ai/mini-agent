# Vermay Documentation

This directory is the durable documentation memory for Vermay. It has three
explicit layers so that contributors and AI tools can distinguish the product
that exists, the work authorized now, and the evidence that explains earlier
decisions without reconstructing them from chat history.

Before substantial work, read the concise
[AI Collaboration Summary](AI-collaboration-summary.md). Use the complete
[AI Collaboration Guide](AI-collaboration-guide.md) when organizing
documentation, resolving authority conflicts, or preparing a durable handoff.

## Documentation Model

| Layer | Question it answers | Entry |
| --- | --- | --- |
| Stable Reference | What is Vermay now, and how does the supported system work? | [overview/](overview/README.md) |
| Active Development | What work is authorized now, and what evidence closes it? | [dev/](dev/README.md) |
| Historical Evidence | Why was an earlier decision made, and what work is already complete? | [history/](history/README.md) |

Stable Reference is the product truth. Active Development may change
frequently but must not silently override stable architecture. Historical
Evidence is explanatory and never authorizes new work.

## Start Here

- To understand the current product, follow [Understand The Project](#understand-the-project).
- To continue current work, read the [Active Development index](dev/README.md)
  and its authoritative roadmap.
- To investigate an earlier decision, enter [Historical Evidence](history/README.md)
  only after reading the current stable boundary.

## Project Quick Profile

Vermay is an A2A-first main-agent runtime with a colocated Web console. It
supports direct model-backed Messages, durable local Tasks executed through
LangGraph, and delegation to registered child A2A agents.

### Product Shape

- The Python backend exposes the public A2A JSON-RPC and SSE boundary.
- `MainAgentCore` is the single owner of public Message and Task lifecycle
  decisions.
- Lifecycle mutations enter one immutable typed command surface; accepted local
  and remote Task outcomes are persisted by a core-owned outcome recorder.
- LangGraph is an internal execution engine for local Tasks and checkpoint
  continuation. A LangGraph thread is not a public A2A identity.
- Durable Message Ingress provides idempotent admission before routing or
  execution.
- SQLite stores the current single-host lifecycle state, checkpoints, and
  supporting local data.
- The colocated Next.js application provides the Agent Console, conversation
  surface, Task controls, and runtime Inspector.

### Primary Identities

| Identity | Meaning |
| --- | --- |
| A2A `contextId` | Public conversation context shared across related Messages and Tasks. |
| A2A `messageId` | Public message identity used for durable ingress and idempotency. |
| A2A `taskId` | Public identity of one stateful A2A Task. |
| Local process | Durable internal execution record owned by the Main Agent. |
| LangGraph `thread_id` | Internal checkpoint continuation identity for a local Task execution. |

Do not merge these identities or their state machines for presentation
convenience. See
[Lifecycle And State Ownership](architecture/lifecycle-and-state.md).

### Repository Shape

- `vermay/`: backend application and runtime.
- `web/`: independently buildable Next.js Agent Console.
- `config/`: model and runtime configuration.
- `tests/` and `web/tests/`: backend and browser verification.
- `docs/`: stable reference, active development control, and historical evidence.

The detailed map is in [Repository Map](overview/repository-map.md).

### Current Engineering Position

The current target is a predictable single-host runtime, not a distributed
Agent OS platform. Reliability, lifecycle clarity, protocol correctness,
failure handling, and maintainable boundaries take priority over new
infrastructure or speculative extensibility.

Current implementation facts are owned by
[Current System Architecture](architecture/current-system.md) and the
[Backend Runtime Contracts](components/backend/runtime/README.md). Current
priority and acceptance criteria are owned by the
[Runtime Roadmap](dev/runtime/roadmap.md).

## Stable Reference

### Overview

[overview/](overview/README.md) explains the project position, repository map,
and primary request flows. Start here when first reading the repository.

### Architecture

[architecture/](architecture/README.md) owns current system architecture,
runtime identity and state ownership, and the conditional Agent OS evolution
direction.

### Components

[components/](components/README.md) describes the backend and Web UI as concrete
implementation units, including their module and API boundaries.

### Operations

[operations/](operations/README.md) owns local development, runtime topology,
release boundaries, persistence, and regression gates.

Stable documents describe supported current behavior. They do not contain
implementation queues, temporary rollout narration, or unactivated TODO lists.

## Active Development

[dev/](dev/README.md) owns active priorities, implementation specifications,
deferred work, acceptance criteria, and durable handoff state. Content here may
change frequently and must not override stable architecture silently.

## Historical Evidence

[history/](history/README.md) contains completed roadmaps, dated assessments,
and finished maintenance records. These documents preserve rationale and
verification evidence but do not describe the current system or current
priority.

## Suggested Reading Paths

### Understand The Project

1. [overview/README.md](overview/README.md)
2. [architecture/current-system.md](architecture/current-system.md)
3. [overview/repository-map.md](overview/repository-map.md)
4. [overview/request-flow.md](overview/request-flow.md)
5. [components/README.md](components/README.md)

### Continue Runtime Development

1. [AI-collaboration-summary.md](AI-collaboration-summary.md)
2. [architecture/lifecycle-and-state.md](architecture/lifecycle-and-state.md)
3. [dev/runtime/README.md](dev/runtime/README.md)
4. [dev/runtime/roadmap.md](dev/runtime/roadmap.md)

### Plan A Single-Host Contract Refactor

1. [architecture/current-system.md](architecture/current-system.md)
2. [architecture/lifecycle-and-state.md](architecture/lifecycle-and-state.md)
3. [dev/runtime/roadmap.md](dev/runtime/roadmap.md)
4. [dev/platform/README.md](dev/platform/README.md)
5. [dev/platform/architecture-modernization-plan.md](dev/platform/architecture-modernization-plan.md)
6. [dev/platform/m0-contract-baseline.md](dev/platform/m0-contract-baseline.md)
7. [dev/platform/m1-task-projection.md](dev/platform/m1-task-projection.md)
8. [dev/platform/m2-lifecycle-command-surface.md](dev/platform/m2-lifecycle-command-surface.md)
9. [dev/platform/m3-transaction-post-commit-boundary.md](dev/platform/m3-transaction-post-commit-boundary.md)
10. [dev/platform/m4-bounded-local-execution.md](dev/platform/m4-bounded-local-execution.md)
11. [dev/platform/m5-event-replay-subscription.md](dev/platform/m5-event-replay-subscription.md)

### Change The Backend Or A2A Boundary

1. [components/backend/README.md](components/backend/README.md)
2. [architecture/current-system.md](architecture/current-system.md)
3. [architecture/lifecycle-and-state.md](architecture/lifecycle-and-state.md)
4. [components/backend/api-boundary.md](components/backend/api-boundary.md)
5. [components/backend/modules.md](components/backend/modules.md)

For lifecycle or execution changes, continue with
[dev/runtime/README.md](dev/runtime/README.md).

### Change The Web Console

1. [components/web/README.md](components/web/README.md)
2. [components/web/architecture.md](components/web/architecture.md)
3. [components/web/modules.md](components/web/modules.md)
4. [architecture/lifecycle-and-state.md](architecture/lifecycle-and-state.md)
5. [operations/regression-gate.md](operations/regression-gate.md)

### Review Or Refactor The Codebase

1. [architecture/current-system.md](architecture/current-system.md)
2. [components/README.md](components/README.md)
3. [history/maintenance/README.md](history/maintenance/README.md)
4. [dev/README.md](dev/README.md)

### Run Or Release The System

1. [operations/README.md](operations/README.md)
2. [operations/local-development.md](operations/local-development.md)
3. [operations/runtime-and-release.md](operations/runtime-and-release.md)
4. [operations/regression-gate.md](operations/regression-gate.md)

## Update Rule

1. Record iterative implementation state in `docs/dev/*` first.
2. Promote only settled behavior and boundaries into Stable Reference.
3. Move completed, still-useful implementation evidence into `docs/history/*`.
4. Keep patch history and temporary debugging notes out of Stable Reference.
5. Each major domain has one `README.md` as its navigation and ownership entry.
6. Use relative links so documentation works on GitHub and every checkout.
7. Create a new `docs/dev/<domain>/` only when an independent development
   concern has its own status and multiple related documents. A code module or
   one small task does not justify a documentation domain.

External issue trackers may coordinate assignment and delivery, but repository
documentation remains authoritative for durable technical decisions and
architecture.

## Naming Boundary

The project name is Vermay. The Python package is `vermay`, the CLI command is
`vermay`, and environment configuration uses the `VERMAY_*` prefix.

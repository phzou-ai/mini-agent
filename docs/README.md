# Vermay Documentation

This directory is the durable documentation memory for Vermay. It separates
stable project truth from active implementation work so that contributors and
AI tools can identify the current authority without reconstructing it from
chat history.

Before substantial work, read the concise
[AI Collaboration Summary](AI-collaboration-summary.md). Use the complete
[AI Collaboration Guide](AI-collaboration-guide.md) when organizing
documentation, resolving authority conflicts, or preparing a durable handoff.

## Project Quick Profile

Vermay is an A2A-first main-agent runtime with a colocated Web console. It
supports direct model-backed Messages, durable local Tasks executed through
LangGraph, and delegation to registered child A2A agents.

### Product Shape

- The Python backend exposes the public A2A JSON-RPC and SSE boundary.
- `MainAgentCore` is the single owner of public Message and Task lifecycle
  decisions.
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
- `docs/`: stable reference and active development memory.

The detailed map is in [Repository Map](overview/repository-map.md).

### Current Engineering Position

The current target is a predictable single-host runtime, not a distributed
Agent OS platform. Reliability, lifecycle clarity, protocol correctness,
failure handling, and maintainable boundaries take priority over new
infrastructure or speculative extensibility.

Current implementation status and next priorities are owned by
[Runtime Development](dev/runtime/README.md) and its
[Roadmap](dev/runtime/roadmap.md).

## Documentation Layers

### Stable Overview

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

### Active Development

[dev/](dev/README.md) owns active priorities, implementation specifications,
maintenance plans, deferred work, and dated review evidence. Content here may
change frequently and must not override stable architecture silently.

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
3. [dev/maintenance/README.md](dev/maintenance/README.md)
4. [dev/runtime/README.md](dev/runtime/README.md)

### Run Or Release The System

1. [operations/README.md](operations/README.md)
2. [operations/local-development.md](operations/local-development.md)
3. [operations/runtime-and-release.md](operations/runtime-and-release.md)
4. [operations/regression-gate.md](operations/regression-gate.md)

## Update Rule

1. Record iterative implementation state in `docs/dev/*` first.
2. Promote only settled behavior and boundaries into stable documentation.
3. Keep patch history and temporary debugging notes out of stable reference.
4. Each major domain has one `README.md` as its navigation and ownership entry.
5. Use relative links so documentation works on GitHub and every checkout.
6. Do not create deeper folders until multiple real documents need the split.

External issue trackers may coordinate assignment and delivery, but repository
documentation remains authoritative for durable technical decisions and
architecture.

## Naming Boundary

The project name is Vermay. The Python package is `vermay`, the CLI command is
`vermay`, and environment configuration uses the `VERMAY_*` prefix.

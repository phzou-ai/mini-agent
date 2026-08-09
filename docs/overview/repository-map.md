# Repository Map

This document maps the current repository at a stable, project-wide level.
Use the component documentation for implementation details and the development
documentation for active priorities.

## Top-level Layout

| Path | Responsibility |
| --- | --- |
| `vermay/` | Python application, A2A API binding, Main Agent lifecycle, LangGraph runtime, model clients, MCP integration, tools, and local persistence. |
| `web/` | Colocated Next.js Agent Console, A2A/BFF client contracts, presentation logic, and browser regression tests. |
| `config/` | Runtime configuration such as named model definitions and MCP server selection. |
| `skills/` | Authored local skills available to runtime context assembly. |
| `examples/` | Runnable integration examples, including MCP servers. |
| `tests/` | Python unit and integration tests for backend contracts and runtime behavior. |
| `evals/` | Offline evaluation scenarios and supporting inputs. |
| `scripts/` | Repository maintenance and full-stack regression commands. |
| `docs/` | Stable reference, active development records, operational guidance, and AI collaboration instructions. |
| `data/` | Local runtime state and generated development data; not a source-code contract. |
| `traces/` | Local execution traces used for diagnosis and evaluation. |

Generated directories such as `.venv/`, `web/node_modules/`, `web/.next*/`,
and test result caches are not part of the architecture.

## Backend Boundaries

The backend is organized around one lifecycle owner:

- `vermay/main_agent/` owns public Message and Task lifecycle decisions,
  durable ingress, routing, continuation, cancellation, delegation, and
  protocol-facing projection.
- `vermay/langgraph_runtime/` owns local graph execution and checkpoint
  continuation for Tasks selected for local execution.
- `vermay/api/` exposes the A2A JSON-RPC and SSE binding plus first-party
  management read models. It delegates lifecycle behavior to
  `MainAgentCore`.
- `vermay/model_clients/` isolates provider transports and tool-calling
  protocol differences.
- `vermay/mcp/` discovers selected MCP capabilities and adapts them into the
  same tool and runtime-context boundaries as built-in capabilities.
- `vermay/tools/` contains built-in capability domains.
- `vermay/infra/` contains infrastructure adapters such as SSH command
  construction.

See [Backend Modules](../components/backend/modules.md) for the detailed module
map and [Current System Architecture](../architecture/current-system.md) for
ownership and request flow.

## Web Boundaries

The `web/` application is colocated with the backend but remains a separate
deployment unit:

- `web/app/(agent)/agent/` owns the Agent Console surface and page-level UI
  orchestration.
- `web/lib/agent/` owns A2A/BFF contracts, stream parsing, errors,
  conversation projection, and Task presentation helpers.
- `web/styles/` owns global and feature styling.
- `web/tests/` owns Playwright end-to-end and regression coverage.

See [Web Architecture](../components/web/architecture.md) and
[Web Modules](../components/web/modules.md).

## Configuration And Runtime Data

- `config/models.json` defines named model configurations and the selected
  primary and router models.
- MCP server definitions are configuration-driven and inactive unless
  explicitly selected.
- SQLite databases, checkpoints, traces, evaluation output, and generated skill
  proposals are local operational data. They do not define public A2A identity
  or replace the documented lifecycle ownership model.

Operational setup and persistence boundaries are documented under
[Operations](../operations/README.md).

## Documentation Placement

- Project-wide orientation belongs in `docs/overview/`.
- Settled ownership and system invariants belong in `docs/architecture/`.
- Concrete backend and Web UI boundaries belong in `docs/components/`.
- Running, releasing, and regression gates belong in `docs/operations/`.
- Active plans, implementation specifications, and review evidence belong in
  `docs/dev/`.

Read [AI Collaboration Guide](../AI-collaboration-guide.md) before changing the
documentation structure or promoting active decisions into stable reference.

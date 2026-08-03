# Vermay Agent Documentation

## Scope

This directory contains stable project-facing documentation for Vermay Agent.

The project is positioned as an A2A-native main-agent runtime and inspectable process host, with a practical workbench for validating orchestration, tool execution, approvals, memory, skills, model adapters, MCP integration, delegation, and real-world tool patterns.

## Reading Order

1. [overview.md](overview.md) - project purpose, current capabilities, and operating model.
2. [agent-os-architecture.md](agent-os-architecture.md) - strategic positioning against assistant-style runtimes, Agent OS process model, A2A boundary, router policy, Context causality, state ownership, security boundaries, and incremental migration direction.
3. [modules.md](modules.md) - key packages and module responsibilities.
4. [operations.md](operations.md) - CLI usage, runtime options, environment configuration, and traces.
5. [runtime-and-release.md](runtime-and-release.md) - supported runtime topology, secrets and persistence boundaries, and release gate.
6. [langgraph-interrupt-resume.md](langgraph-interrupt-resume.md) - approval interrupt, checkpoint, and resume flow.
7. [server-api-readiness.md](server-api-readiness.md) - local API surface, session metadata, and approval resume contract.
8. [code-organization-review.md](code-organization-review.md) - current code organization assessment and cleanup order.
9. [runtime-refinement/README.md](runtime-refinement/README.md) - current runtime roadmap, ownership contracts, focused implementation specifications, staged evolution criteria, and dated review records.

For active runtime work, start with the runtime-refinement index. Its roadmap is
the authoritative source for implementation priority; its dated review is
historical evidence rather than a second roadmap. Its runtime evolution path
defines when broader execution, workspace, planning, or distribution work is
justified; it is a conditional capability map, not a default implementation
backlog, and it does not replace the active roadmap.

## Documentation Boundary

Repository docs should describe the current project and its stable module boundaries.

Historical planning notes, batch implementation records, and broader roadmap material are kept outside this repository in the companion `mini-agent-docs` workspace.

Archived implementation material retained in this repository is kept under `archive/` and is not part of the active runtime or default test suite.

## Naming Boundary

The current project name is Vermay Agent. The active Python package is `vermay_agent`, and the preferred CLI command is `vermay-agent`.

The legacy `mini-agent` command, `mini_agent` import namespace, and `MINI_AGENT_*` environment variable prefix remain compatibility aliases during the migration. New code and docs should prefer `vermay-agent`, `vermay_agent`, and `VERMAY_AGENT_*` configuration names. The external planning workspace is still named `mini-agent-docs` for now, so path references to that directory are intentional.

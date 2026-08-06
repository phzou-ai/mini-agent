# Vermay Documentation

## Scope

This directory contains stable project-facing documentation for Vermay.

The project is positioned as an A2A-native main-agent runtime and inspectable process host, with a practical workbench for validating orchestration, tool execution, approvals, memory, skills, model adapters, MCP integration, delegation, and real-world tool patterns.

## Reading Order

1. [overview.md](overview.md) - project purpose, current capabilities, and operating model.
2. [agent-os-architecture.md](agent-os-architecture.md) - strategic positioning against assistant-style runtimes, Agent OS process model, A2A boundary, router policy, Context causality, state ownership, security boundaries, and incremental migration direction.
3. [modules.md](modules.md) - key packages and module responsibilities.
4. [operations.md](operations.md) - CLI usage, runtime options, environment configuration, and traces.
5. [runtime-and-release.md](runtime-and-release.md) - supported runtime topology, secrets and persistence boundaries, and release gate.
6. [langgraph-interrupt-resume.md](langgraph-interrupt-resume.md) - approval interrupt, checkpoint, and resume flow.
7. [server-api-readiness.md](server-api-readiness.md) - local API surface, session metadata, and approval resume contract.
8. [code-organization-review.md](code-organization-review.md) - current code organization assessment and historical cleanup guidance.
9. [cleanup-and-refactor-plan.md](cleanup-and-refactor-plan.md) - completed maintenance-pass scope, protected compatibility boundaries, execution order, and regression evidence.
10. [runtime-refinement/README.md](runtime-refinement/README.md) - current runtime roadmap, ownership contracts, focused implementation specifications, staged evolution criteria, and dated review records.

For active runtime work, start with the runtime-refinement index. Its roadmap is
the authoritative source for implementation priority; its dated review is
historical evidence rather than a second roadmap. Its runtime evolution path
defines when broader execution, workspace, planning, or distribution work is
justified; it is a conditional capability map, not a default implementation
backlog, and it does not replace the active roadmap.

## Documentation Boundary

Repository docs should describe the current project and its stable module boundaries.

Historical planning notes, batch implementation records, and broader roadmap material are kept outside this repository in a companion planning workspace.

Unsupported historical runtime implementations are not retained in the product
package. Dated design and implementation records remain documentation only.

## Naming Boundary

The project name is Vermay. The Python package is `vermay`, the CLI command is `vermay`, and environment configuration uses the `VERMAY_*` prefix.

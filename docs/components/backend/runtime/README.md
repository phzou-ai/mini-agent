# Backend Runtime Contracts

> Status: Stable
> Authority: Implemented backend runtime ownership and persistence contracts

This section owns settled contracts implemented by the Python backend. These
documents describe current behavior and invariants; they do not authorize new
work or maintain a second roadmap.

## Contracts

| Contract | Ownership boundary |
| --- | --- |
| [Runtime Composition And Remote Proxy Synchronization](runtime-composition-and-remote-proxy.md) | Application-owned execution resources and monotonic child-agent proxy synchronization. |
| [Durable Message Ingress](message-ingress.md) | Idempotent admission and durable outcome ownership for a top-level A2A `messageId`. |
| [Direct-Message Failure Presentation](direct-message-failures.md) | Durable failure projection for direct Messages without fabricating an assistant answer or Task. |
| [Durable Context Input Cut](context-input-cut.md) | Context-local ordering and immutable initial-history boundary for a local Task. |
| [Local Process Transition Governance](local-process-transitions.md) | Authoritative internal process transitions and atomic lifecycle events. |
| [Startup Reconciliation](startup-reconciliation.md) | Conservative recovery for durable ingress, queued execution, interrupted work, and uncertain effects. |
| [Task Failure Projection And Safe Retry](task-failure-retry.md) | Durable public failure facts, canonical browser reconciliation, and explicit retry lineage. |
| [Tool Invocation Ledger](tool-invocation-ledger.md) | Durable identity, approval binding, and replay safety for local non-read-only effects. |
| [Governed Execution Kernel](governed-execution-kernel.md) | Immutable execution limits, stop reasons, normalized observations, and bounded model/tool progression. |
| [Model Tool-Calling Boundary](model-tool-calling.md) | Provider-specific tool-call adaptation into the project-owned execution contract. |
| [Workspace And Isolation Boundary](workspace-and-isolation-boundary.md) | Current SSH/Kubernetes process-control boundary and explicit isolation non-goals. |
| [Clean-Slate Storage Baseline](clean-slate-storage.md) | Active SQLite schema family and the explicit retirement boundary for historical local data. |

## Related Authority

- [Lifecycle And State Ownership](../../../architecture/lifecycle-and-state.md)
  defines the normative relationship between A2A Task state, local process
  state, LangGraph continuation state, and effect state.
- [Backend API Boundary](../api-boundary.md) owns the endpoint catalog.
- [Runtime Roadmap](../../../dev/runtime/roadmap.md) owns current development
  priority and may cite these contracts as the implemented baseline.

If observed code behavior conflicts with one of these contracts, treat that as
a documentation or implementation defect. Resolve it explicitly; do not edit
an active roadmap to redefine settled ownership silently.

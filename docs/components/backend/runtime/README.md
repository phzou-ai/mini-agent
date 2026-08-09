# Backend Runtime Contracts

> Status: Stable
> Authority: Implemented backend runtime ownership and persistence contracts

This section owns settled contracts implemented by the Python backend. These
documents describe current behavior and invariants; they do not authorize new
work or maintain a second roadmap.

## Contracts

| Contract | Ownership boundary |
| --- | --- |
| [Durable Message Ingress](message-ingress.md) | Idempotent admission and durable outcome ownership for a top-level A2A `messageId`. |
| [Local Process Transition Governance](local-process-transitions.md) | Authoritative internal process transitions and atomic lifecycle events. |
| [Startup Reconciliation](startup-reconciliation.md) | Conservative recovery for durable ingress, queued execution, interrupted work, and uncertain effects. |
| [Task Failure Projection And Safe Retry](task-failure-retry.md) | Durable public failure facts, canonical browser reconciliation, and explicit retry lineage. |
| [Tool Invocation Ledger](tool-invocation-ledger.md) | Durable identity, approval binding, and replay safety for local non-read-only effects. |

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

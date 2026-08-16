# Platform Contract Refactoring

This domain owns the proposed normalization of cross-cutting contracts that
span the agent lifecycle, local execution scheduling, SQLite transactions,
event delivery, management reads, and the Web console.

It is separate from [Runtime Refinement](../runtime/README.md): the runtime
domain governs current single-host behavior, while this domain defines how the
same behavior can be reorganized without pre-designing distributed
infrastructure.

## Current Status

M0 baseline and contract freeze, M1 Task projection ordering, M2 lifecycle
command normalization, M3 transaction/post-commit ordering, M4 bounded local
execution, M5 durable event replay, and M6 bounded management reads plus focused
Web controllers are implemented and validated. M6 keeps one Task reducer as the
browser projection authority while moving Session reads and Task subscription
orchestration out of the page component. M7 remains unauthorized.

The authoritative proposal is the
[Single-Host Contract Refactor Plan](architecture-modernization-plan.md).

## Boundaries

This domain may define:

- lifecycle command and projection contracts;
- explicit SQLite transaction and post-commit contracts;
- bounded local execution scheduling and continuation contracts;
- event publication, replay, and subscription contracts;
- management read-model and frontend projection boundaries;
- constraints that preserve future infrastructure options without implementing
  them now.

This domain does not authorize:

- adding infrastructure because it may be useful later;
- replacing A2A as the public service boundary;
- making LangGraph own public Task lifecycle;
- introducing multiple lifecycle writers;
- preserving obsolete internal APIs as compatibility layers;
- a big-bang rewrite;
- vendor-shaped interfaces for hypothetical middleware;
- leases, heartbeats, distributed locks, or generic event buses without a
  measured requirement.

## Reading Path

1. [Current System Architecture](../../architecture/current-system.md)
2. [Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md)
3. [Runtime Roadmap](../runtime/roadmap.md)
4. [Single-Host Contract Refactor Plan](architecture-modernization-plan.md)
5. [M0 Contract Baseline](m0-contract-baseline.md)
6. [M1 Task Projection Handoff](m1-task-projection.md)
7. [M2 Lifecycle Command Surface Handoff](m2-lifecycle-command-surface.md)
8. [M3 Transaction And Post-Commit Boundary Handoff](m3-transaction-post-commit-boundary.md)
9. [M4 Bounded Local Execution Handoff](m4-bounded-local-execution.md)
10. [M5 Event Replay And Subscription Handoff](m5-event-replay-subscription.md)
11. [M6 Bounded Reads And Web Controllers Handoff](m6-bounded-reads-web-controllers.md)
12. [Runtime And Release](../../operations/runtime-and-release.md)

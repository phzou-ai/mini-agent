# M1 Task Projection Handoff

> Status: implementation complete and validated  
> Last reviewed: 2026-08-16

## Purpose

M1 removes browser arrival order as a source of Task lifecycle truth. It adds
one durable Task projection version and one frontend reconciliation path while
preserving the current SQLite, A2A, LangGraph, and single-host execution
boundaries.

This milestone does not introduce Event Sourcing, a distributed clock, a new
state store, or a second lifecycle owner.

## Durable Ordering Contract

`lifecycle_revision` and `event_id` have different meanings:

- `lifecycle_revision` versions the current public projection of one Task;
- `event_id` orders durable Task events for replay and reconnect.

New Tasks start at revision 1. A mutation increments the revision only when it
changes public Task state, including lifecycle/error fields, output Message
identity, or artifact state. Idempotent writes do not increment it. An event
inherits the Task revision current when the event is inserted; multiple
additive events may therefore share one revision.

SQLite schema migration 4 adds the revision columns to `main_agent_tasks` and
`main_agent_task_events`. This is a forward migration for the active clean
development database. No legacy read branch or second schema path was added.

## Projection Contract

The backend projects the revision through:

- A2A Task snapshot metadata as `lifecycleRevision`;
- A2A status and artifact event metadata as `lifecycleRevision`;
- first-party management Task and Task-event records as
  `lifecycle_revision`; and
- snapshots returned by cancellation, continuation, retry, hydration, and
  recovery paths.

`MainAgentStore` remains the durable mutation owner. A2A and management
adapters expose projections; they do not calculate or advance revisions.

## Web Ownership Contract

`web/lib/agent/task-projection-reducer.ts` owns all durable Task writes in the
browser. Its inputs include hydration, replay and live SSE, Task snapshots,
continuation responses, retry results, and stream reconciliation.

`mergeTaskProjection()` applies this ordering:

1. accept a higher revision even when its timestamp is older;
2. ignore a lower revision even when its timestamp is newer;
3. at an equal revision, retain current lifecycle-sensitive fields and merge
   only additive evidence;
4. use timestamp comparison only if either side has no usable revision.

Lifecycle-sensitive metadata includes local/A2A status, pending continuation,
output identity, and durable error fields. Equal-revision input cannot restore
an obsolete continuation or error. Session status is derived from the accepted
Task map rather than updated through an independent event-arrival path.

## Validation Evidence

Validated on 2026-08-16:

- `scripts/check_single_host_reliability.sh`: 215 backend contract tests and
  18 Playwright tests passed;
- `scripts/check_full_stack_regression.sh`: 487 Python tests, frontend
  typecheck, Next.js production build, and 18 Playwright tests passed; and
- both gates completed without a tracked-file change introduced by validation.

Focused coverage verifies schema migration, monotonic increment and no-op
behavior, event inheritance, A2A and management projection, revision-first
merge, duplicate events, stale events, and equal-revision additive evidence.

## Preserved Limits And Next Gate

- Context and Session records do not receive a Task revision.
- Task events remain replayed by `event_id`.
- Missing revision data uses a bounded timestamp fallback; it does not create a
  compatibility storage path.
- M1 does not extract lifecycle command handlers or change transaction
  ownership.
- At M1 closure, M2 remained unauthorized. It was later selected and completed
  while preserving this revision and reducer contract; see the
  [M2 Lifecycle Command Surface Handoff](m2-lifecycle-command-surface.md).

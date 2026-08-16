# M5 Event Replay And Subscription Handoff

> Status: implemented and validated
> Closed: 2026-08-16
> Authority: M5 implementation boundary and preserved limits

## Purpose

M5 makes the existing Task-event delivery contract explicit. Durable history
belongs to SQLite; process-local notification only reduces the time a
subscriber waits before reading that history. The implementation does not add
a broker, event bus, outbox, Redis dependency, or distributed subscription
claim.

The milestone closes two user-visible failure modes:

- losing a process-local wake-up must not lose a persisted Task event; and
- a persisted event that cannot be projected to A2A must fail explicitly
  instead of leaving the browser in an indefinite loading state.

## Ownership

| Boundary | Responsibility |
| --- | --- |
| Task event table | Durable append-only history and replay authority ordered by `event_id`. |
| `MainAgentStore` transaction | Insert Task events and register subscriber notification only as an after-commit callback. |
| `InProcessTaskEventNotifier` | Disposable process-local wake-up hint. It does not store events, cursors, subscribers, or delivery acknowledgements. |
| A2A projection adapter | Classify every durable event as a public A2A projection or an explicitly internal audit fact; reject every unknown or malformed record. |
| A2A SSE route | Replay from the caller's cursor, continue live delivery with the same cursor, and surface JSON-RPC errors as named SSE error events. |
| Web Task stream registry | Own at most one physical EventSource per Task and close it on replacement, terminal reconciliation, interruption, protocol failure, or console teardown. |
| Web Task reducer | Reconcile durable Task snapshots and events without treating transport state as lifecycle truth. |

## Commit, Wake, And Replay Flow

```text
lifecycle transaction
  -> append Task event row
  -> register after-commit notification
  -> commit
  -> notify process-local waiters

subscriber
  -> read rows where event_id > cursor
  -> if none, wait for a disposable notification or timeout
  -> re-read rows where event_id > cursor
  -> classify the complete durable batch
  -> emit lifecycle and artifact projections
  -> advance cursor only after every record is classified successfully
```

The durable read occurs both before and after waiting. A lost notification may
therefore add at most one bounded wait interval; it cannot lose history. A
duplicate or spurious wake-up causes another empty durable read and cannot
duplicate an event row.

Initial replay, live continuation, and reconnect all use the same `event_id`
cursor. The cursor is not a Task revision and is not stored in the notifier.

## Projection Classification And Failure Semantics

Durable events use an explicit classification rule:

1. lifecycle records with a local status produce an A2A status update;
2. artifact records with a durable artifact produce an A2A artifact update;
3. named control-plane audit facts such as `task_input_submitted`,
   `task_resumed`, `task_retry_requested`, and `task_retried` intentionally
   produce no public A2A envelope, but remain durable and advance the adapter's
   local replay cursor;
4. any unknown, malformed, or otherwise unclassified record raises
   `TaskEventProjectionError` with the durable Task and event identity;
5. the SSE route emits that failure as a named `error` event; and
6. the browser closes the subscription, reconciles the durable Task snapshot,
   and presents the protocol failure without inventing a Task transition.

The distinction is deliberate. Internal audit facts do not describe a new A2A
Task state, so projecting them as `working`, `submitted`, or another synthetic
status would corrupt lifecycle semantics. At the same time, the adapter may
not silently ignore an arbitrary statusless record: every non-public event
type must be named in the projection contract and covered by regression tests.
If classification fails, the cursor remains before the failing record so a
reconnect cannot skip it silently.

## Validation Evidence

- The post-closure continuation regression passed 151 focused
  `MainAgentCore`, store, and A2A adapter tests. It covers approval resume,
  general input continuation, explicit internal-audit cursor advancement, and
  rejection of an unknown statusless event.
- The complete backend suite passed 506 tests after the projection
  classification correction.
- Focused store and A2A replay/projection tests passed 82 tests.
- `scripts/check_single_host_reliability.sh` passed 228 backend tests and 19
  deterministic Playwright tests.
- `scripts/check_full_stack_regression.sh` passed 500 Python tests, frontend
  type checking, the Next.js production build, and the same 19 Playwright
  tests.
- Browser coverage includes explicit server projection failure, malformed SSE,
  reconnect and terminal reconciliation, one physical subscription per Task,
  and console teardown cleanup.
- Store coverage includes notification loss, duplicate wake-up tolerance,
  commit-before-notify ordering, and cursor replay.

The canonical current-checkout evidence is maintained in the
[Single-Host Reliability Matrix](../../operations/single-host-reliability-matrix.md).

## Preserved Limits

- Event delivery remains single-host and process-local.
- Notification loss is recovered by bounded durable re-read, not by a durable
  notification queue.
- There are no subscriber leases, acknowledgements, consumer groups, retention
  policy changes, Redis, broker, or generic event-bus abstraction.
- SQLite remains the lifecycle and replay store.
- Final-answer Task token streaming remains deferred; M5 streams durable
  lifecycle events and artifacts, not model tokens.

## Phase Gate

At M5 closure, M6 and later milestones were not yet authorized. The current
phase gate is maintained in the [Platform domain index](README.md). Any later
read-model or Web extraction must preserve one durable
`event_id` replay authority, explicit projection failures, and one browser
subscription owner per Task.

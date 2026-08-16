# M6 Bounded Reads And Web Controllers Handoff

> Status: implemented and validated
> Date: 2026-08-16
> Scope: first-party Context detail reads and browser Session/Task orchestration

## 1. Outcome

M6 closes two measured concentration risks without adding a generic query layer
or frontend state framework:

1. the four Context detail endpoints now apply bounded defaults; and
2. Session read orchestration and Task event-stream orchestration no longer live
   directly in `agent-console.tsx`.

The refactor does not change lifecycle authority. SQLite remains the durable
read source, `MainAgentCore` remains the lifecycle owner, and the Task
projection reducer remains the browser's only durable Task-state writer.

## 2. Bounded Read Contract

The following first-party endpoints default to the latest 200 records, accept
`limit` and `offset`, and cap `limit` at 500:

```text
GET /api/contexts/{context_id}/messages
GET /api/contexts/{context_id}/tasks
GET /api/contexts/{context_id}/route-decisions
GET /api/contexts/{context_id}/delegations
```

Storage queries select the requested window newest-first and reverse that
window before returning it. Consumers therefore receive chronological records
while the default request remains bounded to the most recent activity.

Direct-message failure projection is bounded to the Message IDs in the
selected Message page. It no longer scans every failed ingress in the Context
when only one page is being rendered.

Internal lifecycle operations that require complete state, such as safe
Context deletion, continue to use explicit unbounded store reads. M6 bounds
management projections; it does not silently truncate lifecycle decisions.

## 3. Web Ownership

`web/lib/agent/session-read-controller.ts` owns one Session read model:

- Messages;
- Tasks;
- route decisions; and
- delegations.

It requests the first bounded page for all four resources. The controller owns
network orchestration only; React components still own current Session
selection and presentation state.

`web/lib/agent/use-task-event-controller.ts` owns:

- one physical Task EventSource per `taskId`;
- replay hydration and terminal hydration suppression;
- Task snapshot reconciliation after ambiguous stream closure;
- protocol-error recovery; and
- projection of accepted A2A Task envelopes into events and reducer actions.

The controller does not directly merge durable Task objects. Snapshot, replay,
SSE, continuation, retry, and recovery inputs still enter
`taskProjectionReducer()`.

## 4. Deliberate Limits

- The Web console currently shows the latest 200 records for each Context
  detail resource. It does not yet expose "load older" controls.
- Offset pagination is sufficient for the current single-host management UI.
  Cursor pagination remains unjustified without measured concurrent-write or
  deep-history navigation problems.
- Session read and Task stream control are focused modules, not a generic data
  fetching framework.
- Visual components remain local to the Agent surface.

Add incremental history loading only after a retained Context exceeds the
window and a real workflow requires older records. At that point, older pages
must be prepended without changing current Session selection or Task reducer
ownership.

## 5. Validation

Validation completed on 2026-08-16:

- focused Task-stream recovery tests: 2 passed;
- complete runtime reliability Playwright file: 15 passed;
- `scripts/check_single_host_reliability.sh`: 229 backend tests and 20
  Playwright tests passed;
- `scripts/check_full_stack_regression.sh`: 502 Python tests, frontend
  typecheck, Next.js production build, and 20 Playwright tests passed; and
- `git diff --check` passed.

The browser regression verifies that selecting a Session issues exactly one
bounded request for each of the four Context detail resources and keeps the
selected transcript stable.

## 6. Phase Gate

M6 is complete. M7 cleanup is not automatically authorized by this handoff.
Start it only as a bounded cleanup pass over paths demonstrably replaced by
M1-M6; do not use it to introduce a new architecture or product capability.

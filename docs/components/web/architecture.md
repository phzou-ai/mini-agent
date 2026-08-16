# Web Architecture

## Purpose

The Agent Console provides a session transcript, task controls, route
diagnostics, and lifecycle inspection over backend-owned state.

```text
Browser
  -> Agent Console components
  -> frontend conversation and task projections
  -> Next.js BFF /api/bff/agent/*
  -> Vermay A2A /rpc or first-party /api/*
```

The transcript combines direct Messages and Task-backed answers into one
conversation. The Inspector separately presents A2A Task state, durable local
process state, LangGraph `thread_id`, route diagnostics, and event records.

## Rules

- Keep backend error fields `{ code, message, retryable }` intact.
- Deduplicate stream and refreshed records by durable identity.
- Merge every durable Task source through one revision-aware reducer. A cancel,
  approval-resume, ordinary-input, retry, hydration, or replay result captured
  before a newer SSE event must not move the visible Task or Session back to an
  older state.
- Give every physical Task EventSource one registry owner. Replacing a Task
  subscription closes its previous connection, and console teardown closes all
  registered Task streams.
- Reject malformed JSON-RPC envelopes and A2A results explicitly. A Task stream
  protocol error closes the subscription and reconciles the durable Task
  snapshot before the browser decides what to present.
- Treat a named server-side SSE `error` event as protocol data, not as a
  browser transport callback. Preserve its JSON-RPC error envelope, reconcile
  the durable Task, and close the registered subscription explicitly.
- Keep stream transport failures separate from Task lifecycle state. If the
  durable snapshot remains non-terminal after a protocol error, the transcript
  presents a client-only `stream_error`; it does not rewrite the A2A Task or
  local process status to `failed`.
- Treat A2A and management payloads as contracts, not component-local shapes.
- Keep network orchestration outside presentation components.
- Do not infer lifecycle truth from loading UI state alone.

## Task Projection Strategy

Task state has concurrent HTTP hydration, continuation, retry, replay, and live
SSE sources. An SSE event may reach the browser before an earlier HTTP promise
resolves. The console therefore routes all durable Task inputs through
`taskProjectionReducer()` and the revision-aware merge contract in
`web/lib/agent/task-presentation.ts`.

The rule is deliberately bounded:

1. accept a higher `lifecycle_revision` and reject a lower one;
2. at an equal revision, preserve lifecycle-sensitive fields while accepting
   additive evidence;
3. fall back to timestamp comparison only if revision data is unavailable;
4. derive Session lifecycle presentation from the accepted Task projection;
5. continue to reconcile durable Messages and events after the merge.

This prevents response-order races without making React state another
lifecycle authority. The backend remains the durable source of truth.

## Session And Task Transport Controllers

`session-read-controller.ts` owns the bounded four-resource read used to open a
Session: Messages, Tasks, route decisions, and delegations. It does not own
selection or presentation state.

`use-task-event-controller.ts` owns Task replay, one physical EventSource per
Task, terminal hydration suppression, and durable snapshot recovery. Every
accepted Task snapshot or event still enters `taskProjectionReducer()`; the
controller is transport orchestration, not a second Task store.

The current Session projection requests the latest 200 records from each
detail resource. Older-history UI remains deferred until a real retained
Context requires it.

# Web Modules

## Agent Surface

`web/app/(agent)/agent/` owns the Agent Console page. Its surface-local
components live in `web/app/(agent)/agent/_components/`:

- `agent-console.tsx`: page-level orchestration and state owner;
- `agent-sidebar.tsx`: contexts, session controls, and model presentation;
- `agent-transcript.tsx`: conversation activities and task controls;
- `agent-composer.tsx`: Auto, Message, and Task submission;
- `agent-card-panel.tsx`: main-agent and child-agent card presentation;
- `route-diagnostics-panel.tsx`: route-decision inspection.

## Shared Agent Client Layer

`web/lib/agent/` owns A2A and BFF contracts, streaming, errors, conversation
projection, and task presentation. UI components should consume these helpers
instead of reparsing protocol payloads independently.

`stream.ts` validates the shared JSON-RPC/A2A stream envelope and reports
malformed data as `invalid_a2a_stream`. The Agent Console then validates the
endpoint-specific projection: a syntactically valid Task stream result that
cannot become a durable-identity-backed local event is also a protocol error,
not an event to discard silently.

`task-event-stream-registry.ts` owns the browser's physical Task EventSource
connections. It does not interpret A2A events or Task state; it enforces one
replaceable connection per `taskId` and deterministic teardown.

`task-presentation.ts` owns revision extraction and the revision-first Task
merge policy. It uses timestamps only when revision data is missing.

`task-projection-reducer.ts` is the only browser writer for durable Task
projection sources, including hydration, SSE, replay, snapshots,
continuations, retry, and stream reconciliation. Components must not spread a
Task object captured before an async request back into state, because a newer
source may have arrived while that request was in flight.

`session-read-controller.ts` owns the bounded Session read model and coordinates
Messages, Tasks, route decisions, and delegations without owning React
selection state.

`use-task-event-controller.ts` owns Task EventSource lifecycle, replay
hydration, snapshot reconciliation, and protocol-failure recovery. It projects
accepted transport records into the shared reducer rather than merging Task
state itself.

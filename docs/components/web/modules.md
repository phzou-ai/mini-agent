# Web Modules

## Agent Surface

`web/app/(agent)/agent/` owns the Agent Console page and surface-local
components:

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


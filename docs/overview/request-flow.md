# Primary Request Flows

## Purpose

This document gives a compact entry to the three main-agent execution paths.
The detailed component graph is in
[current-system.md](../architecture/current-system.md).

## Admission Flow

```text
A2A Message
  -> persist the user Message and reserve durable ingress
  -> reuse an existing ingress outcome or route the new Message once
  -> local_message, local_task, or remote_agent
  -> persist the resulting Message, Task, delegation, event, or artifact
  -> project the result through A2A
```

## Direct Message

`local_message` calls the primary model and returns an A2A Message without
creating an A2A Task. Direct Message token deltas may stream over SSE.

## Local Task

`local_task` creates a durable A2A Task and local process record before handing
execution to LangGraph. `MainAgentCore` owns the public lifecycle; LangGraph
owns graph execution and checkpoint continuation addressed by `thread_id`.

## Child-agent Delegation

`remote_agent` selects a registered child A2A agent, persists delegation state,
and projects validated remote snapshots through the main-agent boundary.

## Related Documents

- [Lifecycle And State Ownership](../architecture/lifecycle-and-state.md)
- [Backend API Boundary](../components/backend/api-boundary.md)
- [Runtime Development](../dev/runtime/README.md)


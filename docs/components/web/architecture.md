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
- Treat A2A and management payloads as contracts, not component-local shapes.
- Keep network orchestration outside presentation components.
- Do not infer lifecycle truth from loading UI state alone.


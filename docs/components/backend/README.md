# Backend Component

The backend is the Python `vermay` package. It exposes A2A and first-party
management APIs, owns main-agent lifecycle state, executes local Tasks through
LangGraph, and persists single-host state in SQLite.

## Reading Order

1. [modules.md](modules.md)
2. [api-boundary.md](api-boundary.md)
3. [langgraph-interrupt-resume.md](langgraph-interrupt-resume.md)
4. [../../architecture/lifecycle-and-state.md](../../architecture/lifecycle-and-state.md)

Active runtime specifications and priorities are maintained under
[../../dev/runtime/](../../dev/runtime/README.md).

## Boundary And Authority

This directory describes the implemented backend surface. It does not own a
second lifecycle model or an independent roadmap. Public identity and state
ownership are normative in
[Lifecycle And State Ownership](../../architecture/lifecycle-and-state.md);
current runtime priority is normative in the
[Runtime Roadmap](../../dev/runtime/roadmap.md).

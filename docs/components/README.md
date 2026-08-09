# Components

This directory describes the concrete implementation units in the full-stack
repository.

## Components

1. [backend/README.md](backend/README.md) - Python package, FastAPI boundary,
   main-agent control plane, LangGraph runtime, tools, and persistence.
2. [web/README.md](web/README.md) - Next.js Agent Console, BFF, frontend
   contracts, transcript projection, and Inspector.

Cross-component request flow and state ownership remain architecture concerns:

- [Current System Architecture](../architecture/current-system.md)
- [Lifecycle And State Ownership](../architecture/lifecycle-and-state.md)

## Boundary And Authority

Component documentation owns concrete package, module, client, and UI
responsibilities. Cross-component request flow and lifecycle invariants belong
in Architecture. Current implementation priorities and incomplete work belong
under [../dev/](../dev/README.md).

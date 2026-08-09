# Web Component

The `web/` directory contains the first-party Next.js Agent Console. It is an
operational and inspection surface, not an owner of agent lifecycle state.

## Reading Order

1. [architecture.md](architecture.md)
2. [modules.md](modules.md)
3. [../../operations/regression-gate.md](../../operations/regression-gate.md)

## Boundary

The browser calls Next.js BFF route handlers. The BFF uses A2A for execution
and first-party management endpoints for read models and diagnostics. Frontend
state is a projection of backend records and must not invent a second Task or
process lifecycle.


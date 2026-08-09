# Operations

This directory owns supported runtime commands, deployment topology,
persistence and secret boundaries, and regression gates.

## Reading Order

1. [local-development.md](local-development.md)
2. [runtime-and-release.md](runtime-and-release.md)
3. [regression-gate.md](regression-gate.md)

Operational truth belongs here. Temporary rollout notes and implementation
plans belong under [../dev/](../dev/README.md).

## Boundary And Authority

These documents define supported local commands, topology, persistence and
secret handling, release assumptions, and regression gates. They do not own
runtime lifecycle design or active feature priority. When commands or release
boundaries change, update this directory and the applicable root README in the
same work item.

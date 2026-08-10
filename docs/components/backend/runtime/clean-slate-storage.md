# Clean-Slate Storage Baseline

**Status:** implemented, 2026-08-02.

## Decision

The retired `AgentService` / session runtime and its historical SQLite schema
are no longer product paths. This project intentionally does not provide a
reader, export utility, or in-place migration for those local records.

The active store starts from one clean baseline, identified by:

```text
store_metadata.schema_family = main_agent_clean_slate_v1
schema_migrations.version = 3
```

The clean baseline is migration `1`; current clean-slate stores also apply
migration `2`, which adds the Tool Invocation Ledger, and migration `3`, which
persists Task failure retryability and enforces one idempotent direct retry per
retry lineage. Together they create the current shared metadata tables and the
complete `MainAgentCore` record set: Contexts, Messages, route decisions, local
Tasks, events, artifacts, registered agents, delegations, pending
continuations, message ingress records, queued execution commands, and durable
non-read-only tool invocations.

The migration framework remains only for future forward changes to this
baseline. It does not contain retired schema steps or data backfills.

## Existing Local Databases

When `AgentStore` opens a database without the clean-slate family marker, it
treats that database as retired local state and clears it before creating the
active baseline. A database marked with an unknown schema family fails loudly
rather than being silently overwritten.

This is an intentional development-stage cut. It means old sessions, task
events, artifacts, message ingress records, and local metadata are discarded.
They cannot be resumed or inspected through the active product.

## Checkpoint Boundary

`data/agent.sqlite` and `data/checkpoints/langgraph.sqlite` are one logical
runtime boundary. After this cut, remove both databases together before
starting the new runtime. `traces/langgraph_checkpoints.sqlite`, when present,
must also be discarded because it can contain checkpoints for the retired
task identities.

New Contexts and Tasks begin with freshly allocated A2A `contextId` / `taskId`
values and new LangGraph `runtimeThreadId` values. No supported continuation
crosses the clean-slate boundary.

## Verification

- A fresh store creates only the active baseline tables and records schema
  version `3`.
- A historical versioned or unversioned session database is discarded and
  recreated as the active baseline.
- An unknown schema family raises an explicit error.

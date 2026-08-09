# Maintenance Development

This directory contains active or completed cleanup and code-organization work.
It is not a second runtime roadmap.

## Status

The documented maintenance pass is complete. There is no active cleanup
milestone. Use these documents as decision evidence and start another pass only
when a concrete trigger in the organization review is present.

## Documents

1. [cleanup-plan.md](cleanup-plan.md) - scoped cleanup and refactor execution
   record, guardrails, and regression evidence.
2. [code-organization-review.md](code-organization-review.md) - module
   ownership assessment and triggers for future refactoring.

Stable package responsibilities belong in
[../../components/backend/modules.md](../../components/backend/modules.md).

## Next Trigger

The next maintenance pass requires an observed ownership conflict, duplicated
state transition, obscured pure mapping/presentation boundary, or a testability
problem described in
[code-organization-review.md](code-organization-review.md#next-refactor-trigger).
File size or age alone is not sufficient.

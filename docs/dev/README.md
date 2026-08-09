# Active Development

This directory is the physical home for active plans, implementation
specifications, deferred work, maintenance passes, and dated review evidence.

## Development Domains

1. [runtime/README.md](runtime/README.md) - runtime roadmap, focused contracts,
   reliability work, and staged evolution criteria.
2. [maintenance/README.md](maintenance/README.md) - completed code-organization
   and cleanup evidence plus explicit triggers for another maintenance pass.

## Current Authority

The Runtime roadmap owns the current engineering priority. Maintenance has no
active refactor milestone; its completed records are evidence and guardrails,
not authorization for further cleanup.

The current bounded work item is S2, the release baseline refresh. Its scope,
acceptance criteria, and latest durable handoff are maintained in the
[Runtime roadmap](runtime/roadmap.md#active-work-item-s2-release-baseline-refresh).

## Rules

1. Keep the current status and next priority accurate.
2. Use one authoritative roadmap per domain.
3. Treat dated reviews as evidence, not current priority.
4. Promote settled conclusions to stable reference after verification.
5. Do not add deeper folder levels until the volume requires them.

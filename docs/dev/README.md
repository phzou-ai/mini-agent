# Active Development

This directory is the development control layer for active plans, explicitly
deferred work, acceptance criteria, and durable handoff state. A roadmap
authorizes implementation; the presence of a deferred or conditional design
in this directory does not.

## Development Domains

1. [runtime/README.md](runtime/README.md) - runtime roadmap, reliability work,
   and staged evolution criteria.

`runtime/` is a development domain, not a mirror of one Python module. Add a
new sibling domain only when a separate concern has its own active status and
multiple related documents. Keep a small task in its owning roadmap instead of
creating a directory for it.

## Current Authority

The Runtime roadmap owns the current engineering priority. Completed runtime
and maintenance records live under [Historical Evidence](../history/README.md)
and do not authorize more implementation by themselves.

No runtime expansion work item is active. The closed S3 protocol-surface
baseline, acceptance evidence, and latest durable handoff are maintained in
the [Runtime roadmap](runtime/roadmap.md#closed-work-item-s3-protocol-surface-governance).

## Rules

1. Keep the current status and next priority accurate.
2. Use one authoritative roadmap per domain.
3. Move dated reviews and completed plans to `docs/history/*` when they remain
   useful as evidence.
4. Promote settled conclusions to stable reference after verification.
5. Do not add deeper folder levels until the volume requires them.

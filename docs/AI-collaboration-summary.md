# Vermay AI Collaboration Summary

This is the day-to-day execution checklist for contributors and AI coding
tools. Start with this page, then follow the task-oriented reading paths in
[Documentation](README.md). The
[AI Collaboration Guide](AI-collaboration-guide.md) owns the complete rules
and rationale.

## Before Work

1. Read [Documentation](README.md).
2. Follow the narrowest task-oriented reading path.
3. Inspect relevant code and deterministic tests before trusting prose claims.
4. Inspect the current working tree and preserve unrelated changes.
5. State the task boundary and explicit non-goals.

## Authority

1. Code and deterministic tests describe observed implementation.
2. Stable architecture defines intended ownership and invariants.
3. Active plans define authorized work and acceptance criteria.
4. Focused specifications define one work stream.
5. Reviews and historical plans provide evidence, not current priority.
6. Chat history is temporary context only.

When sources conflict, identify and resolve the conflict explicitly. Do not
silently select the source that best matches the intended change.

## During Work

- Keep stable reference separate from changing implementation plans.
- Preserve unrelated changes and sensitive information.
- Do not invent architecture, status, commands, or validation results.
- Update the canonical owner of a fact instead of copying it into several
  documents.

## Before Completion

1. Run validation proportional to the change.
2. Inspect the final diff for unrelated edits and generated-file pollution.
3. Update confirmed stable behavior only when it has settled.
4. Update the active plan or handoff with validation, remaining work, blockers,
   and an evidence-backed next task or an explicit no-active-task state.
5. Report live Git state in the final response. Do not persist branch position,
   staged files, or "not yet committed" text as durable project truth.

## Full Reference

Read the [AI Collaboration Guide](AI-collaboration-guide.md) when organizing
documentation, resolving authority conflicts, coordinating parallel work,
retiring plans, or changing these conventions.

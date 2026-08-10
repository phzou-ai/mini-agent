# Durable Context Input Cut

**Status:** implemented for the current character-bounded M6 scope on
2026-08-02.

## Purpose

A local Task must execute against the conversation state that existed when its
input Message was accepted. Queue delay, concurrent top-level Messages, and a
future restart must not cause later Messages to appear in that Task's initial
prompt.

This is intentionally an input *cut*, not a copied prompt snapshot. Stored
Messages are immutable records, so a durable sequence boundary is enough to
rebuild the same bounded history without duplicating the transcript. The
history and external-context policies below are character-bounded; they are not
an estimate of model tokens or a guarantee that a future runtime configuration
will render an identical complete prompt.

## Durable Data

The active clean-slate SQLite baseline includes three fields:

| Record | Field | Meaning |
| --- | --- | --- |
| `contexts` | `next_message_sequence` | The next positive Context-local sequence allocated to a Message. |
| `messages` | `context_sequence` | Immutable causal order of one Message within its Context. |
| `main_agent_tasks` | `input_context_sequence` | The sequence boundary captured from the Task's `input_message_id`. |

`(context_id, context_sequence)` is unique. New writes allocate a sequence and
persist the Message in one SQLite transaction. Historical records are not
backfilled across the clean-slate boundary.

## Initial Input Policy

Every initial path resolves Messages only through its accepted input boundary.
The newest applicable preceding Messages are retained first; the current input
Message is always retained verbatim. Character caps apply only to preceding
history, so accepting a request never silently changes the request text.

| Path | Maximum Messages | Historical Characters | Per Historical Message |
| --- | ---: | ---: | ---: |
| Router classification | 8 | 6,000 | 1,500 |
| Direct local Message | 12 | 14,000 | 4,000 |
| Initial local Task | 16 | 18,000 | 5,000 |

If a historical message exceeds its applicable cap, the retained text ends
with `[Earlier content truncated for context.]`. The persisted Message itself
is never modified.

For a local Task:

1. `MainAgentCore` persists the input Message.
2. `MainAgentStore.create_task()` copies that Message's sequence into
   `input_context_sequence`.
3. The queued worker receives only `taskId`.
4. Before its first LangGraph run, the worker loads the route-specific bounded
   history from the Task Context with
   `context_sequence <= input_context_sequence`.

The worker therefore has no in-memory history list whose loss or mutation can
change its initial prompt. A later independent Message remains visible in the
Context transcript but is outside the Task's initial input cut.

```text
Context sequence:  1 -------- 2 -------- 3
Messages:       history    task input   later input
                              |
Task input cut:                +---- input_context_sequence = 2

Task initial prompt: [1, 2]
```

## Continuations

The cut governs only a Task's first execution slice. A blocked Task continues
through its durable `taskId`, pending-continuation record, and LangGraph
`runtimeThreadId`; it does not rebuild its original prompt from the latest
Context history. A continuation input is its own process-control operation,
not a later top-level Message accidentally added to the original input.

## Dynamic Runtime Context

The local LangGraph runtime has a separate policy for dynamic system-context
material. `RuntimeContextProvider` injects material in this order:

1. selected MCP prompts;
2. retrieved authored skills;
3. explicit memory;
4. selected MCP resources.

Each section is capped at 5,000 characters and all injected sections together
at 16,000 characters. A truncated section ends with
`[Context section truncated.]`. This budget covers only injected external
context, not the baseline system prompt, the persisted conversation history,
tool-call messages, or a final rendered model request.

The direct-message responder adds the shared baseline system prompt and then
preserves stored roles when converting Context Messages to LangChain messages.
The local Task runtime receives role-preserving history in the same way. The
router uses its own classifier prompt and the smaller route-specific history
slice above.

## Scope And Deferred Work

This increment establishes causal ordering, durable initial input assembly,
and bounded current-context construction. Startup recovery reuses the durable
initial input cut for unclaimed initial worker commands. It deliberately does
not yet add:

- model-token-aware or summarized history;
- a global cap for tool outputs, child-agent traces, or all rendered prompt
  material;
- durable snapshots of the rendered prompt, selected model profile, tool
  catalog, or dynamic retrieval results;
- serialization of an entire Context's route-selection work;

Those concerns remain separate refinements. Local-process transition governance,
startup reconciliation, direct-message failure presentation and ingress
recovery, executor ownership, and remote-proxy synchronization are complete.
Current phase gates and priorities are maintained in
[Runtime Roadmap](../../../dev/runtime/roadmap.md#current-phase-gate).

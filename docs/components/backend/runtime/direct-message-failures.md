# Direct-Message Failure Presentation

## Status

**Implemented, updated 2026-08-03.**

This contract closes the presentation gap for a failed top-level direct
Message. It does not change A2A success or error semantics, create a Task, or
turn an execution failure into an agent answer.

## Problem

`main_agent_message_ingress` already persists the public failure for a direct
Message:

```text
messageId
  -> state: failed
  -> code
  -> public message
  -> retryable
```

The A2A caller receives that failure and a duplicate `messageId` replays it
without rerouting. Before this work, the Context Messages read model omitted
the failure. After a page reload, the user could therefore see an unanswered
user Message with no indication that its direct model invocation had failed.

## Chosen Model

The existing ingress row remains the only durable failure record. This work
does **not** add a second error table and does **not** append a synthetic
`agent` Message to the `messages` table.

The first-party management read model exposes an optional failure attached to
the input user Message:

```json
{
  "message_id": "msg-user-1",
  "role": "user",
  "parts": [{"kind": "text", "text": "..."}],
  "failure": {
    "code": "model_error",
    "message": "The model request could not be completed.",
    "retryable": true
  }
}
```

The field is present only when the matching top-level ingress is terminally
`failed`. It is not a Message part, model context, Task artifact, or A2A
success payload.

The Web UI renders that failure immediately after the associated user Message
as a dedicated failure activity. It is visually distinct from an assistant
answer and uses the same structured `{ code, message, retryable }` contract as
the API error layer.

## Read and Recovery Behavior

The Context Messages read endpoint enriches stored Messages with the matching
failed-ingress projection. It remains an additive management/read-model
change; the persisted `messages` table keeps only actual user, agent, and
system Messages.

The UI may also read one ingress record by `messageId` after a stream error.
That allows a newly created Context to be promoted from its local draft state
to the persisted server Context even when no successful stream result carried
the Context identity.

A JSON-RPC error delivered over the direct-message SSE stream is terminal for
that submission. The browser aborts its local stream reader immediately after
handling the error so the sender's cleanup releases the composer, even when an
intermediate proxy leaves the HTTP response open. This closes the failed
attempt; it does not retry it automatically.

This lookup returns only the delivery state, Context identity, and public
failure contract needed by the UI. It must not expose internal exceptions,
provider credentials, prompt state, or route/execution details.

## User-Initiated Retry

When a direct Message failure is marked `retryable`, the Web UI exposes an
explicit **Retry** action on that failure activity. The action is intentionally
a new top-level Message submission, not a replay of the failed delivery:

- it creates a fresh `messageId` and keeps the original failed ingress row;
- it sends the original text to the same Context;
- it preserves the original execution mode and any explicit remote-agent route
  and target; and
- it leaves an unsent composer draft untouched.

The original `messageId` remains idempotent: sending it again still replays its
stored failure and must not route, invoke a model, or execute tools again. This
distinction keeps a user-initiated retry auditable as a new request while
preserving the existing ingress contract.

Retry is opt-in and only applies to failed direct Messages. It does not retry
Tasks, tool calls, write operations, or an `in_progress` ingress automatically.

## Separation From Task Failure

| Concern | Direct Message | Local Task |
| --- | --- | --- |
| Durable owner | `main_agent_message_ingress` | `main_agent_tasks` |
| Failure form | ingress `failed` plus public error contract | process `failed` plus lifecycle events and task error fields |
| UI representation | failure activity attached to the input Message | dedicated Task failure activity when no final answer exists, plus task status and events |
| Duplicate input | replay the same persisted error; never reroute | Task lifecycle and continuation rules apply |

An error must not create a fake Task solely to make it visible in the UI.
Conversely, a failed Task must not be represented as a direct-message ingress
failure.

## Acceptance Criteria

- A failed direct Message appears after its user Message when a Context is
  reloaded.
- The rendered item is explicitly a failure, never a successful assistant
  response.
- A retryable flag is preserved end to end.
- A retryable direct Message offers an explicit Retry action that creates a
  fresh `messageId` in the same Context while retaining the original failure.
- Repeating the same `messageId` returns the stored error and creates neither a
  second failure activity nor another route/model/tool invocation.
- The direct error stream path can recover the persisted Context identity for a
  newly created session.
- A terminal direct-message SSE error releases the composer instead of leaving
  the Retry action disabled behind an open stream.
- A failed Task without a final answer renders a visible failure activity from
  its safe task error projection; it must never remain a typing indicator.

## Non-Goals

- Automatic retry of a failed direct Message.
- Automatic replay of an abandoned `in_progress` direct-message invocation.
- Final-answer token streaming for Tasks.
- A new event stream or scheduler for direct messages.

An abandoned stream is now recorded as retryable `message_stream_aborted`, and
startup converts a residual prior-process ingress to retryable
`message_ingress_stale`. Both remain non-replayable under their original
`messageId`; they are failure handling, not execution recovery.

## Verification

- `GET /api/contexts/{contextId}/messages` projects a terminal failed ingress
  as the optional `failure` field on its input user Message.
- `GET /api/message-ingress/{messageId}` returns only the Context identity,
  delivery state, and public failure contract needed to recover a newly created
  UI session after an SSE error.
- The Web UI renders the projection as a distinct failure activity rather than
  an assistant Message or synthetic Task.
- Frontend regression coverage verifies that Retry uses a new `messageId`,
  preserves the Context and execution mode, and does not overwrite an unsent
  composer draft.
- Focused backend coverage verifies the read projection and ingress lookup.
- Frontend regression coverage verifies the public SSE error presentation and
  excludes provider diagnostics.
- Failed Task snapshots and terminal `status-update` events project only safe
  `localErrorCode` and `localErrorMessage` metadata. They do not expose raw
  provider or runtime exception details.

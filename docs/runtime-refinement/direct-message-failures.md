# Direct-Message Failure Presentation

## Status

**Implemented, 2026-08-02.**

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

This lookup returns only the delivery state, Context identity, and public
failure contract needed by the UI. It must not expose internal exceptions,
provider credentials, prompt state, or route/execution details.

## Separation From Task Failure

| Concern | Direct Message | Local Task |
| --- | --- | --- |
| Durable owner | `main_agent_message_ingress` | `main_agent_tasks` |
| Failure form | ingress `failed` plus public error contract | process `failed` plus lifecycle events and task error fields |
| UI representation | failure activity attached to the input Message | Task status, events, and task failure information |
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
- Repeating the same `messageId` returns the stored error and creates neither a
  second failure activity nor another route/model/tool invocation.
- The direct error stream path can recover the persisted Context identity for a
  newly created session.
- Task failure rendering and task lifecycle contracts remain unchanged.

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
- Focused backend coverage verifies the read projection and ingress lookup.
- Frontend regression coverage verifies the public SSE error presentation and
  excludes provider diagnostics.
- Full repository regression after this increment: `492 passed`; focused
  Playwright migration regression: `2 passed`.

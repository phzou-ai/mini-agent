# Full-Stack Regression Baseline

The repository owns both the Vermay Agent backend and the Agent Console frontend. The default regression gate verifies both sides without requiring a live model or MCP server.

## Default Gate

Run from the repository root:

```bash
scripts/check_full_stack_regression.sh
```

The gate runs:

1. the complete backend unit and integration suite;
2. frontend TypeScript validation;
3. the frontend production build; and
4. a deterministic Playwright regression for the migrated Agent Console.

The Playwright regression mocks the browser-facing BFF responses. Backend A2A, persistence, routing, task lifecycle, and error projection remain covered by Python integration tests. This split keeps the default gate independent of Ollama, external model providers, and MCP availability.

The gate uses dedicated Next output directories for its production build and
deterministic Playwright server. It therefore does not reuse or overwrite the
normal `web/.next` directory used by a local `pnpm dev` session. This allows the
regression command to run without stopping the developer's frontend server.

## Focused Single-Host Reliability Gate

Run the focused reliability matrix when changing ingress, A2A streaming,
continuation, cancellation, restart reconciliation, execution limits, or
browser recovery behavior:

```bash
scripts/check_single_host_reliability.sh
```

It runs the relevant deterministic Python contracts plus the migrated browser
regression and runtime-reliability Playwright specs. It is intentionally
independent of live model, MCP, SSH, and Kubernetes dependencies. The covered
scenarios and acceptance rules are documented in
[runtime-refinement/single-host-reliability-matrix.md](runtime-refinement/single-host-reliability-matrix.md).

Set `RUN_LIVE_E2E=1` to append the existing live Playwright suite:

```bash
RUN_LIVE_E2E=1 scripts/check_full_stack_regression.sh
```

The live suite requires configured model and MCP dependencies.

## Public Error Contract

Browser-facing BFF errors use one shape:

```json
{
  "code": "model_error",
  "message": "Model request failed.",
  "retryable": true
}
```

A2A JSON-RPC errors retain the standard JSON-RPC envelope. The same local fields are projected through `error.data`:

```json
{
  "jsonrpc": "2.0",
  "id": "request-1",
  "error": {
    "code": -32000,
    "message": "Model request failed.",
    "data": {
      "localCode": "model_error",
      "retryable": true
    }
  }
}
```

SSE error events carry that JSON-RPC error envelope unchanged. The frontend normalizes HTTP, JSON-RPC, and SSE errors through the same contract helper.

### Field semantics

- `code` is the stable, machine-readable error identifier. Frontend behavior must use this field instead of matching user-facing message text.
- `message` is the safe, user-facing description. It must not expose provider URLs, credentials, raw response bodies, or connection exception details.
- `retryable` is backend guidance that the same request has a reasonable chance of succeeding later without changing its input. It is not an instruction to retry automatically.

Typical retryable failures include temporary model or MCP unavailability, network timeouts, rate limits, and upstream `502`, `503`, or `504` responses. Invalid input, missing configuration, unsupported JSON-RPC methods, authorization failures, unknown task IDs, and business-rule violations are not retryable.

Model failures have two deliberately separate codes:

| Code | Meaning | Retry guidance |
| --- | --- | --- |
| `model_error` | The model provider or transport could not complete the request, such as a timeout, connection error, rate limit, or upstream `5xx`. | May be retryable when the provider classifies it as transient. |
| `model_protocol_error` | The provider responded, but the response did not satisfy the Task action contract. | Not retryable automatically. The runtime safely accepts a plain final answer only after a real tool observation; it never infers or executes a tool from prose. |

This distinction prevents a Task action-format problem from being presented as
an unavailable model. Internal parser diagnostics and raw provider bodies stay
out of public errors, persisted task metadata, and the browser.

The frontend represents normalized failures as `RequestError`, carrying the same `code` and `retryable` values alongside `message`. This lets the UI offer deterministic recovery actions without parsing or translating error text. For example, it may show a retry action for a temporary provider failure or a settings action for a configuration error.

For a retryable failed direct Message, the frontend exposes a user-triggered
Retry action. It submits the original input with a new `messageId` in the same
Context; it never reuses the failed ingress ID. The frontend must not
automatically retry tasks, write operations, or tool calls because they may be
non-idempotent or may already have produced side effects.

Provider URLs, credentials, raw response bodies, and connection exception strings are not public error messages. Backend logs retain the internal exception for local diagnosis. Failed task records and transcript messages store or display the public message.

For a failed local Task, the A2A Task snapshot and its terminal
`status-update` event add the same safe information through extension metadata:

```json
{
  "localErrorCode": "model_error",
  "localErrorMessage": "Model request failed."
}
```

The browser uses this projection to replace a pending task answer with a
visible Task failure activity. It does not turn the error into a successful
assistant Message or expose raw provider diagnostics.

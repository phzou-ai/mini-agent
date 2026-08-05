# Model Tool-Calling Boundary

**Status:** implemented for the current Ollama primary model, 2026-08-04.

## Purpose

The runtime owns the agent loop, permission checks, tool execution, and A2A
Task lifecycle. A model provider only decides whether to return a final answer
or structured tool calls. This document defines the narrow adapter boundary
between those responsibilities.

Provider-specific request and response formats are converted into the
project-owned values below before LangGraph sees them:

```text
provider response
  -> ModelResponse(content, tool_calls: ToolCall[])
  -> LangChain AIMessage.tool_calls
  -> LangGraph permission / ToolNode path
```

`ToolCall` is a description only. It does not execute a capability. Existing
permission, approval, Tool Invocation Ledger, cancellation, and LangGraph
checkpoint behavior remain downstream owners of execution.

## Configured Strategies

`options.tool_calling` is a model-level request/response strategy. It is not a
runtime mode and does not change routing, A2A state, or approval policy.

| Provider | Supported values | Default | Current use |
| --- | --- | --- | --- |
| `ollama` | `native`, `prompt_json`, `none` | `prompt_json` when no setting is supplied | `local_ollama` explicitly uses `native`. |
| `openai_compatible` | `native`, `none` | `native` | Standard Chat Completions function calling. |
| Router classifier | Not applicable | N/A | It receives no tools and returns its separate routing JSON decision. |

### `native`

When an invocation exposes tools, the adapter sends the provider's standard
function schema and accepts only the provider's structured tool-call field.
For Ollama, this is `tools` plus `message.tool_calls`; after a tool result, the
next request retains the assistant's function call and a `role: tool` message
with `tool_name`.

When no tools are exposed, `native` sends a normal plain-text chat request.
This is the direct-message behavior for the active `local_ollama` model; it
does not require an artificial JSON action wrapper.

### `prompt_json`

This is an explicit Ollama compatibility mode for endpoints that cannot return
native tool calls. The adapter sends the legacy project JSON-action prompt and
requires either a `final` or `tool_call` action. It is not an automatic
fallback from `native`.

### `none`

The adapter does not send tool definitions or parse text as tool actions. A
provider response that still carries non-empty native tool calls fails closed
with `model_protocol_error`; it cannot enter ToolNode.

## Failure And Safety Rules

- The adapter never retries a request with another tool-calling strategy.
  Switching modes is a deliberate model configuration change.
- Malformed native calls, including non-object arguments, produce a typed
  `model_protocol_error` before any tool execution.
- In `native` and `none` modes, text that merely says "calling tool ..." is a
  final answer, not an executable call.
- In explicit `prompt_json` mode, malformed action output is a typed protocol
  failure. The legacy compatibility parser is isolated to that mode.
- Provider adapters never authorize a call. The normal permission gate still
  decides whether a structured `ToolCall` may execute or must pause for
  approval.

## Current Migration Boundary

`config/models.json` sets the primary `local_ollama` model to:

```json
{
  "provider": "ollama",
  "options": {
    "tool_calling": "native"
  }
}
```

This removes the current Task path's dependence on a model following a custom
JSON action instruction. It does not add task final-answer streaming, a second
tool runner, or any new lifecycle state.

An endpoint that needs a nonstandard protocol should receive a dedicated,
explicit provider adapter after its request and response shapes are verified.
Do not silently reuse `prompt_json` for a provider that claims OpenAI-native
function calling.

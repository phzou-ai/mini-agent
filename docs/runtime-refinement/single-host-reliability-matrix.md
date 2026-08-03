# Single-Host Reliability Matrix

**Status:** implemented for the current deterministic regression baseline,
2026-08-02.

## Purpose

This is the active P0 verification boundary for the current single-host
runtime. It does not introduce a scheduler, a second lifecycle owner, or a
test-only product execution path. It makes the existing guarantees observable
across the durable core, A2A JSON-RPC/SSE projection, and browser console.

Run the focused matrix from the repository root:

```bash
scripts/check_single_host_reliability.sh
```

The command uses deterministic fake responders, runners, and browser BFF
fixtures in tests only. It never requires a live Ollama provider, SSH host, or
MCP server. The broader repository gate remains:

```bash
scripts/check_full_stack_regression.sh
```

## Coverage Matrix

| Scenario | Required outcome | Primary deterministic evidence |
| --- | --- | --- |
| Duplicate top-level delivery | The same `messageId` does not route, create another Task, invoke a model, or execute a tool twice. | `tests/test_main_agent_core.py` ingress replay coverage. |
| Direct-message provider failure | The durable ingress is failed once and the browser receives only `{ code, message, retryable }`, never provider diagnostics. | `tests/test_main_agent_core.py`, `web/tests/e2e/migration-regression.spec.ts`. |
| Local Task acceptance and completion | A Task acceptance, lifecycle events, final artifact, and completed A2A Task projection agree. | `tests/test_api_a2a_adapter.py`, `tests/test_main_agent_core.py`. |
| Late stream failure after durable success | A transport error after a terminal Task event cannot overwrite the durable completed result or render a false failure card. | `tests/test_api_a2a_adapter.py`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Approval and ordinary input | `ResumeTask` can consume only an approval continuation; `SendMessage` with `taskId` can consume only ordinary task input, without rerouting as a new top-level request. | `tests/test_main_agent_core.py`, `tests/test_api_a2a_adapter.py`. |
| Cancellation at a safe boundary | The process records `cancel_requested` while active work is pending, then reaches `canceled` without accepting later model-driven tool work. | `tests/test_main_agent_core.py`, `tests/test_langgraph_runtime.py`, `tests/test_execution_context.py`. |
| Slow or failing model call | Provider timeout and an optional Task elapsed-time budget become structured, inspectable failures rather than indefinite work. | `tests/test_langgraph_runtime.py`, `tests/test_openai_compatible_client.py`. |
| Restart recovery | Unclaimed queued work is resubmitted once; claimed work becomes an explicit retryable failure; approval/input continuations remain resumable. | `tests/test_main_agent_core.py`. |
| Non-read-only tool boundary | Invocation identity, approval binding, evidence, cancellation, and uncertain side effects remain durable. | `tests/test_tool_invocation_ledger.py`. |

## Acceptance Rules

The matrix is green only when all of the following remain true:

1. A durable terminal Task is never presented as a failed direct Message.
2. A JSON-RPC/SSE terminal Task result is not followed by a second public
   error event for the same completed execution.
3. The browser can reload durable Context, Message, and Task records when a
   stream ends ambiguously after Task acceptance.
4. A blocked Task resumes only through its matching continuation interface.
5. Cancellation and restart leave no silently stuck local `queued`, `running`,
   or `cancel_requested` process.
6. Public errors remain safe to display and preserve their stable `code` and
   `retryable` fields.

## Deliberate Limits

This is a deterministic contract matrix, not a claim that every external
dependency has been exercised. Real Ollama, MCP, SSH/Kubernetes, and child
agent workflows remain optional live checks because they depend on operator
configuration. They must not become required inputs to the default regression
gate.

The matrix also does not change the current cancellation guarantee: it is
cooperative at model and tool safe boundaries, rather than a global force-kill
mechanism for arbitrary Python code.

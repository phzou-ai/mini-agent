# Single-Host Reliability Matrix

**Status:** deterministic and real configured-model single-host baselines
validated on 2026-08-16.

## Purpose

This is the deterministic verification contract for the current single-host
runtime. It does not introduce a scheduler, a second lifecycle owner, or a
test-only product execution path. It makes the existing guarantees observable
across the durable core, A2A JSON-RPC/SSE projection, and browser console. The
[Runtime Roadmap](../dev/runtime/roadmap.md) decides when this matrix must be
refreshed as an active release gate.

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

## Latest Validation Evidence

The current checkout was validated in three layers:

1. On 2026-08-16 the focused backend reliability suite passed all 229 tests and
   all 20 Playwright tests. The full-stack gate passed all 502 Python tests,
   frontend type checking, the Next.js production build, and the same 20
   Playwright tests. The browser coverage includes revision-first Task
   projection, continuation freshness, malformed Message SSE, unprojectable
   Task SSE, Task subscription ownership, and approval continuation without a
   page refresh, the M2 typed command surface, the M3 transaction/post-commit
   boundary, and M4's versioned immutable local execution commands plus the
   single Core-owned claim/outcome path. M5 adds durable replay under lost or
   duplicate notification, explicit persisted-event projection failures, and
   browser cleanup after a server-side stream error. M6 adds bounded Context
   detail reads, stable Session selection, and focused Session/Task transport
   controllers without adding another Task-state writer.
2. On 2026-08-16 an isolated API server was started with the current configured
   model, a temporary lifecycle database, and a temporary LangGraph checkpoint
   database. `scripts/a2a_dev_smoke.sh` completed successfully through A2A
   Message and Task requests, Task completion, `message/stream`,
   `tasks/resubscribe`, the late-cancel boundary, and removed-endpoint checks.
   The smoke prompt was intentionally non-destructive.
3. Approval and ordinary-input continuation remain covered by the deterministic
   lifecycle tests. They were not triggered against a live Kubernetes or SSH
   target in this validation, because doing so would require a real external
   side effect target and operator-approved credentials.

This evidence establishes the current single-host baseline. It does not claim
that every configured MCP server, SSH target, Kubernetes operation, or child
agent has been exercised live.

An opt-in read-only live Kubernetes gate is available through
`RUN_LIVE_K8S=1 scripts/check_live_kubernetes_workflow.sh`. Its presence does
not constitute validation evidence by itself. Record a dated result here only
after it has run against an explicitly configured target; keep the default
deterministic gate independent of that target.

## Coverage Matrix

| Scenario | Required outcome | Primary deterministic evidence |
| --- | --- | --- |
| Duplicate top-level delivery | The same `messageId` does not route, create another Task, invoke a model, or execute a tool twice. | `tests/test_main_agent_core.py` ingress replay coverage. |
| Direct-message provider failure | The durable ingress is failed once and the browser receives only `{ code, message, retryable }`, never provider diagnostics. | `tests/test_main_agent_core.py`, `web/tests/e2e/frontend-regression.spec.ts`. |
| Local Task acceptance and completion | A Task acceptance, lifecycle events, final artifact, and completed A2A Task projection agree. | `tests/test_api_a2a_adapter.py`, `tests/test_main_agent_core.py`. |
| Transaction and post-commit ordering | A rolled-back lifecycle workflow cannot wake a worker or subscriber; local execution, continuation, cancellation signaling, and remote delivery observe committed durable state. | `tests/test_lifecycle_transactions.py`, `tests/test_main_agent_core.py`, `tests/test_main_agent_store.py`, `tests/test_storage.py`. |
| Bounded local execution path | Initial execution, approval continuation, and ordinary-input continuation persist a supported versioned immutable command before wake-up; the process adapter has no lifecycle-write path, and every slice uses one Core-owned atomic claim and typed outcome callback. | `tests/test_main_agent_core.py`, `tests/test_main_agent_store.py`, `tests/test_storage.py`, `vermay/main_agent/local_execution.py`. |
| Late stream failure after durable success | A transport error after a terminal Task event cannot overwrite the durable completed result or render a false failure card. | `tests/test_api_a2a_adapter.py`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Terminal Task event replay | A finite replay stream for an already-terminal Task is hydrated at most once; its normal EventSource close cannot repeatedly reload the Context or resubscribe through `/rpc`. | `web/tests/e2e/runtime-reliability.spec.ts`. |
| Task event subscription ownership | The browser owns at most one physical EventSource per Task, replaces stale connections deterministically, and closes all registered Task streams when the console unmounts. | `web/lib/agent/task-event-stream-registry.ts`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Durable event replay and notification loss | The Task-event table remains authoritative. A lost, duplicate, or spurious process-local wake-up cannot lose, duplicate, or reorder durable history; reconnect resumes after the last accepted `event_id`. | `tests/test_main_agent_store.py`, `vermay/main_agent/task_event_subscription.py`. |
| Continuation snapshot freshness | A delayed cancel, approval-resume, or ordinary-input HTTP response cannot regress a Task already advanced by SSE; a current snapshot merges metadata instead of replacing it. | `web/lib/agent/task-presentation.ts`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Task projection ordering | A higher lifecycle revision wins regardless of timestamp, a lower revision cannot regress state, equal revisions accept only additive evidence, and timestamp comparison is used only when revision data is unavailable. | `tests/test_main_agent_store.py`, `tests/test_api_a2a_adapter.py`, `web/lib/agent/task-projection-reducer.ts`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Invalid SSE protocol data | Malformed JSON, invalid JSON-RPC/A2A envelopes, and Task events without a projectable durable identity stop the subscription and render `invalid_a2a_stream`; they cannot be silently discarded as an indefinitely loading answer. A non-terminal durable Task remains non-terminal. | `web/lib/agent/stream.ts`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Durable Task final-answer presentation | A client-only SSE artifact answer is replaced by the durable assistant Message for the same Task after a terminal status update; the conversation renders exactly one answer. | `web/tests/e2e/runtime-reliability.spec.ts`. |
| Slow Task stream | The browser does not impose a short client-side terminal deadline after Task acceptance; provider and optional Task-budget limits remain backend-owned. | `web/tests/e2e/runtime-reliability.spec.ts`, `tests/test_langgraph_runtime.py`. |
| Safe failed-Task retry | A retryable local failed Task with no potentially side-effecting invocation creates one new Task/message/thread attempt in the same Context; a repeated request returns that same child. | `tests/test_main_agent_core.py`, `tests/test_api_app.py`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Unsafe failed-Task retry | A failed Task with a potentially side-effecting invocation cannot be retried through the generic retry action. | `tests/test_main_agent_core.py`. |
| Approval and ordinary input | `tasks/resume` can consume only an approval continuation; `message/send` with `taskId` can consume only ordinary task input, without rerouting as a new top-level request. | `tests/test_main_agent_core.py`, `tests/test_api_a2a_adapter.py`. |
| Approval continuation presentation | After a finite `input-required` stream closes, approval opens a new Task event subscription from the interruption event id and presents the durable final answer exactly once without a page refresh or background resubscription. | `web/tests/e2e/runtime-reliability.spec.ts`. |
| Cancellation at a safe boundary | The process records `cancel_requested` while active work is pending, then reaches `canceled` without accepting later model-driven tool work. | `tests/test_main_agent_core.py`, `tests/test_langgraph_runtime.py`, `tests/test_execution_context.py`. |
| Slow or failing model call | Provider timeout and an optional Task elapsed-time budget become structured, inspectable failures rather than indefinite work. | `tests/test_langgraph_runtime.py`, `tests/test_openai_compatible_client.py`. |
| Restart recovery | Unclaimed queued work is resubmitted once; claimed work becomes an explicit retryable failure; approval/input continuations remain resumable. | `tests/test_main_agent_core.py`. |
| Bounded Context list | The management API applies a bounded default and page offset, while fallback titles for old untitled Contexts are read in one bulk query. | `tests/test_main_agent_store.py`, `tests/test_api_app.py`. |
| Bounded Context detail reads | Messages, Tasks, route decisions, and delegations default to the latest bounded page, return that page chronologically, and are requested once per selected Session. | `tests/test_main_agent_store.py`, `tests/test_api_app.py`, `web/tests/e2e/runtime-reliability.spec.ts`. |
| Non-read-only tool boundary | Invocation identity, approval binding, evidence, cancellation, and uncertain side effects remain durable. | `tests/test_tool_invocation_ledger.py`. |

## Acceptance Rules

The matrix is green only when all of the following remain true:

1. A durable terminal Task is never presented as a failed direct Message.
2. A JSON-RPC/SSE terminal Task result is not followed by a second public
   error event for the same completed execution.
3. The browser can reload durable Context, Message, and Task records when a
   stream ends ambiguously after Task acceptance.
4. A completed Task's finite event replay cannot become background polling once
   the browser is idle.
5. A blocked Task resumes only through its matching continuation interface.
6. Cancellation and restart leave no silently stuck local `queued`, `running`,
   or `cancel_requested` process.
7. Public errors remain safe to display and preserve their stable `code` and
   `retryable` fields.
8. A durable Task failure preserves its safe `code`, `message`, and retryability
   through the Task record, A2A extension metadata, and browser failure card.
9. Manual retry never mutates or replays the source Task; it creates at most
   one lineage-linked child attempt after the side-effect safety check.
10. Approval continuation resumes event hydration from the last durable event
    and presents its final answer without requiring a browser refresh.
11. Every physical Task EventSource has one registry owner and is closed on
    replacement, terminal hydration, interruption, or console teardown.
12. Invalid SSE data becomes an explicit protocol failure after durable Task
    reconciliation; it is never silently discarded and never fabricates a
    durable Task lifecycle transition.
13. An older continuation response cannot overwrite a newer Task state already
    observed through SSE or durable reconciliation.
14. The default Context-list request is bounded and does not perform one title
    fallback query per untitled Context.
15. All durable browser Task sources enter one reducer; an older or
    equal-revision input cannot restore obsolete lifecycle state, continuation
    metadata, or errors.
16. Every local execution slice is represented by a supported versioned queue
    command and enters the same Core-owned claim/outcome path; process-local
    scheduling state is never treated as durable lifecycle truth.
17. Task-event notification is never treated as durable delivery. Replay
    always re-reads SQLite by `event_id`, and the cursor cannot advance beyond
    an unprojectable durable event.
18. Selecting a Session uses one bounded request per Context detail resource;
    network orchestration cannot create a second browser Task-state writer.

## Deliberate Limits

This is a deterministic contract matrix, not a claim that every external
dependency has been exercised. Real Ollama, MCP, SSH/Kubernetes, and child
agent workflows remain optional live checks because they depend on operator
configuration. They must not become required inputs to the default regression
gate.

The matrix also does not change the current cancellation guarantee: it is
cooperative at model and tool safe boundaries, rather than a global force-kill
mechanism for arbitrary Python code.

The M4 adapter remains intentionally single-host and process-local. This matrix
does not claim worker leases, distributed queue ownership, automatic replay of
ambiguous side effects, or compatibility with unknown queue-command versions.

The M5 notifier is likewise process-local and disposable. This matrix does not
claim durable subscriber acknowledgements, consumer groups, broker retention,
multi-process notification, Redis, or a general event bus.

The M6 read window intentionally has no automatic deep-history pagination in
the browser. The latest 200 records are sufficient for the validated baseline;
older-history controls require a demonstrated workflow before implementation.

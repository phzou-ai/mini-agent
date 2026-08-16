# M0 Contract Baseline

> Status: M0 implementation snapshot; M1 was completed later  
> Last reviewed: 2026-08-16

## Purpose

This document is the executable starting point for the
[Single-Host Contract Refactor Plan](architecture-modernization-plan.md). It
records the current single-host preservation baseline, the owner of each
lifecycle write path, and the exact regression evidence required before a
structural milestone starts.

It is an inventory of the M0 closure state, not a claim that the target
architecture already exists. The later M1 implementation is recorded in the
[M1 Task Projection Handoff](m1-task-projection.md).

## Preserved Product Baseline

M0 preserves these current choices:

- A2A JSON-RPC and SSE are the public agent boundary;
- `MainAgentCore` is the application lifecycle owner;
- `MainAgentStore` and SQLite own durable lifecycle facts;
- the durable queued-execution row and local thread pool run local work;
- LangGraph owns reasoning and checkpoint continuation, not public Task state;
- persisted Task events own replay history;
- process-local notification only wakes subscribers after commit; and
- the Web console projects durable state and does not own lifecycle truth.

PostgreSQL, Redis, Temporal, distributed workers, leases, and a generic event
bus are outside this baseline.

## Lifecycle Ownership Inventory

| Concern | Current entry or mutation owner | Durable or external result | M0 assessment |
| --- | --- | --- | --- |
| Message admission and replay | `MainAgentCore.handle_message()`, `stream_message()`, and Message ingress workflows in `MainAgentStore` | Message, ingress state, route decision, and outcome identity | One application owner exists; extraction is deferred. |
| Local Task acceptance | `MainAgentStore.accept_local_task_from_message()` | Route decision, Task, ingress resolution, queued execution, and initial events | Atomic acceptance already exists. Worker scheduling must remain post-return and post-commit. |
| Local Task transition | `MainAgentStore.transition_local_task()` and `vermay/main_agent/lifecycle.py` | Current Task state plus one lifecycle event | Local transition policy is explicit and must remain the only local status path. |
| Remote proxy synchronization | `MainAgentCore` remote synchronization workflow and `MainAgentStore.update_task_status()` | Proxy Task snapshot, delegation metadata, and remote result projection | Route acceptance is durable before outbound; the response-persistence crash window is recorded without an exactly-once claim. |
| Approval continuation | `MainAgentCore.resume_task()` | Consumed approval continuation and queued execution command | Must never consume ordinary input continuation. |
| Ordinary input continuation | `MainAgentCore.submit_task_input()` | Consumed input continuation and queued execution command | Must preserve the original Task input cut and bypass top-level routing. |
| Cancellation | `MainAgentCore.cancel_task()` | Cancel request or terminal cancellation at a safe boundary | Cooperative cancellation remains the current guarantee. |
| Safe failed-Task retry | `MainAgentCore.retry_failed_task()` | One lineage-linked Task attempt | Side-effecting or uncertain tool work remains a hard retry boundary. |
| Execution claim and scheduling | Queue methods in `MainAgentStore`; `_schedule_queued_task_execution()` in `MainAgentCore`; local submitter in `executor.py` | Atomic queue claim followed by one local execution slice | Scheduler must not mutate lifecycle state outside application commands. |
| Task result persistence | Result workflows in `MainAgentCore`, Task/artifact/message methods in `MainAgentStore` | Output Message, artifact, error, and terminal Task state | Still concentrated; typed command outcomes are deferred to M2. |
| Task event append | `MainAgentStore.append_task_event()` | Durable event ordered by `event_id` | Event insertion is durable; subscriber notification now uses the post-commit contract. |
| Task event wake-up | `AgentStore.register_after_commit()` and `MainAgentStore._notify_task_event_committed()` | Disposable process-local wake-up | Implemented in M0. Rollback discards the wake-up. |
| SSE replay and subscription | A2A adapter/routes plus `MainAgentStore.list_task_events()` and `wait_for_task_events()` | Replay and live delivery from durable `event_id` | Durable events remain authoritative; notification is not a data source. |
| LangGraph continuation | Runtime checkpoint store keyed by `runtime_thread_id` | Internal execution continuation | Must remain separate from A2A `task_id`. |
| Web Task reconciliation | `mergeTaskWithA2ASnapshot()` plus multiple `setTasks()` call sites in `agent-console.tsx` | Browser Task and conversation projection | At M0 closure, freshness protection existed but one reducer did not yet own every write. M1 subsequently closed this gap. |

## Transaction And Post-Commit Contract

The accepted M0 ordering is:

```text
validate
  -> write durable lifecycle facts
  -> commit SQLite transaction
  -> wake local subscribers or workers
```

`AgentStore.register_after_commit()` is deliberately small. Nested transaction
scopes join the outer SQLite transaction and are not independent savepoints.
Their callbacks therefore commit or roll back with the outer transaction.
Callbacks run outside the SQLite lock and cannot roll back an already committed
command. Callback failure is logged and cannot rewrite the durable outcome as
failed.

M0 uses this helper for Task event notification only. Local Task acceptance and
continuation already return from their transaction before calling
`_schedule_queued_task_execution()`, so queue submission is also post-commit
without being registered as a callback.

Remote delegation has a different boundary: Message ingress and the route
decision are durable before the child A2A request, while the local delegation
result and optional proxy Task can only be stored after the child responds. A
process failure in that interval can leave the remote outcome uncertain. M0
records that bounded single-host limitation; it does not add an outbox,
automatic remote replay, or an exactly-once claim. A later remote-delegation
milestone must start from a reproduced reliability requirement.

## Named Regression Matrix

The maintained broad gates are:

```bash
scripts/check_single_host_reliability.sh
scripts/check_full_stack_regression.sh
```

The following tests are named contract evidence, not illustrative examples:

| Contract | Required focused evidence |
| --- | --- |
| Transaction rollback and post-commit notification | `tests/test_storage.py::test_agent_store_transaction_rolls_back_execute_calls`, `tests/test_storage.py::test_agent_store_runs_callbacks_only_after_outer_commit`, `tests/test_storage.py::test_agent_store_discards_callbacks_from_rolled_back_transaction`, `tests/test_storage.py::test_agent_store_keeps_nested_callback_when_outer_transaction_commits`, `tests/test_main_agent_store.py::test_main_agent_store_defers_task_event_notification_until_commit` |
| Duplicate Message admission | `tests/test_main_agent_core.py::test_main_agent_core_replays_duplicate_message_id_without_routing_or_execution`, `tests/test_main_agent_core.py::test_main_agent_core_replays_duplicate_task_message_without_creating_task` |
| Direct Message and Message stream | `tests/test_main_agent_core.py::test_main_agent_core_local_message_persists_messages_without_task`, `tests/test_api_a2a_adapter.py::test_a2a_rpc_send_streaming_message_emits_partial_local_message_events` |
| Local Task acceptance and completion | `tests/test_main_agent_core.py::test_main_agent_core_rolls_back_async_task_acceptance_when_queue_write_fails`, `tests/test_main_agent_core.py::test_main_agent_core_local_task_runner_persists_output_message_artifact_and_events` |
| Approval and ordinary input | `tests/test_main_agent_core.py::test_main_agent_core_background_resume_consumes_approval_and_completes`, `tests/test_main_agent_core.py::test_main_agent_core_background_input_resume_consumes_request_and_completes`, `tests/test_main_agent_core.py::test_main_agent_core_rejects_wrong_resume_interface_for_pending_input` |
| Cancellation | `tests/test_main_agent_core.py::test_main_agent_core_running_background_task_honors_cancel_at_safe_boundary`, `tests/test_langgraph_runtime.py::test_langgraph_runtime_stops_before_executing_tools_after_cancellation_during_model_call` |
| Safe and unsafe retry | `tests/test_main_agent_core.py::test_main_agent_core_retries_a_safe_model_failure_as_a_new_task_attempt`, `tests/test_main_agent_core.py::test_main_agent_core_does_not_retry_a_failed_task_with_side_effecting_tool_work` |
| Restart recovery | `tests/test_main_agent_core.py::test_main_agent_core_reconciles_unclaimed_queued_execution_after_restart`, `tests/test_main_agent_core.py::test_main_agent_core_recovers_durable_approval_continuation_after_restart`, `tests/test_main_agent_core.py::test_main_agent_core_recovers_durable_input_continuation_after_restart` |
| SSE replay and cursor | `tests/test_api_a2a_adapter.py::test_a2a_rpc_task_resubscribe_accepts_after_event_id`, `tests/test_api_a2a_adapter.py::test_a2a_rpc_subscribe_to_task_replays_artifact_update` |
| Malformed or stale browser input | `web/tests/e2e/runtime-reliability.spec.ts` tests `renders an invalid Task event stream instead of loading forever`, `renders malformed Message SSE data as a protocol failure`, and `does not let an older continuation snapshot regress Task state` |
| Approval without refresh and one stream owner | `web/tests/e2e/runtime-reliability.spec.ts` tests `shows an approved task result without requiring a page refresh` and `owns and closes one physical event stream per task` |
| Uncertain external effects | `tests/test_tool_invocation_ledger.py::test_ledger_blocks_a_duplicate_succeeded_effect_within_one_task`, `tests/test_tool_invocation_ledger.py::test_core_binds_approval_resume_to_the_interrupted_invocation` |
| Deterministic child-agent delegation | `tests/test_main_agent_core.py::test_main_agent_core_remote_message_records_delegation_and_assistant_message`, `tests/test_main_agent_core.py::test_main_agent_core_remote_task_records_proxy_task_and_delegation` |

## M0 Validation Evidence

Validated on 2026-08-16:

- `scripts/check_single_host_reliability.sh`: 214 backend contract tests and 16
  Playwright tests passed;
- `scripts/check_full_stack_regression.sh`: 486 Python tests, frontend
  typecheck, Next.js production build, and 16 Playwright tests passed;
- deterministic documentation audit: 53 Markdown files passed; and
- the tracked working-tree diff fingerprint was unchanged after both gates.

Live model, MCP, SSH/Kubernetes, and child-agent dependencies were not invoked
by this deterministic M0 pass. They remain environment-dependent validation,
not a reason to weaken the repository gate.

## M0 Completion And Handoff

Completed in M0:

- recorded lifecycle and projection ownership;
- named the deterministic regression evidence;
- fixed Task event notification to occur after durable commit;
- added rollback, nested-transaction, and subscriber notification tests for
  the new contract;
- confirmed local Task and continuation scheduling occurs after transaction
  completion;
- recorded the remote-delegation uncertainty window without introducing
  speculative infrastructure;
- passed both maintained regression gates without modifying tracked source;
  and
- preserved the current single-host runtime and public A2A boundary.

At M0 closure, the next milestone required a separate decision. M1 was later
authorized and completed; command-handler extraction, remote-delivery
infrastructure, and middleware adoption remain outside this historical
baseline.

# Governed Execution Kernel

**Status:** implemented for locally owned LangGraph Tasks, 2026-08-02.

## Purpose

R2 makes one local LangGraph execution slice bounded and inspectable without
creating a planner, scheduler, or second Task lifecycle. `MainAgentCore`
continues to own the durable local Agent Process and its A2A projection.
`LangGraphAgentRuntime` owns only model/tool progression and checkpoint
continuation for the process's `runtimeThreadId`.

## Task-Scoped Policy

The runtime copies an `ExecutionPolicy` into the initial graph state. The
model cannot modify it, and the same policy remains in the checkpoint across
approval or user-input continuation.

| Limit | Default | Enforcement point |
| --- | --- | --- |
| `max_model_calls` | `5` | Before a model invocation. |
| `max_tool_calls` | `20` (`max(8, max_loop_steps * 4)`) | Before `ToolNode` execution. |
| `max_failures` | `2` | Immediately after normalized tool observations are recorded. |
| `max_loop_steps` | `5` | Between ReAct iterations. |
| `max_elapsed_seconds` | Disabled | Before model/tool/loop work when explicitly configured. |

The elapsed-time limit measures wall-clock process age, including time spent
waiting for a human continuation. It is disabled by default so an operator
does not lose a blocked approval merely because they took time to review it.

Older checkpoints with only the historical `max_loops` field retain that
bound when resumed; they are not silently assigned a different fixed policy.

## Model Call Deadline And Safe-Boundary Cancellation

The provider timeout remains the outer safety bound for every HTTP model call.
For an active local Task with `max_elapsed_seconds`, the runtime also passes
the Task's remaining wall-clock budget to the model adapter. The client uses
the smaller of the configured provider timeout and that remaining budget. A
Task can therefore shorten a provider wait, but cannot extend the configured
provider safety limit.

`MainAgentCore` still owns the durable `cancel_requested` process transition.
`ExecutionContextRegistry` is only its in-memory signal to an active local
runner. A blocking HTTP request is not falsely presented as force-killed; once
the model call returns or times out, LangGraph checks the cancellation signal
before accepting that response, evaluating permission, or executing a tool.
The core then projects the process to `canceled` at that safe boundary.

This keeps the failure distinction stable:

- a provider failure before the Task deadline remains a typed, retryable model
  failure;
- a call that exhausts the Task's elapsed-time budget becomes the structured
  `budget_exhausted` execution outcome;
- a cancellation request prevents later model-driven tool work and becomes
  `canceled` when the active operation reaches its next safe boundary.

Direct A2A Messages do not run inside a local Task execution context. They
continue to use only the provider-configured timeout.

The Web console preserves this distinction: an A2A `working` status with
`metadata.localStatus = cancel_requested` is shown as **cancellation
requested**, disables duplicate stop/send actions, and explains that the
runtime is waiting for the current operation to reach a safe boundary.

## Structured Task Model Actions

The Ollama adapter uses a project-owned JSON action protocol for Task-mode
model calls: every response is either a `final` action or a `tool_call`
action. This boundary is intentionally fail-closed. If a model emits natural
language such as "Calling tool ..." without a valid action object, the runtime
records a typed `model_error` rather than treating the announcement as a final
answer or guessing which tool to execute.

Reasoning markup is not a second protocol. The shared parser may recover a
valid action embedded after model-specific `<think>...</think>` text, but that
text is never presented as an accepted Task answer. Direct A2A Messages retain
their plain-text response contract and do not use this Task action parser.

## Current Timeout Boundary

The single-host runtime has capability-level timeout controls. It does not yet
have one preemptive global timeout that can interrupt every arbitrary Python or
LangChain tool call.

| Boundary | Current control | Relationship to the Task deadline |
| --- | --- | --- |
| Model provider HTTP call | Per-model `timeout_seconds`; the checked-in model configurations use `120` seconds. | When `max_elapsed_seconds` is configured, the client uses the smaller of this timeout and the Task's remaining elapsed-time budget. |
| SSH/Kubernetes subprocess | `SshClient` has its own command timeout (`20` seconds by default; selected Kubernetes operations use `30`). It can terminate, then kill, its child process. | When a Task deadline exists, SSH uses the smaller of its command timeout and the remaining Task budget. |
| MCP discovery, prompts, resources, and tool calls | Per-server `timeout_seconds`, enforced by the MCP transport (`30` seconds by default). | Not yet shortened by the active Task deadline. |
| Weather HTTP tool | A fixed `15`-second HTTP timeout. | Not yet shortened by the active Task deadline. |

The configured `max_elapsed_seconds` is an optional **wall-clock process-age
limit**, not a default interactive Task timeout. Its timer starts with the
initial graph state and continues while the process waits for approval or
ordinary user input. It is intentionally disabled by default because an
operator-paced continuation should not silently expire during review.

Consequently, the practical protection in the default interactive runtime is
per-provider and per-tool timeout plus model/tool/loop budgets and cooperative
cancellation. A future unified deadline, if a real workload requires it,
should distinguish active model/tool execution time from suspended
approval/input lifetime. It should not turn the current
`max_elapsed_seconds` process-age limit into a short global default.

## Stop Reasons And Process Projection

`stop_reason` is an execution-kernel fact, not a new A2A or local-process
status. `MainAgentCore` projects the `RunResult` into the existing process
state machine.

| Stop reason | Meaning | Local Agent Process result |
| --- | --- | --- |
| `completed` | The model returned a final answer. | `completed`. |
| `input_required` | The model requested ordinary user input. | `input_required`; resumable. |
| `approval_required` | A protected capability needs authorization. | `auth_required`; resumable. |
| `budget_exhausted` | A model, tool, loop, or optional elapsed-time limit was reached. | `failed` with a structured execution summary. |
| `repeated_failure` | The normalized tool-failure budget was reached. | `failed` with observations retained. |
| `policy_blocked` | A capability was rejected by policy or approval. | `completed` with a refusal answer; the summary makes the blocked effect explicit. |
| `canceled` | The control plane ended the process at a safe boundary. | `canceled`. |
| `environment_failure` | The core could not execute or persist the slice safely. | `failed`, with structured retryability. |

The last two reasons are control-plane outcomes recorded by `MainAgentCore`.
They are listed with kernel reasons so an inspector has one complete execution
summary, but they do not make LangGraph an A2A lifecycle owner.

## Normalized Tool Observations

After `ToolNode` returns, the runtime records one bounded observation per
`ToolMessage`:

```text
loop_index
tool_call_id, tool_name
ok, summary, structured_data
error_category, retryable
changed_resources, artifact_refs
```

Failure classification uses the `ToolMessage` status plus structured tool
fields such as `error_code`, `error_category`, and `retryable`. It never asks
the model to infer failure type or retryability from an arbitrary error string.
Large structured values are bounded before persistence. Non-read-only calls
also cite the R1 Tool Invocation Ledger result artifact when one exists.

The observations are persisted as a normal Task artifact:

```text
artifactId = <taskId>:tool_observations
parts      = [{ kind: "data", data: { observations: [...] } }]
```

The Web UI or operational tooling can read them through:

```text
GET /api/tasks/{task_id}/observations
```

This is an inspection projection, not another task execution API.

## Completion Claim, Evidence, And Residual Risk

When a model returns a final answer, the runtime records a `completion` object
inside the execution summary. `completion.claimed` means only that a final
answer was produced. It is not a semantic proof that the answer is correct.

Before a final answer can be recorded, the runtime also rejects the narrow
protocol violation where an answer explicitly says it is calling a registered
tool (for example, `Calling tool ssh_kubectl_get`) but contains no structured
tool call. That text would otherwise bypass `ToolNode`, create no observation,
and incorrectly complete the Task. The Task fails as a model protocol error
instead; ordinary final answers that merely mention a tool remain valid.

The surrounding fields are deterministic facts derived from recorded
observations:

- `evidence`: successful tool observations and their artifact references;
- `residual_risks`: tool failures, budget stops, environment failure, or the
  absence of tool evidence for a model-only answer;
- `completion.evidence_count` and `completion.residual_risk_count`: stable
  counts for inspectors and future verifiers.

The completed Task event and final-answer artifact carry this execution
summary. An observation artifact is also created or updated before the final
artifact, so the final artifact can refer to it by id.

## Deliberate Boundaries

- R2 does not introduce a persistent plan, plan DAG, replanning model, or
  model-as-judge verifier.
- R2 does not change A2A Task state names or use `runtimeThreadId` as a public
  identity.
- R2 does not claim exactly-once tool execution; R1 remains the side-effect
  safety boundary.
- Final-answer token streaming, workspace isolation, and distributed worker
  execution remain separate later decisions.

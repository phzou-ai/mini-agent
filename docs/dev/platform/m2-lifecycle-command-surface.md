# M2 Lifecycle Command Surface Handoff

> Status: complete and validated  
> Last reviewed: 2026-08-16  
> Authority: M2 implementation boundary, evidence, and preserved limits

## Purpose

M2 gives externally meaningful lifecycle mutations one typed application
surface without creating another lifecycle owner. `MainAgentCore` remains the
facade, composition point, and sole owner of public A2A Message and Task
lifecycle decisions.

## Implemented Boundary

`vermay/main_agent/commands.py` defines immutable commands for:

| Command | Intent |
| --- | --- |
| `AdmitMessageCommand` | Admit one top-level Message or continue one Task input. |
| `CancelTaskCommand` | Request cancellation of one Task. |
| `ResolveApprovalCommand` | Approve or reject one approval continuation. |
| `SubmitTaskInputCommand` | Supply ordinary input to an existing continuation. |
| `RetryTaskCommand` | Create or reuse one safe retry attempt. |
| `ReconcileStartupCommand` | Reconcile durable work after process startup. |
| Internal `Record*Command` types | Record an already accepted local, remote, recovery, or cancellation outcome. |

The public outcomes are `MessageCommandOutcome`, `MessageStreamOutcome`,
`TaskCommandOutcome`, and `StartupReconciliationOutcome`. Protocol adapters
unwrap these outcomes explicitly instead of depending on raw store records as
the application mutation contract.

`MainAgentCore.execute()` dispatches non-streaming commands.
`MainAgentCore.stream()` accepts `AdmitMessageCommand` and yields typed stream
outcomes. Existing named facade methods remain available to in-process callers,
but each delegates immediately to one of these entry points; they do not own a
second implementation.

## Task Outcome Recorder

`vermay/main_agent/task_outcomes.py` contains `TaskOutcomeRecorder`, a
core-owned subordinate that persists already accepted execution outcomes. It
may:

- record completed, interrupted, failed, recovered, or canceled local results;
- persist observations, final-answer artifacts, and assistant Messages;
- synchronize validated child-agent snapshots into a local proxy Task; and
- close unresolved tool-invocation evidence at terminal boundaries.

It may not route a Message, schedule work, invoke a model or tool, accept a
continuation, or decide public protocol policy. Terminal state is rechecked
inside the persistence transaction before an outcome is applied.

## Migrated Boundaries

- A2A `message/send` and `message/stream` construct
  `AdmitMessageCommand`.
- A2A cancel, approval continuation, and ordinary-input continuation construct
  their matching typed commands.
- FastAPI startup reconciliation and the first-party failed-Task retry endpoint
  use the same application command surface.
- Local runner callbacks and child-agent snapshots re-enter the core as typed
  accepted-outcome commands.

Management queries remain read-model calls and are intentionally not converted
into lifecycle commands.

## Validation Evidence

The following checks passed on 2026-08-16:

- focused main-agent, A2A adapter, boundary, and API coverage: 137 tests;
- `scripts/check_single_host_reliability.sh`: 217 backend tests and 18
  Playwright tests; and
- `scripts/check_full_stack_regression.sh`: 489 Python tests, frontend type
  checking, Next.js production build, and 18 Playwright tests.

The deterministic gates do not claim live Ollama, MCP, SSH/Kubernetes, or
child-agent coverage. Those remain optional checks requiring operator
configuration.

## Preserved Limits And Next Gate

- M2 does not change SQLite transaction ownership or make post-commit actions
  explicit. That is possible M3 scope.
- `MainAgentCore` remains intentionally concentrated as the only lifecycle
  facade; M2 did not split it into independent lifecycle owners.
- `TaskOutcomeRecorder` is not a generic repository or middleware abstraction.
- Remote calls retain their documented crash uncertainty; M2 does not claim
  exactly-once child-agent delivery.
- No Redis, PostgreSQL, Temporal, lease, heartbeat, distributed lock, or event
  bus abstraction was introduced.
- At M2 closure, M3 remained unauthorized. It was later selected and completed
  while preserving the M1 revision/reducer contract and this single command
  surface. See the
  [M3 Transaction And Post-Commit Boundary Handoff](m3-transaction-post-commit-boundary.md).

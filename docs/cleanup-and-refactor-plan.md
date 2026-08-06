# Cleanup And Refactor Plan

**Status:** completed maintenance pass, 2026-08-05
**Scope:** reduce accidental complexity without adding product capabilities or
changing the current A2A, local-process, or LangGraph contracts.

## Why This Pass Exists

The single-host runtime is now functionally complete enough to exercise real
model, tool, continuation, cancellation, retry, and browser-recovery flows.
The next risk is not missing functionality. It is that implementation detail
spreads across a few large modules and historical compatibility surfaces become
hard to distinguish from active product code.

This pass therefore treats cleanup as an engineering task with explicit
evidence and regression gates. It is not a rewrite and it is not an Agent OS
expansion milestone.

## Guardrails

- Do not add features, public endpoints, state values, storage schemas, or
  infrastructure dependencies.
- Do not replace `MainAgentCore` as the lifecycle owner or move public A2A
  ownership into LangGraph, FastAPI, or the Web UI.
- Do not delete code merely because it is old, small, or currently test-only.
  Each deletion needs a verified absence of active imports and a stated
  replacement or archival policy.
- Keep each change batch narrow, behavior-preserving, and independently
  reviewable.
- Preserve the current SQLite baseline and do not introduce data migration work
  during this pass.
- Prefer moving pure helpers or presentational components before changing a
  service boundary.
- Add no linting, formatting, or test dependency unless a separate decision
  justifies the new maintenance surface.

## Scan Summary

The inventory covered the active Python runtime, API and transport layers, the
colocated Next.js console, tests, scripts, and active documentation.

| Area | Finding | Assessment |
| --- | --- | --- |
| Runtime lifecycle | `MainAgentCore` is the single active owner; `langgraph_runtime` is the execution kernel. | Keep the boundary. Do not split ownership again. |
| Main-agent implementation | `main_agent/core.py` and `main_agent/store.py` are each about 2,000 lines. | Candidates for narrow extraction of pure projection and mapping helpers, not a service/repository rewrite. |
| LangGraph runtime | `langgraph_runtime/nodes.py` is about 1,000 lines and combines nodes, progress, and trace work. | Deliberately defer. Its current cohesion is acceptable; split only when a concrete new node family makes maintenance difficult. |
| Web console | `web/app/(agent)/agent/_components/agent-console.tsx` began at about 4,500 lines and mixed orchestration, projections, transcript rendering, sidebar, composer, and inspector views. It is now about 2,600 lines after the safe presentation extractions below. | Keep one page-level owner for request and stream state; extract only leaf presentation and pure projection boundaries. |
| Provider support | Router JSON calls and model factories independently parse some provider options and format some HTTP failures. | Candidate for a small shared validation/HTTP utility after a focused contract baseline; do not merge routing with general model invocation. |
| BFF routes | The Next route tree contains many thin filesystem-route wrappers over shared proxy helpers. | Keep. This repetition is framework structure, not harmful duplicated lifecycle logic. |
| Legacy aliases | `mini-agent`, `mini_agent`, and `MINI_AGENT_*` are documented compatibility aliases. | Decision-gated; do not remove in this pass without an explicit compatibility retirement decision. |
| A2A transport compatibility | Canonical `/rpc` and supported path-style bindings are tested and exercised by scripts. | Decision-gated; do not remove while they remain a documented/tested surface. |
| Archived harness | `archive/` is explicitly outside the active product path. `observation.py` and `tool_executor.py` are retained compatibility/reference modules with focused tests. | Keep for now; classify clearly rather than deleting reference material. |
| Documentation | Active runtime material is well covered, but the roadmap, dated review, current assessment, and prior organization review overlap in purpose. | Add this plan as the active cleanup authority; preserve dated records as evidence rather than rewriting history. |
| Styling | `preflight.css` is a substantial compatibility/reset layer. | Investigate only after visual regression evidence; do not remove during the first batches. |

## Protected And Decision-Gated Areas

The following are not automatic slimming targets:

1. `archive/hands_on_langgraph_runtime/` and its reference tests.
2. `vermay_agent/observation.py` and `vermay_agent/tool_executor.py`.
3. `mini_agent` package and `mini-agent` command alias.
4. `MINI_AGENT_*` environment fallbacks.
5. A2A path-style compatibility bindings and the `/rpc` contract.
6. Next.js BFF filesystem routes.
7. `prompt_json` model-tool compatibility mode.
8. `langgraph_runtime/nodes.py` as a speculative folder split.
9. `web/styles/preflight.css` without screenshot-based verification.

Removing any item above needs a separate product or compatibility decision,
not a mechanical cleanup change.

## Execution Order

### C0. Baseline And Change Discipline

**Status:** completed, including the final closeout baseline.

- Record the active architecture and protected areas in this document.
- Keep the working tree clean before each independent batch.
- Run targeted Python tests and Web checks before and after every batch.

**Acceptance:** a reviewer can identify why a moved, retained, or deferred
module belongs to its current boundary.

### C1. Decompose The Web Console Without Changing State Ownership

**Priority:** highest.

**Status:** completed for this maintenance pass. Pure task and conversation
projection helpers now live in `web/lib/agent/`; the sidebar, composer, and
transcript are standalone presentation components. `AgentConsole` remains the
sole owner of request dispatch, stream consumption, selected Context state,
and optimistic reconciliation. The Inspector remains colocated intentionally:
its registered-agent editing callbacks still belong to the page-level owner,
and extracting it now would add prop plumbing without a demonstrated lifecycle
or readability gain.

**Goal:** make `agent-console.tsx` readable without introducing a global store,
new controller, or a second browser-side lifecycle owner.

**Allowed work:**

- Extract pure status, transcript, and display-projection helpers into
  colocated or `web/lib/agent/` modules with explicit names.
- Extract leaf presentational sections such as the sidebar, transcript,
  composer, and inspector into focused components when their inputs and
  callbacks are already explicit.
- Keep request dispatch, SSE stream consumption, selected Context state, and
  optimistic UI reconciliation in one page-level owner until there is concrete
  evidence that a new state boundary is needed.
- Deduplicate test-only fixture builders only when the shared fixture still
  expresses one stable contract.

**Do not:** change UI behavior, network routes, event normalization semantics,
or the browser retry/continuation contract.

**Acceptance:** the console has a small orchestration root; extracted modules
have clear single responsibilities; `pnpm typecheck`, browser regression, and
E2E coverage remain green.

### C2. Extract SQLite Row Mapping And Serialization Helpers

**Priority:** high, after C1.

**Status:** completed for the intended narrow scope. SQLite row-to-record
mappers and JSON normalization now live in `main_agent/store_mappers.py`.
`MainAgentStore` still owns SQL, transactions, schema-facing methods, and
lifecycle-safe deletion. The focused store and tool-ledger suite passed after
the move.

**Goal:** reduce `MainAgentStore` mechanical density without hiding storage
operations behind a new repository hierarchy.

**Allowed work:**

- Move pure `sqlite3.Row` -> record-model mappers and JSON normalization helpers
  from `main_agent/store.py` to a clearly named private module such as
  `main_agent/store_mappers.py`.
- Keep SQL statements, transactions, schema initialization, and public store
  methods in `MainAgentStore`.

**Do not:** rename durable fields, change SQL semantics, alter transactions, or
split CRUD methods into speculative repositories.

**Acceptance:** storage tests demonstrate identical record values and lifecycle
operations after extraction.

### C3. Extract Pure Main-Agent Projection Helpers

**Priority:** medium, only if C2 shows a useful stable pattern.

**Status:** completed for local task-result projection. The pure conversion of
`LocalTaskRunResult` into process state, continuation requests, lifecycle
payloads, execution metadata, and observations now lives in
`main_agent/task_result_projection.py`. `MainAgentCore` retains every state
transition, side effect, executor submission, continuation operation, and
Core-to-store sequence. Remote-proxy helpers remain with Core because their
validation is part of delegated-task lifecycle ownership.

**Goal:** reduce `MainAgentCore` navigation cost while preserving it as the only
public lifecycle owner.

**Allowed work:**

- Move pure result-to-event, artifact, and error-payload projection helpers to
  a narrowly named module.
- Keep transitions, side effects, executor submission, continuation handling,
  and Core-to-store sequencing in `MainAgentCore`.

**Do not:** turn helpers into a second lifecycle service or split the core by
transport route.

**Acceptance:** the extracted functions have direct unit coverage and no
observable A2A or local-process behavior changes.

### C4. Consolidate Provider Configuration Primitives

**Priority:** medium and contract-sensitive.

**Status:** deferred by design. Router JSON classification and normal model
invocation currently have different validation, payload, and response semantics.
No provider-neutral helper can be introduced without first defining a shared
behavior contract, so merging them would be speculative rather than slimming.

**Goal:** remove duplicated parsing of provider options and duplicated safe HTTP
error rendering, while retaining separate request/response adapters for router
JSON and normal model invocation.

**Allowed work:**

- Introduce a small provider-neutral internal utility for validated strings,
  positive timeouts, and sanitized HTTP error details if the existing tests can
  protect both callers.
- Keep JSON-classifier prompts, provider payloads, response parsing, native
  tool-call normalization, and retry semantics in their current adapters.

**Do not:** combine `RouterJsonHttpClient` with a chat-model client, silently
fall back between tool-calling modes, or change provider configuration values.

**Acceptance:** Ollama and OpenAI-compatible unit tests prove equivalent
validation and error contracts, including protected secret handling.

### C5. Documentation And Naming Consolidation

**Priority:** medium/low; perform alongside completed code batches.

**Goal:** make active guidance easy to find without deleting implementation
history.

**Allowed work:**

- Keep this document as the active cleanup sequence.
- Update indices and short module descriptions when a batch moves code.
- Mark dated reviews and completed refactor notes as evidence, not live plans.
- Normalize only local/private filenames where no public import, script, or
  documented compatibility contract is affected.

**Do not:** rename public package paths, commands, API paths, environment
variables, or persisted names as a cosmetic operation.

### C6. Re-evaluate Protected Compatibility Candidates

**Priority:** last and decision-gated.

**Status:** completed for this pass. The re-scan found no production module
that can be deleted without either breaking a documented compatibility surface
or discarding intentional reference material. Generated artifacts remain
ignored and untouched; no deletion proposal is warranted.

After C1-C5, re-scan imports, scripts, documentation, package contents, and
real user workflows. Produce an explicit deletion proposal only for a candidate
with no active contract and a clear replacement. No deletion occurs under this
plan without that evidence.

## Verification Gates

Each batch must use the smallest relevant checks first, then the broader
baseline before closeout:

```bash
python -m pytest <focused Python tests>
pnpm --dir web typecheck
pnpm --dir web test:regression
pnpm --dir web test:e2e
scripts/check_single_host_reliability.sh
scripts/check_full_stack_regression.sh
git diff --check
```

Some browser and full-stack checks require their documented local services.
When a service is unavailable, record the gap rather than treating an
unexecuted check as a pass.

## Implementation Log

| Batch | Scope | Evidence |
| --- | --- | --- |
| C1.1 | Extracted pure browser task presentation and conversation projection helpers. | `pnpm --dir web typecheck`; browser regression 9/9 passed. |
| C1.2 | Extracted sidebar and Composer leaf components; preserved explicit props, callbacks, and test IDs. | `pnpm --dir web typecheck`; browser regression 9/9 passed. |
| C1.3 | Extracted transcript rendering, auto-scroll, failure cards, approval/input cards, Markdown rendering, and retry/continue callbacks. | `pnpm --dir web typecheck`; browser regression 9/9 passed. |
| C2 | Extracted SQLite row mappers and JSON serializers to `store_mappers.py`. | `tests/test_main_agent_store.py` and `tests/test_tool_invocation_ledger.py`: 21 passed. |
| C3 | Extracted pure local task-result projection to `task_result_projection.py`. | `tests/test_main_agent_core.py`: 61 passed. |
| C4 | Reviewed provider adapter overlap. | Deferred: router JSON and general model invocation have deliberately different contracts; no safe consolidation without a separate contract decision. |
| C5/C6 | Updated the documentation index and re-scanned protected compatibility candidates. | No safe production deletion candidate found; aliases, A2A bindings, BFF wrappers, and archived reference material remain intentionally retained. |
| C0 closeout | Ran the deterministic reliability and full-stack regression baselines after all extractions. | Python: 471/471; production Web build and typecheck passed; browser regression 9/9 passed. A separate full browser E2E pass also passed 29/29. |

## Completion Criteria

This maintenance pass is complete when:

- the Web console has clear orchestration and presentation boundaries;
- storage and core mechanical helpers are separated only where their contracts
  are demonstrably pure and stable;
- provider configuration overlap has an explicit keep/defer decision without
  collapsing adapter responsibilities;
- active and historical documentation have distinct roles;
- protected compatibility surfaces have an evidence-based keep/remove decision;
- no feature, public API, persistence, or lifecycle behavior changed; and
- the final focused and full-stack regression baseline is recorded.

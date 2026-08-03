# Code Organization Review

## Scope

This review focuses on the active project path:

- `vermay_agent/main.py`
- `vermay_agent/app_factory.py`
- `vermay_agent/langgraph_runtime/`
- shared harness modules under `vermay_agent/`
- active tests under `tests/`

The archived hands-on runtime is outside the active maintenance path and should not drive new architecture decisions.

## Current Assessment

The project now has one active execution stack:

```text
A2A / management ingress
  -> vermay_agent/main_agent/
     -> vermay_agent/langgraph_runtime/
```

`MainAgentCore` owns message routing and durable process lifecycle;
`langgraph_runtime` uses standard LangChain / LangGraph data structures and
`ToolNode` for local execution and checkpoint continuation. The earlier
explicit harness runtime has been moved to:

```text
archive/hands_on_langgraph_runtime/
```

This removes the main structural ambiguity in the project. Future work should extend the active runtime unless a task explicitly asks for historical comparison.

## Active Module Boundaries

### `main.py`

Responsibilities:

- provide the `vermay-agent` console entry point and keep `mini-agent` as a compatibility alias
- route prompt execution to `vermay_agent/cli/prompt.py`
- route named subcommands to `vermay_agent/cli/subcommands.py`
- keep compatibility re-exports while tests and downstream imports migrate

Current status: thin dispatcher.

### `vermay_agent/cli/`

Responsibilities:

- prompt-run argument parsing
- model provider option parsing
- trace path validation
- interactive approval prompting
- subcommand parsing for `serve`, memory, skills, eval replay, and MCP inspection

Current status: active CLI implementation boundary.

### `app_factory.py`

Responsibilities:

- build runtime dependencies
- register tools
- construct model adapters through `ModelProviderConfig`
- wire permission, trace, progress, and checkpoint components
- own factory-level paths such as `trace_path` and `checkpoint_path`

Current status: active runtime assembly boundary.

### `vermay_agent/langgraph_runtime/`

Responsibilities:

- graph state
- graph topology
- node implementations
- routing
- runtime wrapper
- model adapter boundary

Current status: active production-oriented path.

Watch point:

- `nodes.py` still combines graph node behavior, progress events, and trace events. This is acceptable for the current size, but it is the first file to split if memory, skills, model adapter orchestration, or MCP nodes are added.

Potential future split:

```text
vermay_agent/langgraph_runtime/
  nodes/
    model.py
    permission.py
    approval.py
    tools.py
    loop.py
  events.py
```

Do not split this until there is a concrete maintenance trigger.

### `vermay_agent/api/`

Responsibilities:

- FastAPI application composition and lifespan ownership
- A2A JSON-RPC/SSE binding and first-party management/read-model endpoints
- no independent task lifecycle ownership

Current status: active transport and composition boundary over `MainAgentCore`.
The old service/session implementation and its dedicated tests have been
removed; the API package has no alternate lifecycle owner.

### Shared Harness Modules

Current shared modules:

- `context_builder.py`
- `tooling.py`
- `tool_registry.py`
- `permission.py`
- `progress.py`
- `trace.py`
- `result_summary.py`
- `types.py`

Current status: acceptable.

Current classification:

```text
active:
  tool_registry.py
  tool_schema.py
  tooling.py

active bridge / compatibility:
  context_builder.py
  types.py

compatibility / archived harness reference:
  observation.py
  tool_executor.py
```

`context_builder.py` remains active as the source of baseline context policy text, but its project-message builder is a legacy shape compared with the active LangGraph message state. `types.py` still provides active bridge types for model adapters and permission checks. `observation.py` and `tool_executor.py` are intentionally retained for explicit harness tests and archived-runtime reference; they are not part of the active `ToolNode` execution path.

Tool schema policy:

```text
Pydantic args_schema
  -> StructuredTool
  -> ToolRegistry.schemas()
  -> model prompt schema
  -> ToolNode validation and execution
```

The active runtime should not reintroduce a second tool-parameter schema beside the Pydantic `args_schema`.

Do not move these into a separate package yet. A package-level refactor would create import churn without a clear immediate benefit.

## Archived Runtime Policy

`archive/hands_on_langgraph_runtime/` exists for historical reference only.

Policy:

- do not expose it through CLI
- do not include it in default pytest collection
- do not add new production features there
- do not use it as the basis for future runtime expansion

If the archived runtime is needed for explanation or comparison, read it as reference material rather than reactivating it.

## Test Organization

Active tests should cover the active runtime and shared modules.

Archived reference tests are stored under:

```text
archive/hands_on_langgraph_runtime/reference_tests/
```

They intentionally avoid the `test_` filename prefix so default pytest does not collect them.

## Recommended Cleanup Order

1. Keep a single active CLI runtime.
2. Keep active docs aligned with `vermay_agent/langgraph_runtime/`.
3. Keep prompt and subcommand CLI logic under `vermay_agent/cli/`.
4. Keep archived runtime out of main test and CLI paths.
5. Split `langgraph_runtime/nodes.py` only when new runtime capabilities make the file materially harder to maintain.
6. Keep server/API work on explicit durable checkpoint injection rather than direct-constructor in-memory defaults.

## Do Not Do Yet

- Do not introduce another runtime selection layer.
- Do not move all shared harness modules into a new package.
- Do not expand memory, MCP, A2A, or model adapter orchestration during this cleanup pass.
- Do not treat archive code as a supported runtime.

# Code Organization Review

> Status: Completed review, 2026-08-06
> Authority: Maintenance evidence and future refactor triggers; not an active plan

## Scope

This review describes the supported product path after the August 2026
maintenance pass. It focuses on ownership boundaries rather than file size.

## Active Execution Stack

```text
A2A ingress / first-party Web APIs
  -> MainAgentCore
     -> direct Message response
     -> delegated child agent
     -> local Task process
        -> LangGraphAgentRuntime
```

There is one public lifecycle owner and one local graph execution kernel:

- `vermay/main_agent/` owns Context, Message, A2A Task, local process,
  continuation, cancellation, retry, persistence, and protocol projection.
- `vermay/langgraph_runtime/` owns graph state, model/tool iterations,
  permission and approval nodes, checkpoints, and runtime results.
- `vermay/api/` binds these capabilities to FastAPI, JSON-RPC, SSE, and
  first-party read models. It does not own an alternate lifecycle.

The CLI may invoke the LangGraph runtime directly as a development harness. It
does not create a second server-side Task lifecycle.

## Composition Boundary

`vermay/app_factory.py` assembles model adapters, tools, permission
checks, checkpoints, memory, skills, MCP context, tracing, and owned resource
cleanup. `vermay/system_prompt.py` owns the baseline system prompt shared
by runtime assembly and direct Message response.

The former `context_builder.py` mixed that active prompt with an unused legacy
project-message builder. It was replaced by the narrower `system_prompt.py`.

The model boundary has two deliberate protocols with distinct names:

- `model_clients.ModelClient` accepts project `Message` values and returns a
  provider-neutral `ModelResponse`.
- `langgraph_runtime.GraphModelClient` accepts LangChain messages and tools and
  returns a `ModelInvocation` containing an `AIMessage`.

`build_graph_model_client()` constructs the second protocol through the
provider adapters. This naming prevents raw provider clients and graph-ready
adapters from being treated as interchangeable.

## Tool Boundary

```text
Pydantic args_schema
  -> StructuredTool
  -> ToolRegistry.schemas()
  -> model-facing tool schema
  -> ToolNode validation and execution
```

`StructuredTool` is the active tool object. The removed legacy
`tool_executor.py`, `observation.py`, `ToolResult`, and `Observation` types did
not participate in this path.

## Service Shape

`create_app()` always exposes the A2A boundary and the first-party management
and diagnostic APIs required by the Web UI. The removed `enable_a2a` factory
switch and `--disable-a2a` CLI option created an untested management-only
application shape that did not match the product's A2A-native position.

## Naming Boundary

The supported project naming surface is intentionally singular:

- project: Vermay;
- Python package: `vermay`;
- CLI command: `vermay`;
- environment prefix: `VERMAY_*`;
- Web backend base URL: `VERMAY_API_BASE`.

Historical project-name aliases have been removed from packaging, imports,
configuration loading, and the Web server boundary. Protocol-level
compatibility is evaluated separately and is not part of the naming surface.

## Historical Runtime Decision

The old `archive/hands_on_langgraph_runtime/` tree was not a self-contained
reference: it imported removed product classes and could not be imported. It
also forced active modules to retain dead compatibility types. The tree and
those bridges were deleted. Historical architecture decisions remain in dated
documentation and Git history instead of executable product source.

## Intentionally Retained Structure

- Thin Next.js BFF route files remain because they are framework route
  boundaries, not duplicate lifecycle implementations.
- `MainAgentCore`, `MainAgentStore`, `langgraph_runtime/nodes.py`, and the page
  controller remain relatively large. Their responsibilities are cohesive
  enough for the current rapid-development stage; splitting them without a
  concrete ownership boundary would add indirection.
- Supported A2A compatibility bindings remain decision-gated public contracts.
- Provider-specific clients remain separate because router classification and
  task model invocation have different payload and parsing contracts.
- MCP prompt and resource providers retain separate selection and safety
  semantics. Their small truncation/deduplication helpers do not justify a
  shared abstraction yet.

## Web Rendering Boundary

Assistant Markdown rendering remains a presentation concern owned by
`AgentTranscript`. GFM parsing, math delimiter normalization, and KaTeX output
do not participate in A2A projection, runtime state, or persisted message
contracts.

The Web dependency graph intentionally resolves one KaTeX version for both the
renderer and stylesheet. The focused migration regression protects headings,
lists, code spans and blocks, tables, inline and display formulas, narrow-screen
overflow, and wide-screen transcript/composer alignment. These concerns do not
justify extracting another shared rendering or page-controller layer yet.

## Next Refactor Trigger

Do not add another runtime selection layer or repository hierarchy. Consider a
new extraction only when one of these concrete triggers appears:

1. a module gains a second lifecycle owner;
2. the same state transition is implemented in more than one layer;
3. a pure mapping or presentation concern obscures transaction or execution
   logic; or
4. focused tests cannot isolate a responsibility without constructing an
   unrelated subsystem.

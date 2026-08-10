# LangGraph Interrupt And Resume

## Scope

This document describes the current approval and model-requested-input interrupt/resume flows in the LangGraph runtime.

The implementation is centered on:

- `vermay/langgraph_runtime/runner.py`
- `vermay/langgraph_runtime/nodes.py`
- `vermay/langgraph_runtime/graph.py`

## Runtime Entry

The console entry point in `vermay/main.py` dispatches prompt execution to `vermay/cli/prompt.py`. Prompt execution builds the LangGraph runtime through `build_runtime()` in `vermay/app_factory.py`.

Default execution path:

```text
vermay "<prompt>"
  -> main()
  -> run_prompt()
  -> build_runtime()
  -> TTY: CLI run_langgraph_with_interactive_approval(...)
  -> non-TTY: LangGraphAgentRuntime.run()
```

Manual approval resume path:

```text
vermay --thread-id <id> --resume-approval true
  -> main()
  -> run_prompt()
  -> build_runtime()
  -> LangGraphAgentRuntime.resume(thread_id=<id>, approved=true)
```

The CLI runtime factory injects a SQLite checkpointer at:

```text
data/checkpoints/langgraph.sqlite
```

Direct `LangGraphAgentRuntime(...)` construction still falls back to LangGraph `InMemorySaver` when no checkpointer is provided. That keeps unit tests and embedded memory-only usage lightweight while making the CLI approval resume path durable.

The CLI closes factory-owned resources through `runtime.close()` after the run finishes.

## Initial Run

`LangGraphAgentRuntime.run()` creates a fresh graph state and delegates to `start()`:

```text
messages=[SystemMessage?, HumanMessage]
permission=None
approval=None
final_answer=None
loop_index=1
max_loops=<configured max_loops>
errors=[]
```

`start()` then assigns a `thread_id`:

```text
provided thread_id
  or generated UUID
```

The `thread_id` is passed to LangGraph through:

```python
{"configurable": {"thread_id": thread_id}}
```

This value identifies the checkpoint lineage for the run. A single thread can contain multiple checkpoints as the graph advances.

## Invoke

The default runtime uses:

```python
graph.invoke(...)
```

`invoke` returns after the graph either:

- finishes normally, or
- pauses at an interrupt.

The active CLI runtime uses `graph.invoke` and does not expose graph-stream
inspection. Earlier stream-inspection experiments are historical context, not
an alternate executable runtime in the current product path.

## Approval Decision Point

Dangerous tools are stopped by `PermissionGate`.

Graph-level routing sends approval-required decisions to `approval_required_node()`:

```text
check_permission
  -> route_permission
  -> approval_required
```

Inside `approval_required_node()`, the node calls:

```python
interrupt(payload)
```

The payload contains the approval request:

```text
kind=approval_required
loop=<current loop>
tool_call=<tool call payload>
permission=<permission decision payload>
message=<human-readable approval message>
```

At this point LangGraph pauses graph execution and stores the current graph state through the configured checkpointer.

## Model-Requested Input

The built-in `request_user_input` tool lets the model pause a task when required information is missing. `check_permission_node()` intercepts this tool before normal tool execution and routes it to `user_input_required_node()`.

The interrupt payload contains:

```text
kind=user_input_required
prompt=<human-readable question>
choices=<optional fixed choices>
inputSchema=<text input schema>
toolCallId=<original model tool-call id>
```

The public A2A continuation uses another `SendMessage` with the existing `taskId`. `MainAgentCore.submit_task_input()` validates the task and context, persists the user message, and resumes the same runtime `thread_id`. The router is not called again.

The runtime resumes with:

```python
Command(resume={"parts": parts, "metadata": metadata})
```

`user_input_required_node()` converts the submitted text into a `ToolMessage` associated with the original tool call. The graph then continues the ReAct loop from the existing checkpoint.

## `__interrupt__`

`__interrupt__` is a LangGraph runtime control field returned when execution pauses at `interrupt(...)`.

It is not part of the project-defined `AgentState`, and it should not be written as business state.

Current usage:

```python
interrupts = state.get("__interrupt__")
```

The runner reads this field in `_extract_interrupt()` to determine whether the graph paused for approval.

If present, the runner builds a `RunResult` with both the raw interrupt payload and a user-facing interrupt message.

## `RunResult.interrupt_message`

`RunResult.interrupt_message` is the CLI-facing approval message produced from LangGraph interrupt data.

It is not persisted in LangGraph checkpoint state. It is derived from the latest graph result.

Purpose:

```text
LangGraph interrupt result
  -> runtime extracts approval message
  -> runtime returns RunResult(interrupt=..., interrupt_message=...)
  -> CLI prompt displays it
  -> user approves or rejects
  -> runtime resumes the graph
```

The interactive approval wrapper uses the returned `RunResult` as control flow. If `result.interrupt_message` is present, it prompts for approval. If it is absent, the wrapper returns `result.to_output()`.

## Manual Resume

Manual resume uses:

```bash
vermay --thread-id <id> --resume-approval true
```

or:

```bash
vermay --thread-id <id> --resume-approval false
```

The runtime calls:

```python
graph.invoke(
    Command(resume={"approved": approved, "reason": reason}),
    config={"configurable": {"thread_id": thread_id}},
)
```

`Command(resume=...)` tells LangGraph to continue from the checkpointed interrupt for the specified `thread_id`.

The resume payload becomes the return value of the earlier `interrupt(...)` call inside `approval_required_node()`.

The node then stores:

```text
approval.approved
approval.reason
```

Routing continues from that result:

```text
approved=true
  -> tools
  -> record_tool_messages

approved=false
  -> reject_tool
```

## Interactive Resume

Interactive approval is the default CLI behavior when stdin is attached to a terminal. It is a CLI convenience wrapper around the same interrupt/resume mechanism.

Flow:

```text
CLI run_langgraph_with_interactive_approval(...)
  -> runtime.start()
     -> graph.invoke(...)
     -> RunResult with final answer or interrupt
  -> if interrupt exists:
       approval_provider(message, thread_id)
       runtime.resume(thread_id, approved, reason)
  -> return final answer
```

The CLI helper does not repeat an already-completed graph run. It starts the graph once through `start()`. It only resumes when the returned `RunResult` contains an interrupt message.

Current safety limit:

```text
max_approval_rounds=1
```

If the resumed graph hits another approval interrupt, the wrapper stops after the configured approval round limit instead of repeatedly prompting in the same terminal session.

When stdin is not a TTY, the CLI does not prompt. In that case `run()` returns the approval interrupt message. Because the CLI factory uses SQLite checkpointing, the same `thread_id` can later be resumed from a fresh CLI process.

## Checkpoint Storage

The CLI runtime stores LangGraph checkpoints in:

```text
data/checkpoints/langgraph.sqlite
```

This file is local runtime state and is ignored by Git.

Direct `LangGraphAgentRuntime(...)` construction without a checkpointer still uses LangGraph `InMemorySaver`. Its checkpoints are process-local.

The checkpoint contains graph state needed for resume. The terminal progress output and `RunResult.interrupt_message` are not the source of truth for resume.

The source of truth is:

```text
thread_id
checkpointer
Command(resume=...)
```

## Summary

The interrupt mechanism has two layers:

```text
LangGraph layer:
  interrupt(...)
  __interrupt__
  checkpoint
  Command(resume=...)

CLI/runtime layer:
  _extract_interrupt()
  RunResult.interrupt_message
  interactive yes/no prompt
  resume()

Main-agent/A2A layer:
  task event input_request payload
  SendMessage with existing taskId
  submit_task_input()
  resume_input()
```

`graph.invoke(...)` is suitable for approval interrupt scenarios. Graph stream inspection is not required for interrupt/resume correctness.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vermay_agent.execution_context import ExecutionContextRegistry, default_execution_context_registry
from .execution import ExecutionPolicy, execution_summary
from .results import RunResult
from vermay_agent.permission import PermissionGate
from vermay_agent.progress import ProgressReporter
from vermay_agent.runtime_context import RuntimeContextProvider
from vermay_agent.trace import TraceLogger

from .graph import build_graph
from .invocations import ToolInvocationRecorder
from .nodes import GraphComponents, GraphModelClient
from .state import AgentState, build_initial_state


@dataclass
class LangGraphAgentRuntime:
    model: GraphModelClient
    tools: list[BaseTool] = field(default_factory=list)
    permission_gate: PermissionGate | None = None
    system_prompt: str | None = None
    max_loops: int = 5
    execution_policy: ExecutionPolicy | None = None
    checkpointer: object | None = None
    progress: ProgressReporter | None = None
    trace: TraceLogger | None = None
    context_provider: RuntimeContextProvider | None = None
    tool_invocation_recorder: ToolInvocationRecorder | None = None
    execution_context_registry: ExecutionContextRegistry = field(default_factory=default_execution_context_registry)
    close_callbacks: list[Callable[[], None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.execution_policy = self.execution_policy or ExecutionPolicy.from_max_loops(self.max_loops)
        self.max_loops = self.execution_policy.max_loop_steps
        components = GraphComponents(
            model=self.model,
            tools=self.tools,
            permission_gate=self.permission_gate,
            progress=self.progress,
            trace=self.trace,
            tool_invocation_recorder=self.tool_invocation_recorder,
            execution_context_registry=self.execution_context_registry,
        )
        self.graph = build_graph(components, checkpointer=self.checkpointer or InMemorySaver())

    def run(self, user_input: str, thread_id: str | None = None) -> str:
        return self.start(user_input, thread_id=thread_id).to_output()

    def close(self) -> None:
        while self.close_callbacks:
            callback = self.close_callbacks.pop()
            callback()

    def delete_checkpoint(self, thread_id: str) -> None:
        """Discard a completed Task's private LangGraph continuation state.

        A checkpointer is optional for lightweight/test runtimes. Persistent
        SQLite checkpointers expose ``delete_thread``; in-memory backends do
        not need explicit cleanup because their lifetime ends with the runtime.
        """

        if not thread_id:
            raise ValueError("thread_id is required to delete a checkpoint")
        delete_thread = getattr(self.checkpointer, "delete_thread", None)
        if callable(delete_thread):
            delete_thread(thread_id)

    def start(
        self,
        user_input: str,
        thread_id: str | None = None,
        *,
        history_messages: list[BaseMessage] | None = None,
    ) -> RunResult:
        active_thread_id = thread_id or str(uuid4())
        self._emit_run_started(user_input)
        self._log_trace(
            "langgraph_run_started",
            {
                "thread_id": active_thread_id,
                "max_loops": self.max_loops,
                "execution_policy": self.execution_policy.to_dict(),
                "input": user_input,
            },
        )
        state = self._initial_state(
            user_input,
            history_messages=history_messages,
            runtime_thread_id=active_thread_id,
        )
        final_state = self.graph.invoke(state, config=self._config(active_thread_id))
        interrupt = self._extract_interrupt(final_state, active_thread_id)
        if interrupt is not None:
            return interrupt

        result = self._to_run_result(active_thread_id, final_state)
        self._log_trace("langgraph_run_finished", self._run_result_payload(result))
        return result

    def resume(self, thread_id: str, approved: bool, reason: str | None = None) -> RunResult:
        if not thread_id:
            raise ValueError("thread_id is required to resume an approval interrupt")

        self._log_trace(
            "langgraph_run_resumed",
            {"thread_id": thread_id, "approved": approved, "reason": reason},
        )
        return self._resume(thread_id, {"approved": approved, "reason": reason})

    def resume_input(
        self,
        thread_id: str,
        parts: list[dict],
        metadata: dict | None = None,
    ) -> RunResult:
        if not thread_id:
            raise ValueError("thread_id is required to resume an input interrupt")
        self._log_trace(
            "langgraph_run_input_submitted",
            {"thread_id": thread_id, "parts": parts, "metadata": metadata or {}},
        )
        return self._resume(thread_id, {"parts": parts, "metadata": metadata or {}})

    def _resume(self, thread_id: str, payload: dict) -> RunResult:
        final_state = self.graph.invoke(Command(resume=payload), config=self._config(thread_id))
        interrupt = self._extract_interrupt(final_state, thread_id)
        if interrupt is not None:
            return interrupt
        result = self._to_run_result(thread_id, final_state)
        self._log_trace("langgraph_run_finished", self._run_result_payload(result))
        return result

    def _to_run_result(self, thread_id: str, final_state: dict) -> RunResult:
        final_answer = final_state.get("final_answer")
        observations = [item for item in final_state.get("observations", []) if isinstance(item, dict)]
        execution = execution_summary(final_state, final_answer=final_answer)
        if final_answer is not None:
            return RunResult(
                thread_id=thread_id,
                final_answer=final_answer,
                state=final_state,
                stop_reason=str(execution["stop_reason"]),
                observations=observations,
                execution=execution,
            )

        return RunResult(
            thread_id=thread_id,
            state=final_state,
            stop_message=str(final_state.get("stop_message") or "LangGraph runtime stopped without a final answer."),
            stop_reason=str(execution["stop_reason"]),
            observations=observations,
            execution=execution,
        )

    def _initial_state(
        self,
        user_input: str,
        *,
        history_messages: list[BaseMessage] | None = None,
        runtime_thread_id: str | None = None,
    ) -> AgentState:
        context_messages = None
        if self.context_provider is not None:
            context_messages = self.context_provider.context_messages(user_input)
        return build_initial_state(
            user_input,
            system_prompt=self.system_prompt,
            context_messages=context_messages,
            history_messages=history_messages,
            max_loops=self.max_loops,
            execution_policy=self.execution_policy,
            runtime_thread_id=runtime_thread_id,
        )

    def _config(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _extract_interrupt(self, state: dict, thread_id: str) -> RunResult | None:
        interrupts = state.get("__interrupt__")
        if not interrupts:
            return None

        interrupt_value = getattr(interrupts[0], "value", interrupts[0])
        message = None
        if isinstance(interrupt_value, dict):
            message = interrupt_value.get("message")
        message = message or "Additional input is required."
        interrupt_message = f"{message}\nthread_id: {thread_id}"
        execution = execution_summary(state)
        return RunResult(
            thread_id=thread_id,
            interrupt=interrupt_value,
            interrupt_message=interrupt_message,
            state=state,
            stop_reason=str(execution["stop_reason"]),
            observations=[item for item in state.get("observations", []) if isinstance(item, dict)],
            execution=execution,
        )

    def _emit_run_started(self, user_input: str) -> None:
        if self.progress is not None:
            self.progress.event(None, "run_started", input=user_input, max_steps=self.max_loops)

    def _log_trace(self, event_type: str, payload: dict) -> None:
        if self.trace is not None:
            self.trace.log_event(event_type, payload)

    def _run_result_payload(self, result: RunResult) -> dict:
        return {
            "thread_id": result.thread_id,
            "status": result.status,
            "final_answer": result.final_answer,
            "stop_message": result.stop_message,
            "stop_reason": result.stop_reason,
            "execution": result.execution,
        }

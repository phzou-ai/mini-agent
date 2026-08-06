from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from vermay.app_factory import RuntimeFactoryConfig, build_runtime
from vermay.execution_context import ExecutionContextRegistry, default_execution_context_registry
from vermay.langgraph_runtime import LangGraphAgentRuntime
from vermay.main_agent.context import text_from_parts, to_langchain_message

from .models import MessageRecord, MessageRole, TaskStatus


@dataclass(frozen=True)
class LocalTaskRunResult:
    status: TaskStatus
    parts: list[dict] = field(default_factory=list)
    artifact_parts: list[dict] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    input_request: dict | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    execution: dict[str, Any] = field(default_factory=dict)


class LocalTaskRunner(Protocol):
    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        """Run a local task with bounded context history."""

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        """Resume a paused local task after human approval input."""

    def resume_input(
        self,
        *,
        thread_id: str,
        parts: list[dict],
        metadata: dict | None = None,
    ) -> LocalTaskRunResult:
        """Resume a paused local task with requested user input."""

    def discard_checkpoint(self, *, thread_id: str) -> None:
        """Discard a terminal Task's private runtime continuation state."""


class DirectLangGraphLocalTaskRunner:
    def __init__(
        self,
        runtime: LangGraphAgentRuntime | None = None,
        *,
        execution_context_registry: ExecutionContextRegistry | None = None,
    ) -> None:
        self.runtime = runtime or build_runtime(RuntimeFactoryConfig(show_progress=False))
        runtime_registry = getattr(self.runtime, "execution_context_registry", None)
        if runtime_registry is not None and execution_context_registry is not None:
            if runtime_registry is not execution_context_registry:
                raise ValueError(
                    "The local task runner and LangGraph runtime must share one execution context registry."
                )
        self.execution_context_registry = (
            runtime_registry or execution_context_registry or default_execution_context_registry()
        )
        self._guard = threading.RLock()
        self._idle = threading.Condition(self._guard)
        self._close_lock = threading.Lock()
        self._thread_locks: dict[str, _ThreadLockEntry] = {}
        self._closed = False

    def run(self, messages: list[MessageRecord], *, thread_id: str) -> LocalTaskRunResult:
        user_input, history_messages = _task_initial_input(messages)
        with self._acquire_thread(thread_id):
            with self.execution_context_registry.activate(thread_id):
                result = self.runtime.start(
                    user_input,
                    thread_id=thread_id,
                    history_messages=history_messages,
                )
        return _run_result_to_local_task_result(result)

    def resume(self, *, thread_id: str, approved: bool, reason: str | None = None) -> LocalTaskRunResult:
        with self._acquire_thread(thread_id):
            with self.execution_context_registry.activate(thread_id):
                result = self.runtime.resume(thread_id=thread_id, approved=approved, reason=reason)
        return _run_result_to_local_task_result(result)

    def resume_input(
        self,
        *,
        thread_id: str,
        parts: list[dict],
        metadata: dict | None = None,
    ) -> LocalTaskRunResult:
        with self._acquire_thread(thread_id):
            with self.execution_context_registry.activate(thread_id):
                result = self.runtime.resume_input(thread_id=thread_id, parts=parts, metadata=metadata)
        return _run_result_to_local_task_result(result)

    def request_cancellation(self, *, thread_id: str, reason: str | None = None) -> bool:
        """Forward a core-owned cancellation request to an active capability call."""

        return self.execution_context_registry.request_cancellation(thread_id, reason=reason)

    def discard_checkpoint(self, *, thread_id: str) -> None:
        with self._acquire_thread(thread_id):
            self.runtime.delete_checkpoint(thread_id)

    def close(self) -> None:
        with self._close_lock:
            with self._idle:
                if self._closed:
                    return
                self._closed = True
                self._idle.wait_for(lambda: not self._thread_locks)
            self.runtime.close()

    @contextmanager
    def _acquire_thread(self, thread_id: str) -> Iterator[None]:
        with self._guard:
            if self._closed:
                raise RuntimeError("local task runner is closed")
            entry = self._thread_locks.get(thread_id)
            if entry is None:
                entry = _ThreadLockEntry()
                self._thread_locks[thread_id] = entry
            entry.users += 1
        try:
            with entry.lock:
                yield
        finally:
            with self._guard:
                entry.users -= 1
                if entry.users == 0 and self._thread_locks.get(thread_id) is entry:
                    del self._thread_locks[thread_id]
                    self._idle.notify_all()


@dataclass
class _ThreadLockEntry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


def _run_result_to_local_task_result(result) -> LocalTaskRunResult:
    observations = list(getattr(result, "observations", []) or [])
    execution = dict(getattr(result, "execution", {}) or {})
    if result.status == "completed":
        parts = [{"kind": "text", "text": result.final_answer or ""}]
        return LocalTaskRunResult(
            status=TaskStatus.COMPLETED,
            parts=parts,
            observations=observations,
            execution=execution,
        )
    if result.status == "interrupted":
        input_request = result.interrupt if isinstance(result.interrupt, dict) else None
        message = (
            str(input_request.get("message"))
            if input_request and input_request.get("message")
            else result.interrupt_message or "Additional input is required."
        )
        parts = [{"kind": "text", "text": message}]
        status = (
            TaskStatus.AUTH_REQUIRED
            if input_request and input_request.get("kind") == "approval_required"
            else TaskStatus.INPUT_REQUIRED
        )
        return LocalTaskRunResult(
            status=status,
            parts=parts,
            error_code=getattr(result, "stop_reason", None) or "input_required",
            error_message=message,
            input_request=input_request,
            observations=observations,
            execution=execution,
        )
    return LocalTaskRunResult(
        status=TaskStatus.FAILED,
        error_code=getattr(result, "stop_reason", None) or result.status,
        error_message=result.stop_message or "Local task did not complete.",
        error_retryable=_execution_failure_is_retryable(execution),
        observations=observations,
        execution=execution,
    )


def _execution_failure_is_retryable(execution: dict[str, Any]) -> bool:
    residual_risks = execution.get("residual_risks")
    if not isinstance(residual_risks, list):
        return False
    return any(
        isinstance(risk, dict) and risk.get("retryable") is True
        for risk in residual_risks
    )


def _task_initial_input(messages: list[MessageRecord]):
    latest_user = next((message for message in reversed(messages) if message.role == MessageRole.USER), None)
    if latest_user is None:
        return "", []
    history = [
        to_langchain_message(message)
        for message in messages
        if message.message_id != latest_user.message_id and text_from_parts(message.parts)
    ]
    return text_from_parts(latest_user.parts), history

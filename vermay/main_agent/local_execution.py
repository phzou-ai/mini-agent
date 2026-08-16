"""Bounded single-host execution adapter for durably accepted local Tasks.

The adapter owns process-local scheduling mechanics and runner dispatch. It
does not own Task lifecycle policy: claiming and recording typed outcomes are
callbacks into ``MainAgentCore``, which remains the sole lifecycle authority.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from .models import (
    ApprovalTaskExecutionPayload,
    InitialTaskExecutionPayload,
    MessageRecord,
    QueuedTaskExecutionKind,
    QueuedTaskExecutionRecord,
    TaskRecord,
    UserInputTaskExecutionPayload,
)
from .task_runner import LocalTaskRunResult, LocalTaskRunner


logger = logging.getLogger(__name__)


class TaskSubmitter(Protocol):
    def submit(self, func: Callable[..., object], *args: object) -> object: ...


@dataclass(frozen=True)
class ClaimedLocalExecution:
    """One Core-claimed execution slice ready for runner dispatch."""

    task: TaskRecord
    command: QueuedTaskExecutionRecord
    input_messages: tuple[MessageRecord, ...] = ()
    route_decision_id: str | None = None


@dataclass(frozen=True)
class LocalExecutionSucceeded:
    task_id: str
    result: LocalTaskRunResult
    route_decision_id: str | None = None


@dataclass(frozen=True)
class LocalExecutionFailed:
    task_id: str
    error: Exception


LocalExecutionOutcome = LocalExecutionSucceeded | LocalExecutionFailed


@dataclass(frozen=True)
class LocalExecutionLifecycleCallbacks:
    """Core-owned lifecycle operations consumed by the local adapter."""

    claim: Callable[[str], ClaimedLocalExecution | None]
    current_task: Callable[[str], TaskRecord | None]
    record_outcome: Callable[[LocalExecutionOutcome], TaskRecord]


class InProcessLocalExecutionAdapter:
    """Wake, deduplicate, and dispatch local execution on one host.

    SQLite queue records remain the durable source of pending work. The sets
    below are deliberately process-local optimizations only; the Core-owned
    atomic claim is the correctness boundary.
    """

    def __init__(
        self,
        *,
        runner: LocalTaskRunner,
        lifecycle: LocalExecutionLifecycleCallbacks,
        submitter: TaskSubmitter | None = None,
    ) -> None:
        self.runner = runner
        self.lifecycle = lifecycle
        self.submitter = submitter
        self._guard = threading.RLock()
        self._active_task_ids: set[str] = set()
        self._scheduled_task_ids: set[str] = set()

    @property
    def supports_startup_recovery(self) -> bool:
        """Recovery must not block application startup on a synchronous runner."""

        return self.submitter is not None

    def wake(self, task_id: str) -> bool:
        """Wake one committed queue record at most once in this process."""

        with self._guard:
            if task_id in self._active_task_ids or task_id in self._scheduled_task_ids:
                return False
            self._scheduled_task_ids.add(task_id)

        if self.submitter is None:
            self._run_scheduled(task_id)
            return True

        try:
            self.submitter.submit(self._run_scheduled, task_id)
        except Exception:
            self._forget_scheduled(task_id)
            raise
        return True

    def is_active(self, task_id: str) -> bool:
        with self._guard:
            return task_id in self._active_task_ids

    def is_live(self, task_id: str) -> bool:
        with self._guard:
            return task_id in self._active_task_ids or task_id in self._scheduled_task_ids

    def request_cancellation(self, *, thread_id: str, reason: str | None) -> None:
        request_cancellation = getattr(self.runner, "request_cancellation", None)
        if not callable(request_cancellation):
            return
        try:
            request_cancellation(thread_id=thread_id, reason=reason)
        except Exception:
            logger.warning(
                "Unable to signal active local execution cancellation for runtime thread %s",
                thread_id,
                exc_info=True,
            )

    def discard_checkpoint(self, *, thread_id: str) -> None:
        discard = getattr(self.runner, "discard_checkpoint", None)
        if callable(discard):
            discard(thread_id=thread_id)

    def _run_scheduled(self, task_id: str) -> TaskRecord | None:
        try:
            return self._run_once(task_id)
        finally:
            self._forget_scheduled(task_id)

    def _run_once(self, task_id: str) -> TaskRecord | None:
        try:
            with _ActiveLocalExecution(self._guard, self._active_task_ids, task_id):
                claimed = self.lifecycle.claim(task_id)
                if claimed is None:
                    return self.lifecycle.current_task(task_id)
                result = self._dispatch(claimed)
                outcome: LocalExecutionOutcome = LocalExecutionSucceeded(
                    task_id=task_id,
                    result=result,
                    route_decision_id=claimed.route_decision_id,
                )
        except Exception as exc:
            outcome = LocalExecutionFailed(task_id=task_id, error=exc)
        return self.lifecycle.record_outcome(outcome)

    def _dispatch(self, claimed: ClaimedLocalExecution) -> LocalTaskRunResult:
        task = claimed.task
        command = claimed.command
        payload = command.payload

        if command.kind == QueuedTaskExecutionKind.INITIAL:
            if not isinstance(payload, InitialTaskExecutionPayload):
                raise ValueError(f"queued initial command is invalid: {task.task_id}")
            if not claimed.input_messages:
                raise RuntimeError(f"queued initial task has no input messages: {task.task_id}")
            return self.runner.run(
                list(claimed.input_messages),
                thread_id=task.runtime_thread_id,
            )

        if command.kind == QueuedTaskExecutionKind.APPROVAL:
            if not isinstance(payload, ApprovalTaskExecutionPayload):
                raise ValueError(f"queued approval command is invalid: {task.task_id}")
            return self.runner.resume(
                thread_id=task.runtime_thread_id,
                approved=payload.approved,
                reason=payload.reason,
            )

        if command.kind == QueuedTaskExecutionKind.USER_INPUT:
            if not isinstance(payload, UserInputTaskExecutionPayload):
                raise ValueError(f"queued input command is invalid: {task.task_id}")
            return self.runner.resume_input(
                thread_id=task.runtime_thread_id,
                parts=payload.materialize_parts(),
                metadata=payload.materialize_metadata(),
            )

        raise ValueError(f"unsupported queued local execution kind: {command.kind}")

    def _forget_scheduled(self, task_id: str) -> None:
        with self._guard:
            self._scheduled_task_ids.discard(task_id)


class _ActiveLocalExecution:
    def __init__(self, guard: threading.RLock, active_task_ids: set[str], task_id: str) -> None:
        self._guard = guard
        self._active_task_ids = active_task_ids
        self._task_id = task_id

    def __enter__(self) -> None:
        with self._guard:
            self._active_task_ids.add(self._task_id)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        with self._guard:
            self._active_task_ids.discard(self._task_id)

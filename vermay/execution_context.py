from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from threading import Event, RLock
from typing import Iterator


@dataclass
class ExecutionCancellation:
    """In-memory cancellation signal for one active local runtime thread.

    This is deliberately not persisted and does not own an A2A Task lifecycle.
    ``MainAgentCore`` owns the durable cancellation request; an execution
    adapter may observe this signal while its process is still active.
    """

    _requested: Event
    _guard: RLock
    _reason: str | None = None

    @classmethod
    def create(cls) -> "ExecutionCancellation":
        return cls(_requested=Event(), _guard=RLock())

    @property
    def requested(self) -> bool:
        return self._requested.is_set()

    @property
    def reason(self) -> str | None:
        with self._guard:
            return self._reason

    def request(self, reason: str | None = None) -> None:
        with self._guard:
            if reason and self._reason is None:
                self._reason = reason
            self._requested.set()


@dataclass(frozen=True)
class ExecutionContext:
    """Ephemeral execution facts supplied to a concrete capability adapter.

    The context is intentionally smaller than an Agent Process. It carries no
    public identity, queue state, or A2A status. A workspace is represented by
    an optional opaque id only; the current SSH/Kubernetes adapter has no
    shared filesystem workspace and therefore leaves it unset.
    """

    runtime_thread_id: str | None
    invocation_id: str | None = None
    deadline_monotonic: float | None = None
    cancellation: ExecutionCancellation | None = None
    workspace_id: str | None = None

    def remaining_seconds(self, *, now: float | None = None) -> float | None:
        if self.deadline_monotonic is None:
            return None
        return max(0.0, self.deadline_monotonic - (time.monotonic() if now is None else now))


_current_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    "vermay_execution_context",
    default=None,
)


def current_execution_context() -> ExecutionContext | None:
    """Return the context bound around the current capability invocation."""

    return _current_execution_context.get()


@dataclass
class _ActiveExecution:
    cancellation: ExecutionCancellation
    users: int = 0


class ExecutionContextRegistry:
    """Bridge active local runtime threads to cancellable capability calls.

    The registry has no persistence and never changes Task state. It only
    makes a durable cancellation request observable by a currently active
    local execution adapter. An idle or restarted Task therefore cannot be
    "canceled" through this runtime-only object.
    """

    def __init__(self) -> None:
        self._guard = RLock()
        self._active: dict[str, _ActiveExecution] = {}

    @contextmanager
    def activate(self, runtime_thread_id: str) -> Iterator[ExecutionContext]:
        if not runtime_thread_id:
            raise ValueError("runtime_thread_id is required to activate an execution context")
        with self._guard:
            active = self._active.get(runtime_thread_id)
            if active is None:
                active = _ActiveExecution(cancellation=ExecutionCancellation.create())
                self._active[runtime_thread_id] = active
            active.users += 1
        context = ExecutionContext(
            runtime_thread_id=runtime_thread_id,
            cancellation=active.cancellation,
        )
        token = _current_execution_context.set(context)
        try:
            yield context
        finally:
            _current_execution_context.reset(token)
            with self._guard:
                active.users -= 1
                if active.users == 0 and self._active.get(runtime_thread_id) is active:
                    del self._active[runtime_thread_id]

    def request_cancellation(self, runtime_thread_id: str, *, reason: str | None = None) -> bool:
        """Signal a currently active local execution, returning whether one exists."""

        with self._guard:
            active = self._active.get(runtime_thread_id)
            if active is None or active.users == 0:
                return False
            active.cancellation.request(reason)
            return True

    @contextmanager
    def bind_tool_context(
        self,
        *,
        runtime_thread_id: str | None,
        invocation_id: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> Iterator[ExecutionContext]:
        """Bind one ToolNode call to the active runtime control signal.

        ToolNode may run this wrapper on a different worker thread from the
        caller, so it looks up the active control by ``runtime_thread_id``
        instead of depending only on ContextVar propagation.
        """

        with self._bind_capability_context(
            runtime_thread_id=runtime_thread_id,
            invocation_id=invocation_id,
            deadline_monotonic=deadline_monotonic,
        ) as context:
            yield context

    @contextmanager
    def bind_model_context(
        self,
        *,
        runtime_thread_id: str | None,
        deadline_monotonic: float | None = None,
    ) -> Iterator[ExecutionContext]:
        """Bind one model invocation to the active execution controls.

        Model calls are not durable invocations in the R1 effect ledger, so
        they intentionally have no ``invocation_id``. They still receive the
        same task deadline and cooperative cancellation signal as tools.
        """

        with self._bind_capability_context(
            runtime_thread_id=runtime_thread_id,
            deadline_monotonic=deadline_monotonic,
        ) as context:
            yield context

    def cancellation_requested(self, runtime_thread_id: str | None) -> bool:
        """Return whether a currently active runtime has requested cancellation."""

        if not runtime_thread_id:
            return False
        with self._guard:
            active = self._active.get(runtime_thread_id)
            return bool(active is not None and active.cancellation.requested)

    @contextmanager
    def _bind_capability_context(
        self,
        *,
        runtime_thread_id: str | None,
        invocation_id: str | None = None,
        deadline_monotonic: float | None = None,
    ) -> Iterator[ExecutionContext]:
        parent = current_execution_context()
        cancellation = parent.cancellation if parent is not None else None
        workspace_id = parent.workspace_id if parent is not None else None
        if runtime_thread_id:
            with self._guard:
                active = self._active.get(runtime_thread_id)
                if active is not None:
                    cancellation = active.cancellation
        context = ExecutionContext(
            runtime_thread_id=runtime_thread_id or (parent.runtime_thread_id if parent else None),
            invocation_id=invocation_id,
            deadline_monotonic=deadline_monotonic,
            cancellation=cancellation,
            workspace_id=workspace_id,
        )
        token: Token[ExecutionContext | None] = _current_execution_context.set(context)
        try:
            yield context
        finally:
            _current_execution_context.reset(token)


_DEFAULT_EXECUTION_CONTEXT_REGISTRY = ExecutionContextRegistry()


def default_execution_context_registry() -> ExecutionContextRegistry:
    """Return the process-local registry used by the default local runner."""

    return _DEFAULT_EXECUTION_CONTEXT_REGISTRY

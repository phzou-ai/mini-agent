from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from vermay_agent.app_factory import RuntimeFactoryConfig, build_runtime
from vermay_agent.errors import (
    InvalidSessionStateError,
    SessionNotFoundError,
    TaskNotFoundError,
    error_info_from_exception,
)
from vermay_agent.langgraph_runtime import LangGraphAgentRuntime, ModelProviderConfig
from vermay_agent.langgraph_runtime.results import RunResult
from vermay_agent.mcp.selection import MCPSelectionConfig
from vermay_agent.model_selection import resolve_model_selection

from .lifecycle import LifecycleContext, LifecycleObserver, NullLifecycleObserver, lifecycle_payload
from .output_envelope import final_answer_envelope
from .session_models import TaskStatus, is_cancelable, is_resumable, is_terminal
from .session_store import SessionRecord, SessionStore, TaskArtifactRecord, TaskEventRecord, TaskRecord
from .task_contract import TaskEventType as Event
from .task_execution import TaskEventNotifier, TaskExecutionLocks, TaskExecutionService


RuntimeBuilder = Callable[[RuntimeFactoryConfig], LangGraphAgentRuntime]

FINAL_ANSWER_A2A_ARTIFACT_ID = "final_answer"


class SessionConflictError(InvalidSessionStateError):
    pass


@dataclass(frozen=True)
class AgentStartOptions:
    model: ModelProviderConfig | None = None
    max_loops: int | None = None
    mcp: MCPSelectionConfig | None = None


class AgentService:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        default_config: RuntimeFactoryConfig | None = None,
        runtime_builder: RuntimeBuilder = build_runtime,
        lifecycle_observer: LifecycleObserver | None = None,
        task_execution_service: TaskExecutionService | None = None,
    ) -> None:
        self.session_store = session_store
        self.default_config = default_config or RuntimeFactoryConfig(show_progress=False)
        self.runtime_builder = runtime_builder
        self.lifecycle_observer = lifecycle_observer or NullLifecycleObserver()
        self._default_runtime = runtime_builder(self.default_config)
        self._execution_locks = TaskExecutionLocks(conflict_error=SessionConflictError)
        self._task_event_notifier = TaskEventNotifier()
        self.task_execution_service = task_execution_service or TaskExecutionService()

    def create_session(
        self,
        *,
        session_id: str | None = None,
        context_id: str | None = None,
        title: str | None = None,
        metadata: dict | None = None,
    ) -> SessionRecord:
        active_session_id = session_id or self._new_session_id()
        if self.session_store.get_session(active_session_id) is not None:
            raise SessionConflictError(f"session already exists: {active_session_id}")
        return self.session_store.create_session(
            session_id=active_session_id,
            context_id=context_id,
            title=title,
            metadata=metadata,
        )

    def start_task(
        self,
        session_id: str,
        user_input: str,
        *,
        task_id: str | None = None,
        options: AgentStartOptions | None = None,
        wait: bool = True,
    ) -> TaskRecord:
        if self.session_store.get_session(session_id) is None:
            raise SessionNotFoundError(session_id)
        active_options = options or AgentStartOptions()
        active_task_id = task_id or self._new_task_id()
        thread_id = _thread_id_for_task(active_task_id, attempt=1)
        with self._execution_locks.acquire(active_task_id):
            if self.session_store.get_task(active_task_id) is not None:
                raise SessionConflictError(f"task already exists: {active_task_id}")
            model_payload = _model_payload(active_options.model)
            mcp_payload = _mcp_payload(active_options.mcp)
            task = self.session_store.create_task(
                task_id=active_task_id,
                session_id=session_id,
                thread_id=thread_id,
                user_input=user_input,
                model=model_payload,
                max_loops=active_options.max_loops,
                mcp=mcp_payload,
                status=TaskStatus.RUNNING if wait else TaskStatus.QUEUED,
            )
            lifecycle = self._lifecycle_context(
                session_id=session_id,
                task_id=active_task_id,
                thread_id=thread_id,
                operation="start_task",
                options=active_options,
            )
            self._record_task_event(task.task_id, Event.CREATED.value, status="created")
            self._emit_lifecycle(Event.CREATED.value, lifecycle, status="created")
            if not wait:
                self._record_task_event(task.task_id, Event.QUEUED.value, status="queued")
                self._emit_lifecycle(Event.QUEUED.value, lifecycle, status="queued")
                self.task_execution_service.submit(self._run_queued_start_task, task.task_id)
                return task

            self._record_task_event(task.task_id, Event.STARTED.value, status="running")
            self._emit_lifecycle(Event.STARTED.value, lifecycle, status="running")
            return self._execute_start_task(task, active_options, lifecycle)

    def resume_task(self, task_id: str, *, approved: bool, reason: str | None = None, wait: bool = True) -> TaskRecord:
        with self._execution_locks.acquire(task_id):
            task = self.session_store.get_task(task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            if not is_resumable(task.status):
                raise SessionConflictError(f"task is not waiting for resume: {task_id}")
            options = _options_from_task(task)
            lifecycle = self._lifecycle_context(
                session_id=task.session_id,
                task_id=task.task_id,
                thread_id=task.thread_id,
                operation="resume_task",
                options=options,
            )
            self._record_task_event(task.task_id, Event.RESUMED.value, status=task.status.value)
            self._emit_lifecycle(Event.RESUMED.value, lifecycle, status=task.status.value)
            if not wait:
                task = self.session_store.mark_task_queued(task_id)
                self._record_task_event(task.task_id, Event.QUEUED.value, status="queued")
                self._emit_lifecycle(Event.QUEUED.value, lifecycle, status="queued")
                self.task_execution_service.submit(self._run_queued_resume_task, task_id, approved, reason)
                return task

            task = self.session_store.mark_task_running(task_id)
            self._record_task_event(task.task_id, Event.STARTED.value, status="running")
            self._emit_lifecycle(Event.STARTED.value, lifecycle, status="running")
            return self._execute_resume_task(task, options, lifecycle, approved=approved, reason=reason)

    def retry_task(
        self,
        task_id: str,
        *,
        new_task_id: str | None = None,
        reason: str | None = None,
        wait: bool = True,
    ) -> TaskRecord:
        source = self.session_store.get_task(task_id)
        if source is None:
            raise TaskNotFoundError(task_id)
        if not is_terminal(source.status):
            raise SessionConflictError(f"task is not retryable: {task_id}")

        root_task_id = source.root_task_id or source.task_id
        active_task_id = new_task_id or self._new_task_id()
        with self._execution_locks.acquire(f"retry-root:{root_task_id}", blocking=True):
            with self._execution_locks.acquire(active_task_id):
                source = self.session_store.get_task(task_id)
                if source is None:
                    raise TaskNotFoundError(task_id)
                if not is_terminal(source.status):
                    raise SessionConflictError(f"task is not retryable: {task_id}")
                if self.session_store.get_task(active_task_id) is not None:
                    raise SessionConflictError(f"task already exists: {active_task_id}")

                root_task_id = source.root_task_id or source.task_id
                attempt = _next_retry_attempt(self.session_store.list_task_retries(root_task_id), source=source)
                thread_id = _thread_id_for_task(active_task_id, attempt=attempt)
                options = _options_from_task(source)
                retry = self.session_store.create_task(
                    task_id=active_task_id,
                    session_id=source.session_id,
                    thread_id=thread_id,
                    root_task_id=root_task_id,
                    retry_of_task_id=source.task_id,
                    user_input=source.input,
                    model=source.model,
                    max_loops=source.max_loops,
                    mcp=source.mcp,
                    attempt=attempt,
                    status=TaskStatus.RUNNING if wait else TaskStatus.QUEUED,
                )
                source_lifecycle = self._lifecycle_context(
                    session_id=source.session_id,
                    task_id=source.task_id,
                    thread_id=source.thread_id,
                    operation="retry_task",
                    options=options,
                )
                retry_lifecycle = self._lifecycle_context(
                    session_id=retry.session_id,
                    task_id=retry.task_id,
                    thread_id=retry.thread_id,
                    operation="retry_task",
                    options=options,
                )
                self._record_task_event(
                    source.task_id,
                    Event.RETRY_REQUESTED.value,
                    status=source.status.value,
                    payload=_retry_request_payload(reason),
                )
                self._emit_lifecycle(Event.RETRY_REQUESTED.value, source_lifecycle, status=source.status.value)
                self._record_task_event(
                    source.task_id,
                    Event.RETRIED.value,
                    status=source.status.value,
                    payload={"new_task_id": retry.task_id, "attempt": retry.attempt},
                )
                self._emit_lifecycle(Event.RETRIED.value, source_lifecycle, status=source.status.value)
                self._record_task_event(retry.task_id, Event.CREATED.value, status="created")
                self._emit_lifecycle(Event.CREATED.value, retry_lifecycle, status="created")
                if not wait:
                    self._record_task_event(retry.task_id, Event.QUEUED.value, status="queued")
                    self._emit_lifecycle(Event.QUEUED.value, retry_lifecycle, status="queued")
                    self.task_execution_service.submit(self._run_queued_start_task, retry.task_id)
                    return retry

                self._record_task_event(retry.task_id, Event.STARTED.value, status="running")
                self._emit_lifecycle(Event.STARTED.value, retry_lifecycle, status="running")
                return self._execute_start_task(retry, options, retry_lifecycle)

    def cancel_task(self, task_id: str, *, reason: str | None = None) -> TaskRecord:
        task = self.session_store.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if is_terminal(task.status):
            raise SessionConflictError(f"task is already terminal: {task_id}")
        if not is_cancelable(task.status):
            raise SessionConflictError(f"task is not cancelable: {task_id}")
        if task.status == TaskStatus.CANCEL_REQUESTED:
            return task

        options = _options_from_task(task)
        lifecycle = self._lifecycle_context(
            session_id=task.session_id,
            task_id=task.task_id,
            thread_id=task.thread_id,
            operation="cancel_task",
            options=options,
        )
        payload = _cancel_payload(reason)
        if task.status == TaskStatus.RUNNING:
            updated = self.session_store.mark_task_cancel_requested(
                task_id,
                stop_message=_cancel_stop_message(reason),
            )
            self._record_task_event(
                updated.task_id,
                Event.CANCEL_REQUESTED.value,
                status=updated.status.value,
                payload=payload,
            )
            self._emit_lifecycle(Event.CANCEL_REQUESTED.value, lifecycle, status=updated.status.value)
            return updated

        updated = self.session_store.mark_task_canceled(task_id, stop_message=_cancel_stop_message(reason))
        self._record_task_event(
            updated.task_id,
            Event.CANCEL_REQUESTED.value,
            status="cancel_requested",
            payload=payload,
        )
        self._emit_lifecycle(Event.CANCEL_REQUESTED.value, lifecycle, status="cancel_requested")
        self._record_task_event(updated.task_id, Event.CANCELLED.value, status=updated.status.value, payload=payload)
        self._emit_lifecycle(Event.CANCELLED.value, lifecycle, status=updated.status.value)
        return updated

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self.session_store.get_session(session_id)

    def get_session_by_context_id(self, context_id: str) -> SessionRecord | None:
        return self.session_store.get_session_by_context_id(context_id)

    def list_sessions(self) -> list[SessionRecord]:
        return self.session_store.list_sessions()

    def delete_session(self, session_id: str) -> None:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        active_tasks = [
            task
            for task in self.session_store.list_session_tasks(session_id)
            if not is_terminal(task.status)
        ]
        if active_tasks:
            raise SessionConflictError(
                f"session has non-terminal tasks: {session_id}",
            )

        deleted = self.session_store.delete_session(session_id)
        if not deleted:
            raise SessionNotFoundError(session_id)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.session_store.get_task(task_id)

    def list_task_artifacts(self, task_id: str) -> list[TaskArtifactRecord]:
        if self.session_store.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        return self.session_store.list_task_artifacts(task_id)

    def get_task_artifact_by_a2a_id(self, *, task_id: str, a2a_artifact_id: str) -> TaskArtifactRecord | None:
        if self.session_store.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        return self.session_store.get_task_artifact_by_a2a_id(
            task_id=task_id,
            a2a_artifact_id=a2a_artifact_id,
        )

    def list_task_events(self, task_id: str) -> list[TaskEventRecord]:
        if self.session_store.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        return self.session_store.list_task_events(task_id)

    def wait_for_task_events(
        self,
        task_id: str,
        *,
        after_event_id: int,
        timeout_seconds: float,
    ) -> list[TaskEventRecord]:
        if self.session_store.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        events = _events_after(self.session_store.list_task_events(task_id), after_event_id)
        if events:
            return events
        self._task_event_notifier.wait(task_id, after_event_id=after_event_id, timeout_seconds=timeout_seconds)
        return _events_after(self.session_store.list_task_events(task_id), after_event_id)

    def close(self) -> None:
        self.task_execution_service.shutdown()
        self._default_runtime.close()

    def _new_session_id(self) -> str:
        while True:
            session_id = str(uuid4())
            if self.session_store.get_session(session_id) is None:
                return session_id

    def _new_task_id(self) -> str:
        while True:
            task_id = str(uuid4())
            if self.session_store.get_task(task_id) is None:
                return task_id

    def _runtime_for(self, options: AgentStartOptions) -> LangGraphAgentRuntime:
        if options.model is None and options.max_loops is None and options.mcp is None:
            return self._default_runtime
        mcp_servers = self.default_config.mcp_servers
        mcp_prompts = self.default_config.mcp_prompts
        mcp_resources = self.default_config.mcp_resources
        if options.mcp is not None:
            mcp_servers = options.mcp.servers
            mcp_prompts = options.mcp.to_runtime_prompts()
            mcp_resources = options.mcp.to_runtime_resources()
        config = RuntimeFactoryConfig(
            model=options.model or self.default_config.model,
            model_config_path=self.default_config.model_config_path,
            max_loops=options.max_loops or self.default_config.max_loops,
            show_progress=False,
            trace_path=self.default_config.trace_path,
            checkpoint_path=self.default_config.checkpoint_path,
            agent_store_path=self.default_config.agent_store_path,
            skills_path=self.default_config.skills_path,
            skill_proposals_path=self.default_config.skill_proposals_path,
            mcp_config_path=self.default_config.mcp_config_path,
            mcp_servers=mcp_servers,
            mcp_prompts=mcp_prompts,
            mcp_resources=mcp_resources,
        )
        return self.runtime_builder(config)

    def _run_queued_start_task(self, task_id: str) -> None:
        with self._execution_locks.acquire(task_id, blocking=True):
            task = self.session_store.get_task(task_id)
            if task is None:
                return
            if task.status == TaskStatus.CANCELED:
                return
            if task.status == TaskStatus.CANCEL_REQUESTED:
                self._mark_task_canceled_after_safe_boundary(task)
                return
            options = _options_from_task(task)
            lifecycle = self._lifecycle_context(
                session_id=task.session_id,
                task_id=task.task_id,
                thread_id=task.thread_id,
                operation=_start_operation_for_task(task),
                options=options,
            )
            task = self.session_store.mark_task_running(task_id)
            self._record_task_event(task.task_id, Event.STARTED.value, status="running")
            self._emit_lifecycle(Event.STARTED.value, lifecycle, status="running")
            self._execute_start_task(task, options, lifecycle)

    def _run_queued_resume_task(self, task_id: str, approved: bool, reason: str | None) -> None:
        with self._execution_locks.acquire(task_id, blocking=True):
            task = self.session_store.get_task(task_id)
            if task is None:
                return
            if task.status == TaskStatus.CANCELED:
                return
            if task.status == TaskStatus.CANCEL_REQUESTED:
                self._mark_task_canceled_after_safe_boundary(task)
                return
            options = _options_from_task(task)
            lifecycle = self._lifecycle_context(
                session_id=task.session_id,
                task_id=task.task_id,
                thread_id=task.thread_id,
                operation="resume_task",
                options=options,
            )
            task = self.session_store.mark_task_running(task_id)
            self._record_task_event(task.task_id, Event.STARTED.value, status="running")
            self._emit_lifecycle(Event.STARTED.value, lifecycle, status="running")
            self._execute_resume_task(task, options, lifecycle, approved=approved, reason=reason)

    def _execute_start_task(
        self,
        task: TaskRecord,
        options: AgentStartOptions,
        lifecycle: LifecycleContext,
    ) -> TaskRecord:
        runtime = None
        try:
            runtime = self._runtime_for(options)
            result = runtime.start(task.input, thread_id=task.thread_id)
            return self._save_task_runtime_result(task, result, options, lifecycle)
        except Exception as exc:
            return self._mark_task_runtime_failed(task, lifecycle, exc)
        finally:
            if runtime is not None and runtime is not self._default_runtime:
                runtime.close()

    def _execute_resume_task(
        self,
        task: TaskRecord,
        options: AgentStartOptions,
        lifecycle: LifecycleContext,
        *,
        approved: bool,
        reason: str | None,
    ) -> TaskRecord:
        runtime = None
        try:
            runtime = self._runtime_for(options)
            result = runtime.resume(thread_id=task.thread_id, approved=approved, reason=reason)
            return self._save_task_runtime_result(task, result, options, lifecycle)
        except Exception as exc:
            return self._mark_task_runtime_failed(task, lifecycle, exc)
        finally:
            if runtime is not None and runtime is not self._default_runtime:
                runtime.close()

    def _save_task_runtime_result(
        self,
        task: TaskRecord,
        result: RunResult,
        options: AgentStartOptions,
        lifecycle: LifecycleContext,
    ) -> TaskRecord:
        _ensure_result_thread_id(result, task.thread_id)
        current = self.session_store.get_task(task.task_id)
        if current is not None and current.status in {TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED}:
            return self._mark_task_canceled_after_safe_boundary(current, lifecycle=lifecycle)
        updated = self.session_store.save_task_result(
            task_id=task.task_id,
            result=result,
            model=_model_payload(options.model),
            max_loops=options.max_loops,
            mcp=_mcp_payload(options.mcp),
        )
        if updated.status == TaskStatus.COMPLETED and updated.final_answer is not None:
            artifact_event_type, artifact_payload = self._upsert_final_answer_artifact(updated)
            self._record_task_event(updated.task_id, artifact_event_type, status=updated.status.value, payload=artifact_payload)
        event_type = _event_type_for_result(result)
        self._record_task_event(updated.task_id, event_type, status=result.status)
        self._emit_lifecycle(event_type, lifecycle, status=result.status)
        return updated

    def _upsert_final_answer_artifact(self, task: TaskRecord) -> tuple[str, dict]:
        existing = self.session_store.get_task_artifact_by_a2a_id(
            task_id=task.task_id,
            a2a_artifact_id=FINAL_ANSWER_A2A_ARTIFACT_ID,
        )
        artifact = self.session_store.upsert_task_artifact(
            artifact_id=_final_answer_artifact_id(task.task_id),
            task_id=task.task_id,
            a2a_artifact_id=FINAL_ANSWER_A2A_ARTIFACT_ID,
            name="Final answer",
            description="Final text answer returned by the agent.",
            parts=[{"text": task.final_answer, "mediaType": "text/plain"}],
            metadata=final_answer_envelope().to_metadata(),
            extensions=[],
        )
        event_type = Event.ARTIFACT_UPDATED.value if existing is not None else Event.ARTIFACT_CREATED.value
        return event_type, _artifact_event_payload(artifact.artifact_id, artifact.a2a_artifact_id, artifact.name)

    def _mark_task_runtime_failed(
        self,
        task: TaskRecord,
        lifecycle: LifecycleContext,
        exc: Exception,
    ) -> TaskRecord:
        current = self.session_store.get_task(task.task_id)
        if current is not None and current.status in {TaskStatus.CANCEL_REQUESTED, TaskStatus.CANCELED}:
            return self._mark_task_canceled_after_safe_boundary(current, lifecycle=lifecycle)
        error = error_info_from_exception(exc)
        updated = self.session_store.mark_task_failed(
            task_id=task.task_id,
            error_code=error.code.value,
            error_message=error.message,
        )
        failure_payload = {"error_code": error.code.value}
        if hasattr(exc, "retryable"):
            failure_payload["retryable"] = bool(exc.retryable)
        self._record_task_event(
            updated.task_id,
            Event.FAILED.value,
            status="failed",
            payload=failure_payload,
        )
        self._emit_lifecycle(Event.FAILED.value, lifecycle, status="failed", **failure_payload)
        raise exc

    def _mark_task_canceled_after_safe_boundary(
        self,
        task: TaskRecord,
        lifecycle: LifecycleContext | None = None,
    ) -> TaskRecord:
        if task.status == TaskStatus.CANCELED:
            return task
        updated = self.session_store.mark_task_canceled(
            task.task_id,
            stop_message=task.stop_message or _cancel_stop_message(None),
        )
        self._record_task_event(updated.task_id, Event.CANCELLED.value, status=updated.status.value)
        if lifecycle is not None:
            self._emit_lifecycle(Event.CANCELLED.value, lifecycle, status=updated.status.value)
        return updated

    def _lifecycle_context(
        self,
        *,
        session_id: str,
        task_id: str,
        thread_id: str,
        operation: str,
        options: AgentStartOptions,
    ) -> LifecycleContext:
        model = options.model or self.default_config.model
        if model is None:
            model = resolve_model_selection(config_path=self.default_config.model_config_path)
        max_loops = options.max_loops if options.max_loops is not None else self.default_config.max_loops
        return LifecycleContext.create(
            session_id=session_id,
            task_id=task_id,
            thread_id=thread_id,
            operation=operation,
            model_provider=model.provider,
            max_loops=max_loops,
            mcp_selected=self._mcp_selected(options),
        )

    def _mcp_selected(self, options: AgentStartOptions) -> bool:
        if options.mcp is not None:
            return bool(options.mcp.servers or options.mcp.prompts or options.mcp.resources)
        return bool(
            self.default_config.mcp_servers
            or self.default_config.mcp_prompts
            or self.default_config.mcp_resources
        )

    def _record_task_event(
        self,
        task_id: str,
        event_type: str,
        *,
        status: str | None,
        payload: dict | None = None,
    ) -> TaskEventRecord:
        event = self.session_store.append_task_event(
            task_id=task_id,
            event_type=event_type,
            status=status,
            payload=payload,
        )
        self._task_event_notifier.notify(task_id, event.event_id)
        return event

    def _emit_lifecycle(
        self,
        event_type: str,
        context: LifecycleContext,
        *,
        status: str,
        error_code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        try:
            self.lifecycle_observer.emit(
                event_type,
                lifecycle_payload(context, status=status, error_code=error_code, retryable=retryable),
            )
        except Exception:
            return None


def _model_payload(model: ModelProviderConfig | None) -> dict | None:
    if model is None:
        return None
    return {"provider": model.provider, "options": dict(model.options)}


def _mcp_payload(mcp: MCPSelectionConfig | None) -> dict | None:
    if mcp is None:
        return None
    return mcp.to_payload()


def _cancel_payload(reason: str | None) -> dict:
    if reason is None:
        return {}
    return {"reason": reason}


def _cancel_stop_message(reason: str | None) -> str:
    if reason:
        return f"Task canceled: {reason}"
    return "Task canceled."


def _retry_request_payload(reason: str | None) -> dict:
    if reason is None:
        return {}
    return {"reason": reason}


def _final_answer_artifact_id(task_id: str) -> str:
    return f"{task_id}:final_answer"


def _artifact_event_payload(artifact_id: str, a2a_artifact_id: str, name: str | None) -> dict:
    return {
        "artifact_id": artifact_id,
        "a2a_artifact_id": a2a_artifact_id,
        "name": name,
    }


def _next_retry_attempt(tasks: list[TaskRecord], *, source: TaskRecord) -> int:
    if not tasks:
        return source.attempt + 1
    return max(task.attempt for task in tasks) + 1


def _start_operation_for_task(task: TaskRecord) -> str:
    if task.retry_of_task_id is not None:
        return "retry_task"
    return "start_task"


def _options_from_task(task: TaskRecord) -> AgentStartOptions:
    model = None
    if task.model is not None:
        model = ModelProviderConfig(
            provider=str(task.model.get("provider") or "ollama"),
            options=dict(task.model.get("options") or {}),
        )
    return AgentStartOptions(
        model=model,
        max_loops=task.max_loops,
        mcp=MCPSelectionConfig.from_payload(task.mcp),
    )


def _event_type_for_result(result: RunResult) -> str:
    if result.status == "completed":
        return Event.COMPLETED.value
    if result.status == "interrupted":
        return Event.INTERRUPTED.value
    return Event.STOPPED.value


def _ensure_result_thread_id(result: RunResult, expected_thread_id: str) -> None:
    if result.thread_id != expected_thread_id:
        raise RuntimeError(
            f"runtime returned mismatched thread_id: expected {expected_thread_id}, got {result.thread_id}"
        )


def _thread_id_for_task(task_id: str, *, attempt: int) -> str:
    return f"task:{task_id}:attempt:{attempt}"


def _events_after(events: list[TaskEventRecord], event_id: int) -> list[TaskEventRecord]:
    return [event for event in events if event.event_id > event_id]

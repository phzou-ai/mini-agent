"""Typed application commands and outcomes for the main-agent lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import MainAgentRequest, MainAgentResult, MainAgentStreamResult, TaskRecord
from .remote_agent import RemoteAgentTaskSnapshot
from .task_runner import LocalTaskRunResult


@dataclass(frozen=True)
class AdmitMessageCommand:
    request: MainAgentRequest


@dataclass(frozen=True)
class CancelTaskCommand:
    task_id: str
    reason: str | None = None


@dataclass(frozen=True)
class ResolveApprovalCommand:
    task_id: str
    approved: bool
    reason: str | None = None


@dataclass(frozen=True)
class SubmitTaskInputCommand:
    task_id: str
    request: MainAgentRequest


@dataclass(frozen=True)
class RetryTaskCommand:
    task_id: str


@dataclass(frozen=True)
class ReconcileStartupCommand:
    pass


@dataclass(frozen=True)
class RecordLocalTaskResultCommand:
    task_id: str
    result: LocalTaskRunResult
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecordLocalTaskFailureCommand:
    task_id: str
    error: Exception


@dataclass(frozen=True)
class RecordRuntimeRecoveryFailureCommand:
    task_id: str
    error_code: str
    error_message: str


@dataclass(frozen=True)
class RecordTaskCancellationCommand:
    task_id: str


@dataclass(frozen=True)
class RecordRemoteTaskSnapshotCommand:
    task_id: str
    snapshot: RemoteAgentTaskSnapshot


MainAgentCommand = (
    AdmitMessageCommand
    | CancelTaskCommand
    | ResolveApprovalCommand
    | SubmitTaskInputCommand
    | RetryTaskCommand
    | ReconcileStartupCommand
    | RecordLocalTaskResultCommand
    | RecordLocalTaskFailureCommand
    | RecordRuntimeRecoveryFailureCommand
    | RecordTaskCancellationCommand
    | RecordRemoteTaskSnapshotCommand
)


@dataclass(frozen=True)
class MessageCommandOutcome:
    result: MainAgentResult


@dataclass(frozen=True)
class MessageStreamOutcome:
    result: MainAgentStreamResult


@dataclass(frozen=True)
class TaskCommandOutcome:
    task: TaskRecord


@dataclass(frozen=True)
class StartupReconciliationOutcome:
    """Inspectable outcome of one conservative in-process worker recovery pass."""

    scheduled_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()
    retained_task_ids: tuple[str, ...] = ()
    failed_message_ids: tuple[str, ...] = ()


MainAgentCommandOutcome = (
    MessageCommandOutcome
    | TaskCommandOutcome
    | StartupReconciliationOutcome
)

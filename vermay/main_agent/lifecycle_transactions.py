from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Generic, Protocol, TypeVar, overload


CommittedT = TypeVar("CommittedT")
PostCommitT = TypeVar("PostCommitT")


class LifecycleTransactionOwner(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...


class LifecyclePostCommitActionKind(str, Enum):
    """The bounded process-local effects allowed after a lifecycle commit."""

    START_LOCAL_EXECUTION = "start_local_execution"
    SIGNAL_LOCAL_CANCELLATION = "signal_local_cancellation"
    SEND_REMOTE_MESSAGE = "send_remote_message"


@dataclass(frozen=True)
class LifecyclePostCommitAction(Generic[CommittedT, PostCommitT]):
    kind: LifecyclePostCommitActionKind
    callback: Callable[[CommittedT], PostCommitT] = field(repr=False, compare=False)

    def execute(self, committed: CommittedT) -> PostCommitT:
        return self.callback(committed)


@dataclass(frozen=True)
class LifecycleTransactionOutcome(Generic[CommittedT, PostCommitT]):
    committed: CommittedT
    post_commit_result: PostCommitT | None = None


class LifecycleTransactionRunner:
    """Commit one lifecycle mutation before its process-local side effect.

    This is intentionally not a repository, event bus, or generic unit of
    work. It only makes the current single-host ordering contract executable:
    durable lifecycle writes commit first, then one named local effect may run.
    A post-commit failure never rolls back already committed lifecycle facts.
    """

    def __init__(self, owner: LifecycleTransactionOwner) -> None:
        self._owner = owner

    @overload
    def execute(
        self,
        workflow: Callable[[], CommittedT],
        *,
        post_commit: None = None,
    ) -> LifecycleTransactionOutcome[CommittedT, None]: ...

    @overload
    def execute(
        self,
        workflow: Callable[[], CommittedT],
        *,
        post_commit: LifecyclePostCommitAction[CommittedT, PostCommitT],
    ) -> LifecycleTransactionOutcome[CommittedT, PostCommitT]: ...

    def execute(
        self,
        workflow: Callable[[], CommittedT],
        *,
        post_commit: LifecyclePostCommitAction[CommittedT, PostCommitT] | None = None,
    ) -> LifecycleTransactionOutcome[CommittedT, PostCommitT | None]:
        with self._owner.transaction():
            committed = workflow()

        if post_commit is None:
            return LifecycleTransactionOutcome(committed=committed)
        return LifecycleTransactionOutcome(
            committed=committed,
            post_commit_result=post_commit.execute(committed),
        )

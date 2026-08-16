from __future__ import annotations

import threading
from typing import Protocol


class TaskEventNotifier(Protocol):
    """Disposable wake-up hint for subscribers of durable Task events."""

    def observe(self, task_id: str) -> int: ...

    def wait_for_change(
        self,
        task_id: str,
        *,
        observed_version: int,
        timeout_seconds: float,
    ) -> int: ...

    def notify(self, task_id: str) -> None: ...


class InProcessTaskEventNotifier:
    """Best-effort process-local notification; never an event authority."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._versions: dict[str, int] = {}

    def observe(self, task_id: str) -> int:
        with self._condition:
            return self._versions.get(task_id, 0)

    def wait_for_change(
        self,
        task_id: str,
        *,
        observed_version: int,
        timeout_seconds: float,
    ) -> int:
        with self._condition:
            if (
                timeout_seconds > 0
                and self._versions.get(task_id, 0) <= observed_version
            ):
                self._condition.wait(timeout=timeout_seconds)
            return self._versions.get(task_id, 0)

    def notify(self, task_id: str) -> None:
        with self._condition:
            self._versions[task_id] = self._versions.get(task_id, 0) + 1
            self._condition.notify_all()

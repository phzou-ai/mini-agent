from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable


class InProcessTaskExecutor:
    """Small application-owned submitter for MainAgentCore worker slices."""

    def __init__(
        self,
        *,
        max_workers: int = 4,
        thread_name_prefix: str = "vermay-main-agent-task",
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=thread_name_prefix,
        )

    def submit(self, func: Callable[..., object], *args: object) -> Future:
        return self._executor.submit(func, *args)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)

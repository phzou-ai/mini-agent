from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from vermay_agent.main_agent.task_runner import DirectLangGraphLocalTaskRunner


class ConcurrentRuntime:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active_by_thread: dict[str, int] = {}
        self.started_count = 0
        self.two_started = threading.Event()
        self.release = threading.Event()
        self.same_thread_overlap = False
        self.closed = False

    def start(self, user_input: str, thread_id: str, *, history_messages=None):
        with self._guard:
            active = self.active_by_thread.get(thread_id, 0) + 1
            self.active_by_thread[thread_id] = active
            self.same_thread_overlap = self.same_thread_overlap or active > 1
            self.started_count += 1
            if self.started_count >= 2:
                self.two_started.set()
        assert self.release.wait(timeout=2)
        with self._guard:
            self.active_by_thread[thread_id] -= 1
        return SimpleNamespace(status="completed", final_answer=user_input)

    def resume(self, thread_id: str, approved: bool, reason: str | None = None):
        return self.start(reason or "resumed", thread_id)

    def close(self) -> None:
        self.closed = True


def test_direct_task_runner_runs_different_threads_concurrently():
    runtime = ConcurrentRuntime()
    runner = DirectLangGraphLocalTaskRunner(runtime)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runner.run, [], thread_id="thread-1")
        second = executor.submit(runner.run, [], thread_id="thread-2")
        assert runtime.two_started.wait(timeout=2)
        runtime.release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert runtime.same_thread_overlap is False
    runner.close()


def test_direct_task_runner_serializes_the_same_thread():
    runtime = ConcurrentRuntime()
    runner = DirectLangGraphLocalTaskRunner(runtime)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runner.run, [], thread_id="thread-1")
        second = executor.submit(runner.run, [], thread_id="thread-1")
        deadline = time.monotonic() + 1
        queued_users = 0
        while time.monotonic() < deadline:
            with runner._guard:
                entry = runner._thread_locks.get("thread-1")
                queued_users = entry.users if entry is not None else 0
            if queued_users == 2:
                break
            time.sleep(0.01)
        assert queued_users == 2
        assert runtime.started_count == 1
        runtime.release.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert runtime.same_thread_overlap is False
    runner.close()


def test_direct_task_runner_close_waits_for_active_execution():
    runtime = ConcurrentRuntime()
    runner = DirectLangGraphLocalTaskRunner(runtime)
    execution = threading.Thread(target=runner.run, args=([],), kwargs={"thread_id": "thread-1"})
    execution.start()
    deadline = time.monotonic() + 1
    while runtime.started_count < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runtime.started_count == 1

    closed = threading.Event()

    def close_runner() -> None:
        runner.close()
        closed.set()

    closer = threading.Thread(target=close_runner)
    closer.start()
    assert closed.wait(timeout=0.05) is False
    assert runtime.closed is False

    runtime.release.set()
    execution.join(timeout=2)
    closer.join(timeout=2)

    assert not execution.is_alive()
    assert not closer.is_alive()
    assert runtime.closed is True

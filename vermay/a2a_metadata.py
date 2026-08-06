from __future__ import annotations

from typing import Any

LOCAL_THREAD_ID_KEY = "localThreadId"
RUNTIME_THREAD_ID_KEY = "runtimeThreadId"


def thread_metadata(thread_id: str | None, *, include_runtime_alias: bool = False) -> dict[str, Any]:
    if thread_id is None:
        return {}
    metadata: dict[str, Any] = {LOCAL_THREAD_ID_KEY: thread_id}
    if include_runtime_alias:
        metadata[RUNTIME_THREAD_ID_KEY] = thread_id
    return metadata

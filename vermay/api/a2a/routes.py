from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from vermay.a2a_protocol import (
    MESSAGE_SEND_METHOD,
    MESSAGE_STREAM_METHOD,
    TASK_CANCEL_METHOD,
    TASK_GET_METHOD,
    TASK_RESUBSCRIBE_METHOD,
    TASK_RESUME_METHOD,
)
from vermay.errors import TaskEventProjectionError

from .adapter import A2AAdapter
from .rpc import (
    jsonrpc_error_payload as _jsonrpc_error_payload,
    jsonrpc_error_response as _jsonrpc_error_response,
    jsonrpc_protocol_error_response as _jsonrpc_protocol_error_response,
    jsonrpc_success_payload as _jsonrpc_success_payload,
    parse_rpc_request as _parse_rpc_request,
    rpc_after_event_id as _rpc_after_event_id,
    rpc_params as _rpc_params,
    rpc_task_id as _rpc_task_id,
)


# A task subscription must close for both terminal tasks and tasks waiting for
# user or authentication input. These are A2A transport states, not a legacy
# session-projection concern.
_STREAM_END_STATES = frozenset(
    {
        "completed",
        "failed",
        "canceled",
        "rejected",
        "input-required",
        "auth-required",
    }
)


def create_a2a_router(adapter: A2AAdapter) -> APIRouter:
    router = APIRouter()
    router.state = {"adapter": adapter}

    @router.get("/.well-known/agent-card.json")
    def get_agent_card() -> dict[str, Any]:
        return adapter.get_agent_card()

    @router.post("/rpc", response_model=None)
    async def rpc(request: Request) -> dict[str, Any] | JSONResponse | StreamingResponse:
        rpc_request = await _parse_rpc_request(request)
        if rpc_request.error is not None:
            return rpc_request.error
        payload = rpc_request.payload
        assert payload is not None
        return _dispatch_rpc_request(adapter=adapter, payload=payload, request=request)

    return router


def _dispatch_rpc_request(
    *,
    adapter: A2AAdapter,
    payload: dict[str, Any],
    request: Request,
) -> dict[str, Any] | JSONResponse | StreamingResponse:
    request_id = payload.get("id")
    method = payload.get("method")
    try:
        if method == MESSAGE_SEND_METHOD:
            return adapter.send_message_payload(payload)
        if method == TASK_GET_METHOD:
            params = _rpc_params(payload)
            task_id = _rpc_task_id(params)
            return _jsonrpc_success_payload(request_id, adapter.get_task(task_id))
        if method == TASK_CANCEL_METHOD:
            params = _rpc_params(payload)
            task_id = _rpc_task_id(params)
            reason = params.get("reason")
            if reason is not None and not isinstance(reason, str):
                return _jsonrpc_protocol_error_response(
                    request_id,
                    code=-32602,
                    message="JSON-RPC params.reason must be a string.",
                )
            return _jsonrpc_success_payload(request_id, adapter.cancel_task(task_id, reason=reason))
        if method == TASK_RESUME_METHOD:
            params = _rpc_params(payload)
            task_id = _rpc_task_id(params)
            approved = params.get("approved")
            if not isinstance(approved, bool):
                return _jsonrpc_protocol_error_response(
                    request_id,
                    code=-32602,
                    message="JSON-RPC params.approved must be a boolean.",
                )
            reason = params.get("reason")
            if reason is not None and not isinstance(reason, str):
                return _jsonrpc_protocol_error_response(
                    request_id,
                    code=-32602,
                    message="JSON-RPC params.reason must be a string.",
                )
            return _jsonrpc_success_payload(
                request_id,
                adapter.resume_task(task_id, approved=approved, reason=reason),
            )
        if method == MESSAGE_STREAM_METHOD:
            return _a2a_sse_response(_rpc_stream_message_events(adapter, payload))
        if method == TASK_RESUBSCRIBE_METHOD:
            return _a2a_sse_response(_rpc_subscribe_task_events(adapter, payload, request))
        return _jsonrpc_protocol_error_response(
            request_id,
            code=-32601,
            message="JSON-RPC method not found.",
            local_code="method_not_found",
        )
    except ValueError as exc:
        return _jsonrpc_protocol_error_response(request_id, code=-32602, message=str(exc))
    except Exception as exc:
        return _jsonrpc_error_response(request_id, exc)


def _a2a_sse_response(event_stream: Any) -> StreamingResponse:
    return StreamingResponse(
        event_stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _rpc_stream_message_events(adapter: A2AAdapter, payload: dict[str, Any]):
    request_id = payload.get("id")
    reached_end_state = False
    try:
        async for event in _stream_message_result_events(
            adapter,
            {**payload, "method": MESSAGE_SEND_METHOD},
            task_event_request_id=request_id,
            wrap_task_events=True,
        ):
            reached_end_state = reached_end_state or _is_stream_end_state(
                _task_state(event)
            )
            yield _format_a2a_sse_event(event)
    except Exception as exc:
        # Once an A2A task's terminal or input-required state has reached the
        # client, a trailing replay/transport failure must not overwrite that
        # durable outcome with a JSON-RPC error event.
        if reached_end_state and not isinstance(exc, TaskEventProjectionError):
            return
        yield _format_a2a_sse_event(_jsonrpc_error_payload(request_id, exc))


async def _stream_message_result_events(
    adapter: A2AAdapter,
    payload: dict[str, Any],
    *,
    task_event_request_id: Any | None = None,
    wrap_task_events: bool = False,
):
    async for event in _iterate_blocking(adapter.stream_message_payload(payload)):
        yield event
        task_id = _task_id_from_message_result(event)
        if not task_id:
            continue
        last_event_id = 0
        while True:
            batch = await asyncio.to_thread(
                adapter.wait_for_task_events,
                task_id,
                after_event_id=last_event_id,
                timeout_seconds=1.0,
            )
            last_event_id = max(last_event_id, batch.last_event_id)
            for task_event in batch.events:
                if wrap_task_events:
                    yield _jsonrpc_success_payload(task_event_request_id, task_event)
                else:
                    yield task_event

            task = adapter.get_task(task_id)
            if not _is_stream_end_state(_task_state(task)):
                continue

            trailing_batch = await asyncio.to_thread(
                adapter.wait_for_task_events,
                task_id,
                after_event_id=last_event_id,
                timeout_seconds=0.0,
            )
            for task_event in trailing_batch.events:
                if wrap_task_events:
                    yield _jsonrpc_success_payload(task_event_request_id, task_event)
                else:
                    yield task_event
            break


async def _iterate_blocking(iterator):
    sentinel = object()
    iterable = iter(iterator)
    while True:
        item = await asyncio.to_thread(next, iterable, sentinel)
        if item is sentinel:
            break
        yield item


async def _rpc_subscribe_task_events(adapter: A2AAdapter, payload: dict[str, Any], request: Request):
    request_id = payload.get("id")
    try:
        params = _rpc_params(payload)
        task_id = _rpc_task_id(params)
        after_event_id = _rpc_after_event_id(params)
        adapter.get_task(task_id)
    except Exception as exc:
        yield _format_a2a_sse_event(_jsonrpc_error_payload(request_id, exc))
        return

    last_event_id = after_event_id
    try:
        while True:
            if await request.is_disconnected():
                break
            batch = await asyncio.to_thread(
                adapter.wait_for_task_events,
                task_id,
                after_event_id=last_event_id,
                timeout_seconds=1.0,
            )
            last_event_id = max(last_event_id, batch.last_event_id)
            for event in batch.events:
                yield _format_a2a_sse_event(_jsonrpc_success_payload(request_id, event))
            task = adapter.get_task(task_id)
            state = _task_state(task)
            if _is_stream_end_state(state):
                trailing_batch = await asyncio.to_thread(
                    adapter.wait_for_task_events,
                    task_id,
                    after_event_id=last_event_id,
                    timeout_seconds=0.0,
                )
                last_event_id = max(last_event_id, trailing_batch.last_event_id)
                for event in trailing_batch.events:
                    yield _format_a2a_sse_event(_jsonrpc_success_payload(request_id, event))
                break
    except Exception as exc:
        yield _format_a2a_sse_event(_jsonrpc_error_payload(request_id, exc))


def _format_a2a_sse_event(event: dict[str, Any]) -> str:
    event_type = _sse_event_type(event)
    event_id = _event_id(event)
    data = json.dumps(event, ensure_ascii=False, sort_keys=True)
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event_type}\ndata: {data}\n\n"


def _event_id(event: dict[str, Any]) -> int | None:
    if event.get("jsonrpc") == "2.0":
        result = event.get("result")
        if isinstance(result, dict):
            metadata = result.get("metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("localEventId"), int):
                return metadata["localEventId"]
    metadata = event.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("localEventId"), int):
        return metadata["localEventId"]
    body = next(iter(event.values()), None)
    if not isinstance(body, dict):
        return None
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        return None
    event_id = metadata.get("localEventId")
    if isinstance(event_id, int):
        return event_id
    return None


def _sse_event_type(event: dict[str, Any]) -> str:
    if event.get("jsonrpc") == "2.0":
        if isinstance(event.get("error"), dict):
            return "error"
        result = event.get("result")
        if isinstance(result, dict) and isinstance(result.get("kind"), str):
            return result["kind"]
    if isinstance(event.get("kind"), str):
        return event["kind"]
    return next(iter(event))


def _task_id_from_message_result(event: dict[str, Any]) -> str | None:
    if event.get("jsonrpc") == "2.0":
        result = event.get("result")
        if isinstance(result, dict) and result.get("kind") == "task" and isinstance(result.get("id"), str):
            return result["id"]
        return None
    if event.get("kind") == "task" and isinstance(event.get("id"), str):
        return event["id"]
    return None


def _task_state(task: dict[str, Any]) -> Any:
    if task.get("jsonrpc") == "2.0":
        result = task.get("result")
        if isinstance(result, dict):
            return result.get("status", {}).get("state")
    if task.get("kind") == "task":
        status = task.get("status")
        if isinstance(status, dict):
            return status.get("state")
    return None


def _is_stream_end_state(state: Any) -> bool:
    return isinstance(state, str) and state.strip().lower() in _STREAM_END_STATES

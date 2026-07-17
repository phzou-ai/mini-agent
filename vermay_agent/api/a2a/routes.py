from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from vermay_agent.errors import error_info_from_exception, public_error_payload

from .adapter import A2AAdapter
from .projection import is_terminal_a2a_state
from .rpc import (
    is_jsonrpc_request as _is_jsonrpc_request,
    jsonrpc_error_payload as _jsonrpc_error_payload,
    jsonrpc_error_response as _jsonrpc_error_response,
    jsonrpc_protocol_error_response as _jsonrpc_protocol_error_response,
    jsonrpc_success_payload as _jsonrpc_success_payload,
    parse_rpc_request as _parse_rpc_request,
    rpc_after_event_id as _rpc_after_event_id,
    rpc_params as _rpc_params,
    rpc_task_id as _rpc_task_id,
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

    @router.post("/message:send", response_model=None)
    def send_message(request: dict[str, Any]) -> dict[str, Any] | JSONResponse:
        try:
            return adapter.send_message_payload(request)
        except Exception as exc:
            if _is_jsonrpc_request(request):
                return _jsonrpc_error_response(request.get("id"), exc)
            raise _a2a_http_exception(exc) from exc

    @router.post("/message:stream")
    async def stream_message(request: dict[str, Any]) -> StreamingResponse:
        async def event_stream():
            try:
                async for event in _stream_message_result_events(adapter, request):
                    yield _format_a2a_sse_event(event)
            except Exception as exc:
                if _is_jsonrpc_request(request):
                    yield _format_a2a_sse_event(_jsonrpc_error_payload(request.get("id"), exc))
                else:
                    error = error_info_from_exception(exc)
                    yield _format_a2a_sse_event(
                        {"error": public_error_payload(error)}
                    )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        try:
            return _jsonrpc_success_payload(f"task-get-{task_id}", adapter.get_task(task_id))
        except Exception as exc:
            raise _a2a_http_exception(exc) from exc

    @router.post("/tasks/{task_id}:cancel", response_model=None)
    async def cancel_task(task_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        cancel_request = await _parse_cancel_request(request, route_task_id=task_id)
        request_id = cancel_request.request_id if cancel_request.jsonrpc else f"cancel-{task_id}"
        if cancel_request.error is not None:
            if cancel_request.jsonrpc:
                return _jsonrpc_error_response(request_id, cancel_request.error)
            raise _a2a_http_exception(cancel_request.error)
        try:
            return _jsonrpc_success_payload(
                request_id,
                adapter.cancel_task(task_id, reason=cancel_request.reason),
            )
        except Exception as exc:
            if cancel_request.jsonrpc:
                return _jsonrpc_error_response(request_id, exc)
            raise _a2a_http_exception(exc) from exc

    @router.post("/tasks/{task_id}:resume", response_model=None)
    async def resume_task(task_id: str, request: Request) -> dict[str, Any] | JSONResponse:
        resume_request = await _parse_resume_request(request, route_task_id=task_id)
        request_id = resume_request.request_id if resume_request.jsonrpc else f"resume-{task_id}"
        if resume_request.error is not None:
            if resume_request.jsonrpc:
                return _jsonrpc_error_response(request_id, resume_request.error)
            raise _a2a_http_exception(resume_request.error)
        try:
            return _jsonrpc_success_payload(
                request_id,
                adapter.resume_task(
                    task_id,
                    approved=resume_request.approved,
                    reason=resume_request.reason,
                ),
            )
        except Exception as exc:
            if resume_request.jsonrpc:
                return _jsonrpc_error_response(request_id, exc)
            raise _a2a_http_exception(exc) from exc

    @router.post("/tasks/{task_id}:subscribe")
    async def subscribe_task_events(
        task_id: str,
        request: Request,
        after: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        subscribe_request = await _parse_subscribe_request(request, route_task_id=task_id, query_after=after)
        if subscribe_request.error is None and not subscribe_request.jsonrpc:
            try:
                adapter.get_task(task_id)
            except Exception as exc:
                raise _a2a_http_exception(exc) from exc

        async def event_stream():
            if subscribe_request.error is not None:
                yield _format_a2a_sse_event(subscribe_request.error)
                return
            try:
                adapter.get_task(task_id)
            except Exception as exc:
                if subscribe_request.jsonrpc:
                    yield _format_a2a_sse_event(_jsonrpc_error_payload(subscribe_request.request_id, exc))
                    return
                raise

            last_event_id = subscribe_request.after_event_id
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
                    yield _format_a2a_sse_event(event)
                task = adapter.get_task(task_id)
                state = _task_state(task)
                if _is_terminal_state(state):
                    trailing_batch = await asyncio.to_thread(
                        adapter.wait_for_task_events,
                        task_id,
                        after_event_id=last_event_id,
                        timeout_seconds=0.0,
                    )
                    last_event_id = max(last_event_id, trailing_batch.last_event_id)
                    for event in trailing_batch.events:
                        yield _format_a2a_sse_event(event)
                    break

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

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
        if method in {"SendMessage", "message/send"}:
            return adapter.send_message_payload({**payload, "method": "message/send"})
        if method in {"GetTask", "tasks/get"}:
            params = _rpc_params(payload)
            task_id = _rpc_task_id(params)
            return _jsonrpc_success_payload(request_id, adapter.get_task(task_id))
        if method in {"CancelTask", "tasks/cancel"}:
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
        if method in {"ResumeTask", "tasks/resume"}:
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
        if method in {"SendStreamingMessage", "message/stream"}:
            return _a2a_sse_response(_rpc_stream_message_events(adapter, payload))
        if method in {"SubscribeToTask", "tasks/subscribe"}:
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
    try:
        async for event in _stream_message_result_events(
            adapter,
            {**payload, "method": "message/send"},
            task_event_request_id=request_id,
            wrap_task_events=True,
        ):
            yield _format_a2a_sse_event(event)
    except Exception as exc:
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
        batch = await asyncio.to_thread(
            adapter.wait_for_task_events,
            task_id,
            after_event_id=0,
            timeout_seconds=0.0,
        )
        for task_event in batch.events:
            if wrap_task_events:
                yield _jsonrpc_success_payload(task_event_request_id, task_event)
            else:
                yield task_event


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
        if _is_terminal_state(state):
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


class _SubscribeRequest:
    def __init__(
        self,
        *,
        request_id: Any = None,
        after_event_id: int = 0,
        error: dict[str, Any] | None = None,
        jsonrpc: bool = False,
    ) -> None:
        self.request_id = request_id
        self.after_event_id = after_event_id
        self.error = error
        self.jsonrpc = jsonrpc


class _CancelRequest:
    def __init__(
        self,
        *,
        request_id: Any = None,
        reason: str | None = None,
        error: Exception | None = None,
        jsonrpc: bool = False,
    ) -> None:
        self.request_id = request_id
        self.reason = reason
        self.error = error
        self.jsonrpc = jsonrpc


class _ResumeRequest:
    def __init__(
        self,
        *,
        request_id: Any = None,
        approved: bool = False,
        reason: str | None = None,
        error: Exception | None = None,
        jsonrpc: bool = False,
    ) -> None:
        self.request_id = request_id
        self.approved = approved
        self.reason = reason
        self.error = error
        self.jsonrpc = jsonrpc


async def _parse_cancel_request(
    request: Request,
    *,
    route_task_id: str,
) -> _CancelRequest:
    body = await request.body()
    if not body.strip():
        return _CancelRequest()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _CancelRequest(jsonrpc=True, error=exc)
    if not isinstance(payload, dict):
        return _CancelRequest(jsonrpc=True, error=ValueError("JSON-RPC request must be an object."))

    if not _is_jsonrpc_request(payload) and "params" not in payload:
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            return _CancelRequest(error=ValueError("cancel reason must be a string."))
        return _CancelRequest(reason=reason)

    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _CancelRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC request jsonrpc must be '2.0'."),
        )
    if payload.get("method") not in {None, "tasks/cancel"}:
        return _CancelRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC method must be 'tasks/cancel'."),
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _CancelRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params must be an object."),
        )
    param_task_id = params.get("id")
    if param_task_id is not None and param_task_id != route_task_id:
        return _CancelRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params.id must match the route task id."),
        )
    reason = params.get("reason")
    if reason is not None and not isinstance(reason, str):
        return _CancelRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params.reason must be a string."),
        )
    return _CancelRequest(jsonrpc=True, request_id=request_id, reason=reason)


async def _parse_resume_request(
    request: Request,
    *,
    route_task_id: str,
) -> _ResumeRequest:
    body = await request.body()
    if not body.strip():
        return _ResumeRequest(error=ValueError("resume approved flag is required."))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _ResumeRequest(jsonrpc=True, error=exc)
    if not isinstance(payload, dict):
        return _ResumeRequest(jsonrpc=True, error=ValueError("JSON-RPC request must be an object."))

    if not _is_jsonrpc_request(payload) and "params" not in payload:
        approved = payload.get("approved")
        if not isinstance(approved, bool):
            return _ResumeRequest(error=ValueError("resume approved flag must be a boolean."))
        reason = payload.get("reason")
        if reason is not None and not isinstance(reason, str):
            return _ResumeRequest(error=ValueError("resume reason must be a string."))
        return _ResumeRequest(approved=approved, reason=reason)

    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC request jsonrpc must be '2.0'."),
        )
    if payload.get("method") not in {None, "tasks/resume"}:
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC method must be 'tasks/resume'."),
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params must be an object."),
        )
    param_task_id = params.get("id")
    if param_task_id is not None and param_task_id != route_task_id:
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params.id must match the route task id."),
        )
    approved = params.get("approved")
    if not isinstance(approved, bool):
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params.approved must be a boolean."),
        )
    reason = params.get("reason")
    if reason is not None and not isinstance(reason, str):
        return _ResumeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=ValueError("JSON-RPC params.reason must be a string."),
        )
    return _ResumeRequest(jsonrpc=True, request_id=request_id, approved=approved, reason=reason)


async def _parse_subscribe_request(
    request: Request,
    *,
    route_task_id: str,
    query_after: int,
) -> _SubscribeRequest:
    body = await request.body()
    if not body.strip():
        return _SubscribeRequest(after_event_id=query_after)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return _SubscribeRequest(jsonrpc=True, error=_jsonrpc_error_payload(None, exc))
    if not isinstance(payload, dict):
        return _SubscribeRequest(
            jsonrpc=True,
            error=_jsonrpc_error_payload(None, ValueError("JSON-RPC request must be an object.")),
        )

    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _SubscribeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=_jsonrpc_error_payload(request_id, ValueError("JSON-RPC request jsonrpc must be '2.0'.")),
        )
    if payload.get("method") not in {None, "tasks/subscribe"}:
        return _SubscribeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=_jsonrpc_error_payload(request_id, ValueError("JSON-RPC method must be 'tasks/subscribe'.")),
        )
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _SubscribeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=_jsonrpc_error_payload(request_id, ValueError("JSON-RPC params must be an object.")),
        )
    param_task_id = params.get("id")
    if param_task_id is not None and param_task_id != route_task_id:
        return _SubscribeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=_jsonrpc_error_payload(
                request_id,
                ValueError("JSON-RPC params.id must match the route task id."),
            ),
        )
    after_event_id = params.get("afterEventId", query_after)
    if not isinstance(after_event_id, int) or after_event_id < 0:
        return _SubscribeRequest(
            jsonrpc=True,
            request_id=request_id,
            error=_jsonrpc_error_payload(
                request_id,
                ValueError("JSON-RPC params.afterEventId must be a non-negative integer."),
            ),
        )
    return _SubscribeRequest(jsonrpc=True, request_id=request_id, after_event_id=after_event_id)


def _a2a_http_exception(exc: Exception) -> HTTPException:
    error = error_info_from_exception(exc)
    return HTTPException(
        status_code=error.http_status,
        detail=public_error_payload(error),
    )


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


def _is_terminal_state(state: Any) -> bool:
    if is_terminal_a2a_state(state):
        return True
    return state in {"completed", "failed", "canceled", "rejected"}

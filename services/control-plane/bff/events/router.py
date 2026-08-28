"""BFF Events domain router.

Consolidates:
  - GET /bff/events: authenticated paginated event/audit feed with telemetry and audit filtering
  - GET /bff/events/stream: authenticated replay-capable SSE stream / unauthenticated liveness stream
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Request, Response
from starlette.responses import JSONResponse, StreamingResponse

try:
    from models import ErrorCode
except ImportError:
    class ErrorCode:
        VALIDATION_FAILED = "VALIDATION_FAILED"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        FORBIDDEN = "FORBIDDEN"
        RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
        INTERNAL_ERROR = "INTERNAL_ERROR"

log = logging.getLogger(__name__)

DEFAULT_SSE_CHANNELS: frozenset[str] = frozenset({
    "system",
    "telemetry",
    "alerts",
    "trading",
    "governance",
    "runtime",
    "evolution",
    "inbox",
    "command_center",
    "kpi",
    "approvals",
    "feed",
    "signals",
    "decisions",
    "risk",
    "backtest",
    "research",
})

_FRONTEND_SSE_SCHEMA_VERSION = 1


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _default_utc_now()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


def _default_page_slice(
    items: Sequence[Any],
    page_token: Optional[str],
    page_size: int,
) -> Tuple[List[Any], Optional[str]]:
    start = 0
    if page_token:
        try:
            start = int(page_token)
        except (TypeError, ValueError):
            start = 0
    page_items = list(items[start: start + page_size])
    next_token = str(start + page_size) if start + page_size < len(items) else None
    return page_items, next_token


def _default_bff_error(
    status_code: int,
    code: str,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    if suggestion:
        detail["error"]["suggestion"] = suggestion
    if details_extra:
        detail["error"].setdefault("details", {}).update(details_extra)
    return HTTPException(status_code=status_code, detail=detail)


def _default_extract_identity(
    authorization: Optional[str] = None,
    mfa_token: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> Any:
    class DummyIdentity:
        operator_id = "anonymous"
        roles = {"operator", "viewer", "admin"}
        is_authenticated = False

    ident = DummyIdentity()
    token = authorization or session_cookie
    if token:
        ident.is_authenticated = True
        if "op-" in token:
            ident.operator_id = token.split(":")[0].replace("Bearer ", "").strip()
        else:
            ident.operator_id = "op-user"
    return ident


def _default_require_read_role(identity: Any) -> None:
    pass


def _frontend_sse_event(
    *,
    channel: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schemaVersion": _FRONTEND_SSE_SCHEMA_VERSION,
        "id": event_id or f"evt-bff-{now}",
        "channel": channel,
        "type": event_type,
        "occurredAt": now,
        "payload": payload or {},
    }


def _frontend_sse_format(event: Dict[str, Any]) -> str:
    event_id = str(event.get("id", ""))
    data_str = json.dumps(event, ensure_ascii=False)
    return f"id: {event_id}\ndata: {data_str}\n\n"


async def _default_frontend_bff_event_stream(
    channels: Tuple[str, ...],
) -> AsyncGenerator[str, None]:
    channel_list = list(channels) if channels else ["system"]
    yield _frontend_sse_format(
        _frontend_sse_event(
            channel="system",
            event_type="system.connected",
            payload={"channels": channel_list, "transport": "sse"},
        )
    )
    while True:
        await asyncio.sleep(15.0)
        yield _frontend_sse_format(
            _frontend_sse_event(
                channel="system",
                event_type="system.heartbeat",
                payload={"channels": channel_list},
            )
        )


async def _default_sse_stream(
    buffer: deque,
    subscribers: list,
    last_event_id: Optional[str],
    channel: str,
) -> AsyncGenerator[str, None]:
    q: asyncio.Queue = asyncio.Queue()
    subscribers.append(q)
    try:
        yield f": connected to {channel}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                if isinstance(event, dict):
                    event_id = event.get("id", "")
                    event_type = event.get("type", "message")
                    data_str = json.dumps(event, ensure_ascii=False)
                    yield f"id: {event_id}\nevent: {event_type}\ndata: {data_str}\n\n"
                else:
                    yield f"data: {str(event)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        if q in subscribers:
            subscribers.remove(q)


def _default_handle_sse_stream(
    channel: str,
    buffer: Any,
    subscribers: Any,
    last_event_id: Optional[str],
    extra_headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-SSE-Channel": channel,
        "X-SSE-Replay-Supported": "true",
    }
    if extra_headers:
        headers.update(extra_headers)

    buf = buffer if isinstance(buffer, deque) else deque()
    subs = subscribers if isinstance(subscribers, list) else []
    return StreamingResponse(
        _default_sse_stream(buf, subs, last_event_id, channel),
        media_type="text/event-stream",
        headers=headers,
    )


def create_events_router(
    *,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    snapshot_meta: Optional[Callable[[str], Dict[str, Any]]] = None,
    dataset_surface_status: Optional[Callable[..., Dict[str, Any]]] = None,
    list_governance_audit_events: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    sse_buffers: Optional[Dict[str, Any]] = None,
    sse_subscribers: Optional[Dict[str, Any]] = None,
    sse_channels: Optional[Union[frozenset[str], Set[str], Sequence[str]]] = None,
    handle_sse_stream: Optional[Callable[..., Any]] = None,
    frontend_bff_event_stream: Optional[Callable[..., Any]] = None,
    resolve_session_kind: Optional[Callable[..., str]] = None,
) -> APIRouter:
    """Create canonical BFF Events router.

    Owns:
      - GET /bff/events: list recent events (telemetry + audit)
      - GET /bff/events/stream: SSE stream (authenticated replay + unauthenticated liveness)
    """
    router = APIRouter()

    _utc_now = utc_now or _default_utc_now
    _snapshot_meta = snapshot_meta or _default_snapshot_meta
    _extract_ident = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _err = bff_error or _default_bff_error
    _active_sse_channels = frozenset(sse_channels) if sse_channels else DEFAULT_SSE_CHANNELS
    _buffers = sse_buffers if sse_buffers is not None else {c: deque() for c in _active_sse_channels}
    _subscribers = sse_subscribers if sse_subscribers is not None else {c: [] for c in _active_sse_channels}
    _handle_sse = handle_sse_stream or _default_handle_sse_stream
    _frontend_stream = frontend_bff_event_stream or _default_frontend_bff_event_stream

    @router.get("/bff/events")
    async def bff_list_events(
        event_type: Optional[str] = None,
        actor: Optional[str] = None,
        target_type: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: list recent system/audit events (execute-plans compatibility surface)."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        action_types = [event_type] if event_type else None

        events: List[Dict[str, Any]] = []

        if list_governance_audit_events is not None:
            events = list_governance_audit_events(
                actor=actor,
                action_types=action_types,
                target_type=target_type,
            )
        elif get_read_store is not None:
            store = get_read_store()
            if hasattr(store, "list_governance_audit_events"):
                events = store.list_governance_audit_events(
                    actor=actor,
                    action_types=action_types,
                    target_type=target_type,
                )
            elif hasattr(store, "list_events_bff"):
                events = store.list_events_bff(event_type=event_type, page_size=page_size)

        if dataset_surface_status is not None:
            surface = dataset_surface_status("audit_log", snapshot_at=snapshot_at)
        else:
            if get_read_store is not None:
                store = get_read_store()
                src = getattr(store, "dataset_source", lambda ds: "local_snapshot")("audit_log")
                if src in ("missing", "unavailable"):
                    surface = {"status": "unavailable", "source": src}
                else:
                    surface = {"status": "ok", "source": src}
            else:
                surface = {"status": "ok", "source": "local_snapshot"}

        if surface.get("status") == "unavailable":
            events = []
            next_page_token = None
        else:
            events, next_page_token = _default_page_slice(events, page_token, page_size)

        meta = _snapshot_meta(snapshot_at)
        meta["surfaces"] = {"events": surface}
        return {
            "items": events,
            "page_info": {"next_page_token": next_page_token},
            "meta": meta,
        }

    @router.get("/bff/events/stream")
    async def stream_bff_events(
        channels: Optional[str] = Query(default=None),
        channel: Optional[str] = Query(default=None),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        pantheon_session: Optional[str] = Cookie(default=None),
    ):
        """BFF-wide SSE stream for the frontend shell.

        lastEventId is accepted for the browser client, but this transitional
        liveness stream only applies to unauthenticated callers. Authenticated
        cookie or Bearer callers use the real replay-capable SSE substrate.
        """
        channels_value = channels if isinstance(channels, str) else None
        channel_value = channel if isinstance(channel, str) else None
        last_event_id_value = last_event_id if isinstance(last_event_id, str) else None
        last_event_id_camel_value = last_event_id_camel if isinstance(last_event_id_camel, str) else None
        last_event_id_header_value = last_event_id_header if isinstance(last_event_id_header, str) else None
        authorization_value = authorization if isinstance(authorization, str) else None
        x_mfa_token_value = x_mfa_token if isinstance(x_mfa_token, str) else None
        pantheon_session_value = pantheon_session if isinstance(pantheon_session, str) else None

        resolved_last_event_id = (
            last_event_id_value or last_event_id_camel_value or last_event_id_header_value
        )

        requested = tuple(
            ch.strip()
            for ch in (channel_value or channels_value or "system").split(",")
            if ch.strip()
        )
        if authorization_value or pantheon_session_value:
            selected_channel = requested[0] if requested else "system"
            if selected_channel not in _active_sse_channels:
                raise _err(
                    400,
                    ErrorCode.VALIDATION_FAILED,
                    f"Unknown SSE channel: {selected_channel}",
                    f"Channel must be one of {sorted(list(_active_sse_channels))}",
                )
            identity = _extract_ident(
                authorization_value,
                mfa_token=x_mfa_token_value,
                session_cookie=pantheon_session_value,
            )
            _require_read(identity)
            extra_headers: Dict[str, str] = {}
            if resolve_session_kind is not None:
                extra_headers["X-BFF-Session-Kind"] = resolve_session_kind(identity)
            buf = _buffers.get(selected_channel)
            if buf is None:
                buf = deque()
                _buffers[selected_channel] = buf
            subs = _subscribers.get(selected_channel)
            if subs is None:
                subs = []
                _subscribers[selected_channel] = subs
            return _handle_sse(
                selected_channel,
                buf,
                subs,
                resolved_last_event_id,
                extra_headers=extra_headers if extra_headers else None,
            )

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-SSE-Channel": "bff",
            "X-SSE-Replay-Supported": "false",
            "X-SSE-Replay-Store": "liveness-only",
            "X-SSE-Resync-Routes": "/health,/readyz",
        }
        return StreamingResponse(
            _frontend_stream(requested),
            media_type="text/event-stream",
            headers=headers,
        )

    return router

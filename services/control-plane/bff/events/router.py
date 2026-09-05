"""BFF Events domain router.

Consolidates:
  - GET /bff/events: authenticated paginated event/audit feed with telemetry and audit filtering
  - GET /bff/events/stream: authenticated replay-capable SSE stream / unauthenticated liveness stream
  - /api/v1/stream plus ten execute-plans compatibility subscriptions and internal SSE delivery
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

from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Query, Request, Response
from starlette.responses import JSONResponse, StreamingResponse

from .service import EventStreamService

try:
    from services.control_plane.bff.models import ErrorCode
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
    read_surface: Optional[Any] = None,
    command_store: Optional[Any] = None,
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
    event_stream_service: Optional[EventStreamService] = None,
    include_domain_sse_aliases: bool = True,
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
    # ``EventStreamService`` owns replay, connection management, and internal
    # delivery.  The assembly layer can inject the live BFF buffers later;
    # this prepared router deliberately does not import ``main``.
    _event_stream = event_stream_service or EventStreamService(
        channels=sse_channels,
        buffers=sse_buffers,
        subscribers=sse_subscribers,
    )
    _active_sse_channels = frozenset(_event_stream.channels)
    _buffers = _event_stream.buffers
    _subscribers = _event_stream.subscribers
    _frontend_stream = frontend_bff_event_stream or _default_frontend_bff_event_stream

    def _stream_channel(
        channel: str,
        last_event_id: Optional[str],
        authorization: Optional[str],
    ) -> StreamingResponse:
        if channel not in _active_sse_channels:
            raise _err(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"Unknown SSE channel: {channel}",
                f"Channel must be one of {sorted(_active_sse_channels)}",
            )
        identity = _extract_ident(authorization)
        _require_read(identity)
        extra_headers: Dict[str, str] = {}
        if resolve_session_kind is not None:
            extra_headers["X-BFF-Session-Kind"] = resolve_session_kind(identity)
        if handle_sse_stream is not None:
            return handle_sse_stream(
                channel,
                _buffers[channel],
                _subscribers[channel],
                last_event_id,
                extra_headers=extra_headers or None,
            )
        return _event_stream.stream_response(
            channel,
            last_event_id,
            bff_error=_err,
            conflict_code=ErrorCode.RESOURCE_CONFLICT,
            extra_headers=extra_headers or None,
        )

    @router.get(
        "/bff/events",
        summary="List recent events (telemetry + governance audit)",
        operation_id="listBffEvents",
    )
    async def list_events(
        event_type: Optional[str] = Query(default=None),
        actor: Optional[str] = Query(default=None),
        action_types: Optional[str] = Query(default=None),
        target_type: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        read_store = _resolve_read_store()

        events: List[Dict[str, Any]] = []

        if list_governance_audit_events is not None:
            events = list_governance_audit_events(
                actor=actor,
                action_types=action_types,
                target_type=target_type,
            )
        elif read_store is not None:
            if hasattr(read_store, "list_governance_audit_events"):
                events = read_store.list_governance_audit_events(
                    actor=actor,
                    action_types=action_types,
                    target_type=target_type,
                )
            elif hasattr(read_store, "list_events_bff"):
                events = read_store.list_events_bff(event_type=event_type, page_size=page_size)

        if dataset_surface_status is not None:
            surface = dataset_surface_status("audit_log", snapshot_at=snapshot_at)
        else:
            if read_store is not None:
                src = getattr(read_store, "dataset_source", lambda ds: "local_snapshot")("audit_log")
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
            if handle_sse_stream is not None:
                return handle_sse_stream(
                    selected_channel,
                    _buffers[selected_channel],
                    _subscribers[selected_channel],
                    resolved_last_event_id,
                    extra_headers=extra_headers or None,
                )
            return _event_stream.stream_response(
                selected_channel,
                resolved_last_event_id,
                bff_error=_err,
                conflict_code=ErrorCode.RESOURCE_CONFLICT,
                extra_headers=extra_headers or None,
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

    @router.get("/api/v1/stream/{channel}")
    async def stream_generic_events(
        channel: str,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        """Authenticated replay-capable stream for a catalog channel."""
        return _stream_channel(channel, last_event_id, authorization)

    # Execute-plans compatibility subscriptions.  These aliases intentionally
    # delegate to the same generic subscription path and therefore retain one
    # replay/error/header contract.
    @router.get("/bff/sse/notifications")
    async def bff_sse_notifications_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("inbox", last_event_id, authorization)

    @router.get("/bff/sse/command-center/kpi")
    async def bff_sse_cc_kpi_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("ranking", last_event_id, authorization)

    @router.get("/bff/sse/command-center/events")
    async def bff_sse_cc_events_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("loop", last_event_id, authorization)

    @router.get("/bff/sse/jobs/{jobId}/progress")
    async def bff_sse_job_progress_alias(
        jobId: str,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        """Subscription is channel-based; job filtering remains client-side."""
        return _stream_channel("tool", last_event_id, authorization)

    @router.get("/bff/sse/alerts")
    async def bff_sse_alerts_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("sentinel", last_event_id, authorization)

    @router.get("/bff/sse/incidents/{incidentId}/timeline")
    async def bff_sse_incident_timeline_alias(
        incidentId: str,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("journal", last_event_id, authorization)

    if include_domain_sse_aliases:
        @router.get("/bff/sse/deployment/events")
        async def bff_sse_deployment_events_alias(
            last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
            authorization: Optional[str] = Header(default=None),
        ) -> StreamingResponse:
            return _stream_channel("artifact", last_event_id, authorization)

        @router.get("/bff/sse/agora/signals")
        async def bff_sse_agora_signals_alias(
            last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
            authorization: Optional[str] = Header(default=None),
        ) -> StreamingResponse:
            return _stream_channel("signal", last_event_id, authorization)

        @router.get("/bff/sse/agora/sessions/{sessionId}")
        async def bff_sse_agora_session_alias(
            sessionId: str,
            last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
            authorization: Optional[str] = Header(default=None),
        ) -> StreamingResponse:
            return _stream_channel("ask", last_event_id, authorization)

    @router.get("/bff/sse/review/updates")
    async def bff_sse_review_updates_alias(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ) -> StreamingResponse:
        return _stream_channel("approval", last_event_id, authorization)

    @router.post("/api/v1/internal/sse/publish")
    async def publish_sse_event(
        event_type: str = Query(..., description="Event type: runtime_state_changed, incident_created, etc."),
        channel: Optional[str] = Query(default=None, description="Optional channel name; inferred from event_type if missing"),
        runtime_id: Optional[str] = Query(default=None),
        incident_id: Optional[str] = Query(default=None),
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, str]:
        """Deliver an internal event through the domain-owned SSE outbox."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        event_id = _event_stream.publish_internal(
            event_type=event_type,
            channel=channel,
            runtime_id=runtime_id,
            incident_id=incident_id,
            payload=payload,
            bff_error=_err,
            validation_code=ErrorCode.VALIDATION_FAILED,
        )
        return {"event_id": event_id, "status": "published"}

    return router

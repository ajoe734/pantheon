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
from dataclasses import dataclass
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

from fastapi import (
    APIRouter,
    Body,
    Cookie,
    Depends,
    Header,
    HTTPException,
    Path as FastApiPath,
    Query,
    Request,
    Response,
)
from fastapi.sse import EventSourceResponse, ServerSentEvent, format_sse_event
from starlette.responses import JSONResponse, StreamingResponse

from .service import EventStreamService

from services.control_plane.bff.models import ErrorCode

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


@dataclass
class _StreamSubscription:
    channel: str
    cursor: Optional[str]
    filter_func: Optional[Callable[[Dict[str, Any]], bool]]
    is_liveness: bool = False
    requested_channels: Tuple[str, ...] = ("system",)


def _resolve_cursor(
    last_event_id: Optional[str] = None,
    last_event_id_camel: Optional[str] = None,
    last_event_id_header: Optional[str] = None,
) -> Optional[str]:
    for cand in (last_event_id, last_event_id_camel, last_event_id_header):
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    return None


def _extract_field(obj: Any, *field_names: str) -> Optional[str]:
    for name in field_names:
        if isinstance(obj, dict):
            val = obj.get(name)
        else:
            val = getattr(obj, name, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _get_event_candidates(event: Dict[str, Any], *field_names: str) -> Set[str]:
    candidates: Set[str] = set()
    for name in field_names:
        val = event.get(name)
        if isinstance(val, str) and val.strip():
            candidates.add(val.strip())
    for sub in ("data", "payload"):
        sub_obj = event.get(sub)
        if isinstance(sub_obj, dict):
            for name in field_names:
                val = sub_obj.get(name)
                if isinstance(val, str) and val.strip():
                    candidates.add(val.strip())
    return candidates


def _make_scope_filter(
    identity: Any,
    extra_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Optional[Callable[[Dict[str, Any]], bool]]:
    clean_tenant = _extract_field(identity, "tenant_id", "tenantId", "tenant")
    clean_operator = _extract_field(identity, "operator_id", "operatorId", "actor", "user_id")

    def _filter(event: Dict[str, Any]) -> bool:
        if clean_tenant:
            event_tenants = _get_event_candidates(event, "tenant_id", "tenantId", "tenant")
            if event_tenants and clean_tenant not in event_tenants:
                return False

        if clean_operator:
            target_actors = _get_event_candidates(
                event, "target_operator_id", "target_operator", "target_actor", "recipient_id"
            )
            if target_actors and clean_operator not in target_actors:
                return False

        if extra_filter is not None and not extra_filter(event):
            return False

        return True

    return _filter


def _parse_sse_wire_chunk(chunk: Union[str, ServerSentEvent, Dict[str, Any]]) -> ServerSentEvent:
    if isinstance(chunk, ServerSentEvent):
        return chunk
    if isinstance(chunk, dict):
        return ServerSentEvent(data=chunk)
    if isinstance(chunk, str):
        lines = chunk.splitlines()
        evt_id = None
        evt_event = None
        data_parts = []
        comment = None
        for line in lines:
            if line.startswith("id:"):
                evt_id = line[3:].strip()
            elif line.startswith("event:"):
                evt_event = line[6:].strip()
            elif line.startswith("data:"):
                data_parts.append(line[5:].lstrip())
            elif line.startswith(":"):
                comment = line[1:].strip()
        if data_parts:
            return ServerSentEvent(raw_data="\n".join(data_parts), id=evt_id, event=evt_event, comment=comment)
        elif comment:
            return ServerSentEvent(comment=comment)
        return ServerSentEvent(raw_data=chunk)
    return ServerSentEvent(data=chunk)


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
    return format_sse_event(data_str=data_str, id=event_id if event_id else None).decode("utf-8")


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
    data_dir: Optional[Union[str, Path]] = None,
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
        data_dir=data_dir,
    )
    router.event_stream_service = _event_stream
    _active_sse_channels = frozenset(_event_stream.channels)
    _buffers = _event_stream.buffers
    _subscribers = _event_stream.subscribers
    _frontend_stream = frontend_bff_event_stream or _default_frontend_bff_event_stream

    def _resolve_read_store() -> Any:
        if get_read_store is not None:
            return get_read_store()
        return read_surface

    def _validate_subscription(
        response: Response,
        channel: str,
        last_event_id: Optional[str] = None,
        last_event_id_camel: Optional[str] = None,
        last_event_id_header: Optional[str] = None,
        authorization: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        pantheon_session: Optional[str] = None,
        extra_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> _StreamSubscription:
        cursor = _resolve_cursor(last_event_id, last_event_id_camel, last_event_id_header)
        if channel not in _active_sse_channels:
            raise _err(
                400,
                ErrorCode.VALIDATION_FAILED,
                f"Unknown SSE channel: {channel}",
                f"Channel must be one of {sorted(_active_sse_channels)}",
            )
        identity = _extract_ident(
            authorization,
            mfa_token=x_mfa_token,
            session_cookie=pantheon_session,
        )
        _require_read(identity)

        if hasattr(_event_stream, "replay_headers"):
            for k, v in _event_stream.replay_headers(channel).items():
                response.headers[k] = v
        else:
            response.headers["X-SSE-Channel"] = channel
            response.headers["X-SSE-Replay-Supported"] = "true"
        if resolve_session_kind is not None:
            response.headers["X-BFF-Session-Kind"] = resolve_session_kind(identity)

        if hasattr(_event_stream, "check_replay"):
            _event_stream.check_replay(
                channel,
                cursor,
                bff_error=_err,
                conflict_code=ErrorCode.RESOURCE_CONFLICT,
            )
        filter_func = _make_scope_filter(identity, extra_filter)
        return _StreamSubscription(
            channel=channel,
            cursor=cursor,
            filter_func=filter_func,
        )

    def _stream_channel(
        channel: str,
        last_event_id: Optional[str],
        authorization: Optional[str],
        event_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> EventSourceResponse:
        sub = _validate_subscription(
            response=Response(),
            channel=channel,
            last_event_id=last_event_id,
            authorization=authorization,
            extra_filter=event_filter,
        )
        return _event_stream.stream_response(
            channel,
            sub.cursor,
            bff_error=_err,
            conflict_code=ErrorCode.RESOURCE_CONFLICT,
            event_filter=sub.filter_func,
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

        if event_type:
            events = [
                e for e in events
                if e.get("action_type") == event_type or e.get("type") == event_type
            ]

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

    async def _bff_events_stream_dep(
        response: Response,
        channels: Optional[str] = Query(default=None),
        channel: Optional[str] = Query(default=None),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> _StreamSubscription:
        channels_value = channels if isinstance(channels, str) else None
        channel_value = channel if isinstance(channel, str) else None
        authorization_value = authorization if isinstance(authorization, str) else None
        pantheon_session_value = pantheon_session if isinstance(pantheon_session, str) else None

        requested = tuple(
            ch.strip()
            for ch in (channel_value or channels_value or "system").split(",")
            if ch.strip()
        )
        if authorization_value or pantheon_session_value:
            selected_channel = requested[0] if requested else "system"
            return _validate_subscription(
                response=response,
                channel=selected_channel,
                last_event_id=last_event_id,
                last_event_id_camel=last_event_id_camel,
                last_event_id_header=last_event_id_header,
                authorization=authorization,
                x_mfa_token=x_mfa_token,
                pantheon_session=pantheon_session,
            )

        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["X-SSE-Channel"] = "bff"
        response.headers["X-SSE-Replay-Supported"] = "false"
        response.headers["X-SSE-Replay-Store"] = "liveness-only"
        response.headers["X-SSE-Resync-Routes"] = "/health,/readyz"
        return _StreamSubscription(
            channel="bff",
            cursor=None,
            filter_func=None,
            is_liveness=True,
            requested_channels=requested,
        )

    async def _stream_events(
        sub: _StreamSubscription,
    ) -> AsyncGenerator[ServerSentEvent, None]:
        buffer = _buffers.get(sub.channel)
        subscribers = _subscribers.get(sub.channel)
        if hasattr(_event_stream, "stream"):
            async for event in _event_stream.stream(
                sub.channel, buffer, subscribers, sub.cursor, event_filter=sub.filter_func
            ):
                yield event
        elif hasattr(_event_stream, "stream_response"):
            resp = _event_stream.stream_response(
                sub.channel,
                sub.cursor,
                bff_error=_err,
                conflict_code=ErrorCode.RESOURCE_CONFLICT,
                event_filter=sub.filter_func,
            )
            async for chunk in resp.body_iterator:
                yield _parse_sse_wire_chunk(chunk)

    @router.get(
        "/bff/events/stream",
        response_class=EventSourceResponse,
        summary="BFF-wide SSE stream for the frontend shell.",
    )
    async def stream_bff_events(
        sub: _StreamSubscription = Depends(_bff_events_stream_dep),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        """BFF-wide SSE stream for the frontend shell.

        lastEventId is accepted for the browser client, but this transitional
        liveness stream only applies to unauthenticated callers. Authenticated
        cookie or Bearer callers use the real replay-capable SSE substrate.
        """
        if sub.is_liveness:
            async for chunk in _frontend_stream(sub.requested_channels):
                yield _parse_sse_wire_chunk(chunk)
        else:
            async for event in _stream_events(sub):
                yield event

    async def _generic_sub_dep(
        response: Response,
        channel: str = FastApiPath(...),
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> _StreamSubscription:
        return _validate_subscription(
            response=response,
            channel=channel,
            last_event_id=last_event_id,
            last_event_id_camel=last_event_id_camel,
            last_event_id_header=last_event_id_header,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            pantheon_session=pantheon_session,
        )

    @router.get(
        "/api/v1/stream/{channel}",
        response_class=EventSourceResponse,
        summary="Authenticated replay-capable stream for a catalog channel.",
    )
    async def stream_generic_events(
        channel: str,
        sub: _StreamSubscription = Depends(_generic_sub_dep),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        """Authenticated replay-capable stream for a catalog channel."""
        async for event in _stream_events(sub):
            yield event

    def _make_channel_sub_dep(channel_name: str):
        async def _dep(
            response: Response,
            last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
            last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
            last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
            authorization: Optional[str] = Header(default=None),
            x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
            pantheon_session: Optional[str] = Cookie(default=None),
        ) -> _StreamSubscription:
            return _validate_subscription(
                response=response,
                channel=channel_name,
                last_event_id=last_event_id,
                last_event_id_camel=last_event_id_camel,
                last_event_id_header=last_event_id_header,
                authorization=authorization,
                x_mfa_token=x_mfa_token,
                pantheon_session=pantheon_session,
            )
        return _dep

    _dep_inbox = _make_channel_sub_dep("inbox")
    _dep_cc_kpi = _make_channel_sub_dep("ranking")
    _dep_cc_events = _make_channel_sub_dep("loop")
    _dep_alerts = _make_channel_sub_dep("sentinel")
    _dep_deployment = _make_channel_sub_dep("artifact")
    _dep_signals = _make_channel_sub_dep("signal")
    _dep_reviews = _make_channel_sub_dep("approval")

    # Execute-plans compatibility subscriptions.  These aliases intentionally
    # delegate to the same generic subscription path and therefore retain one
    # replay/error/header contract.
    @router.get("/bff/sse/notifications", response_class=EventSourceResponse)
    async def bff_sse_notifications_alias(
        sub: _StreamSubscription = Depends(_dep_inbox),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

    @router.get("/bff/sse/command-center/kpi", response_class=EventSourceResponse)
    async def bff_sse_cc_kpi_alias(
        sub: _StreamSubscription = Depends(_dep_cc_kpi),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

    @router.get("/bff/sse/command-center/events", response_class=EventSourceResponse)
    async def bff_sse_cc_events_alias(
        sub: _StreamSubscription = Depends(_dep_cc_events),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

    async def _job_progress_sub_dep(
        jobId: str,
        response: Response,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> _StreamSubscription:
        clean_job_id = str(jobId or "").strip()

        def _matches_job(event: Dict[str, Any]) -> bool:
            candidate_ids = {
                str(event.get("job_id") or "").strip(),
                str(event.get("jobId") or "").strip(),
            }
            data = event.get("data")
            if isinstance(data, dict):
                candidate_ids.add(str(data.get("job_id") or "").strip())
                candidate_ids.add(str(data.get("jobId") or "").strip())
            payload = event.get("payload")
            if isinstance(payload, dict):
                candidate_ids.add(str(payload.get("job_id") or "").strip())
                candidate_ids.add(str(payload.get("jobId") or "").strip())
            candidate_ids.discard("")
            return clean_job_id in candidate_ids

        return _validate_subscription(
            response=response,
            channel="tool",
            last_event_id=last_event_id,
            last_event_id_camel=last_event_id_camel,
            last_event_id_header=last_event_id_header,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            pantheon_session=pantheon_session,
            extra_filter=_matches_job,
        )

    @router.get("/bff/sse/jobs/{jobId}/progress", response_class=EventSourceResponse)
    async def bff_sse_job_progress_alias(
        jobId: str,
        sub: _StreamSubscription = Depends(_job_progress_sub_dep),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        """Subscription is channel-based AND server-side filtered by jobId.

        BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001: previously this
        stream only carried a channel-level subscription and relied entirely
        on the client to discard events for other jobs. It now also filters
        every replayed and live event on the ``tool`` channel so a client
        subscribed to job A never receives job B's events, matching the
        conventions of the ``incidentId``-aware sibling route.
        """
        async for event in _stream_events(sub):
            yield event

    @router.get("/bff/sse/alerts", response_class=EventSourceResponse)
    async def bff_sse_alerts_alias(
        sub: _StreamSubscription = Depends(_dep_alerts),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

    async def _incident_timeline_sub_dep(
        incidentId: str,
        response: Response,
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
        last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
        authorization: Optional[str] = Header(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        pantheon_session: Optional[str] = Cookie(default=None),
    ) -> _StreamSubscription:
        return _validate_subscription(
            response=response,
            channel="journal",
            last_event_id=last_event_id,
            last_event_id_camel=last_event_id_camel,
            last_event_id_header=last_event_id_header,
            authorization=authorization,
            x_mfa_token=x_mfa_token,
            pantheon_session=pantheon_session,
        )

    @router.get("/bff/sse/incidents/{incidentId}/timeline", response_class=EventSourceResponse)
    async def bff_sse_incident_timeline_alias(
        incidentId: str,
        sub: _StreamSubscription = Depends(_incident_timeline_sub_dep),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

    if include_domain_sse_aliases:
        @router.get("/bff/sse/deployment/events", response_class=EventSourceResponse)
        async def bff_sse_deployment_events_alias(
            sub: _StreamSubscription = Depends(_dep_deployment),
        ) -> AsyncGenerator[ServerSentEvent, None]:
            async for event in _stream_events(sub):
                yield event

        @router.get("/bff/sse/agora/signals", response_class=EventSourceResponse)
        async def bff_sse_agora_signals_alias(
            sub: _StreamSubscription = Depends(_dep_signals),
        ) -> AsyncGenerator[ServerSentEvent, None]:
            async for event in _stream_events(sub):
                yield event

        async def _agora_session_sub_dep(
            sessionId: str,
            response: Response,
            last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
            last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
            last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
            authorization: Optional[str] = Header(default=None),
            x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
            pantheon_session: Optional[str] = Cookie(default=None),
        ) -> _StreamSubscription:
            return _validate_subscription(
                response=response,
                channel="ask",
                last_event_id=last_event_id,
                last_event_id_camel=last_event_id_camel,
                last_event_id_header=last_event_id_header,
                authorization=authorization,
                x_mfa_token=x_mfa_token,
                pantheon_session=pantheon_session,
            )

        @router.get("/bff/sse/agora/sessions/{sessionId}", response_class=EventSourceResponse)
        async def bff_sse_agora_session_alias(
            sessionId: str,
            sub: _StreamSubscription = Depends(_agora_session_sub_dep),
        ) -> AsyncGenerator[ServerSentEvent, None]:
            async for event in _stream_events(sub):
                yield event

    @router.get("/bff/sse/review/updates", response_class=EventSourceResponse)
    async def bff_sse_review_updates_alias(
        sub: _StreamSubscription = Depends(_dep_reviews),
    ) -> AsyncGenerator[ServerSentEvent, None]:
        async for event in _stream_events(sub):
            yield event

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

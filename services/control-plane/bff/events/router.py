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
import os
from pathlib import Path
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

from services.control_plane.bff.models import ErrorCode, OperatorIdentity

try:
    from services.control_plane.bff.auth.policy import (
        bff_me_tenant_payload as _auth_bff_me_tenant_payload,
        get_session_state as _auth_get_session_state,
    )
except ImportError:
    _auth_bff_me_tenant_payload = None
    _auth_get_session_state = None

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


def _first_nonblank(*candidates: Any) -> Optional[str]:
    for cand in candidates:
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
    claims = getattr(obj, "claims", None)
    if claims is None and isinstance(obj, dict):
        claims = obj.get("claims")
    if isinstance(claims, dict):
        for name in field_names:
            val = claims.get(name)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if "." in name:
                cur: Any = claims
                for part in name.split("."):
                    if not isinstance(cur, dict):
                        cur = None
                        break
                    cur = cur.get(part)
                if isinstance(cur, str) and cur.strip():
                    return cur.strip()
    return None


def _get_event_candidates(event: Dict[str, Any], *field_names: str) -> Set[str]:
    candidates: Set[str] = set()

    def _collect(val: Any) -> None:
        if isinstance(val, str) and val.strip():
            candidates.add(val.strip())
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                if isinstance(item, str) and item.strip():
                    candidates.add(item.strip())

    for name in field_names:
        _collect(event.get(name))
    for sub in ("data", "payload"):
        sub_obj = event.get(sub)
        if isinstance(sub_obj, dict):
            for name in field_names:
                _collect(sub_obj.get(name))
    return candidates


def _resolve_caller_tenant_scope(
    identity: Any,
    requested_tenant: Optional[str] = None,
    session_store: Optional[Any] = None,
    tenant_payload_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
) -> Tuple[Optional[str], Set[str], bool]:
    """Resolve caller effective tenant, allowed tenants set, and global scope flag.

    Enforces allowed tenant boundaries and fails closed for unauthorized requests.
    """
    session_tenant = None
    if session_store is not None and _auth_get_session_state is not None:
        try:
            s_state = _auth_get_session_state(identity, session_store)
            if isinstance(s_state, dict):
                session_tenant = _first_nonblank(s_state.get("tenant_id"), s_state.get("tenantId"))
        except Exception:
            session_tenant = None

    target_tenant = _first_nonblank(requested_tenant, session_tenant)

    effective_tenant: Optional[str] = None
    allowed_tenants: Set[str] = set()
    is_global: bool = False

    fn = tenant_payload_fn or _auth_bff_me_tenant_payload
    if fn is not None:
        try:
            policy_ident = identity
            if not hasattr(identity, "claims") or not isinstance(getattr(identity, "claims", None), dict):
                claims: Dict[str, Any] = {}
                t_id = _extract_field(identity, "tenant_id", "tenantId", "tenant")
                if t_id:
                    claims["tenant_id"] = t_id
                t_allowed = getattr(identity, "allowed_tenants", None) or getattr(identity, "allowedTenants", None)
                if t_allowed:
                    if isinstance(t_allowed, (list, tuple, set)):
                        claims["allowed_tenants"] = list(t_allowed)
                    elif isinstance(t_allowed, str):
                        claims["allowed_tenants"] = [t.strip() for t in t_allowed.split(",") if t.strip()]
                op_id = getattr(identity, "operator_id", None) or getattr(identity, "actor_id", "op-user")
                roles = getattr(identity, "roles", [])
                if isinstance(roles, set):
                    roles = sorted(roles)
                elif not isinstance(roles, list):
                    roles = list(roles) if roles else ["viewer"]
                policy_ident = OperatorIdentity(
                    operator_id=str(op_id),
                    roles=roles,
                    claims=claims,
                    mfa_verified=bool(getattr(identity, "mfa_verified", False)),
                )
            t_payload = fn(policy_ident, requested_tenant=target_tenant)
            effective_tenant = t_payload.get("id")
            allowed_list = t_payload.get("allowed_ids") or []
            allowed_tenants = set(allowed_list)
            is_global = (t_payload.get("scope") == "global") or ("*" in allowed_tenants)
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("Tenant payload policy resolution fallback: %s", exc)

    if effective_tenant is None and not is_global:
        claims = getattr(identity, "claims", None)
        if claims is None and isinstance(identity, dict):
            claims = identity.get("claims")
        if not isinstance(claims, dict):
            claims = {}

        claim_tenant = _first_nonblank(
            claims.get("tenant_id"),
            claims.get("tenantId"),
            claims.get("tenant"),
            claims.get("tid"),
            claims.get("org_id"),
            _extract_field(identity, "tenant_id", "tenantId", "tenant"),
        )

        allowed_list = []
        for k in ("allowed_tenants", "allowedTenants", "tenant_ids", "tenantIds", "tenants"):
            val = claims.get(k)
            if isinstance(val, (list, tuple, set)):
                allowed_list.extend([str(x).strip() for x in val if str(x).strip()])
            elif isinstance(val, str) and val.strip():
                allowed_list.extend([x.strip() for x in val.split(",") if x.strip()])
        top_allowed = getattr(identity, "allowed_tenants", None) or getattr(identity, "allowedTenants", None)
        if isinstance(top_allowed, (list, tuple, set)):
            allowed_list.extend([str(x).strip() for x in top_allowed if str(x).strip()])
        elif isinstance(top_allowed, str) and top_allowed.strip():
            allowed_list.extend([x.strip() for x in top_allowed.split(",") if x.strip()])

        if allowed_list:
            allowed_tenants = set(allowed_list)
        elif claim_tenant:
            allowed_tenants = {claim_tenant}

        is_global = "*" in allowed_tenants

        default_tenant = _first_nonblank(
            claim_tenant,
            os.getenv("PANTHEON_BFF_TENANT_ID"),
            os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
            os.getenv("PANTHEON_TENANT_ID"),
            "pantheon-dev",
        )

        eff = target_tenant or default_tenant
        if allowed_tenants and not is_global and eff not in allowed_tenants:
            err_fn = bff_error or _default_bff_error
            raise err_fn(
                403,
                ErrorCode.FORBIDDEN,
                "Tenant access denied",
                "Requested tenant is outside the caller tenant scope",
                precondition_failed="tenant_scope",
                suggestion="Switch to an allowed tenant or request access from an administrator",
                details_extra={"tenantId": eff, "allowedTenantIds": sorted(allowed_tenants)},
            )
        effective_tenant = eff

    return effective_tenant, allowed_tenants, is_global


def _make_scope_filter(
    identity: Any,
    extra_filter: Optional[Callable[[Dict[str, Any]], bool]] = None,
    clean_tenant: Optional[str] = None,
    allowed_tenants: Optional[Set[str]] = None,
    is_global: bool = False,
    requested_tenant: Optional[str] = None,
) -> Optional[Callable[[Dict[str, Any]], bool]]:
    if clean_tenant is None and not is_global:
        clean_tenant = _extract_field(
            identity,
            "tenant_id", "tenantId", "tenant", "tid", "org_id",
        )
    clean_operator = _extract_field(identity, "operator_id", "operatorId", "actor", "user_id")

    def _filter(event: Dict[str, Any]) -> bool:
        # Tenant isolation
        event_tenants = _get_event_candidates(
            event,
            "tenant_id", "tenantId", "tenant",
            "tenant_ids", "tenantIds", "tenants",
            "allowed_tenants", "allowedTenants",
        )
        if event_tenants:
            if is_global and not requested_tenant:
                pass  # Global caller with no specific tenant constraint sees all
            elif clean_tenant:
                if clean_tenant not in event_tenants:
                    return False
            else:
                # Unresolved scope fails closed on tenant-scoped events
                return False

        # Operator / actor isolation
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
    session_lifecycle_store: Optional[Any] = None,
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None,
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
    _tenant_payload_fn = bff_me_tenant_payload or _auth_bff_me_tenant_payload
    _session_store = session_lifecycle_store
    if _session_store is None:
        store_dir = data_dir or os.getenv("PANTHEON_BFF_DATA_DIR") or os.getenv("BFF_DATA_DIR")
        if store_dir:
            store_path = os.path.join(str(store_dir), "session_lifecycle.json")
            if os.path.exists(store_path):
                try:
                    from services.control_plane.bff.session_lifecycle_store import SessionLifecycleStore
                    _session_store = SessionLifecycleStore(store_path)
                except Exception:
                    pass
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
        x_tenant_id: Optional[str] = None,
        x_pantheon_tenant: Optional[str] = None,
        tenant_query: Optional[str] = None,
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

        req_tenant = _first_nonblank(x_tenant_id, x_pantheon_tenant, tenant_query)
        eff_tenant, allowed_set, is_glob = _resolve_caller_tenant_scope(
            identity=identity,
            requested_tenant=req_tenant,
            session_store=_session_store,
            tenant_payload_fn=_tenant_payload_fn,
            bff_error=_err,
        )

        if eff_tenant:
            response.headers["X-Tenant-Id"] = eff_tenant

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
        filter_func = _make_scope_filter(
            identity,
            extra_filter,
            clean_tenant=eff_tenant,
            allowed_tenants=allowed_set,
            is_global=is_glob,
            requested_tenant=req_tenant,
        )
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
        x_tenant_id: Optional[str] = None,
        x_pantheon_tenant: Optional[str] = None,
        tenant_query: Optional[str] = None,
    ) -> EventSourceResponse:
        sub = _validate_subscription(
            response=Response(),
            channel=channel,
            last_event_id=last_event_id,
            authorization=authorization,
            extra_filter=event_filter,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            tenant_query=tenant_query,
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
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
        tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
        tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
                x_tenant_id=x_tenant_id,
                x_pantheon_tenant=x_pantheon_tenant,
                tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
        tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
        tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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
            x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
            x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
            tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
            tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
            tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
                x_tenant_id=x_tenant_id,
                x_pantheon_tenant=x_pantheon_tenant,
                tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
        tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
        tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
        tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
        tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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
            x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
            x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
            tenant_query: Optional[str] = Query(default=None, alias="tenant_id"),
            tenant_camel_query: Optional[str] = Query(default=None, alias="tenantId"),
            tenant_short_query: Optional[str] = Query(default=None, alias="tenant"),
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
                x_tenant_id=x_tenant_id,
                x_pantheon_tenant=x_pantheon_tenant,
                tenant_query=_first_nonblank(tenant_query, tenant_camel_query, tenant_short_query),
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

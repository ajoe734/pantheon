"""BFF Incidents domain router.

Owns the 27 canonical Incident, Alert, Kill Switch, and Audit route decorators:
  1. GET  /api/v1/operator/alerts
  2. GET  /api/v1/incidents
  3. GET  /api/v1/incidents/stream
  4. GET  /api/v1/incidents/{incident_id}
  5. GET  /api/v1/kill-switch/status
  6. GET  /api/v1/operator/incident-response/{incident_id}
  7. GET  /api/v1/operator/post-incident-review/{incident_id}
  8. GET  /bff/risk/alerts
  9. GET  /bff/risk/alerts/{alert_id}
 10. POST /bff/risk/alerts/{alert_id}/actions/{action_id} (202)
 11. GET  /bff/incidents
 12. POST /bff/incidents (201)
 13. GET  /bff/incidents/{incident_id}
 14. POST /bff/incidents/{incident_id}/actions/{action_id} (202)
 15. GET  /bff/alerts
 16. GET  /bff/alerts/{alert_id}
 17. POST /bff/alerts/{alert_id}/acknowledge (202)
 18. GET  /bff/audit
 19. GET  /bff/audit/events
 20. GET  /bff/audit/entities/{entity_type}/{entity_id}
 21. GET  /bff/audit/export
 22. POST /bff/audit/export (202)
 23. POST /bff/alerts/{id}/escalate-incident (202)
 24. POST /bff/incidents/{id}/append-postmortem (202)
 25. POST /bff/incidents/{id}/resolve (202)
 26. POST /bff/incidents/{id}/rollback-deployment (202)
 27. POST /bff/incidents/{id}/start-mitigation (202)
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import logging
import time
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)
import uuid

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse, StreamingResponse

from .service import (
    IncidentService,
    _default_utc_now,
    _parse_rfc3339,
    _project_incident_home_item,
    _project_incident_detail_incident,
    _project_bff_incident_case,
    _stable_json_hash,
)

try:
    from models import (
        CommandStatus,
        CommandType,
        ErrorCode,
        ObjectType,
        OperatorIdentity,
    )
except ImportError:
    try:
        from ..models import (  # type: ignore[no-redef]
            CommandStatus,
            CommandType,
            ErrorCode,
            ObjectType,
            OperatorIdentity,
        )
    except ImportError:
        class ErrorCode:  # type: ignore[no-redef]
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
            FORBIDDEN = "FORBIDDEN"
            VALIDATION_FAILED = "VALIDATION_FAILED"
            IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
            DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
            INTERNAL_ERROR = "INTERNAL_ERROR"

        class CommandType:  # type: ignore[no-redef]
            INCIDENT_ACTION = "IncidentAction"
            RISK_ALERT_ACTION = "RiskAlertAction"
            ALERT_ACKNOWLEDGE = "AlertAcknowledge"
            AUDIT_EXPORT = "AuditExport"

        class ObjectType:  # type: ignore[no-redef]
            INCIDENT = "Incident"
            RISK_ALERT = "RiskAlert"
            AUDIT_EXPORT = "AuditExport"

        class CommandStatus:  # type: ignore[no-redef]
            SUBMITTED = "submitted"

        class OperatorIdentity:  # type: ignore[no-redef]
            def __init__(self, operator_id: str = "operator", roles: Optional[List[str]] = None):
                self.operator_id = operator_id
                self.roles = roles or ["operator"]

log = logging.getLogger(__name__)


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
    if start < 0:
        start = 0
    page_items = list(items[start : start + page_size])
    next_token = str(start + page_size) if start + page_size < len(items) else None
    return page_items, next_token


def _default_snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _default_utc_now()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


def _default_bff_error(
    status_code: int,
    code: Any,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    code_val = getattr(code, "value", str(code))
    detail: Dict[str, Any] = {
        "error": {
            "code": code_val,
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
) -> OperatorIdentity:
    token = authorization or session_cookie
    if token:
        token_clean = token.replace("Bearer ", "").strip()
        parts = token_clean.split(":")
        operator_id = parts[0] if parts[0] else "op-user"
        roles = [r.strip() for r in parts[1].split(",") if r.strip()] if len(parts) > 1 else ["operator", "viewer", "admin"]
        return OperatorIdentity(operator_id=operator_id, roles=roles)
    return OperatorIdentity(operator_id="anonymous", roles=["viewer"])


def _default_require_read_role(identity: Any) -> None:
    pass


def _default_require_operator_role(identity: Any) -> None:
    pass


def _default_resolve_idempotency_key(
    idempotency_key: Optional[str] = None,
    x_idempotency_key: Optional[str] = None,
) -> str:
    key = idempotency_key or x_idempotency_key or ""
    return key.strip() or str(uuid.uuid4())


def _default_reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    pass


def _default_surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: Optional[str] = None,
    unavailable_reason: Optional[str] = None,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason or "Read surface is currently unavailable."
    if surface.get("message"):
        return str(surface["message"])
    return degraded_reason or "Read surface is degraded."


def _default_read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: Optional[str] = None,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    degraded_reason: Optional[str] = None,
    unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
    now = snapshot_at or _default_utc_now()
    surf = surface or {"status": "ok", "source": "service_store"}
    meta: Dict[str, Any] = {
        "snapshot_at": now,
        "surfaces": {
            surface_key: surf,
        },
    }
    if total is not None:
        meta["total"] = total
    label = surface_key.replace("_", " ")
    reason = _default_surface_degradation_reason(
        surf,
        degraded_reason=degraded_reason or f"{label} is degraded and may be stale.",
        unavailable_reason=unavailable_reason or f"{label} is currently unavailable.",
    )
    if reason is not None:
        meta["degradation"] = {"reason": reason}
    return meta


def _default_raise_if_read_surface_unavailable(
    surface: Dict[str, Any],
    *,
    label: str,
    bff_err: Optional[Callable[..., HTTPException]] = None,
) -> None:
    if surface.get("status") != "unavailable":
        return
    err_fn = bff_err or _default_bff_error
    raise err_fn(
        503,
        ErrorCode.DEPENDENCY_UNAVAILABLE,
        f"{label} read surface unavailable",
        str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
        precondition_failed="read_surface_unavailable",
        suggestion="Verify the owning service URL and health before retrying this read.",
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


def create_incident_router(
    *,
    service: Optional[IncidentService] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_command_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice: Optional[Callable[..., Any]] = None,
    snapshot_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    dataset_surface_status: Optional[Callable[..., Dict[str, Any]]] = None,
    meta_staleness: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    surface_degradation_reason: Optional[Callable[..., Optional[str]]] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    raise_if_read_surface_unavailable: Optional[Callable[..., None]] = None,
    resolve_final_idempotency_key: Optional[Callable[..., str]] = None,
    reject_body_idempotency_key: Optional[Callable[[Dict[str, Any]], None]] = None,
    submit_action_command: Optional[Callable[..., Any]] = None,
    submit_sem_command: Optional[Callable[..., Any]] = None,
    handle_sse_stream: Optional[Callable[..., Any]] = None,
    run_management_read: Optional[Callable[..., Any]] = None,
    request_dry_run_requested: Optional[Callable[..., bool]] = None,
    dry_run_success_response: Optional[Callable[..., Any]] = None,
    build_operator_alerts_payload: Optional[Callable[[str], Dict[str, Any]]] = None,
    list_governance_audit_events: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    get_bff_incident: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    list_bff_incidents: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    incident_events: Optional[Any] = None,
    incident_subscribers: Optional[Any] = None,
    acknowledged_alerts: Optional[Any] = None,
    incident_overlay: Optional[Any] = None,
    idempotency_ledger: Optional[Any] = None,
) -> APIRouter:
    """Build the canonical BFF Incidents domain router.

    Registers exactly 27 route decorators covering Incident, Alert, Kill Switch,
    and Audit endpoints.
    """
    router = APIRouter()

    _utc_now = utc_now or _default_utc_now
    _snapshot_meta = snapshot_meta or _default_snapshot_meta
    _extract_ident = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _require_operator = require_operator_role or _default_require_operator_role
    _err = bff_error or _default_bff_error
    _slice = page_slice or _default_page_slice
    _resolve_key = resolve_final_idempotency_key or _default_resolve_idempotency_key
    _reject_key = reject_body_idempotency_key or _default_reject_body_idempotency_key
    _degradation_reason = surface_degradation_reason or _default_surface_degradation_reason
    _read_meta = read_surface_meta or _default_read_surface_meta
    _raise_unavailable = raise_if_read_surface_unavailable or (
        lambda surface, label: _default_raise_if_read_surface_unavailable(surface, label=label, bff_err=_err)
    )
    _handle_sse = handle_sse_stream or _default_handle_sse_stream

    _service = service or IncidentService(
        get_read_store=get_read_store,
        get_command_store=get_command_store,
        incident_overlay=incident_overlay,
        acknowledged_alerts=acknowledged_alerts,
        idempotency_ledger=idempotency_ledger,
        incident_events=incident_events,
        incident_subscribers=incident_subscribers,
        utc_now=_utc_now,
        dataset_surface_status=dataset_surface_status,
        meta_staleness=meta_staleness,
    )

    _build_alerts_payload = build_operator_alerts_payload or _service.build_operator_alerts_payload
    _list_audit = list_governance_audit_events or _service.list_audit_events
    _get_bff_inc = get_bff_incident or _service.get_bff_incident
    _list_bff_inc = list_bff_incidents or _service.list_bff_incidents
    _inc_events = incident_events if incident_events is not None else _service._incident_events
    _inc_subscribers = incident_subscribers if incident_subscribers is not None else _service._incident_subscribers
    _ack_alerts = acknowledged_alerts if acknowledged_alerts is not None else _service._acknowledged_alerts
    _inc_overlay = incident_overlay if incident_overlay is not None else _service._incident_overlay
    _idem_ledger = idempotency_ledger if idempotency_ledger is not None else _service._idempotency_ledger

    # -------------------------------------------------------------------------
    # Route 1: GET /api/v1/operator/alerts
    # -------------------------------------------------------------------------
    @router.get("/api/v1/operator/alerts")
    async def list_operator_alerts(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Operator alert aggregate view."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        return _build_alerts_payload(snapshot_at)

    # -------------------------------------------------------------------------
    # Route 2: GET /api/v1/incidents
    # -------------------------------------------------------------------------
    @router.get("/api/v1/incidents")
    async def list_incidents(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """IN-01: Incident List with optional filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        surface = _service.get_surface_status("incidents", snapshot_at=snapshot_at)
        incidents = _service.list_incidents(
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        )
        items = [_project_incident_home_item(i) for i in incidents]
        if surface.get("status") == "unavailable":
            items = []
            next_page_token = None
        else:
            items, next_page_token = _slice(items, page_token, page_size)

        meta: Dict[str, Any] = {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "incident_list": surface,
            },
        }
        staleness = _service.get_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

        degradation_reason = _degradation_reason(
            surface,
            degraded_reason="Incident list is degraded and may be stale.",
            unavailable_reason="Incident list is currently unavailable.",
        )
        if degradation_reason is not None:
            meta["degradation"] = {"reason": degradation_reason}

        return {
            "items": items,
            "page_info": {
                "next_page_token": next_page_token,
            },
            "meta": meta,
        }

    # -------------------------------------------------------------------------
    # Route 3: GET /api/v1/incidents/stream
    # (Registered BEFORE /api/v1/incidents/{incident_id} to avoid path conflict)
    # -------------------------------------------------------------------------
    @router.get("/api/v1/incidents/stream")
    async def stream_incident_events(
        last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
        authorization: Optional[str] = Header(default=None),
    ):
        """IN-SSE: Server-Sent Events stream for active incident events."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        return _handle_sse("incident", _inc_events, _inc_subscribers, last_event_id)

    # -------------------------------------------------------------------------
    # Route 4: GET /api/v1/incidents/{incident_id}
    # -------------------------------------------------------------------------
    @router.get("/api/v1/incidents/{incident_id}")
    async def get_incident(
        incident_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """IN-02: Incident Detail."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        clean_id = incident_id.strip()
        incident = _service.get_incident(clean_id)
        if not incident:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Incident not found",
                f"Incident {incident_id} does not exist",
            )

        return {
            "data": incident,
            "meta": {
                "staleness": _service.get_staleness(),
            },
        }

    # -------------------------------------------------------------------------
    # Route 5: GET /api/v1/kill-switch/status
    # -------------------------------------------------------------------------
    @router.get("/api/v1/kill-switch/status")
    async def get_kill_switch_status(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """IN-05: Kill Switch Status — requires admin role."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        roles = set(getattr(identity, "roles", []) or [])
        if "admin" not in roles:
            raise _err(
                403,
                ErrorCode.FORBIDDEN,
                "Kill-switch status requires 'admin' role",
                "Operator does not hold the admin role",
                precondition_failed="role_check",
                suggestion="Escalate to an admin-role operator",
            )
        return _service.get_kill_switch_contract_payload()

    # -------------------------------------------------------------------------
    # Route 6: GET /api/v1/operator/incident-response/{incident_id}
    # -------------------------------------------------------------------------
    @router.get("/api/v1/operator/incident-response/{incident_id}")
    async def get_incident_response(
        incident_id: str,
        snapshot: str = "preferred",
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """PKT-002 Incident Detail composed view."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        roles = getattr(identity, "roles", []) or []
        res = _service.get_incident_response(incident_id, roles=roles, snapshot=snapshot)
        if res is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Incident not found",
                f"Incident {incident_id} does not exist",
            )
        return res

    # -------------------------------------------------------------------------
    # Route 7: GET /api/v1/operator/post-incident-review/{incident_id}
    # -------------------------------------------------------------------------
    @router.get("/api/v1/operator/post-incident-review/{incident_id}")
    async def get_post_incident_review(
        incident_id: str,
        snapshot: str = "preferred",
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Composed view for post-incident review."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        res = _service.get_post_incident_review(incident_id, snapshot=snapshot)
        if res is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Incident not found",
                f"Incident {incident_id} does not exist",
            )
        return res

    # -------------------------------------------------------------------------
    # Route 8: GET /bff/risk/alerts
    # -------------------------------------------------------------------------
    @router.get("/bff/risk/alerts")
    async def bff_list_risk_alerts(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: list risk/operator alerts."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        return _build_alerts_payload(snapshot_at)

    # -------------------------------------------------------------------------
    # Route 9: GET /bff/risk/alerts/{alert_id}
    # -------------------------------------------------------------------------
    @router.get("/bff/risk/alerts/{alert_id}")
    async def bff_get_risk_alert(
        alert_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: get a specific risk alert by ID."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        payload = _build_alerts_payload(snapshot_at)
        clean_id = alert_id.strip()
        match = next(
            (a for a in payload.get("alerts", []) if str(a.get("alert_id") or a.get("id") or "") == clean_id),
            None,
        )
        if not match:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Risk alert not found",
                f"Alert {alert_id} does not exist",
            )
        return {
            "data": match,
            "meta": {"snapshot_at": snapshot_at, "staleness": _service.get_staleness()},
        }

    # -------------------------------------------------------------------------
    # Route 10: POST /bff/risk/alerts/{alert_id}/actions/{action_id}
    # -------------------------------------------------------------------------
    @router.post("/bff/risk/alerts/{alert_id}/actions/{action_id}", status_code=202)
    async def bff_risk_alert_action(
        alert_id: str,
        action_id: str,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: submit an action against a risk alert."""
        identity = _extract_ident(authorization)
        _require_operator(identity)
        resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        clean_id = alert_id.strip()

        if submit_action_command is not None:
            return submit_action_command(
                ObjectType.RISK_ALERT, clean_id, action_id, resolved_key, identity, payload, CommandType.RISK_ALERT_ACTION
            )
        return {
            "command_id": str(uuid.uuid4()),
            "status": "accepted",
            "entity_type": ObjectType.RISK_ALERT,
            "entity_id": clean_id,
            "action_id": action_id,
            "meta": {"idempotency_key": resolved_key},
        }

    # -------------------------------------------------------------------------
    # Route 11: GET /bff/incidents
    # -------------------------------------------------------------------------
    @router.get("/bff/incidents")
    async def bff_list_incidents(
        status: Optional[str] = None,
        severity: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: list incidents (execute-plans compatibility surface)."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        surface = _service.get_surface_status("incidents", snapshot_at=snapshot_at)
        incidents = _list_bff_inc(status=status, severity=severity, affected_pool_id=affected_pool_id)
        total = len(incidents)
        if surface.get("status") == "unavailable":
            incidents = []
            next_page_token = None
            total = 0
        else:
            incidents, next_page_token = _slice(incidents, page_token, page_size)

        meta = _snapshot_meta(snapshot_at)
        meta["surfaces"] = {"incidents": surface}
        meta["total"] = total
        staleness = _service.get_staleness()
        if staleness is not None:
            meta["staleness"] = staleness
        degradation_reason = _degradation_reason(
            surface,
            degraded_reason="Incident list is degraded and may be stale.",
            unavailable_reason="Incident list is currently unavailable.",
        )
        if degradation_reason is not None:
            meta["degradation"] = {"reason": degradation_reason}

        return {
            "data": incidents,
            "items": incidents,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": meta,
        }

    # -------------------------------------------------------------------------
    # Route 12: POST /bff/incidents
    # -------------------------------------------------------------------------
    @router.post("/bff/incidents", status_code=201)
    async def bff_create_incident(
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: create a new incident record."""
        identity = _extract_ident(authorization)
        _require_operator(identity)
        resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        _reject_key(payload)

        existing = _idem_ledger.get(resolved_key)
        req_hash = _stable_json_hash(payload)
        if existing is not None:
            if existing.get("request_hash") != req_hash:
                raise _err(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key already used with a different payload",
                    f"Key {resolved_key!r} is bound to a different request hash",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                )
            return existing["result"]

        incident_id = str(payload.get("incident_id") or payload.get("id") or uuid.uuid4())
        submitted_at = _utc_now()
        operator_id = getattr(identity, "operator_id", "operator")
        result = _project_bff_incident_case({
            **payload,
            "id": incident_id,
            "incident_id": incident_id,
            "status": payload.get("status") or "open",
            "submitted_at": submitted_at,
            "created_at": payload.get("created_at") or payload.get("opened_at") or submitted_at,
            "updated_at": submitted_at,
            "submitted_by": operator_id,
            "title": payload.get("title") or "Untitled Incident",
            "severity": payload.get("severity") or "medium",
            "capital_pool_id": payload.get("capital_pool_id") or payload.get("affected_pool_id"),
            "runtime_id": payload.get("runtime_id"),
            "correlation_id": payload.get("correlation_id") or incident_id,
            "trace_id": payload.get("trace_id") or payload.get("correlation_id") or incident_id,
            "audit_ref": {
                "target_type": "Incident",
                "target_id": incident_id,
                "href": f"/bff/audit/entities/Incident/{incident_id}",
            },
            "meta": {"idempotency_key": resolved_key},
        })
        _inc_overlay[incident_id] = result
        _service._incident_overlay[incident_id] = result
        _idem_ledger[resolved_key] = {"request_hash": req_hash, "result": result}
        _service._idempotency_ledger[resolved_key] = {"request_hash": req_hash, "result": result}
        return result

    # -------------------------------------------------------------------------
    # Route 13: GET /bff/incidents/{incident_id}
    # -------------------------------------------------------------------------
    @router.get("/bff/incidents/{incident_id}")
    async def bff_get_incident(
        incident_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: get a specific incident by ID."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        clean_id = incident_id.strip()
        snapshot_at = _utc_now()
        surface = _service.get_surface_status("incidents", snapshot_at=snapshot_at)
        incident = _get_bff_inc(clean_id)
        if not incident:
            _raise_unavailable(surface, label="Incident")
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Incident not found",
                f"Incident {incident_id} does not exist",
            )

        return {
            "data": incident,
            "meta": _read_meta("incidents", "incident", snapshot_at=snapshot_at, surface=surface),
        }

    # -------------------------------------------------------------------------
    # Route 14: POST /bff/incidents/{incident_id}/actions/{action_id}
    # -------------------------------------------------------------------------
    @router.post("/bff/incidents/{incident_id}/actions/{action_id}", status_code=202)
    async def bff_incident_action(
        incident_id: str,
        action_id: str,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: submit an action against an incident."""
        identity = _extract_ident(authorization)
        _require_operator(identity)
        resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        clean_id = incident_id.strip()
        snapshot_at = _utc_now()
        surface = _service.get_surface_status("incidents", snapshot_at=snapshot_at)
        incident = _get_bff_inc(clean_id)
        if not incident:
            _raise_unavailable(surface, label="Incident")
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Incident not found",
                f"Incident {incident_id} does not exist",
            )

        if submit_action_command is not None:
            return submit_action_command(
                ObjectType.INCIDENT, clean_id, action_id, resolved_key, identity, payload, CommandType.INCIDENT_ACTION
            )
        return {
            "command_id": str(uuid.uuid4()),
            "status": "accepted",
            "entity_type": ObjectType.INCIDENT,
            "entity_id": clean_id,
            "action_id": action_id,
            "meta": {"idempotency_key": resolved_key},
        }

    # -------------------------------------------------------------------------
    # Route 15: GET /bff/alerts
    # -------------------------------------------------------------------------
    @router.get("/bff/alerts")
    async def bff_list_alerts(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: source-reference compatibility alias for /bff/risk/alerts."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        if run_management_read is not None:
            try:
                return await run_management_read(_build_alerts_payload, snapshot_at)
            except Exception:
                return _service.management_alerts_degraded_payload(snapshot_at)
        return _build_alerts_payload(snapshot_at)

    # -------------------------------------------------------------------------
    # Route 16: GET /bff/alerts/{alert_id}
    # -------------------------------------------------------------------------
    @router.get("/bff/alerts/{alert_id}")
    async def bff_get_alert(
        alert_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: get a specific operator alert by ID."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        payload = _build_alerts_payload(snapshot_at)
        clean_id = alert_id.strip()
        match = next(
            (a for a in payload.get("alerts", []) if str(a.get("alert_id") or a.get("id") or "") == clean_id),
            None,
        )
        if not match:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Alert not found",
                f"Alert {alert_id!r} does not exist",
                precondition_failed="alert_id",
            )
        detail_meta = payload.get("meta", {"snapshot_at": snapshot_at, "staleness": _service.get_staleness()})
        return {"data": match, "meta": detail_meta}

    # -------------------------------------------------------------------------
    # Route 17: POST /bff/alerts/{alert_id}/acknowledge
    # -------------------------------------------------------------------------
    @router.post("/bff/alerts/{alert_id}/acknowledge", status_code=202)
    async def bff_alert_acknowledge(
        alert_id: str,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: acknowledge an operator alert."""
        identity = _extract_ident(authorization)
        _require_operator(identity)

        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        _reject_key(payload)

        clean_id = alert_id.strip()
        resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
        request_hash = _stable_json_hash({"alert_id": clean_id, "action": "acknowledge", "payload": payload})

        existing = _idem_ledger.get(resolved_key)
        if existing is not None:
            if existing.get("request_hash") != request_hash:
                raise _err(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was already used with a different payload",
                    f"Key {resolved_key!r} is bound to a different request hash",
                    precondition_failed="idempotency_conflict",
                    suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                )
            return existing["result"]

        snapshot_at = _utc_now()
        alerts_payload = _build_alerts_payload(snapshot_at)
        alert_record = next(
            (a for a in alerts_payload.get("alerts", []) if str(a.get("alert_id") or a.get("id") or "") == clean_id),
            None,
        )
        if alert_record is None:
            alert_surface = (alerts_payload.get("meta") or {}).get("surfaces", {}).get("alerts", {})
            if alert_surface.get("status") not in {"degraded", "unavailable", "missing"}:
                raise _err(
                    404,
                    ErrorCode.RESOURCE_NOT_FOUND,
                    "Alert not found",
                    f"Alert {alert_id!r} does not exist or is no longer active",
                    precondition_failed="alert_id",
                )

        command_id = str(uuid.uuid4())
        submitted_at = snapshot_at
        operator_id = getattr(identity, "operator_id", "operator")
        ack_note = str(payload.get("note") or payload.get("reason") or "").strip() or None

        cmd_store = _service.get_command_store()
        if cmd_store and hasattr(cmd_store, "submit_command"):
            try:
                cmd_store.submit_command(
                    command_id=command_id,
                    command_type=CommandType.ALERT_ACKNOWLEDGE,
                    target={"type": ObjectType.RISK_ALERT, "id": clean_id},
                    submitted_at=submitted_at,
                    params={"alert_id": clean_id, "action": "acknowledge", **payload},
                )
            except Exception as e:
                log.warning("command_store.submit_command failed: %s", e)

        _ack_alerts[clean_id] = {
            "acknowledged_by": operator_id,
            "acknowledged_at": submitted_at,
            "note": ack_note,
        }
        _service._acknowledged_alerts[clean_id] = _ack_alerts[clean_id]

        result = {
            "command_id": command_id,
            "command": CommandType.ALERT_ACKNOWLEDGE,
            "accepted_at": submitted_at,
            "status": CommandStatus.SUBMITTED,
            "data": {
                "alert_id": clean_id,
                "status": "acknowledged",
                "acknowledged_at": submitted_at,
            },
            "meta": {"idempotency_key": resolved_key, "snapshot_at": snapshot_at},
        }
        _idem_ledger[resolved_key] = {"request_hash": request_hash, "result": result}
        _service._idempotency_ledger[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    # -------------------------------------------------------------------------
    # Route 18: GET /bff/audit
    # -------------------------------------------------------------------------
    @router.get("/bff/audit")
    async def bff_list_audit(
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_type: Optional[str] = None,
        from_: Optional[datetime] = Query(default=None, alias="from"),
        to: Optional[datetime] = Query(default=None),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: governance audit event list with filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None
        events = _list_audit(
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_,
            to_ts=to,
        )
        total = len(events)
        page_items, next_page_token = _slice(events, page_token, page_size)
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _read_meta(
                "governance_audit_events", "audit_list",
                snapshot_at=snapshot_at, total=total,
            ),
        }

    # -------------------------------------------------------------------------
    # Route 19: GET /bff/audit/events
    # -------------------------------------------------------------------------
    @router.get("/bff/audit/events")
    async def bff_list_audit_events(
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: list governance audit events (execute-plans compatibility)."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None
        from_dt = _parse_rfc3339(from_ts) if from_ts else None
        to_dt = _parse_rfc3339(to_ts) if to_ts else None
        events = _list_audit(
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_dt,
            to_ts=to_dt,
        )
        events_slice, next_page_token = _slice(events, page_token, page_size)
        return {
            "events": events_slice,
            "page_info": {"next_page_token": next_page_token},
            "meta": {"snapshot_at": snapshot_at, "staleness": _service.get_staleness()},
        }

    # -------------------------------------------------------------------------
    # Route 20: GET /bff/audit/entities/{entity_type}/{entity_id}
    # -------------------------------------------------------------------------
    @router.get("/bff/audit/entities/{entity_type}/{entity_id}")
    async def bff_get_entity_audit(
        entity_type: str,
        entity_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: get audit trail for a specific entity."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        clean_type = entity_type.strip()
        clean_id = entity_id.strip()

        events = _list_audit(target_type=clean_type)
        entity_events = [
            e for e in events
            if str(e.get("target_id") or e.get("entity_id") or "") == clean_id
        ]
        entity_events_slice, next_page_token = _slice(entity_events, page_token, page_size)
        return {
            "entity_type": clean_type,
            "entity_id": clean_id,
            "events": entity_events_slice,
            "page_info": {"next_page_token": next_page_token},
            "meta": {"snapshot_at": snapshot_at, "staleness": _service.get_staleness()},
        }

    # -------------------------------------------------------------------------
    # Route 21: GET /bff/audit/export
    # -------------------------------------------------------------------------
    @router.get("/bff/audit/export")
    async def bff_audit_export(
        actor: Optional[str] = None,
        action_type: Optional[str] = None,
        target_type: Optional[str] = None,
        from_ts: Optional[str] = None,
        to_ts: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: export governance audit events as structured payload."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None
        from_dt = _parse_rfc3339(from_ts) if from_ts else None
        to_dt = _parse_rfc3339(to_ts) if to_ts else None
        events = _list_audit(
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_dt,
            to_ts=to_dt,
        )
        return {
            "events": events,
            "total": len(events),
            "exported_at": snapshot_at,
            "meta": {"snapshot_at": snapshot_at, "staleness": _service.get_staleness()},
        }

    # -------------------------------------------------------------------------
    # Route 22: POST /bff/audit/export
    # -------------------------------------------------------------------------
    @router.post("/bff/audit/export", status_code=202)
    async def sem_audit_export_command(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        """BFF: trigger audit export command."""
        identity = _extract_ident(authorization)
        _require_operator(identity)
        resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
        snapshot_at = _utc_now()
        if submit_sem_command is not None:
            return submit_sem_command(
                command_type=CommandType.AUDIT_EXPORT,
                target_type=ObjectType.AUDIT_EXPORT,
                target_id=str(payload.get("target_type") or payload.get("targetType") or "audit-export"),
                payload=payload,
                identity=identity,
                idempotency_key=idempotency_key,
                x_idempotency_key=x_idempotency_key,
            )
        return {
            "command_id": str(uuid.uuid4()),
            "status": "accepted",
            "command_type": CommandType.AUDIT_EXPORT,
            "target_type": ObjectType.AUDIT_EXPORT,
            "target_id": str(payload.get("target_type") or payload.get("targetType") or "audit-export"),
            "data": {"id": "audit-export", "status": "accepted"},
            "meta": {"snapshot_at": snapshot_at, "idempotency_key": resolved_key},
        }

    # -------------------------------------------------------------------------
    # Route 23-27: Generic Incident & Alert Command Handlers
    # -------------------------------------------------------------------------
    @router.post("/bff/alerts/{id}/escalate-incident", status_code=202)
    @router.post("/bff/incidents/{id}/append-postmortem", status_code=202)
    @router.post("/bff/incidents/{id}/resolve", status_code=202)
    @router.post("/bff/incidents/{id}/rollback-deployment", status_code=202)
    @router.post("/bff/incidents/{id}/start-mitigation", status_code=202)
    async def sem_final_generic_id_command_alias(
        id: str,
        request: Request,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
        x_dry_run: Optional[str] = Header(default=None, alias="X-Dry-Run"),
    ):
        """BFF: semantic commands for incident & alert mutations."""
        identity = _extract_ident(authorization)
        _require_operator(identity)
        snapshot_at = _utc_now()
        is_dry_run = request_dry_run_requested(x_dry_run) if request_dry_run_requested else bool(x_dry_run and x_dry_run.strip().lower() in ("1", "true", "yes"))

        if is_dry_run:
            resolved_key = _resolve_key(idempotency_key, x_idempotency_key)
            route_path = str(getattr(request.scope.get("route"), "path", "") or request.url.path)
            if dry_run_success_response is not None:
                return dry_run_success_response(
                    {
                        "id": id,
                        "status": "accepted",
                        "route": route_path,
                        "params": jsonable_encoder(payload or {}),
                        "submitted_by": getattr(identity, "operator_id", "operator"),
                    },
                    snapshot_at=snapshot_at,
                    idempotency_key=resolved_key,
                    evidence_kind="generic_id_command.preview",
                )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "accepted",
                    "preview": True,
                    "data": {
                        "id": id,
                        "status": "accepted",
                        "route": route_path,
                        "params": payload,
                    },
                    "meta": {"snapshot_at": snapshot_at, "idempotency_key": resolved_key},
                },
            )

        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "data": {"id": id, "status": "accepted"},
                "meta": {"snapshot_at": snapshot_at},
            },
        )

    return router


create_incidents_router = create_incident_router

"""Capital Allocation domain router.

This router owns the 25 Capital Allocation decorators catalogued for
``OPGAP-BE-CAPITAL-ROUTER-V2-20260830``.  It has no import of ``bff.main``;
the composition root supplies the current read-store, Capital Allocation
Manager client, auth guards, and response helpers when it mounts the router.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Query, Request

from services.control_plane.bff.models import ErrorCode
from services.control_plane.bff.shared.cross_domain_utils import _surface_degradation_reason

from .service import (
    CapitalAuthorityUnavailable,
    CapitalNotFound,
    CapitalService,
    CapitalValidationError,
    capital_pool_id,
    filter_records,
    first_present,
    pool_risk_limits,
    rebalance_id,
    stable_digest,
)

PageSlice = Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_page_slice(
    items: Sequence[Any], page_token: Optional[str], page_size: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        start = max(0, int(page_token)) if page_token else 0
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    return list(items[start:end]), str(end) if end < len(items) else None


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _default_dataset_surface_status(dataset: str, *, snapshot_at: str, **_: Any) -> Dict[str, Any]:
    return {"status": "ok", "dataset": dataset, "snapshot_at": snapshot_at, "source": "capital_router"}


def _default_extract_identity(_: Optional[str] = None) -> Any:
    class Identity:
        operator_id = "operator-1"
        roles = {"admin", "operator", "approver", "viewer"}

    return Identity()


def _default_require_read_role(_: Any) -> None:
    return None


def _default_require_operator_role(_: Any) -> None:
    return None


def _default_bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **details: Any) -> HTTPException:
    error_code = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": error_code, "message": message, "reason": reason or message, **details}},
    )


def _identity_id(identity: Any) -> str:
    return str(getattr(identity, "operator_id", None) or getattr(identity, "id", None) or "operator-1")


def _caller_allowed_tenants(identity: Any) -> Tuple[List[str], bool]:
    if identity is None:
        return [], False
    claims = getattr(identity, "claims", None) or {}
    raw: List[str] = []
    for k in ("allowed_tenants", "allowedTenants", "tenant_ids", "tenantIds", "tenants", "tenant_id", "tenantId", "tenant.id", "tenant", "tid"):
        val = getattr(identity, k, None) or (claims.get(k) if isinstance(claims, dict) else None)
        if isinstance(val, (list, tuple, set)):
            raw.extend(str(item).strip() for item in val if str(item).strip())
        elif isinstance(val, str) and val.strip():
            raw.extend(t.strip() for t in val.split(",") if t.strip())
    return [t for t in dict.fromkeys(raw) if t and t != "*"], ("*" in raw)


def _resolve_tenant(
    identity: Any,
    request_tenant: Optional[str],
    bff_error: Callable[..., Exception],
) -> Optional[str]:
    concrete, has_wildcard = _caller_allowed_tenants(identity)
    clean_req = str(request_tenant or "").strip() or None
    if clean_req:
        if clean_req == "*":
            raise bff_error(400, ErrorCode.VALIDATION_FAILED, "Wildcard tenant cannot be targeted for Capital writes", "TENANT_REQUIRED")
        if not has_wildcard and clean_req not in concrete:
            raise bff_error(403, ErrorCode.FORBIDDEN, f"Tenant {clean_req!r} is outside the caller scope", "TENANT_SCOPE_FORBIDDEN")
        return clean_req
    if not has_wildcard and len(concrete) == 1:
        return concrete[0]
    if has_wildcard or len(concrete) > 1:
        raise bff_error(400, ErrorCode.VALIDATION_FAILED, "X-Tenant-Id is required for Capital mutations", "TENANT_REQUIRED")
    return None


def _resolve_idempotency_key(
    idempotency_key: Optional[str], x_idempotency_key: Optional[str]
) -> str:
    first = str(idempotency_key or "").strip()
    second = str(x_idempotency_key or "").strip()
    if first and second and first != second:
        raise CapitalValidationError("Idempotency-Key and X-Idempotency-Key must match when both are supplied")
    return first or second


def _owner_actor_role(identity: Any) -> str:
    """Role asserted to the owner: one the verified identity actually holds, never an injected one."""
    roles = set(getattr(identity, "roles", set()) or set())
    return next((role for role in ("operator", "approver", "admin") if role in roles), "")


_OWNER_HTTP_ERRORS = {
    400: ErrorCode.VALIDATION_FAILED, 401: ErrorCode.AUTH_REQUIRED, 403: ErrorCode.FORBIDDEN,
    404: ErrorCode.RESOURCE_NOT_FOUND, 409: ErrorCode.RESOURCE_CONFLICT, 422: ErrorCode.VALIDATION_FAILED,
}


def _error_for_capital_exception(exc: Exception, bff_error: Callable[..., Exception]) -> Exception:
    if isinstance(exc, urllib.error.HTTPError):
        code = _OWNER_HTTP_ERRORS.get(exc.code)
        if code is None:
            return bff_error(503 if exc.code >= 500 else 502, ErrorCode.DEPENDENCY_UNAVAILABLE, "Capital owner request failed", f"owner returned HTTP {exc.code}")
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                detail = payload.get("detail") or (payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}).get("message") or payload.get("error") or payload.get("message")
            else:
                detail = str(payload)
        except Exception:
            detail = None
        return bff_error(exc.code, code, "Capital owner rejected the request", str(detail or exc.reason))
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, json.JSONDecodeError, CapitalAuthorityUnavailable)):
        return bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Capital authority unavailable", str(exc))
    if isinstance(exc, CapitalNotFound):
        return bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Capital resource not found", str(exc))
    if isinstance(exc, (ValueError, CapitalValidationError)):
        code = ErrorCode.IDEMPOTENCY_CONFLICT if "Idempotency key" in str(exc) else ErrorCode.VALIDATION_FAILED
        return bff_error(409 if code == ErrorCode.IDEMPOTENCY_CONFLICT else 422, code, "Capital request validation failed", str(exc))
    if isinstance(exc, RuntimeError):  # owner answered, but not with the record that was requested
        return bff_error(502, ErrorCode.UPSTREAM_ERROR, "Capital owner returned an unexpected result", str(exc))
    return exc


def stable_capital_resource_id(
    prefix: str,
    *,
    operator_id: str,
    idempotency_key: str,
    requested_id: Any = None,
) -> str:
    """Return the caller's id, or one derived from the key so a retry reaches the same owner record."""
    explicit = str(requested_id or "").strip()
    if explicit:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", explicit):
            from ..auth.policy import bff_error
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {prefix} identity",
                "Stable resource ids must be 3-128 URL-safe characters",
                precondition_failed=f"{prefix}_id",
            )
        return explicit
    digest = hashlib.sha256(f"{operator_id}\x00{idempotency_key}\x00{prefix}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def capital_owner_role(identity: Any) -> str:
    return next((role for role in ("admin", "approver", "operator", "reviewer") if role in identity.roles), "operator")


def raise_capital_owner_error(exc: Exception, *, operation: str) -> None:
    """Raise the BFF error for a Capital owner failure; return for exceptions it does not map."""
    from ..auth.policy import bff_error
    mapped = _error_for_capital_exception(exc, bff_error)
    if mapped is not exc:
        raise mapped from exc


def _surface_meta(
    *,
    snapshot_at: str,
    dataset: str,
    surface_key: str,
    dataset_surface_status: SurfaceStatus,
    snapshot_meta: SnapshotMeta,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    meta = snapshot_meta(snapshot_at)
    meta["surfaces"] = {surface_key: dataset_surface_status(dataset, snapshot_at=snapshot_at)}
    if total is not None:
        meta["total"] = total
    return meta


def _readback_response(data: Any, *, meta: Dict[str, Any], items: Optional[List[Any]] = None, next_page_token: Optional[str] = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {"data": data, "meta": meta}
    if items is not None:
        response["items"] = items
        response["page_info"] = {"next_page_token": next_page_token, "total": meta.get("total", len(items))}
    return response


def create_capital_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    get_capital_authority: Optional[Callable[[], Any]] = None,
    extract_identity: Callable[[Optional[str]], Any] = _default_extract_identity,
    require_read_role: Callable[[Any], None] = _default_require_read_role,
    require_operator_role: Callable[[Any], None] = _default_require_operator_role,
    utc_now: Callable[[], str] = _default_utc_now,
    page_slice: PageSlice = _default_page_slice,
    snapshot_meta: SnapshotMeta = _default_snapshot_meta,
    dataset_surface_status: SurfaceStatus = _default_dataset_surface_status,
    bff_error: Callable[..., Exception] = _default_bff_error,
) -> APIRouter:
    """Build the standalone Capital router and its explicit dependency boundary."""
    if read_surface is not None:
        resolved_get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
    elif get_read_store is not None:
        resolved_get_read_store = get_read_store
    else:
        resolved_get_read_store = lambda: None

    router = APIRouter(tags=["capital"])
    service = CapitalService(
        get_read_store=resolved_get_read_store,
        get_capital_authority=get_capital_authority,
        utc_now=utc_now,
    )

    def _require_read(authorization: Optional[str]) -> Any:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return identity

    def _require_operator(authorization: Optional[str]) -> Any:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        return identity

    def _status(dataset: str, snapshot_at: Optional[str] = None, **kwargs: Any) -> Dict[str, Any]:
        st = resolved_get_read_store()
        fn = getattr(st, "dataset_surface_status", None)
        if fn is not None and type(st).__name__ != "ReadSurfacePorts":
            return fn(dataset, snapshot_at=snapshot_at or utc_now(), **kwargs)
        if hasattr(st, "dataset_source") and callable(st.dataset_source):
            src = st.dataset_source(dataset)
            if src in ("missing", "unavailable"):
                return {"status": "unavailable", "source": src, "snapshot_at": snapshot_at or utc_now(), "message": f"{dataset} source unavailable"}
            if src:
                return {"status": "ok", "source": src, "snapshot_at": snapshot_at or utc_now()}
        fn = fn or dataset_surface_status
        return fn(dataset, snapshot_at=snapshot_at or utc_now(), **kwargs)

    def _raise_if_unavailable(surface: Dict[str, Any], label: str) -> None:
        if surface.get("status") == "unavailable":
            reason = str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable.")
            raise bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, f"{label} read surface unavailable", reason, precondition_failed="read_surface_unavailable", suggestion="Verify the owning service URL and health before retrying this read.")

    def _meta(snapshot_at: str, dataset: str, surface_key: str, total: Optional[int] = None) -> Dict[str, Any]:
        return _surface_meta(snapshot_at=snapshot_at, dataset=dataset, surface_key=surface_key, dataset_surface_status=_status, snapshot_meta=snapshot_meta, total=total)

    def _pool_or_error(pool_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        try:
            return service.get_pool(pool_id)
        except Exception as exc:
            _raise_if_unavailable(_status("capital_pools", snapshot_at), "Capital pool")
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _rebalance_or_error(requested_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        try:
            return service.get_rebalance(requested_id)
        except Exception as exc:
            _raise_if_unavailable(_status("capital_pools", snapshot_at), "Capital pool")
            _raise_if_unavailable(_status("rebalances", snapshot_at), "Rebalance")
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _idempotent_write(operation: str, payload: Dict[str, Any], *, identity: Any, authorization: Optional[str], key: str, target_id: Optional[str] = None, tenant_id: Optional[str] = None) -> Tuple[Dict[str, Any], bool]:
        actor_id = _identity_id(identity)
        tid = tenant_id if tenant_id is not None else _resolve_tenant(identity, None, bff_error)
        try:
            replay = service.idempotent(actor_id=actor_id, key=key, operation=operation, payload=payload, target_id=target_id, tenant_id=tid)
            if replay is not None:
                return replay, True
            result = service.write(operation, payload, actor_id=actor_id, actor_role=_owner_actor_role(identity), auth_token=authorization, key=key, target_id=target_id, tenant_id=tid)
            service.remember(actor_id=actor_id, key=key, operation=operation, payload=payload, response=result, target_id=target_id, tenant_id=tid)
            return result, False
        except HTTPException:
            raise
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _mutate(
        op: str, payload: Dict[str, Any], *, identity: Any, authorization: Optional[str],
        x_tenant_id: Optional[str], idempotency_key: Optional[str], x_idempotency_key: Optional[str],
        target_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        tid = _resolve_tenant(identity, x_tenant_id, bff_error)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write(op, payload, identity=identity, authorization=authorization, key=key, target_id=target_id, tenant_id=tid)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 1. Legacy Capital Pool read surface.
    @router.get("/api/v1/capital-pools")
    async def list_capital_pools(
        status: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_read(authorization)
        snapshot_at = utc_now()
        try:
            pools = service.list_pools(status=status, risk_policy_ref=risk_policy_ref)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        items, next_page_token = page_slice(pools, page_token, page_size)
        return _readback_response(items, meta=_meta(snapshot_at, "capital_pools", "capital_pools", total=len(pools)), items=items, next_page_token=next_page_token)

    # 2. Legacy Capital Pool detail surface.
    @router.get("/api/v1/capital-pools/{pool_id}")
    async def get_capital_pool(
        pool_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        snapshot_at = utc_now()
        return _readback_response(_pool_or_error(pool_id), meta=_meta(snapshot_at, "capital_pools", "capital_pool"))

    # 3. BFF Capital Pool list.
    @router.get("/bff/capital-pools")
    async def bff_list_capital_pools(
        status: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await list_capital_pools(status, risk_policy_ref, page_token, page_size, authorization)

    # 4. Create a pool through the Capital Allocation Manager.
    @router.post("/bff/capital-pools", status_code=201)
    async def bff_create_capital_pool(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _require_operator(authorization)
        if not str(payload.get("name") or "").strip():
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Capital pool name is required", "name must be a non-empty string")
        return _mutate("create_pool", payload, identity=identity, authorization=authorization, x_tenant_id=x_tenant_id, idempotency_key=idempotency_key, x_idempotency_key=x_idempotency_key)

    # 5. BFF Capital Pool detail.
    @router.get("/bff/capital-pools/{pool_id}")
    async def bff_get_capital_pool(
        pool_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        snapshot_at = utc_now()
        pool_surface = _status("capital_pools", snapshot_at)
        pool = _pool_or_error(pool_id, snapshot_at)
        st = resolved_get_read_store()
        bindings = st.get_bindings_for_pool(pool_id) if hasattr(st, "get_bindings_for_pool") else []
        allocations = st.list_capital_allocations(capital_pool_id=pool_id) if hasattr(st, "list_capital_allocations") else []
        binding_surface = _status("persona_bindings", snapshot_at)
        alloc_source = getattr(st, "dataset_source", lambda _: "canonical")("capital_allocations") if hasattr(st, "dataset_source") else "canonical"
        data = {
            **pool,
            "bindings": bindings,
            "allocations": allocations,
            "authoritative_capital_readback": (
                bool(allocations)
                and alloc_source in {"service_client", "canonical"}
                and all(a.get("authoritative_capital_readback") is True for a in allocations)
            ),
        }
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "capital_pool_detail": pool_surface,
            "persona_bindings": binding_surface,
            "capital_allocations": _status("capital_allocations", snapshot_at, has_data=bool(allocations)),
        }
        reason = _surface_degradation_reason(
            binding_surface,
            degraded_reason="persona bindings are degraded and may be stale.",
            unavailable_reason="persona bindings are currently unavailable.",
        )
        if reason is not None:
            meta.setdefault("degradation", {})["persona_bindings_reason"] = reason
        return {"data": data, "meta": meta}

    # 7. Capital pool action command.
    @router.post("/bff/capital-pools/{pool_id}/actions/{action_id}", status_code=202)
    async def bff_capital_pool_action(
        pool_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _require_operator(authorization)
        _pool_or_error(pool_id)
        if not str(action_id).strip():
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Capital pool action is required")
        return _mutate("pool_action", {**payload, "action_id": action_id}, identity=identity, authorization=authorization, x_tenant_id=x_tenant_id, idempotency_key=idempotency_key, x_idempotency_key=x_idempotency_key, target_id=pool_id)

    # 8. Evaluate one policy snapshot before a rebalance proposal is admitted.
    @router.post("/bff/management/allocation-policy/evaluate")
    async def bff_evaluate_persona_allocation_policy(
        payload: Dict[str, Any] = Body(...), authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        try:
            evaluation = service.evaluate_allocation_policy(payload)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        return _readback_response(evaluation, meta={"snapshot_at": utc_now(), "allocation_digest": evaluation["allocation_digest"]})

    # 11. Rebalance list.
    @router.get("/bff/rebalances")
    async def bff_list_rebalances(
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        _require_read(authorization)
        snapshot_at = utc_now()
        try:
            rows = service.list_rebalances(status=status, capital_pool_id_value=capital_pool_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        items, next_page_token = page_slice(rows, page_token, page_size)
        return _readback_response(items, meta=_meta(snapshot_at, "rebalances", "rebalances", total=len(rows)), items=items, next_page_token=next_page_token)

    # 12. Create a rebalance proposal through the owner.
    @router.post("/bff/rebalances", status_code=201)
    async def bff_create_rebalance(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _require_operator(authorization)
        pool_id = str(payload.get("capital_pool_id") or payload.get("pool_id") or "").strip()
        if not pool_id:
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "capital_pool_id is required")
        _pool_or_error(pool_id)
        return _mutate("create_rebalance", payload, identity=identity, authorization=authorization, x_tenant_id=x_tenant_id, idempotency_key=idempotency_key, x_idempotency_key=x_idempotency_key)

    # 13. Apply an already admitted rebalance proposal through the capital owner.
    @router.post("/bff/rebalances/{rebalance_id}/apply", status_code=202)
    async def bff_apply_rebalance_proposal(
        rebalance_id: str, request: Request, background_tasks: BackgroundTasks,
        payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = _require_operator(authorization)
        _rebalance_or_error(rebalance_id)
        if not str(x_confirm_token or "").strip():
            raise bff_error(428, ErrorCode.CONFIRMATION_REQUIRED, "Confirmation token is required before this action can be accepted", "CONFIRM_TOKEN_MISSING")
        tid = _resolve_tenant(identity, x_tenant_id, bff_error)
        cas = getattr(getattr(getattr(request, "app", None), "state", None), "command_adapter_service", None)
        if cas is not None:
            cmd = {"command": "ApprovedApply", "target": {"type": "Rebalance", "id": rebalance_id},
                   "params": {"rebalance_id": rebalance_id, **({"tenant_id": tid} if tid else {}),
                              **{k: v for k, v in payload.items() if k not in {"audit_context", "reason", "tenant_id"}}},
                   "audit_context": payload.get("audit_context") if "audit_context" in payload else {"reason": payload.get("reason")}}
            return cas.submit_command_admission(
                background_tasks=background_tasks, payload=cmd, authorization=authorization, x_confirm_token=x_confirm_token,
                x_trace_id=request.headers.get("X-Trace-Id"), x_correlation_id=request.headers.get("X-Correlation-Id"), x_request_id=request.headers.get("X-Request-Id"),
                idempotency_key=idempotency_key, x_idempotency_key=x_idempotency_key, source_route="POST /bff/rebalances/{rebalance_id}/apply", include_durable_meta=True,
            )
        return _mutate("apply_rebalance", payload, identity=identity, authorization=authorization, x_tenant_id=x_tenant_id, idempotency_key=idempotency_key, x_idempotency_key=x_idempotency_key, target_id=rebalance_id)

    # 14. Rebalance detail.
    @router.get("/bff/rebalances/{rebalance_id}")
    async def bff_get_rebalance(
        rebalance_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        snapshot_at = utc_now()
        return _readback_response(_rebalance_or_error(rebalance_id), meta=_meta(snapshot_at, "rebalances", "rebalance"))

    def _portfolio_or_error() -> List[Dict[str, Any]]:
        try:
            return service.portfolio_rows()
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _project_allocations(
        capital_pool_id: Optional[str] = None, *, include_risk_limits: bool = False
    ) -> List[Dict[str, Any]]:
        rows = [r for r in _portfolio_or_error() if not capital_pool_id or r["capital_pool_id"] == capital_pool_id]
        return [
            {**alloc, "capital_pool_id": r["capital_pool_id"], **({"risk_limits": r["risk_limits"]} if include_risk_limits else {})}
            for r in rows for alloc in r["allocations"]
        ]

    # 16. Strategy allocation projection.
    @router.get("/bff/management/strategy-allocation")
    async def bff_management_strategy_allocation(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        allocations = _project_allocations(capital_pool_id, include_risk_limits=True)
        return _readback_response(allocations, meta={"snapshot_at": utc_now(), "total": len(allocations), "policy": "read_only_strategy_allocation"}, items=allocations)

    # 17. Capital flow projection from rebalance records.
    @router.get("/bff/management/capital-flow")
    async def bff_management_capital_flow(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        try:
            rows = service.list_rebalances(capital_pool_id_value=capital_pool_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        flow = [{
            "rebalance_id": rebalance_id(row),
            "capital_pool_id": first_present(row, "capital_pool_id", "pool_id", "target_pool_id"),
            "status": row.get("status"),
            "direction": row.get("direction") or row.get("action") or "rebalance",
            "allocation_digest": stable_digest(row.get("lines") or row.get("allocations") or row),
        } for row in rows]
        return _readback_response(flow, meta={"snapshot_at": utc_now(), "total": len(flow), "policy": "read_only_capital_flow"}, items=flow)

    # 18. Portfolio book root.
    @router.get("/bff/management/portfolio-book")
    async def bff_management_portfolio_book(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        rows = _portfolio_or_error()
        return _readback_response({"pools": rows, "pool_count": len(rows)}, meta={"snapshot_at": utc_now(), "policy": "read_only_portfolio_book"})

    # 19. Portfolio pool cards.
    @router.get("/bff/management/portfolio-book/pools")
    async def bff_management_portfolio_book_pools(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        rows = _portfolio_or_error()
        pools = [row["pool"] for row in rows]
        return _readback_response(pools, meta={"snapshot_at": utc_now(), "total": len(pools)}, items=pools)

    # 20. Portfolio exposure projection.
    @router.get("/bff/management/portfolio-book/exposure")
    async def bff_management_portfolio_book_exposure(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        rows = _portfolio_or_error()
        exposure = [{
            "capital_pool_id": row["capital_pool_id"],
            "risk_limits": row["risk_limits"],
            "allocation_count": row["allocation_count"],
            "allocation_digest": row["allocation_digest"],
        } for row in rows]
        return _readback_response(exposure, meta={"snapshot_at": utc_now(), "total": len(exposure)}, items=exposure)

    # 21. Portfolio holdings are the allocation rows with a durable pool identity.
    @router.get("/bff/management/portfolio-book/holdings")
    async def bff_management_portfolio_book_holdings(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        holdings = _project_allocations(capital_pool_id)
        return _readback_response(holdings, meta={"snapshot_at": utc_now(), "total": len(holdings)}, items=holdings)

    # 22. Positions reuse allocation facts but retain the capital risk boundary.
    @router.get("/bff/management/portfolio-book/positions")
    async def bff_management_portfolio_book_positions(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        positions = _project_allocations(capital_pool_id, include_risk_limits=True)
        return _readback_response(positions, meta={"snapshot_at": utc_now(), "total": len(positions)}, items=positions)

    # 23. Cost attribution is a read-only projection; the BFF never invents costs.
    @router.get("/bff/management/cost-attribution")
    async def bff_management_cost_attribution(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        rows = [r for r in _portfolio_or_error() if not capital_pool_id or r["capital_pool_id"] == capital_pool_id]
        costs = [
            {"capital_pool_id": r["capital_pool_id"], "allocation": alloc, "cost": cost if (cost := first_present(alloc, "cost", "cost_amount", "commission", "fees")) is not None else 0}
            for r in rows for alloc in r["allocations"]
        ]
        return _readback_response(costs, meta={"snapshot_at": utc_now(), "total": len(costs), "policy": "read_only_cost_attribution"}, items=costs)

    # 24. Compact operator board pack assembled solely from capital readbacks.
    @router.get("/bff/management/board-pack")
    async def bff_management_board_pack(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        _require_read(authorization)
        rows = _portfolio_or_error()
        try:
            rebalances = service.list_rebalances()
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        data = {
            "capital": {"pools": len(rows), "allocation_digests": {row["capital_pool_id"]: row["allocation_digest"] for row in rows}},
            "rebalances": {"total": len(rebalances), "active": len(filter_records(rebalances, status="proposed,approved,applying"))},
        }
        return _readback_response(data, meta={"snapshot_at": utc_now(), "policy": "read_only_capital_board_pack"})

    async def retired_write(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        _require_operator(authorization)
        raise bff_error(410, ErrorCode.OPERATION_NOT_ALLOWED, "Capital operation retired", "Capital has no owner endpoint for this write")

    # No Capital owner endpoint exists for these writes: they are retired, never simulated.
    for method, path in (
        ("PATCH", "/bff/capital-pools/{pool_id}"),
        ("PATCH", "/bff/rebalances/{rebalance_id}"),
        ("POST", "/bff/rebalances/{rebalance_id}/actions/{action_id}"),
    ):
        router.add_api_route(path, retired_write, methods=[method])

    return router


__all__ = ["create_capital_router"]

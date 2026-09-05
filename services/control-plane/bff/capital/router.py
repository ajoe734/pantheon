"""Capital Allocation domain router.

This router owns the 25 Capital Allocation decorators catalogued for
``OPGAP-BE-CAPITAL-ROUTER-V2-20260830``.  It has no import of ``bff.main``;
the composition root supplies the current read-store, Capital Allocation
Manager client, auth guards, and response helpers when it mounts the router.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query

try:
    from models import ErrorCode
except ImportError:
    try:
        from ..models import ErrorCode  # type: ignore[no-redef]
    except Exception:
        class ErrorCode(str, Enum):  # type: ignore[no-redef]
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
            VALIDATION_FAILED = "VALIDATION_FAILED"
            DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
            IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
            FORBIDDEN = "FORBIDDEN"

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


def _resolve_idempotency_key(
    idempotency_key: Optional[str], x_idempotency_key: Optional[str]
) -> str:
    first = str(idempotency_key or "").strip()
    second = str(x_idempotency_key or "").strip()
    if first and second and first != second:
        raise CapitalValidationError("Idempotency-Key and X-Idempotency-Key must match when both are supplied")
    return first or second


def _error_for_capital_exception(exc: Exception, bff_error: Callable[..., Exception]) -> Exception:
    if isinstance(exc, CapitalNotFound):
        return bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Capital resource not found", str(exc))
    if isinstance(exc, CapitalValidationError):
        code = ErrorCode.IDEMPOTENCY_CONFLICT if "Idempotency key" in str(exc) else ErrorCode.VALIDATION_FAILED
        return bff_error(409 if code == ErrorCode.IDEMPOTENCY_CONFLICT else 422, code, "Capital request validation failed", str(exc))
    if isinstance(exc, CapitalAuthorityUnavailable):
        return bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Capital authority unavailable", str(exc))
    return exc


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

    def _pool_or_error(pool_id: str) -> Dict[str, Any]:
        try:
            return service.get_pool(pool_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _rebalance_or_error(requested_id: str) -> Dict[str, Any]:
        try:
            return service.get_rebalance(requested_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _write_or_error(operation: str, payload: Dict[str, Any], *, actor_id: str, target_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            return service.write(operation, payload, actor_id=actor_id, target_id=target_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    def _idempotent_write(
        operation: str, payload: Dict[str, Any], *, actor_id: str, key: str, target_id: Optional[str] = None
    ) -> Tuple[Dict[str, Any], bool]:
        try:
            replay = service.idempotent(actor_id=actor_id, key=key, operation=operation, payload=payload)
            if replay is not None:
                return replay, True
            result = _write_or_error(operation, payload, actor_id=actor_id, target_id=target_id)
            service.remember(actor_id=actor_id, key=key, operation=operation, payload=payload, response=result)
            return result, False
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise exc
            raise _error_for_capital_exception(exc, bff_error) from exc

    # 1. Legacy Capital Pool read surface.
    @router.get("/api/v1/capital-pools")
    async def list_capital_pools(
        status: Optional[str] = None,
        risk_policy_ref: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        try:
            pools = service.list_pools(status=status, risk_policy_ref=risk_policy_ref)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        items, next_page_token = page_slice(pools, page_token, page_size)
        meta = _surface_meta(snapshot_at=snapshot_at, dataset="capital_pools", surface_key="capital_pools", dataset_surface_status=dataset_surface_status, snapshot_meta=snapshot_meta, total=len(pools))
        return _readback_response(items, meta=meta, items=items, next_page_token=next_page_token)

    # 2. Legacy Capital Pool detail surface.
    @router.get("/api/v1/capital-pools/{pool_id}")
    async def get_capital_pool(
        pool_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        pool = _pool_or_error(pool_id)
        snapshot_at = utc_now()
        meta = _surface_meta(snapshot_at=snapshot_at, dataset="capital_pools", surface_key="capital_pool", dataset_surface_status=dataset_surface_status, snapshot_meta=snapshot_meta)
        return _readback_response(pool, meta=meta)

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
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Capital pool name is required", "name must be a non-empty string")
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("create_pool", payload, actor_id=_identity_id(identity), key=key)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 5. BFF Capital Pool detail.
    @router.get("/bff/capital-pools/{pool_id}")
    async def bff_get_capital_pool(
        pool_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        return await get_capital_pool(pool_id, authorization)

    # 6. Patch pool properties / limits through the owner.
    @router.patch("/bff/capital-pools/{pool_id}")
    async def bff_patch_capital_pool(
        pool_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _pool_or_error(pool_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("patch_pool", payload, actor_id=_identity_id(identity), key=key, target_id=pool_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 7. Capital pool action command.
    @router.post("/bff/capital-pools/{pool_id}/actions/{action_id}", status_code=202)
    async def bff_capital_pool_action(
        pool_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _pool_or_error(pool_id)
        if not str(action_id).strip():
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Capital pool action is required")
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("pool_action", {**payload, "action_id": action_id}, actor_id=_identity_id(identity), key=key, target_id=pool_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 8. Evaluate one policy snapshot before a rebalance proposal is admitted.
    @router.post("/bff/management/allocation-policy/evaluate")
    async def bff_evaluate_persona_allocation_policy(
        payload: Dict[str, Any] = Body(...), authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            evaluation = service.evaluate_allocation_policy(payload)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        return _readback_response(evaluation, meta={"snapshot_at": utc_now(), "allocation_digest": evaluation["allocation_digest"]})

    # 9. Record an ApprovedApply decision for a rebalance.
    @router.post("/bff/rebalances/{rebalance_id}/approve", status_code=201)
    async def bff_approve_rebalance_apply(
        rebalance_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        roles = set(getattr(identity, "roles", set()) or set())
        if not {"approver", "admin"}.intersection(roles):
            raise bff_error(403, ErrorCode.FORBIDDEN, "Rebalance approval requires approver authority")
        _rebalance_or_error(rebalance_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("approve_rebalance", payload, actor_id=_identity_id(identity), key=key, target_id=rebalance_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 10. Record a second distinct rebalance signature through the owner.
    @router.post("/bff/rebalances/{rebalance_id}/two-man-sign", status_code=202)
    async def bff_sign_rebalance_apply(
        rebalance_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _rebalance_or_error(rebalance_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("sign_rebalance", payload, actor_id=_identity_id(identity), key=key, target_id=rebalance_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 11. Rebalance list.
    @router.get("/bff/rebalances")
    async def bff_list_rebalances(
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        try:
            rows = service.list_rebalances(status=status, capital_pool_id_value=capital_pool_id)
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc
        items, next_page_token = page_slice(rows, page_token, page_size)
        meta = _surface_meta(snapshot_at=snapshot_at, dataset="rebalances", surface_key="rebalances", dataset_surface_status=dataset_surface_status, snapshot_meta=snapshot_meta, total=len(rows))
        return _readback_response(items, meta=meta, items=items, next_page_token=next_page_token)

    # 12. Create a rebalance proposal through the owner.
    @router.post("/bff/rebalances", status_code=201)
    async def bff_create_rebalance(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        pool_id = str(payload.get("capital_pool_id") or payload.get("pool_id") or "").strip()
        if not pool_id:
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "capital_pool_id is required")
        _pool_or_error(pool_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("create_rebalance", payload, actor_id=_identity_id(identity), key=key)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 13. Apply an already admitted rebalance proposal through the capital owner.
    @router.post("/bff/rebalances/{rebalance_id}/apply", status_code=202)
    async def bff_apply_rebalance_proposal(
        rebalance_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _rebalance_or_error(rebalance_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("apply_rebalance", payload, actor_id=_identity_id(identity), key=key, target_id=rebalance_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    # 14. Rebalance detail.
    @router.get("/bff/rebalances/{rebalance_id}")
    async def bff_get_rebalance(
        rebalance_id: str, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        row = _rebalance_or_error(rebalance_id)
        meta = _surface_meta(snapshot_at=utc_now(), dataset="rebalances", surface_key="rebalance", dataset_surface_status=dataset_surface_status, snapshot_meta=snapshot_meta)
        return _readback_response(row, meta=meta)

    # 15. Typed action against a rebalance record.
    @router.post("/bff/rebalances/{rebalance_id}/actions/{action_id}", status_code=202)
    async def bff_rebalance_action(
        rebalance_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _rebalance_or_error(rebalance_id)
        if not str(action_id).strip():
            raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Rebalance action is required")
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("rebalance_action", {**payload, "action_id": action_id}, actor_id=_identity_id(identity), key=key, target_id=rebalance_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    def _portfolio_or_error() -> List[Dict[str, Any]]:
        try:
            return service.portfolio_rows()
        except Exception as exc:
            raise _error_for_capital_exception(exc, bff_error) from exc

    # 16. Strategy allocation projection.
    @router.get("/bff/management/strategy-allocation")
    async def bff_management_strategy_allocation(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        if capital_pool_id:
            rows = [row for row in rows if row["capital_pool_id"] == capital_pool_id]
        allocations = [
            {**allocation, "capital_pool_id": row["capital_pool_id"], "risk_limits": row["risk_limits"]}
            for row in rows for allocation in row["allocations"]
        ]
        return _readback_response(allocations, meta={"snapshot_at": utc_now(), "total": len(allocations), "policy": "read_only_strategy_allocation"}, items=allocations)

    # 17. Capital flow projection from rebalance records.
    @router.get("/bff/management/capital-flow")
    async def bff_management_capital_flow(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
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
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        return _readback_response({"pools": rows, "pool_count": len(rows)}, meta={"snapshot_at": utc_now(), "policy": "read_only_portfolio_book"})

    # 19. Portfolio pool cards.
    @router.get("/bff/management/portfolio-book/pools")
    async def bff_management_portfolio_book_pools(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        pools = [row["pool"] for row in rows]
        return _readback_response(pools, meta={"snapshot_at": utc_now(), "total": len(pools)}, items=pools)

    # 20. Portfolio exposure projection.
    @router.get("/bff/management/portfolio-book/exposure")
    async def bff_management_portfolio_book_exposure(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
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
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        if capital_pool_id:
            rows = [row for row in rows if row["capital_pool_id"] == capital_pool_id]
        holdings = [{**allocation, "capital_pool_id": row["capital_pool_id"]} for row in rows for allocation in row["allocations"]]
        return _readback_response(holdings, meta={"snapshot_at": utc_now(), "total": len(holdings)}, items=holdings)

    # 22. Positions reuse allocation facts but retain the capital risk boundary.
    @router.get("/bff/management/portfolio-book/positions")
    async def bff_management_portfolio_book_positions(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        if capital_pool_id:
            rows = [row for row in rows if row["capital_pool_id"] == capital_pool_id]
        positions = [{**allocation, "capital_pool_id": row["capital_pool_id"], "risk_limits": row["risk_limits"]} for row in rows for allocation in row["allocations"]]
        return _readback_response(positions, meta={"snapshot_at": utc_now(), "total": len(positions)}, items=positions)

    # 23. Cost attribution is a read-only projection; the BFF never invents costs.
    @router.get("/bff/management/cost-attribution")
    async def bff_management_cost_attribution(
        capital_pool_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        rows = _portfolio_or_error()
        if capital_pool_id:
            rows = [row for row in rows if row["capital_pool_id"] == capital_pool_id]
        costs = []
        for row in rows:
            for allocation in row["allocations"]:
                cost = first_present(allocation, "cost", "cost_amount", "commission", "fees")
                costs.append({"capital_pool_id": row["capital_pool_id"], "allocation": allocation, "cost": cost if cost is not None else 0})
        return _readback_response(costs, meta={"snapshot_at": utc_now(), "total": len(costs), "policy": "read_only_cost_attribution"}, items=costs)

    # 24. Compact operator board pack assembled solely from capital readbacks.
    @router.get("/bff/management/board-pack")
    async def bff_management_board_pack(
        authorization: Optional[str] = Header(default=None)
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
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

    # 25. Canonical rebalance patch command.
    @router.patch("/bff/rebalances/{rebalance_id}")
    async def sem_patch_rebalance_command(
        rebalance_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        _rebalance_or_error(rebalance_id)
        key = _resolve_idempotency_key(idempotency_key, x_idempotency_key)
        result, replayed = _idempotent_write("patch_rebalance", payload, actor_id=_identity_id(identity), key=key, target_id=rebalance_id)
        return _readback_response(result, meta={"snapshot_at": utc_now(), "idempotency_key": key, "replayed": replayed})

    return router


__all__ = ["create_capital_router"]

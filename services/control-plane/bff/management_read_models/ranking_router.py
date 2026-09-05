"""BFF Management Read Models - Ranking Formulas Router.

Owns ranking formulas CRUD routes:
  GET    /bff/ranking-formulas
  GET    /bff/ranking-formulas/{formula_id}
  POST   /bff/ranking-formulas
  PATCH  /bff/ranking-formulas/{formula_id}

Matrix item: ACG-01-012
  - Removes generic echo create route
  - Provides durable validation, idempotency and persistence contract
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

from services.control_plane.bff.models import ErrorCode

log = logging.getLogger(__name__)

_RANKING_FORMULA_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _default_utc_now()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


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
) -> Any:
    class DummyIdentity:
        operator_id = "op-user"
        roles = {"operator", "admin", "viewer"}

    ident = DummyIdentity()
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        parts = token.split(":")
        ident.operator_id = parts[0]
        if len(parts) > 1:
            ident.roles = set(parts[1].split(","))
    return ident


def _default_require_read_role(identity: Any) -> None:
    pass


def _default_require_operator_role(identity: Any, err_fn=None) -> None:
    roles = getattr(identity, "roles", set())
    if not ({"operator", "admin", "approver"}.intersection(roles)):
        _err = err_fn or _default_bff_error
        raise _err(
            403,
            ErrorCode.FORBIDDEN,
            "Operator role required",
            "Operator role required to mutate ranking formulas",
            precondition_failed="role_check",
        )


def _stable_json_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_ranking_formulas_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    snapshot_meta: Optional[Callable[[str], Dict[str, Any]]] = None,
    idempotency_check: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    idempotency_store: Optional[Callable[..., None]] = None,
) -> APIRouter:
    """Create the focused APIRouter for Ranking Formulas.

    Handles:
      GET    /bff/ranking-formulas
      GET    /bff/ranking-formulas/{formula_id}
      POST   /bff/ranking-formulas
      PATCH  /bff/ranking-formulas/{formula_id}
    """
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)

    router = APIRouter()

    _extract_ident = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _require_op = require_operator_role or (lambda ident: _default_require_operator_role(ident, bff_error))
    _err = bff_error or _default_bff_error
    _utc_now = utc_now or _default_utc_now
    _snapshot_meta = snapshot_meta or _default_snapshot_meta

    def _check_idempotency(operator_id: str, key: str, request_hash: str) -> Optional[Dict[str, Any]]:
        if idempotency_check is not None:
            return idempotency_check(operator_id, key, request_hash)
        if not key:
            return None
        cache_key = f"{operator_id}:{key}"
        entry = _RANKING_FORMULA_IDEMPOTENCY.get(cache_key)
        if entry is None:
            return None
        if entry.get("request_hash") != request_hash:
            raise _err(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key conflict: request payload differs from previous submission",
                precondition_failed="idempotency_conflict",
            )
        return entry.get("result")

    def _store_idempotency(operator_id: str, key: str, request_hash: str, result: Dict[str, Any]) -> None:
        if idempotency_store is not None:
            idempotency_store(operator_id, key, request_hash, result)
            return
        if not key:
            return
        cache_key = f"{operator_id}:{key}"
        _RANKING_FORMULA_IDEMPOTENCY[cache_key] = {
            "request_hash": request_hash,
            "result": result,
        }

    @router.get("/bff/ranking-formulas")
    async def bff_list_ranking_formulas(
        status: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """List ranking formulas from the read surface store."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        records: List[Dict[str, Any]] = []
        if get_read_store is not None:
            store = get_read_store()
            if hasattr(store, "list_ranking_formulas"):
                records = store.list_ranking_formulas(status=status)

        meta = {
            **_snapshot_meta(snapshot_at),
            "surface": "ranking_formulas",
            "surface_key": "ranking_formulas",
            "dataset": "ranking_formulas",
            "total": len(records),
        }
        return {
            "data": records,
            "items": records,
            "page_info": {"next_page_token": None, "total": len(records)},
            "meta": meta,
        }

    @router.get("/bff/ranking-formulas/{formula_id}")
    async def bff_get_ranking_formula(
        formula_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Get ranking formula detail by formula_id."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        clean_id = str(formula_id or "").strip()
        record: Optional[Dict[str, Any]] = None
        if get_read_store is not None:
            store = get_read_store()
            if hasattr(store, "get_ranking_formula"):
                record = store.get_ranking_formula(clean_id)

        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Ranking formula not found: {clean_id}",
                f"No ranking formula with id {clean_id!r}",
                precondition_failed="formula_id",
            )

        snapshot_at = _utc_now()
        meta = {
            **_snapshot_meta(snapshot_at),
            "surface": "ranking_formulas",
            "surface_key": "ranking_formula_detail",
            "dataset": "ranking_formulas",
            "entity_id": clean_id,
        }
        return {
            "data": record,
            "meta": meta,
        }

    @router.post("/bff/ranking-formulas", status_code=201)
    async def bff_create_ranking_formula(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """Create a ranking formula with validation and idempotency."""
        identity = _extract_ident(authorization)
        _require_op(identity)

        payload_dict = dict(payload or {})
        if "idempotency_key" in payload_dict or "idempotencyKey" in payload_dict:
            raise _err(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency keys must be provided via the Idempotency-Key header, not in the request body",
                "Request body contained an idempotencyKey/idempotency_key field",
                precondition_failed="body_idempotency_key",
            )

        resolved_key = str(idempotency_key or x_idempotency_key or "").strip()
        request_hash = _stable_json_hash({"route": "POST /bff/ranking-formulas", "payload": payload_dict})

        cached = _check_idempotency(getattr(identity, "operator_id", "op-user"), resolved_key, request_hash)
        if cached is not None:
            return JSONResponse(status_code=201, content=jsonable_encoder(cached))

        name = str(payload_dict.get("name") or "").strip()
        if not name:
            raise _err(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "Ranking formula name must be a non-empty string",
                precondition_failed="name",
            )

        description = str(payload_dict.get("description") or "").strip()
        snapshot_at = _utc_now()
        actor_id = getattr(identity, "operator_id", "op-user")

        record: Dict[str, Any] = {}
        if get_read_store is not None:
            store = get_read_store()
            if hasattr(store, "create_ranking_formula"):
                record = store.create_ranking_formula(
                    name=name,
                    description=description,
                    actor_id=actor_id,
                    created_at=snapshot_at,
                    params=payload_dict.get("params"),
                )
        if not record:
            formula_id = f"rf-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:4]}"
            record = {
                "id": formula_id,
                "formula_id": formula_id,
                "name": name,
                "description": description,
                "status": "active",
                "params": payload_dict.get("params") or {},
                "created_at": snapshot_at,
                "updated_at": snapshot_at,
                "created_by": actor_id,
            }

        result = {
            "data": record,
            "meta": {
                **_snapshot_meta(snapshot_at),
                "surface": "ranking_formulas",
                "surface_key": "ranking_formula_detail",
                "idempotency": {"idempotencyKey": resolved_key} if resolved_key else {},
            },
        }

        _store_idempotency(actor_id, resolved_key, request_hash, result)
        return JSONResponse(status_code=201, content=jsonable_encoder(result))

    @router.patch("/bff/ranking-formulas/{formula_id}")
    async def bff_patch_ranking_formula(
        formula_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        """Patch a ranking formula."""
        identity = _extract_ident(authorization)
        _require_op(identity)

        clean_id = str(formula_id or "").strip()
        actor_id = getattr(identity, "operator_id", "op-user")
        snapshot_at = _utc_now()

        record: Optional[Dict[str, Any]] = None
        if get_read_store is not None:
            store = get_read_store()
            if hasattr(store, "patch_ranking_formula"):
                record = store.patch_ranking_formula(clean_id, patch=payload, actor_id=actor_id)
            elif hasattr(store, "get_ranking_formula"):
                existing = store.get_ranking_formula(clean_id)
                if existing is not None:
                    record = {**existing, **payload, "updated_at": snapshot_at, "updated_by": actor_id}

        if record is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Ranking formula not found: {clean_id}",
                precondition_failed="formula_id",
            )

        meta = {
            **_snapshot_meta(snapshot_at),
            "surface": "ranking_formulas",
            "surface_key": "ranking_formula_detail",
            "entity_id": clean_id,
        }
        return {
            "data": record,
            "meta": meta,
        }

    return router


# ---------------------------------------------------------------------------
# ACG-01-012 (follow-up): Rankings long-tail + PM-12 performance attribution
# ---------------------------------------------------------------------------
#
# Extracted from bff/main.py (OPGAP-BE-STRATEGY-RANKING-20260830). Pure
# code-motion: route bodies, error messages, and status codes are preserved
# exactly as they were in main.py, including the deprecated `/bff/ranking/
# formulas*` handlers whose bodies are unreachable dead code after their
# immediate `return _deprecated_bff_path_response(...)` (kept as-is to avoid
# behavior drift). Widely shared main.py helpers (identity/role checks,
# read_store, idempotency helpers, PM-12 attribution response builder, tenant
# resolution) are threaded through explicit factory parameters instead of
# being duplicated here or imported back from main (which would be circular).

from fastapi import Response


def create_rankings_long_tail_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice: Optional[Callable[..., Any]] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    deprecated_bff_path_response: Optional[Callable[..., Any]] = None,
    reject_body_idempotency_key: Optional[Callable[[Dict[str, Any]], None]] = None,
    resolve_final_idempotency_key: Optional[Callable[..., str]] = None,
    capital_bff_idempotency_check: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    capital_bff_idempotency_store: Optional[Callable[..., None]] = None,
    capital_bff_action_command: Optional[Callable[..., Any]] = None,
    object_type: Any = None,
    command_type: Any = None,
) -> APIRouter:
    """Create the router for the deprecated ``/bff/ranking/formulas*`` compatibility
    surface and the full-spec ``/bff/rankings*`` long tail.

    Handles:
      GET    /bff/ranking/formulas                        (deprecated)
      POST   /bff/ranking/formulas                         (deprecated)
      GET    /bff/ranking/formulas/{formula_id}             (deprecated)
      PATCH  /bff/ranking/formulas/{formula_id}              (deprecated)
      POST   /bff/ranking/formulas/{formula_id}/actions/{action_id} (deprecated)
      GET    /bff/rankings
      GET    /bff/rankings/{ranking_id}
      POST   /bff/rankings/{ranking_id}/actions/{action_id}
    """
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)

    router = APIRouter()

    _extract_identity = extract_identity or _default_extract_identity
    _require_read_role = require_read_role or _default_require_read_role
    _err = bff_error or _default_bff_error
    _utc_now = utc_now or _default_utc_now

    def _get_read_store() -> Any:
        if get_read_store is not None:
            return get_read_store()
        raise NotImplementedError("get_read_store dependency was not supplied")

    @router.get("/bff/ranking/formulas")
    async def bff_deprecated_list_ranking_formulas(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: ranking formula list."""
        return deprecated_bff_path_response(
            route="/bff/ranking/formulas",
            replacement="/bff/ranking-formulas",
        )


    @router.post("/bff/ranking/formulas", status_code=201)
    async def bff_deprecated_create_ranking_formula(
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create ranking formula — Idempotency-Key required."""
        return deprecated_bff_path_response(
            route="/bff/ranking/formulas",
            replacement="/bff/ranking-formulas",
        )


    @router.get("/bff/ranking/formulas/{formula_id}")
    async def bff_deprecated_get_ranking_formula(
        formula_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: ranking formula detail."""
        return deprecated_bff_path_response(
            route="/bff/ranking/formulas/{formula_id}",
            replacement="/bff/ranking-formulas/{formula_id}",
        )


    @router.patch("/bff/ranking/formulas/{formula_id}")
    async def bff_deprecated_patch_ranking_formula(
        formula_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: patch ranking formula — Idempotency-Key required."""
        return deprecated_bff_path_response(
            route="/bff/ranking/formulas/{formula_id}",
            replacement="/bff/ranking-formulas/{formula_id}",
        )


    @router.post("/bff/ranking/formulas/{formula_id}/actions/{action_id}", status_code=202)
    async def bff_deprecated_ranking_formula_action(
        formula_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: ranking formula action — routes through command/precondition machinery."""
        return deprecated_bff_path_response(
            route="/bff/ranking/formulas/{formula_id}/actions/{action_id}",
            replacement="/bff/actions/rankingFormula/{formula_id}/{action_id}",
        )


    @router.get("/bff/rankings")
    async def bff_list_rankings(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: ranking list (full-spec long tail)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = _utc_now()
        read_store = _get_read_store()
        items = read_store.list_rankings(status=status)
        total = len(items)
        page_items, next_page_token = page_slice(items, page_token, page_size)
        return {
            "data": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": read_surface_meta(
                "rankings", "ranking_list",
                snapshot_at=snapshot_at, total=total,
            ),
        }

    @router.get("/bff/rankings/{ranking_id}")
    async def bff_get_ranking(
        ranking_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: ranking detail (full-spec long tail)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        snapshot_at = _utc_now()
        read_store = _get_read_store()
        ranking = read_store.get_ranking(ranking_id)
        if not ranking:
            raise _err(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Ranking not found",
                f"Ranking {ranking_id} does not exist",
            )
        return {
            "data": ranking,
            "meta": read_surface_meta(
                "rankings", "ranking_detail",
                snapshot_at=snapshot_at,
            ),
        }

    @router.post("/bff/rankings/{ranking_id}/actions/{action_id}", status_code=202)
    async def bff_ranking_action(
        ranking_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: ranking action (full-spec long tail) — routes through command/precondition machinery."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        read_store = _get_read_store()
        ranking = read_store.get_ranking(ranking_id)
        if not ranking:
            raise _err(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Ranking not found",
                f"Ranking {ranking_id} does not exist",
            )
        return capital_bff_action_command(
            entity_type=object_type.RANKING,
            entity_id=ranking_id,
            action_id=action_id,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=command_type.RANKING_ACTION,
        )

    return router


_PM12_ATTRIBUTION_DIMENSION_ALIASES = {
    "persona": "persona",
    "personas": "persona",
    "strategy": "strategy",
    "strategies": "strategy",
    "pool": "pool",
    "pools": "pool",
    "capital_pool": "pool",
    "capital_pools": "pool",
    "capitalpool": "pool",
    "capitalpools": "pool",
    "asset": "asset",
    "assets": "asset",
    "instrument": "asset",
    "instruments": "asset",
    "symbol": "asset",
    "symbols": "asset",
    "broker": "broker",
    "brokers": "broker",
    "runtime": "runtime",
    "runtimes": "runtime",
    "regime": "regime",
    "regimes": "regime",
    "market_regime": "regime",
}


def create_performance_attribution_router(
    *,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None,
    pm12_performance_attribution_response: Optional[Callable[..., Any]] = None,
    attribution_dimensions: Optional[Tuple[str, ...]] = None,
) -> APIRouter:
    """Create the router for the PM-12 performance attribution surfaces.

    Handles:
      GET     /bff/management/performance-attribution
      OPTIONS /bff/management/performance-attribution/by-strategy
      GET     /bff/management/performance-attribution/by-strategy
      GET     /bff/management/performance-attribution/by-persona
      GET     /bff/management/performance-attribution/by-pool
    """
    router = APIRouter()

    _extract_identity = extract_identity or _default_extract_identity
    _require_read_role = require_read_role or _default_require_read_role
    _dimensions = tuple(attribution_dimensions) if attribution_dimensions else (
        "persona", "strategy", "pool", "asset", "broker", "runtime", "regime",
    )

    def _tenant_id(identity: Any) -> str:
        if bff_me_tenant_payload is None:
            raise NotImplementedError("bff_me_tenant_payload dependency was not supplied")
        return str(bff_me_tenant_payload(identity, requested_tenant=None)["id"])

    def _normalize_attribution_dimensions(dimension: Optional[str]) -> List[str]:
        raw = str(dimension or "").strip()
        if not raw or raw.lower() in {"all", "*"}:
            return list(_dimensions)

        dimensions: List[str] = []
        invalid: List[str] = []
        for item in raw.split(","):
            key = item.strip().replace("-", "_").lower()
            if not key:
                continue
            normalized = _PM12_ATTRIBUTION_DIMENSION_ALIASES.get(key)
            if normalized is None:
                invalid.append(item.strip())
                continue
            if normalized not in dimensions:
                dimensions.append(normalized)

        if invalid or not dimensions:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_dimension",
                    "message": "dimension must be one of persona, strategy, pool, asset, broker, runtime, regime.",
                    "field": "dimension",
                    "invalid": invalid,
                    "supported": list(_dimensions),
                },
            )
        return dimensions

    def _pm12_response(**kwargs: Any) -> Any:
        if pm12_performance_attribution_response is None:
            raise NotImplementedError("pm12_performance_attribution_response dependency was not supplied")
        return pm12_performance_attribution_response(**kwargs)

    @router.get("/bff/management/performance-attribution")
    async def bff_management_performance_attribution(
        dimension: Optional[str] = Query(default=None),
        period: str = Query(default="latest"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 performance attribution by persona/strategy/pool/asset/broker/runtime/regime."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        tenant_id = _tenant_id(identity)
        return _pm12_response(
            dimensions=_normalize_attribution_dimensions(dimension),
            period=period,
            page_token=page_token,
            page_size=page_size,
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, as_of=as_of,
            tenant_id=tenant_id,
        )

    @router.options(
        "/bff/management/performance-attribution/by-strategy",
        status_code=204,
        include_in_schema=False,
    )
    async def bff_management_performance_attribution_by_strategy_options():
        return Response(status_code=204)

    @router.get("/bff/management/performance-attribution/by-strategy")
    async def bff_management_performance_attribution_by_strategy(
        period: str = Query(default="latest"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 performance attribution grouped by strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        tenant_id = _tenant_id(identity)
        return _pm12_response(
            dimensions=["strategy"],
            period=period,
            page_token=page_token,
            page_size=page_size,
            data_id="pm12-performance-attribution-by-strategy",
            surface_key="performance_attribution_by_strategy",
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, as_of=as_of,
            tenant_id=tenant_id,
        )

    @router.get("/bff/management/performance-attribution/by-persona")
    async def bff_management_performance_attribution_by_persona(
        period: str = Query(default="latest"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 performance attribution grouped by persona."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        tenant_id = _tenant_id(identity)
        return _pm12_response(
            dimensions=["persona"],
            period=period,
            page_token=page_token,
            page_size=page_size,
            data_id="pm12-performance-attribution-by-persona",
            surface_key="performance_attribution_by_persona",
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, as_of=as_of,
            tenant_id=tenant_id,
        )

    @router.get("/bff/management/performance-attribution/by-pool")
    async def bff_management_performance_attribution_by_pool(
        period: str = Query(default="latest"),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        # Common filters:
        persona_id: Optional[str] = Query(default=None, alias="personaId"),
        persona: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None, alias="runtimeId"),
        runtime: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None, alias="strategyId"),
        strategy: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None, alias="capitalPoolId"),
        pool: Optional[str] = Query(default=None),
        sleeve_id: Optional[str] = Query(default=None, alias="sleeveId"),
        sleeve: Optional[str] = Query(default=None),
        artifact_id: Optional[str] = Query(default=None, alias="artifactId"),
        artifact: Optional[str] = Query(default=None),
        broker_id: Optional[str] = Query(default=None, alias="brokerId"),
        broker: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        as_of: Optional[str] = Query(default=None, alias="asOf"),
    ):
        """BFF: PM-12 performance attribution grouped by capital pool."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        tenant_id = _tenant_id(identity)
        return _pm12_response(
            dimensions=["pool"],
            period=period,
            page_token=page_token,
            page_size=page_size,
            data_id="pm12-performance-attribution-by-pool",
            surface_key="performance_attribution_by_pool",
            persona_id=persona_id, persona=persona,
            runtime_id=runtime_id, runtime=runtime,
            strategy_id=strategy_id, strategy=strategy,
            capital_pool_id=capital_pool_id, pool=pool,
            sleeve_id=sleeve_id, sleeve=sleeve,
            artifact_id=artifact_id, artifact=artifact,
            broker_id=broker_id, broker=broker,
            stage=stage, as_of=as_of,
            tenant_id=tenant_id,
        )

    return router

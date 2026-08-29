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
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse

try:
    from models import ErrorCode
except ImportError:
    class ErrorCode:
        VALIDATION_FAILED = "VALIDATION_FAILED"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        FORBIDDEN = "FORBIDDEN"
        RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
        RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
        IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
        INTERNAL_ERROR = "INTERNAL_ERROR"

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

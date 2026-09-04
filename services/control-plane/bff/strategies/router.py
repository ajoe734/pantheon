"""BFF Strategies canonical router.

Owns:
  GET    /bff/strategies
  POST   /bff/strategies
  GET    /bff/strategies/{strategy_id}
  PATCH  /bff/strategies/{strategy_id}
  GET    /bff/strategies/{strategy_id}/specs
  POST   /bff/strategies/{strategy_id}/specs
  GET    /bff/strategies/{strategy_id}/experiments
  GET    /bff/strategies/{strategy_id}/artifacts
  GET    /bff/strategies/{strategy_id}/lineage
  GET    /bff/strategies/{strategy_id}/audit
  GET    /bff/strategies/{strategy_id}/ooda
  POST   /bff/strategies/{strategy_id}/actions/{action_id}
  POST   /bff/strategies/{strategy_id}/dry-run
  GET    /bff/management/strategy-seeds
  GET    /bff/management/strategy-seeds/{seed_id}
  POST   /bff/management/strategy-seeds/{seed_id}/review
  POST   /bff/management/strategy-seeds/{seed_id}/merge
  POST   /bff/management/strategy-seeds/{seed_id}/submit-replication

Extracted from the bff/main.py monolith (OPGAP-BE-STRATEGY-RANKING-20260830).
This is a pure code-motion refactor: route bodies, error messages, and status
codes are preserved exactly as they were in main.py. Shared state (the
``_STRATEGY_BFF_OVERLAY`` compatibility overlay, idempotency caches used by
other still-in-main.py routes, and the many cross-cutting helpers such as
``_extract_identity``/``_bff_error``/``read_store``) stays owned by main.py and
is threaded into this router through explicit factory parameters rather than
being duplicated or imported back from main (which would be circular).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.persona.persona_strategy_discovery import (
    PersonaStrategyDiscoveryService,
    extract_persona_strategy_profile,
)
from services.source_ingestion.replication_bridge import (
    StrategySeedReplicationBridge,
    StrategySeedReplicationBridgeError,
)
from services.source_ingestion.strategy_seed_store import (
    SeedReviewDecision,
    StrategySpecSeedReviewError,
    StrategySpecSeedStore,
    StrategySpecSeedStoreError,
)

try:
    from models import CommandType, ErrorCode, ObjectType, OperatorIdentity
except ImportError:  # pragma: no cover - defensive fallback for isolated unit tests.
    class ErrorCode:  # type: ignore[no-redef]
        VALIDATION_FAILED = "VALIDATION_FAILED"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        FORBIDDEN = "FORBIDDEN"
        RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
        RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
        IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
        OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
        INTERNAL_ERROR = "INTERNAL_ERROR"

    class ObjectType:  # type: ignore[no-redef]
        STRATEGY = "strategy"

    class CommandType:  # type: ignore[no-redef]
        STRATEGY_ACTION = "strategy_action"

    OperatorIdentity = Any  # type: ignore[assignment]

log = logging.getLogger(__name__)


def _default_utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _default_extract_identity(authorization: Optional[str] = None) -> Any:
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
            "Operator role required to mutate strategies",
            precondition_failed="role_check",
        )


def _default_page_slice(
    items: List[Dict[str, Any]], page_token: Optional[str], page_size: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return list(items[:page_size]), None


def _default_read_surface_meta(
    surface: str,
    surface_key: str,
    *,
    snapshot_at: str,
    total: Optional[int] = None,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surface": surface,
        "surface_key": surface_key,
        "dataset": surface,
    }
    if total is not None:
        meta["total"] = total
    return meta


def create_strategies_router(
    *,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[..., None]] = None,
    require_operator_role: Optional[Callable[..., None]] = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice: Optional[Callable[..., Any]] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    reject_body_idempotency_key: Optional[Callable[[Dict[str, Any]], None]] = None,
    resolve_final_idempotency_key: Optional[Callable[..., str]] = None,
    stable_json_hash: Optional[Callable[[Dict[str, Any]], str]] = None,
    request_dry_run_requested: Optional[Callable[[], bool]] = None,
    dry_run_success_response: Optional[Callable[..., Any]] = None,
    normalize_lifecycle_state: Optional[Callable[[Any], str]] = None,
    normalize_risk_level: Optional[Callable[[Any], str]] = None,
    strategy_persona_idempotency_check: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    strategy_persona_action_command: Optional[Callable[..., Any]] = None,
    strategy_overlay: Optional[Dict[str, Dict[str, Any]]] = None,
    strategy_persona_idempotency_store: Optional[Dict[str, Dict[str, Any]]] = None,
    strategy_seed_replication_idempotency_store: Optional[Dict[str, Dict[str, Any]]] = None,
    strategy_seed_review_idempotency_store: Optional[Dict[str, Dict[str, Any]]] = None,
    list_governance_audit_events: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ooda_packet_list_payload: Optional[Callable[..., Dict[str, Any]]] = None,
    require_ooda_packet_routes_enabled: Optional[Callable[[], None]] = None,
    deprecated_bff_path_response: Optional[Callable[..., Any]] = None,
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None,
    list_persona_records: Optional[Callable[..., List[Dict[str, Any]]]] = None,
    list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> APIRouter:
    """Create the focused APIRouter for the Strategies and StrategySpecSeed surfaces."""
    router = APIRouter()

    _extract_identity = extract_identity or _default_extract_identity
    _require_read_role = require_read_role or _default_require_read_role
    _require_operator_role = require_operator_role or (
        lambda ident: _default_require_operator_role(ident, bff_error)
    )
    _bff_error = bff_error or _default_bff_error
    utc_now = utc_now or _default_utc_now
    _page_slice = page_slice or _default_page_slice
    _read_surface_meta = read_surface_meta or _default_read_surface_meta

    _strategy_overlay: Dict[str, Dict[str, Any]] = (
        strategy_overlay if strategy_overlay is not None else {}
    )
    _strategy_persona_idempotency: Dict[str, Dict[str, Any]] = (
        strategy_persona_idempotency_store if strategy_persona_idempotency_store is not None else {}
    )
    _strategy_seed_replication_idempotency: Dict[str, Dict[str, Any]] = (
        strategy_seed_replication_idempotency_store
        if strategy_seed_replication_idempotency_store is not None
        else {}
    )
    _strategy_seed_review_idempotency: Dict[str, Dict[str, Any]] = (
        strategy_seed_review_idempotency_store
        if strategy_seed_review_idempotency_store is not None
        else {}
    )

    def _project_strategy_dto(
        summary: Dict[str, Any],
        *,
        detail: Optional[Dict[str, Any]] = None,
        overlay: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Project canonical strategy_spec data into execute-plans Strategy DTO."""
        strategy_id = str(summary.get("strategy_id") or summary.get("id") or "")
        title = summary.get("title") or summary.get("name") or strategy_id
        lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
        governance = (detail or {}).get("governance") if detail else {}
        governance = governance if isinstance(governance, dict) else {}
        market_scope = (detail or {}).get("market_scope") if detail else {}
        market_scope = market_scope if isinstance(market_scope, dict) else {}
        execution_profile = (detail or {}).get("execution_profile") if detail else {}
        execution_profile = execution_profile if isinstance(execution_profile, dict) else {}
        persona_ids: List[str] = []
        if detail and isinstance(detail.get("persona_ids"), list):
            persona_ids = [str(p) for p in detail.get("persona_ids") or [] if str(p).strip()]
        capital_pool_id = str(
            execution_profile.get("capital_pool_id")
            or governance.get("capital_pool_id")
            or summary.get("capital_pool_id")
            or ""
        )
        alpha = str(
            market_scope.get("alpha")
            or summary.get("source_kind")
            or summary.get("hypothesis_excerpt")
            or ""
        )
        allowed = (detail or {}).get("allowedActions") or {}
        available_actions: List[str] = []
        if isinstance(allowed, dict):
            available_actions = sorted([k for k, v in allowed.items() if v])
        dto: Dict[str, Any] = {
            "id": strategy_id,
            "name": title,
            "owner": summary.get("owner") or governance.get("owner") or "pantheon-bff",
            "updatedAt": summary.get("last_modified_at")
            or summary.get("updated_at")
            or (detail or {}).get("created_at")
            or utc_now(),
            "state": normalize_lifecycle_state(lifecycle_raw),
            "risk": normalize_risk_level(governance.get("risk_level")),
            "alpha": alpha,
            "capitalPoolId": capital_pool_id,
            "personaIds": persona_ids,
            "pnl30d": 0.0,
            "sharpe": 0.0,
            "drawdown": 0.0,
            "availableActions": available_actions,
            "labelKey": f"strategy.{strategy_id}" if strategy_id else None,
            "lifecycleStatus": str(lifecycle_raw or ""),
        }
        if overlay:
            for k, v in overlay.items():
                if v is not None:
                    dto[k] = v
        return dto

    def _list_strategy_summaries() -> List[Dict[str, Any]]:
        if list_strategy_summaries is not None:
            return list_strategy_summaries()
        raise NotImplementedError("list_strategy_summaries dependency was not supplied")

    def _get_read_store() -> Any:
        if get_read_store is not None:
            return get_read_store()
        raise NotImplementedError("get_read_store dependency was not supplied")

    def _bff_tenant_id(identity: Any) -> str:
        if bff_me_tenant_payload is None:
            raise NotImplementedError("bff_me_tenant_payload dependency was not supplied")
        return str(bff_me_tenant_payload(identity, requested_tenant=None)["id"])

    # -- Strategy DTO compatibility surfaces (execute-plans Strategy) --------

    @router.get("/bff/strategies")
    async def bff_list_strategies(
        state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: strategy list (execute-plans Strategy DTO compatibility)."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        summaries = _list_strategy_summaries()
        if persona_id:
            summaries = [
                s for s in summaries
                if persona_id in (s.get("persona_ids") or [])
                or s.get("strategy_id") in _strategy_overlay
            ]
        items = []
        for summary in summaries:
            strategy_id = str(summary.get("strategy_id") or "")
            detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
            overlay = _strategy_overlay.get(strategy_id)
            items.append(_project_strategy_dto(summary, detail=detail, overlay=overlay))
        if state:
            items = [s for s in items if s.get("state") == state]
        total = len(items)
        page_items, next_page_token = _page_slice(items, page_token, page_size)
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": _read_surface_meta(
                "strategy_specs", "strategy_list",
                snapshot_at=snapshot_at, total=total,
            ),
        }

    @router.post("/bff/strategies", status_code=201)
    async def bff_create_strategy(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create strategy stub (execute-plans compatibility)."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = stable_json_hash({"route": "POST /bff/strategies", "payload": payload})
        dry_run = request_dry_run_requested()
        if not dry_run:
            cached = strategy_persona_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        name = str(payload.get("name") or "").strip()
        if not name:
            raise _bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Strategy name must be a non-empty string",
                precondition_failed="name",
            )
        snapshot_at = utc_now()
        strategy_id = f"strategy-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        overlay = {
            "id": strategy_id,
            "name": name,
            "owner": str(payload.get("owner") or identity.operator_id),
            "updatedAt": snapshot_at,
            "state": normalize_lifecycle_state(payload.get("state") or "draft"),
            "risk": normalize_risk_level(payload.get("risk")),
            "alpha": str(payload.get("alpha") or ""),
            "capitalPoolId": str(payload.get("capitalPoolId") or payload.get("capital_pool_id") or ""),
            "personaIds": list(payload.get("personaIds") or payload.get("persona_ids") or []),
            "pnl30d": float(payload.get("pnl30d") or 0.0),
            "sharpe": float(payload.get("sharpe") or 0.0),
            "drawdown": float(payload.get("drawdown") or 0.0),
            "availableActions": ["edit", "submit", "retire"],
            "labelKey": f"strategy.{strategy_id}",
        }
        if dry_run:
            return dry_run_success_response(
                overlay,
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="strategy.create",
            )
        _strategy_overlay[strategy_id] = overlay
        result = {
            "data": overlay,
            "meta": {"snapshot_at": snapshot_at},
        }
        _strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/bff/strategies/{strategy_id}")
    async def bff_get_strategy(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: strategy detail."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        overlay = _strategy_overlay.get(strategy_id)
        summary = read_store.get_strategy_spec(strategy_id)
        if not summary and not overlay:
            raise _bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        summary_for_dto = summary or {"strategy_id": strategy_id, "title": (overlay or {}).get("name")}
        detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
        dto = _project_strategy_dto(summary_for_dto, detail=detail, overlay=overlay)
        return {
            "data": dto,
            "meta": _read_surface_meta(
                "strategy_specs", "strategy_detail",
                snapshot_at=snapshot_at,
            ),
        }

    @router.patch("/bff/strategies/{strategy_id}")
    async def bff_patch_strategy(
        strategy_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: patch strategy overlay fields."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = stable_json_hash(
            {"route": "PATCH /bff/strategies/{strategy_id}", "id": strategy_id, "payload": payload}
        )
        cached = strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        read_store = _get_read_store()
        summary = read_store.get_strategy_spec(strategy_id)
        overlay = _strategy_overlay.get(strategy_id)
        if not summary and not overlay:
            raise _bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        snapshot_at = utc_now()
        base = dict(overlay) if overlay else {}
        if not base:
            detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
            base = _project_strategy_dto(summary or {"strategy_id": strategy_id}, detail=detail)
        for field in (
            "name", "owner", "state", "risk", "alpha",
            "capitalPoolId", "personaIds", "pnl30d", "sharpe", "drawdown",
            "availableActions",
        ):
            if field in payload:
                base[field] = payload[field]
        if "state" in payload:
            base["state"] = normalize_lifecycle_state(payload["state"])
        if "risk" in payload:
            base["risk"] = normalize_risk_level(payload["risk"])
        base["updatedAt"] = snapshot_at
        base["id"] = strategy_id
        _strategy_overlay[strategy_id] = base
        result = {"data": base, "meta": {"snapshot_at": snapshot_at}}
        _strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    def _ensure_strategy_exists(strategy_id: str) -> None:
        read_store = _get_read_store()
        if read_store.get_strategy_spec(strategy_id) or strategy_id in _strategy_overlay:
            return
        raise _bff_error(
            404, ErrorCode.RESOURCE_NOT_FOUND,
            "Strategy not found",
            f"Strategy {strategy_id} does not exist",
        )

    @router.get("/bff/strategies/{strategy_id}/specs")
    async def bff_list_strategy_specs(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: spec versions for a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_strategy_exists(strategy_id)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        versions = read_store.list_strategy_spec_versions(strategy_id) or []
        return {
            "data": versions,
            "items": versions,
            "page_info": {"next_page_token": None, "total": len(versions)},
            "meta": _read_surface_meta(
                "strategy_specs", "strategy_spec_versions",
                snapshot_at=snapshot_at, total=len(versions),
            ),
        }

    @router.post("/bff/strategies/{strategy_id}/specs", status_code=201)
    async def bff_create_strategy_spec(
        strategy_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: create new spec version stub for a strategy."""
        identity = _extract_identity(authorization)
        _require_operator_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _ensure_strategy_exists(strategy_id)
        request_hash = stable_json_hash(
            {"route": "POST /bff/strategies/{id}/specs", "id": strategy_id, "payload": payload}
        )
        dry_run = request_dry_run_requested()
        if not dry_run:
            cached = strategy_persona_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = utc_now()
        spec_version_id = f"spec-{strategy_id}-{uuid.uuid4().hex[:8]}"
        result = {
            "data": {
                "strategy_id": strategy_id,
                "spec_version_id": spec_version_id,
                "spec_version": str(payload.get("version") or "draft"),
                "lifecycle_state": "draft",
                "created_at": snapshot_at,
                "created_by": identity.operator_id,
                "params": payload.get("params") or {},
            },
            "meta": {"snapshot_at": snapshot_at},
        }
        if dry_run:
            return dry_run_success_response(
                result["data"],
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="strategy_spec.create",
            )
        _strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/bff/strategies/{strategy_id}/experiments")
    async def bff_list_strategy_experiments(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: experiments related to a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_strategy_exists(strategy_id)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        raw = read_store.list_research_experiments() or []
        items = [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": _read_surface_meta(
                "research_experiments", "strategy_experiments",
                snapshot_at=snapshot_at, total=len(items),
            ),
        }

    @router.get("/bff/strategies/{strategy_id}/artifacts")
    async def bff_list_strategy_artifacts(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: artifacts produced for a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_strategy_exists(strategy_id)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        raw = read_store.list_research_artifacts() or []
        items = [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": _read_surface_meta(
                "research_artifacts", "strategy_artifacts",
                snapshot_at=snapshot_at, total=len(items),
            ),
        }

    @router.get("/bff/strategies/{strategy_id}/lineage")
    async def bff_get_strategy_lineage(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: lineage subgraph rooted at a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_strategy_exists(strategy_id)
        read_store = _get_read_store()
        snapshot_at = utc_now()
        edges = read_store.list_lineage_edges() or []
        nodes_seen: set = set()
        related = []
        for edge in edges:
            node_keys = (
                str(edge.get("from_artifact_id") or edge.get("source_id") or ""),
                str(edge.get("to_artifact_id") or edge.get("target_id") or ""),
                str(edge.get("strategy_id") or ""),
            )
            if strategy_id in node_keys:
                related.append(edge)
                for key in node_keys:
                    if key:
                        nodes_seen.add(key)
        nodes_seen.add(strategy_id)
        return {
            "data": {
                "strategy_id": strategy_id,
                "edges": related,
                "node_ids": sorted(nodes_seen),
            },
            "meta": _read_surface_meta(
                "lineage_edges", "strategy_lineage",
                snapshot_at=snapshot_at, total=len(related),
            ),
        }

    def _filter_audit_events_by_target(events: List[Dict[str, Any]], target_id: str) -> List[Dict[str, Any]]:
        return [
            event for event in events
            if str(event.get("target_id") or event.get("subject_id") or event.get("entity_id") or "") == target_id
        ]

    @router.get("/bff/strategies/{strategy_id}/audit")
    async def bff_get_strategy_audit(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: audit trail for a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        _ensure_strategy_exists(strategy_id)
        snapshot_at = utc_now()
        events = list_governance_audit_events() or []
        filtered = _filter_audit_events_by_target(events, strategy_id)
        return {
            "data": filtered,
            "items": filtered,
            "page_info": {"next_page_token": None, "total": len(filtered)},
            "meta": _read_surface_meta(
                "governance_audit_events", "strategy_audit",
                snapshot_at=snapshot_at, total=len(filtered),
            ),
        }

    @router.get("/bff/strategies/{strategy_id}/ooda")
    async def bff_list_strategy_ooda_packets(
        strategy_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: list OODA packets linked to a strategy."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        require_ooda_packet_routes_enabled()
        clean_id = strategy_id.strip()
        read_store = _get_read_store()
        packets = read_store.list_ooda_packets_for_strategy(clean_id)
        return ooda_packet_list_payload(
            packets,
            surface_key="strategy_ooda_packets",
            page_token=page_token,
            page_size=page_size,
            related={"type": "Strategy", "id": clean_id},
        )

    @router.post("/bff/strategies/{strategy_id}/actions/{action_id}", status_code=202)
    async def bff_strategy_action(
        strategy_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: strategy action — routes through command/precondition machinery."""
        return deprecated_bff_path_response(
            route="/bff/strategies/{strategy_id}/actions/{action_id}",
            replacement="/bff/actions/strategy/{strategy_id}/{action_id}",
        )
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _ensure_strategy_exists(strategy_id)
        return strategy_persona_action_command(
            entity_type=ObjectType.STRATEGY,
            entity_id=strategy_id,
            action_id=action_id,
            resolved_key=resolved_key,
            identity=identity,
            payload=payload,
            command_type=CommandType.STRATEGY_ACTION,
        )

    @router.post("/bff/strategies/{strategy_id}/dry-run", status_code=202)
    async def bff_strategy_dry_run(
        strategy_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: launch a strategy dry-run; returns a stub run handle."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        _ensure_strategy_exists(strategy_id)
        request_hash = stable_json_hash(
            {"route": "POST /bff/strategies/{id}/dry-run", "id": strategy_id, "payload": payload}
        )
        cached = strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = utc_now()
        run_id = f"dryrun-{strategy_id}-{uuid.uuid4().hex[:8]}"
        result = {
            "data": {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "status": "queued",
                "started_at": snapshot_at,
                "params": payload.get("params") or payload,
                "requested_by": identity.operator_id,
            },
            "meta": {"snapshot_at": snapshot_at},
        }
        _strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    # -- StrategySpecSeed governed review inbox ------------------------------

    def _strategy_seed_replication_idempotency_check(
        resolved_key: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        existing = _strategy_seed_replication_idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        import json as _json
        result = _json.loads(_json.dumps(existing.get("result") or {}))
        meta = result.setdefault("meta", {})
        idempotency = meta.setdefault("idempotency", {})
        idempotency["replayed"] = True
        return result

    def _require_strategy_seed_submit_role(identity: OperatorIdentity) -> None:
        if {"operator", "admin"}.intersection(identity.roles):
            return
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Strategy seed replication submit requires operator role",
            "Read-role users cannot submit StrategySpecSeed replication tasks.",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )

    def _strategy_seed_replication_error(exc: StrategySeedReplicationBridgeError) -> HTTPException:
        if exc.code == "seed_not_found":
            return _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                str(exc),
                precondition_failed="seed_id",
            )
        if exc.code == "invalid_seed_status":
            return _bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "StrategySpecSeed is not eligible for replication",
                str(exc),
                precondition_failed="status",
                suggestion="Promote the seed to StrategySpec before submitting replication.",
            )
        return _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "StrategySpecSeed replication request is invalid",
            str(exc),
            precondition_failed=exc.code or "replication_request",
        )

    def _strategy_seed_replication_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        request_hash = stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/submit-replication",
                "seed_id": seed_id,
                "payload": payload,
            }
        )
        cached = _strategy_seed_replication_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached

        try:
            submission = StrategySeedReplicationBridge().submit_seed_to_replication(
                seed_id,
                requested_by=identity.operator_id,
                idempotency_key=resolved_key,
                created_at=payload.get("created_at") or None,
                strategy_spec_version=str(payload.get("strategy_spec_version") or "1.0.0"),
            )
        except StrategySeedReplicationBridgeError as exc:
            raise _strategy_seed_replication_error(exc) from exc

        snapshot_at = submission.created_at or utc_now()
        result = {
            "data": {
                "seed_id": submission.seed_id,
                "replication_ref": submission.replication_ref,
                "experiment_task_id": submission.experiment_task_id,
                "strategy_id": submission.strategy_id,
                "strategy_spec_version": submission.strategy_spec_version,
                "research_task_id": submission.research_task.get("task_id"),
                "status": submission.research_task.get("status") or "queued",
                "experiment_task": dict(submission.experiment_task),
                "registry_write_performed": False,
                "execution_route": "none",
                "deployment_authority": "none",
                "approved_artifact_created": False,
                "deployment_plan_created": False,
                "runtime_binding_created": False,
                "idempotent_replay": submission.idempotent_replay,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
                "idempotency": {
                    "idempotencyKey": resolved_key,
                    "replayed": False,
                },
            },
        }
        _strategy_seed_replication_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    def _strategy_seed_review_idempotency_check(
        resolved_key: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        existing = _strategy_seed_review_idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        import json as _json
        result = _json.loads(_json.dumps(existing.get("result") or {}))
        meta = result.setdefault("meta", {})
        idempotency = meta.setdefault("idempotency", {})
        idempotency["replayed"] = True
        return result

    def _require_strategy_seed_review_role(identity: OperatorIdentity) -> None:
        if {"operator", "admin"}.intersection(identity.roles):
            return
        raise _bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Strategy seed review command requires operator role",
            "Read-role users cannot execute StrategySpecSeed review actions.",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )

    def _strategy_seed_review_error(exc: Exception) -> HTTPException:
        code = getattr(exc, "code", "")
        if code == "idempotency_conflict":
            return _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                str(exc),
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        if code in {"seed_not_found", "merge_target_not_found"}:
            return _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                str(exc),
                precondition_failed="seed_id",
            )
        if code in {"terminal_seed_status", "invalid_status_transition", "invalid_merge_target"}:
            return _bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "StrategySpecSeed review action is not allowed",
                str(exc),
                precondition_failed="status",
            )
        return _bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "StrategySpecSeed review request is invalid",
            str(exc),
            precondition_failed=code or "review_request",
        )

    def _strategy_seed_status_value(seed: Any) -> str:
        status = getattr(seed, "status", "")
        return status.value if hasattr(status, "value") else str(status or "")

    def _strategy_seed_source_kind(seed: Any) -> str:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        return str(
            metadata.get("source_kind")
            or metadata.get("source_type")
            or metadata.get("source_connector_kind")
            or "strategy_spec_seed"
        )

    def _strategy_seed_strategy_family(seed: Any) -> str:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        family = (
            metadata.get("strategy_family")
            or metadata.get("strategy_kind")
            or metadata.get("archetype")
        )
        if family:
            return str(family)
        hints = list(getattr(seed, "feature_hints", []) or [])
        return str(hints[0]) if hints else ""

    _SEED_KINDS_RISK = frozenset({"risk_constraint", "execution_constraint"})
    _SEED_KINDS_NEGATIVE = frozenset({"negative", "negative_memory"})

    def _strategy_seed_allowed_actions(status: str, seed_kind: str = "") -> List[str]:
        actions_by_status = {
            "draft": ["accept", "reject", "request-evidence", "archive", "merge"],
            "needs_more_evidence": ["accept", "reject", "request-evidence", "archive", "merge"],
            "accepted": ["convert-to-spec-seed", "reject", "request-evidence", "archive", "merge"],
            "promoted_to_strategy_spec": ["submit-replication"],
            "rejected": [],
            "archived_as_insight": [],
            "merged": [],
            "converted_to_risk_constraint": [],
            "converted_to_negative": [],
        }
        actions = list(actions_by_status.get(status, []))
        if status in {"draft", "needs_more_evidence", "accepted"}:
            if seed_kind in _SEED_KINDS_RISK and "convert-to-risk" not in actions:
                actions.append("convert-to-risk")
            if seed_kind in _SEED_KINDS_NEGATIVE and "convert-to-negative" not in actions:
                actions.append("convert-to-negative")
        return actions

    def _strategy_seed_metadata_suggestions(seed: Any) -> List[Dict[str, Any]]:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        raw_items = metadata.get("suggested_actions") or metadata.get("suggestions") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if isinstance(raw_items, str):
            raw_items = [{"type": raw_items}]
        suggestions: List[Dict[str, Any]] = []
        iterable = raw_items if isinstance(raw_items, list) else []
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or raw.get("action") or "").strip()
            if not action_type:
                continue
            item = dict(raw)
            item["type"] = action_type
            item.setdefault("source", "seed_metadata")
            item.setdefault("mode", "suggestion")
            item.setdefault("requires_operator_review", True)
            item.setdefault("auto_promote", False)
            suggestions.append(item)

        recommended = metadata.get("recommended_action")
        if isinstance(recommended, str):
            recommended = {"type": recommended}
        if isinstance(recommended, dict):
            action_type = str(recommended.get("type") or recommended.get("action") or "").strip()
            if action_type:
                item = dict(recommended)
                item["type"] = action_type
                item.setdefault("source", "seed_metadata")
                item.setdefault("mode", "suggestion")
                item.setdefault("requires_operator_review", True)
                item.setdefault("auto_promote", False)
                suggestions.append(item)
        return suggestions

    def _strategy_seed_persona_suggestions(
        seed: Any,
        *,
        snapshot_at: str,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        suggestions: List[Dict[str, Any]] = []
        try:
            personas = list_persona_records(tenant_id)
        except Exception as exc:  # pragma: no cover - defensive read surface fallback.
            log.warning("Persona read surface unavailable for seed inbox suggestions: %s", exc)
            return suggestions

        read_store = _get_read_store()
        for persona in personas:
            persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
            if not persona_id:
                continue
            try:
                route_policy = read_store.get_route_policy_for_persona(persona_id) or {}
                capability_snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}
                profile = extract_persona_strategy_profile(
                    persona,
                    route_policy=route_policy,
                    capability_snapshot=capability_snapshot,
                )
                matches = PersonaStrategyDiscoveryService().match_candidates(
                    profile,
                    strategy_seeds=[seed],
                    strategy_specs=[],
                    created_at=snapshot_at,
                    include_blocked=True,
                )
            except Exception as exc:  # pragma: no cover - one bad persona should not break inbox.
                log.warning("Persona strategy suggestion failed for %s: %s", persona_id, exc)
                continue
            for match in matches:
                payload = match.to_dict()
                action = payload.get("recommended_action") or {}
                action_type = str(action.get("type") or "").strip()
                if (
                    payload.get("matched_object_id") == getattr(seed, "seed_id", None)
                    and action_type == "promote_seed_candidate"
                ):
                    suggestions.append(
                        {
                            "type": "promote_seed_candidate",
                            "source": "persona_strategy_discovery",
                            "mode": "suggestion",
                            "requires_operator_review": True,
                            "auto_promote": False,
                            "persona_id": persona_id,
                            "match_id": payload.get("match_id"),
                            "score": payload.get("score"),
                            "blockers": (payload.get("metadata") or {}).get("blockers") or [],
                        }
                    )
        return suggestions

    def _strategy_seed_suggestions(
        seed: Any,
        *,
        snapshot_at: str,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        seen: Set[Tuple[str, str, str]] = set()
        suggestions: List[Dict[str, Any]] = []
        for item in [
            *_strategy_seed_metadata_suggestions(seed),
            *_strategy_seed_persona_suggestions(
                seed,
                snapshot_at=snapshot_at,
                tenant_id=tenant_id,
            ),
        ]:
            key = (
                str(item.get("type") or ""),
                str(item.get("source") or ""),
                str(item.get("match_id") or item.get("persona_id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(item)
        return suggestions

    def _strategy_seed_similar_existing_strategies(seed: Any) -> List[Dict[str, Any]]:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        raw = metadata.get("similar_existing_strategies") or []
        if isinstance(raw, str):
            raw = [{"strategy_id": raw}]
        if isinstance(raw, list) and raw:
            return [
                dict(item) if isinstance(item, dict) else {"strategy_id": str(item)}
                for item in raw[:5]
            ]

        family = _strategy_seed_strategy_family(seed)
        if not family:
            return []
        try:
            candidates = _list_strategy_summaries()
        except Exception:  # pragma: no cover - read-store fallback.
            return []
        similar: List[Dict[str, Any]] = []
        for item in candidates:
            strategy_family = str(
                item.get("strategy_family")
                or (item.get("metadata") or {}).get("strategy_family")
                or item.get("archetype")
                or ""
            )
            if strategy_family != family:
                continue
            similar.append(
                {
                    "strategy_id": item.get("strategy_id") or item.get("id"),
                    "title": item.get("title") or item.get("name"),
                    "strategy_family": strategy_family,
                }
            )
            if len(similar) >= 5:
                break
        return similar

    def _strategy_seed_recommended_action(
        *,
        status: str,
        seed_kind: str = "",
        suggestions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        for item in suggestions:
            if str(item.get("type") or "") == "promote_seed_candidate":
                return dict(item)
        if status == "accepted":
            if seed_kind in _SEED_KINDS_RISK:
                return {"type": "convert-to-risk", "mode": "operator_decision"}
            if seed_kind in _SEED_KINDS_NEGATIVE:
                return {"type": "convert-to-negative", "mode": "operator_decision"}
            return {"type": "convert-to-spec-seed", "mode": "operator_decision"}
        if status in {"draft", "needs_more_evidence"}:
            if seed_kind in _SEED_KINDS_RISK:
                return {"type": "accept", "mode": "operator_decision", "next": "convert-to-risk"}
            if seed_kind in _SEED_KINDS_NEGATIVE:
                return {"type": "accept", "mode": "operator_decision", "next": "convert-to-negative"}
            return {"type": "accept", "mode": "operator_decision"}
        if status == "promoted_to_strategy_spec":
            return {"type": "submit-replication", "mode": "operator_decision"}
        return {"type": "none", "mode": "terminal"}

    def _strategy_seed_negative_memory_warning(seed: Any) -> Dict[str, Any]:
        raw = getattr(seed, "negative_memory_match", None)
        if not raw:
            return {"warning_level": "info", "similarity": 0.0, "reason": ""}
        match = dict(raw) if isinstance(raw, dict) else (raw.to_dict() if hasattr(raw, "to_dict") else {})
        return {
            "warning_level": str(match.get("warning_level") or "info"),
            "similarity": float(match.get("similarity") or 0.0),
            "reason": str(match.get("reason") or ""),
            "matched_memory_id": match.get("matched_memory_id"),
            "matched_memory_kind": match.get("matched_memory_kind"),
            "matched_terms": list(match.get("matched_terms") or []),
        }

    def _strategy_seed_card(
        seed: Any,
        *,
        snapshot_at: str,
        include_audit: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = _strategy_seed_status_value(seed)
        suggestions = _strategy_seed_suggestions(
            seed,
            snapshot_at=snapshot_at,
            tenant_id=tenant_id,
        )
        lineage = dict(getattr(seed, "lineage", {}) or {})
        metadata = dict(getattr(seed, "metadata", {}) or {})
        evidence_refs = list(getattr(seed, "evidence_item_ids", []) or [])
        citation_refs = list(getattr(seed, "citation_refs", []) or [])
        seed_kind = str(metadata.get("seed_kind") or "strategy_spec_seed")
        source_surface = str(metadata.get("source_surface") or "")
        card = {
            "id": seed.seed_id,
            "seed_id": seed.seed_id,
            "source": {
                "source_id": seed.source_id,
                "source_ids": list(getattr(seed, "source_ids", []) or []),
                "source_kind": _strategy_seed_source_kind(seed),
                "source_surface": source_surface or None,
                "evidence_bundle_id": seed.evidence_bundle_id,
            },
            "seed_kind": seed_kind,
            "strategy_family": _strategy_seed_strategy_family(seed),
            "hypothesis": seed.hypothesis,
            "market": {
                "asset_class": list(getattr(seed, "asset_class", []) or []),
                "market_scope": list(getattr(seed, "market_scope", []) or []),
                "holding_period": getattr(seed, "holding_period", None),
            },
            "asset": list(getattr(seed, "asset_class", []) or []),
            "required_data": list(getattr(seed, "required_data", []) or []),
            "evidence_count": len(set([*evidence_refs, *citation_refs])),
            "confidence": getattr(seed, "confidence", None),
            "negative_memory_warning": _strategy_seed_negative_memory_warning(seed),
            "similar_existing_strategies": _strategy_seed_similar_existing_strategies(seed),
            "recommended_action": _strategy_seed_recommended_action(
                status=status,
                seed_kind=seed_kind,
                suggestions=suggestions,
            ),
            "suggested_actions": suggestions,
            "review_status": status,
            "status": status,
            "allowedActions": _strategy_seed_allowed_actions(status, seed_kind),
            "lineage_refs": {
                "evidence_bundle_id": seed.evidence_bundle_id,
                "source_ids": list(getattr(seed, "source_ids", []) or []),
                "evidence_item_ids": evidence_refs,
                "citation_refs": citation_refs,
                "trace_refs": list(getattr(seed, "trace_refs", []) or []),
                "registry_write_performed": lineage.get("registry_write_performed", False),
                "execution_route": lineage.get("execution_route") or "none",
            },
            "created_at": getattr(seed, "created_at", None),
        }
        if include_audit:
            card["review_decisions"] = list(lineage.get("review_decisions") or [])
            card["last_review_decision"] = lineage.get("last_review_decision")
        return card

    def _strategy_seed_matches_filters(
        seed: Any,
        *,
        status: Optional[str],
        source_kind: Optional[str],
        strategy_family: Optional[str],
        seed_kind: Optional[str],
        min_confidence: Optional[float],
    ) -> bool:
        if status and _strategy_seed_status_value(seed) != status:
            return False
        if source_kind and _strategy_seed_source_kind(seed) != source_kind:
            return False
        if strategy_family and _strategy_seed_strategy_family(seed) != strategy_family:
            return False
        if seed_kind:
            metadata = dict(getattr(seed, "metadata", {}) or {})
            actual_seed_kind = str(metadata.get("seed_kind") or "strategy_spec_seed")
            if actual_seed_kind != seed_kind:
                return False
        if min_confidence is not None and float(getattr(seed, "confidence", 0.0) or 0.0) < min_confidence:
            return False
        return True

    def _strategy_seed_list_response(
        *,
        status: Optional[str],
        source_kind: Optional[str],
        strategy_family: Optional[str],
        seed_kind: Optional[str],
        min_confidence: Optional[float],
        page_token: Optional[str],
        page_size: int,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = utc_now()
        store = StrategySpecSeedStore()
        seeds = [
            seed
            for seed in store.list_all()
            if _strategy_seed_matches_filters(
                seed,
                status=status,
                source_kind=source_kind,
                strategy_family=strategy_family,
                seed_kind=seed_kind,
                min_confidence=min_confidence,
            )
        ]
        cards = [
            _strategy_seed_card(seed, snapshot_at=snapshot_at, tenant_id=tenant_id)
            for seed in seeds
        ]
        page_items, next_page_token = _page_slice(cards, page_token, page_size)
        return {
            "data": {
                "id": "management_strategy_seeds",
                "items": page_items,
                "summary": {
                    "total_items": len(cards),
                    "returned_items": len(page_items),
                    "research_only": True,
                    "execution_route": "none",
                },
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(cards),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "store_path": str(store.path),
                "count": len(cards),
                "filters": {
                    "status": status,
                    "source_kind": source_kind,
                    "strategy_family": strategy_family,
                    "seed_kind": seed_kind,
                    "min_confidence": min_confidence,
                },
                "research_only": True,
                "execution_route": "none",
            },
        }

    def _strategy_seed_detail_response(
        seed_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = utc_now()
        store = StrategySpecSeedStore()
        seed = store.get(seed_id)
        if seed is None:
            raise _bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                f"StrategySpecSeed not found: {seed_id}",
                precondition_failed="seed_id",
            )
        return {
            "data": _strategy_seed_card(
                seed,
                snapshot_at=snapshot_at,
                include_audit=True,
                tenant_id=tenant_id,
            ),
            "meta": {
                "snapshot_at": snapshot_at,
                "store_path": str(store.path),
                "research_only": True,
                "execution_route": "none",
            },
        }

    def _strategy_seed_review_action(payload: Dict[str, Any]) -> str:
        action = str(
            payload.get("action")
            or payload.get("decision")
            or payload.get("type")
            or ""
        ).strip().lower().replace("-", "_")
        aliases = {
            "request_more_evidence": "request_evidence",
            "needs_more_evidence": "request_evidence",
            "convert": "convert_to_spec_seed",
            "convert_to_strategy_spec": "convert_to_spec_seed",
            "archive_as_insight": "archive",
            "archived_as_insight": "archive",
            "convert_risk": "convert_to_risk",
            "convert_negative": "convert_to_negative",
            "convert_to_risk_constraint": "convert_to_risk",
        }
        action = aliases.get(action, action)
        if not action:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "StrategySpecSeed review action is required",
                "Set action to accept, reject, request-evidence, convert-to-spec-seed, convert-to-risk, convert-to-negative, or archive.",
                precondition_failed="action",
            )
        if action == "merge":
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Use the merge endpoint for StrategySpecSeed merge actions",
                "POST /bff/management/strategy-seeds/{seed_id}/merge handles merge review decisions.",
                precondition_failed="action",
            )
        return action

    def _strategy_seed_target_refs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = payload.get("target_refs") or payload.get("targetRefs") or []
        refs: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            refs.extend(dict(item) for item in raw if isinstance(item, dict))
        for key, ref_type in (
            ("strategy_spec_id", "strategy_spec"),
            ("strategySpecId", "strategy_spec"),
            ("target_strategy_id", "strategy_spec"),
            ("targetStrategyId", "strategy_spec"),
        ):
            value = str(payload.get(key) or "").strip()
            if value:
                refs.append({"type": ref_type, "id": value})
        return refs

    def _strategy_seed_review_result(
        *,
        updated_seed: Any,
        decision: SeedReviewDecision,
        snapshot_at: str,
        resolved_key: str,
        replayed: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "data": {
                "seed_id": updated_seed.seed_id,
                "status": _strategy_seed_status_value(updated_seed),
                "review_status": _strategy_seed_status_value(updated_seed),
                "decision": decision.to_dict(),
                "seed": _strategy_seed_card(
                    updated_seed,
                    snapshot_at=snapshot_at,
                    include_audit=True,
                    tenant_id=tenant_id,
                ),
                "registry_write_performed": False,
                "execution_route": "none",
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
                "idempotency": {
                    "idempotencyKey": resolved_key,
                    "replayed": replayed,
                },
            },
        }

    def _strategy_seed_review_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        action = _strategy_seed_review_action(payload)
        request_hash = stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/review",
                "seed_id": seed_id,
                "action": action,
                "payload": payload,
            }
        )
        cached = _strategy_seed_review_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = utc_now()
        try:
            updated, decision = StrategySpecSeedStore().record_review_decision(
                seed_id,
                decision=action,
                reviewer_id=identity.operator_id,
                reason=str(payload.get("reason") or ""),
                target_refs=_strategy_seed_target_refs(payload),
                created_at=payload.get("created_at") or snapshot_at,
                idempotency_key=resolved_key,
                request_hash=request_hash,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _strategy_seed_review_error(exc) from exc
        result = _strategy_seed_review_result(
            updated_seed=updated,
            decision=decision,
            snapshot_at=snapshot_at,
            resolved_key=resolved_key,
            replayed=bool(getattr(decision, "idempotent_replay", False)),
            tenant_id=_bff_tenant_id(identity),
        )
        _strategy_seed_review_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    def _strategy_seed_merge_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        target_seed_id = str(
            payload.get("target_seed_id")
            or payload.get("targetSeedId")
            or payload.get("target_id")
            or payload.get("targetId")
            or ""
        ).strip()
        if not target_seed_id:
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "StrategySpecSeed merge target is required",
                "Set target_seed_id to the StrategySpecSeed that will absorb this candidate.",
                precondition_failed="target_seed_id",
            )
        request_hash = stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/merge",
                "seed_id": seed_id,
                "payload": payload,
            }
        )
        cached = _strategy_seed_review_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = utc_now()
        try:
            updated, decision = StrategySpecSeedStore().merge_seed(
                seed_id,
                target_seed_id=target_seed_id,
                reviewer_id=identity.operator_id,
                reason=str(payload.get("reason") or ""),
                target_refs=_strategy_seed_target_refs(payload),
                created_at=payload.get("created_at") or snapshot_at,
                idempotency_key=resolved_key,
                request_hash=request_hash,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _strategy_seed_review_error(exc) from exc
        result = _strategy_seed_review_result(
            updated_seed=updated,
            decision=decision,
            snapshot_at=snapshot_at,
            resolved_key=resolved_key,
            replayed=bool(getattr(decision, "idempotent_replay", False)),
            tenant_id=_bff_tenant_id(identity),
        )
        _strategy_seed_review_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    @router.get("/bff/management/strategy-seeds")
    async def bff_list_strategy_seed_inbox(
        status: Optional[str] = None,
        source_kind: Optional[str] = None,
        strategy_family: Optional[str] = None,
        seed_kind: Optional[str] = None,
        min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: governed StrategySpecSeed review inbox read model."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _strategy_seed_list_response(
            status=status,
            source_kind=source_kind,
            strategy_family=strategy_family,
            seed_kind=seed_kind,
            min_confidence=min_confidence,
            page_token=page_token,
            page_size=page_size,
            tenant_id=_bff_tenant_id(identity),
        )

    @router.get("/bff/management/strategy-seeds/{seed_id}")
    async def bff_get_strategy_seed_card(
        seed_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: governed StrategySpecSeed review card."""
        identity = _extract_identity(authorization)
        _require_read_role(identity)
        return _strategy_seed_detail_response(
            seed_id,
            tenant_id=_bff_tenant_id(identity),
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/review", status_code=202)
    async def bff_review_strategy_seed(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: apply a governed StrategySpecSeed review decision."""
        identity = _extract_identity(authorization)
        _require_strategy_seed_review_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_review_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/merge", status_code=202)
    async def bff_merge_strategy_seed(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: merge a StrategySpecSeed candidate into another seed candidate."""
        identity = _extract_identity(authorization)
        _require_strategy_seed_review_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_merge_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/submit-replication", status_code=202)
    async def bff_submit_strategy_seed_replication(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: submit a promoted StrategySpecSeed to research replication."""
        identity = _extract_identity(authorization)
        _require_strategy_seed_submit_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_replication_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    return router

"""BFF Strategies canonical router.

Decomposed into cohesive resource/use-case subrouters per BFF-ROUTER-STRUCT-001:
  - routes/collection.py: /bff/strategies (list, create)
  - routes/detail.py: /bff/strategies/{strategy_id}* (detail, patch, specs, experiments, artifacts, lineage, audit, ooda, actions, dry-run)
  - routes/seeds.py: /bff/management/strategy-seeds* (inbox, card, review, merge, submit-replication)

Thin parent factory: gathers dependencies into StrategyRouteContext, builds subrouters,
and mounts their routes without proxying symbols or duplicating handlers.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException

from .routes.common import (
    StrategyRouteContext,
    default_bff_error,
    default_extract_identity,
    default_page_slice,
    default_read_surface_meta,
    default_require_operator_role,
    default_require_read_role,
    default_utc_now,
)
from .routes.collection import build_collection_router
from .routes.detail import build_detail_router
from .routes.seeds import build_seeds_router

log = logging.getLogger(__name__)


def create_strategies_router(
    *,
    read_surface: Optional[Any] = None,
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
    _extract_identity = extract_identity or default_extract_identity
    _require_read_role = require_read_role or default_require_read_role
    _bff_error = bff_error or default_bff_error
    _require_operator_role = require_operator_role or (
        lambda ident: default_require_operator_role(ident, _bff_error)
    )
    _utc_now = utc_now or default_utc_now
    _page_slice = page_slice or default_page_slice
    _read_surface_meta = read_surface_meta or default_read_surface_meta

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

    ctx = StrategyRouteContext(
        read_surface=read_surface,
        get_read_store=get_read_store,
        extract_identity=_extract_identity,
        require_read_role=_require_read_role,
        require_operator_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=_utc_now,
        page_slice=_page_slice,
        read_surface_meta=_read_surface_meta,
        reject_body_idempotency_key=reject_body_idempotency_key or (lambda p: None),
        resolve_final_idempotency_key=resolve_final_idempotency_key or (lambda ik, xik: str(ik or xik or "")),
        stable_json_hash=stable_json_hash or (lambda d: ""),
        request_dry_run_requested=request_dry_run_requested or (lambda: False),
        dry_run_success_response=dry_run_success_response or (lambda *a, **kw: {}),
        normalize_lifecycle_state=normalize_lifecycle_state or (lambda s: str(s or "draft")),
        normalize_risk_level=normalize_risk_level or (lambda r: str(r or "medium")),
        strategy_persona_idempotency_check=strategy_persona_idempotency_check or (lambda k, h: None),
        strategy_persona_action_command=strategy_persona_action_command,
        strategy_overlay=_strategy_overlay,
        strategy_persona_idempotency=_strategy_persona_idempotency,
        strategy_seed_replication_idempotency=_strategy_seed_replication_idempotency,
        strategy_seed_review_idempotency=_strategy_seed_review_idempotency,
        list_governance_audit_events=list_governance_audit_events,
        ooda_packet_list_payload=ooda_packet_list_payload,
        require_ooda_packet_routes_enabled=require_ooda_packet_routes_enabled,
        deprecated_bff_path_response=deprecated_bff_path_response,
        bff_me_tenant_payload=bff_me_tenant_payload,
        list_persona_records=list_persona_records,
        list_strategy_summaries=list_strategy_summaries,
    )

    router = APIRouter()

    subrouters = [
        build_collection_router(ctx),
        build_detail_router(ctx),
        build_seeds_router(ctx),
    ]

    for sub in subrouters:
        router.routes.extend(sub.routes)

    return router

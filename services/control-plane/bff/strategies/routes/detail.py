"""Strategy detail, specs, artifacts, experiments, lineage, audit, ooda, actions, and dry-run routes."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, Query

from .common import StrategyRouteContext

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from models import ErrorCode


def build_detail_router(ctx: StrategyRouteContext) -> APIRouter:
    router = APIRouter()

    @router.get("/bff/strategies/{strategy_id}")
    async def bff_get_strategy(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: strategy detail."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
        overlay = ctx.strategy_overlay.get(strategy_id)
        summary = read_store.get_strategy_spec(strategy_id)
        if not summary and not overlay:
            raise ctx.bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        summary_for_dto = summary or {"strategy_id": strategy_id, "title": (overlay or {}).get("name")}
        detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
        dto = ctx.project_strategy_dto(summary_for_dto, detail=detail, overlay=overlay)
        return {
            "data": dto,
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_operator_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        request_hash = ctx.stable_json_hash(
            {"route": "PATCH /bff/strategies/{strategy_id}", "id": strategy_id, "payload": payload}
        )
        cached = ctx.strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        read_store = ctx.get_read_store_port()
        summary = read_store.get_strategy_spec(strategy_id)
        overlay = ctx.strategy_overlay.get(strategy_id)
        if not summary and not overlay:
            raise ctx.bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        snapshot_at = ctx.utc_now()
        base = dict(overlay) if overlay else {}
        if not base:
            detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
            base = ctx.project_strategy_dto(summary or {"strategy_id": strategy_id}, detail=detail)
        for field_name in (
            "name", "owner", "state", "risk", "alpha",
            "capitalPoolId", "personaIds", "pnl30d", "sharpe", "drawdown",
            "availableActions",
        ):
            if field_name in payload:
                base[field_name] = payload[field_name]
        if "state" in payload:
            base["state"] = ctx.normalize_lifecycle_state(payload["state"])
        if "risk" in payload:
            base["risk"] = ctx.normalize_risk_level(payload["risk"])
        base["updatedAt"] = snapshot_at
        base["id"] = strategy_id
        ctx.strategy_overlay[strategy_id] = base
        result = {"data": base, "meta": {"snapshot_at": snapshot_at}}
        ctx.strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/bff/strategies/{strategy_id}/specs")
    async def bff_list_strategy_specs(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: spec versions for a strategy."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.ensure_strategy_exists(strategy_id)
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
        versions = read_store.list_strategy_spec_versions(strategy_id) or []
        return {
            "data": versions,
            "items": versions,
            "page_info": {"next_page_token": None, "total": len(versions)},
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_operator_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        ctx.ensure_strategy_exists(strategy_id)
        request_hash = ctx.stable_json_hash(
            {"route": "POST /bff/strategies/{id}/specs", "id": strategy_id, "payload": payload}
        )
        dry_run = ctx.request_dry_run_requested()
        if not dry_run:
            cached = ctx.strategy_persona_idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached
        snapshot_at = ctx.utc_now()
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
            return ctx.dry_run_success_response(
                result["data"],
                snapshot_at=snapshot_at,
                idempotency_key=resolved_key,
                evidence_kind="strategy_spec.create",
            )
        ctx.strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    @router.get("/bff/strategies/{strategy_id}/experiments")
    async def bff_list_strategy_experiments(
        strategy_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: experiments related to a strategy."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.ensure_strategy_exists(strategy_id)
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
        raw = read_store.list_research_experiments() or []
        items = [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.ensure_strategy_exists(strategy_id)
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
        raw = read_store.list_research_artifacts() or []
        items = [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
        return {
            "data": items,
            "items": items,
            "page_info": {"next_page_token": None, "total": len(items)},
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.ensure_strategy_exists(strategy_id)
        read_store = ctx.get_read_store_port()
        snapshot_at = ctx.utc_now()
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
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.ensure_strategy_exists(strategy_id)
        snapshot_at = ctx.utc_now()
        events = ctx.list_governance_audit_events() if ctx.list_governance_audit_events else []
        filtered = _filter_audit_events_by_target(events or [], strategy_id)
        return {
            "data": filtered,
            "items": filtered,
            "page_info": {"next_page_token": None, "total": len(filtered)},
            "meta": ctx.read_surface_meta(
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
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        if ctx.require_ooda_packet_routes_enabled:
            ctx.require_ooda_packet_routes_enabled()
        clean_id = strategy_id.strip()
        read_store = ctx.get_read_store_port()
        packets = read_store.list_ooda_packets_for_strategy(clean_id)
        if ctx.ooda_packet_list_payload:
            return ctx.ooda_packet_list_payload(
                packets,
                surface_key="strategy_ooda_packets",
                page_token=page_token,
                page_size=page_size,
                related={"type": "Strategy", "id": clean_id},
            )
        return {"data": packets, "items": packets, "meta": ctx.read_surface_meta("ooda_packets", "strategy_ooda", snapshot_at=ctx.utc_now())}

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
        if ctx.deprecated_bff_path_response:
            return ctx.deprecated_bff_path_response(
                route="/bff/strategies/{strategy_id}/actions/{action_id}",
                replacement="/bff/actions/strategy/{strategy_id}/{action_id}",
            )
        return {"data": {"strategy_id": strategy_id, "action_id": action_id, "status": "deprecated"}}

    @router.post("/bff/strategies/{strategy_id}/dry-run", status_code=202)
    async def bff_strategy_dry_run(
        strategy_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: launch a strategy dry-run; returns a stub run handle."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        ctx.ensure_strategy_exists(strategy_id)
        request_hash = ctx.stable_json_hash(
            {"route": "POST /bff/strategies/{id}/dry-run", "id": strategy_id, "payload": payload}
        )
        cached = ctx.strategy_persona_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = ctx.utc_now()
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
        ctx.strategy_persona_idempotency[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

    return router

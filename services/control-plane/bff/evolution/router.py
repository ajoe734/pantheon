"""Evolution domain canonical router.

Design units:
- ACG-01-EVOEXP (docs/04/pantheon_architecture_cleanup_gap_2026-08-27): Reusable evolution experiment
  and evolution program management routes.
- OPGAP-BE-EVOLUTION-ROUTER-20260830: Full domain router extraction handling 13 evolution domain
  routes:
  1. GET /bff/evolution-programs/{program_id}/ooda: OODA packets for evolution program
  2. GET /bff/management/evolution-journal: Aggregated management evolution journal with filters & lineage
  3. GET /api/v1/evolution-decisions: Paginated and filtered evolution decisions
  4. GET /api/v1/evolution-decisions/{decision_id}: Detail for evolution decision
  5. GET /api/v1/freeze-orders: Active and historical freeze orders
  6. GET /api/v1/rollbacks: Global runtime rollbacks
  7. GET /api/v1/lineage: Lineage edge and artifact records
  8. GET /api/v1/lineage/edges/{edge_id}: Lineage edge detail
  9. GET /api/v1/lineage/graph: Lineage graph traversal
  10. GET /api/v1/lineage/inspiration/{artifact_id}: BFF-composed inspiration graph
  11. GET /api/v1/telemetry: Telemetry events list
  12. GET /api/v1/telemetry/{runtime_id}/summary: Telemetry summary for a runtime
  13. GET /api/v1/telemetry/{artifact_id}/performance: Performance chart for an artifact

Also preserves the 7 evolution programs routes via ``create_evolution_programs_router``:
  - GET /bff/evolution-programs
  - POST /bff/evolution-programs
  - GET /bff/evolution-programs/{program_id}
  - PATCH /bff/evolution-programs/{program_id}
  - GET /bff/evolution-programs/{program_id}/runs
  - GET /bff/evolution-programs/{program_id}/candidates
  - POST /bff/evolution-programs/{program_id}/actions/{action_id}
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.bff.models import ErrorCode, ObjectType

from .service import (
    EvolutionService,
    _management_prune_camel_aliases,
    _management_record_id,
    evolution_journal_base_item,
    evolution_journal_decision_item,
    evolution_journal_filter_items,
    evolution_journal_freeze_order_item,
    evolution_journal_is_registered_seed_id,
    evolution_journal_postmortem_item,
    evolution_journal_rollback_item,
    evolution_journal_summary,
    evolution_journal_surfaces,
    evolution_journal_target,
    evolution_journal_timestamp,
    ew04_inspiration_payload,
    ew04_inspiration_projection_from_lineage_edges,
    ooda_packet_list_payload,
    ooda_packet_routes_enabled,
    project_evolution_decision_contract,
    project_freeze_order_contract,
    project_rollback_contract,
    _EVOLUTION_JOURNAL_REFERENCE_FIELD_CATEGORY,
    _EVOLUTION_JOURNAL_TARGET_TYPE_CATEGORY,
)

log = logging.getLogger(__name__)

PageSlice = Callable[[Sequence[Any], Optional[str], int], Tuple[List[Any], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
SubmitAction = Callable[..., Any]


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_page_slice(
    items: Sequence[Any], page_token: Optional[str], page_size: int
) -> Tuple[List[Any], Optional[str]]:
    try:
        start = int(page_token) if page_token else 0
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return list(items[start:end]), next_page_token


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _default_dataset_surface_status(dataset: str, *, snapshot_at: str, **kwargs: Any) -> Dict[str, Any]:
    source = kwargs.get("source", "ok")
    has_data = kwargs.get("has_data")
    if source in ("missing", "unavailable") or has_data is False:
        return {"status": "unavailable", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}
    if source == "local_snapshot":
        return {"status": "degraded", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}
    return {"status": "available", "dataset": dataset, "snapshot_at": snapshot_at, "source": source}


def _default_read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: Optional[str] = None,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    now = snapshot_at or _default_utc_now()
    surf = surface or {"status": "ok", "source": "ok", "dataset": dataset}
    meta: Dict[str, Any] = {
        "snapshot_at": now,
        "surfaces": {surface_key: surf},
    }
    if total is not None:
        meta["total"] = total
    return meta


def _default_raise_if_read_surface_unavailable(surface: Dict[str, Any], *, label: str) -> None:
    if surface.get("status") == "unavailable":
        raise HTTPException(
            status_code=503,
            detail={
                "code": ErrorCode.DEPENDENCY_UNAVAILABLE.value,
                "message": f"{label} read surface unavailable",
                "reason": str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
                "precondition_failed": "read_surface_unavailable",
                "suggestion": "Verify the owning service URL and health before retrying this read.",
            },
        )


def _default_extract_identity(authorization: Optional[str] = None) -> Any:
    class DummyIdentity:
        operator_id = "operator-1"
        roles = {"operator", "viewer", "admin"}

    return DummyIdentity()


def _default_require_read_role(identity: Any) -> None:
    pass


def _default_require_operator_role(identity: Any) -> None:
    pass


def _default_bff_error(status_code: int, code: Any, message: str, reason: Optional[str] = None, **kwargs: Any) -> HTTPException:
    code_val = code.value if hasattr(code, "value") else str(code)
    return HTTPException(
        status_code=status_code,
        detail={"code": code_val, "message": message, "reason": reason or message, **kwargs},
    )


def _filter_by_status_csv(records: List[Dict[str, Any]], status_csv: Optional[str]) -> List[Dict[str, Any]]:
    if not status_csv:
        return records
    requested = {s.strip().lower() for s in status_csv.split(",") if s.strip()}
    return [r for r in records if str(r.get("status") or "").lower() in requested]


def _register_evolution_programs_routes(
    router: APIRouter,
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    require_operator_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: PageSlice,
    snapshot_meta: SnapshotMeta,
    dataset_surface_status: SurfaceStatus,
    submit_program_action: Optional[SubmitAction] = None,
) -> None:
    def _resolve_read_store() -> Any:
        if read_surface is not None:
            return read_surface() if callable(read_surface) else read_surface
        if get_read_store is not None:
            return get_read_store()
        return None

    def _require_program(read_store: Any, program_id: str) -> Dict[str, Any]:
        program = getattr(read_store, "get_evolution_program", lambda pid: None)(program_id)
        if not program:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Evolution program not found",
                f"Evolution program {program_id} does not exist",
            )
        return program

    @router.get("/bff/evolution-programs")
    async def list_evolution_programs(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """List evolution programs. Filter: comma-separated ``status`` (case-insensitive)."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = _resolve_read_store()
        snapshot_at = utc_now()
        raw_programs = getattr(read_store, "list_evolution_programs", lambda: [])() or []
        programs = _filter_by_status_csv(raw_programs, status)
        programs = sorted(programs, key=lambda p: str(p.get("created_at") or ""), reverse=True)
        surface = dataset_surface_status(
            "evolution_programs", snapshot_at=snapshot_at, has_data=bool(programs) or None,
        )
        if surface.get("status") == "unavailable" and not programs:
            page_items, next_page_token = [], None
        else:
            page_items, next_page_token = page_slice(programs, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {"evolution_programs": surface}
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    @router.post("/bff/evolution-programs", status_code=201)
    async def create_evolution_program(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Create an evolution program. ``name`` is required (422 otherwise)."""
        identity = extract_identity(authorization)
        require_operator_role(identity)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Evolution program name must be a non-empty string",
                precondition_failed="name",
            )
        read_store = _resolve_read_store()
        snapshot_at = utc_now()
        program_id = str(
            payload.get("program_id")
            or payload.get("id")
            or f"evp-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        )
        actor_id = getattr(identity, "operator_id", "operator-1")
        return getattr(read_store, "create_evolution_program")(
            program_id=program_id,
            name=name,
            actor_id=actor_id,
            created_at=snapshot_at,
            params=payload.get("params") or {},
        )

    @router.get("/bff/evolution-programs/{program_id}")
    async def get_evolution_program(
        program_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = _resolve_read_store()
        program = _require_program(read_store, program_id.strip())
        snapshot_at = utc_now()
        return {"data": program, "meta": snapshot_meta(snapshot_at)}

    @router.patch("/bff/evolution-programs/{program_id}")
    async def patch_evolution_program(
        program_id: str,
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Patch ``name``/``status``/``params``; other fields are ignored."""
        identity = extract_identity(authorization)
        require_operator_role(identity)
        read_store = _resolve_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        actor_id = getattr(identity, "operator_id", "operator-1")
        updated = getattr(read_store, "patch_evolution_program")(
            clean_id,
            patch={k: payload[k] for k in ("name", "status", "params") if k in payload},
            actor_id=actor_id,
            updated_at=snapshot_at,
        )
        return {"data": updated, "meta": snapshot_meta(snapshot_at)}

    @router.get("/bff/evolution-programs/{program_id}/runs")
    async def list_evolution_program_runs(
        program_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = _resolve_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        runs = getattr(read_store, "list_evolution_program_runs", lambda pid: [])(clean_id) or []
        page_items, next_page_token = page_slice(runs, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    @router.get("/bff/evolution-programs/{program_id}/candidates")
    async def list_evolution_program_candidates(
        program_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = _resolve_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        candidates = getattr(read_store, "list_evolution_program_candidates", lambda pid: [])(clean_id) or []
        page_items, next_page_token = page_slice(candidates, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    @router.post("/bff/evolution-programs/{program_id}/actions/{action_id}", status_code=202)
    async def evolution_program_action(
        program_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        resolved_key = (idempotency_key or x_idempotency_key or "").strip()
        read_store = _resolve_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        if submit_program_action is None:
            raise bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Evolution program actions are not wired",
                "submit_program_action was not injected into create_evolution_programs_router",
            )
        try:
            res = submit_program_action(ObjectType.EVOLUTION_PROGRAM.value, clean_id, action_id, resolved_key, identity, payload)
        except TypeError:
            res = submit_program_action(ObjectType.EVOLUTION_PROGRAM.value, clean_id, action_id, identity, payload)
        return res.model_dump(mode="json") if hasattr(res, "model_dump") else res


def create_evolution_programs_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice: Optional[PageSlice] = None,
    snapshot_meta: Optional[SnapshotMeta] = None,
    dataset_surface_status: Optional[SurfaceStatus] = None,
    submit_program_action: Optional[SubmitAction] = None,
    **kwargs: Any,
) -> APIRouter:
    """Build the Evolution Programs router (ACG-01-006 / ACG-01-007).

    Registers 7 evolution program endpoints. Preserved for backward compatibility and
    used by main.py assembly until cutover.
    """
    router = APIRouter()
    _register_evolution_programs_routes(
        router,
        read_surface=read_surface,
        get_read_store=get_read_store or (lambda: None),
        extract_identity=extract_identity or _default_extract_identity,
        require_read_role=require_read_role or _default_require_read_role,
        require_operator_role=require_operator_role or _default_require_operator_role,
        bff_error=bff_error or _default_bff_error,
        utc_now=utc_now or _default_utc_now,
        page_slice=page_slice or _default_page_slice,
        snapshot_meta=snapshot_meta or _default_snapshot_meta,
        dataset_surface_status=dataset_surface_status or _default_dataset_surface_status,
        submit_program_action=submit_program_action,
    )
    return router


def create_evolution_router(
    *,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Optional[Callable[[Optional[str]], Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    bff_error: Optional[Callable[..., Exception]] = None,
    utc_now: Optional[Callable[[], str]] = None,
    page_slice: Optional[PageSlice] = None,
    snapshot_meta: Optional[SnapshotMeta] = None,
    dataset_surface_status: Optional[SurfaceStatus] = None,
    read_surface_meta: Optional[Callable[..., Dict[str, Any]]] = None,
    raise_if_read_surface_unavailable: Optional[Callable[..., None]] = None,
    meta_staleness: Optional[Callable[[], Any]] = None,
    submit_program_action: Optional[SubmitAction] = None,
    mutation_review_inputs: Optional[Callable[[str], Tuple[Any, Any, Any, Any]]] = None,
    mutation_review_projection: Optional[Callable[..., Dict[str, Any]]] = None,
    evolution_service: Optional[EvolutionService] = None,
) -> APIRouter:
    """Build the canonical Evolution domain router (OPGAP-BE-EVOLUTION-ROUTER-20260830).

    Registers all 13 evolution domain routes and the 7 evolution programs routes.
    """
    router = APIRouter()

    _get_store = (
        (lambda: (read_surface() if callable(read_surface) else read_surface))
        if read_surface is not None
        else (get_read_store or (lambda: getattr(evolution_service, "read_store", None)))
    )
    _extract_ident = extract_identity or _default_extract_identity
    _require_read = require_read_role or _default_require_read_role
    _require_op = require_operator_role or _default_require_operator_role
    _err = bff_error or _default_bff_error
    _utc_now = utc_now or _default_utc_now
    _page_slice_fn = page_slice or _default_page_slice
    _snap_meta = snapshot_meta or _default_snapshot_meta
    _surface_status_fn = dataset_surface_status or _default_dataset_surface_status
    _read_surface_meta_fn = read_surface_meta or _default_read_surface_meta
    _raise_unavailable = raise_if_read_surface_unavailable or _default_raise_if_read_surface_unavailable
    _meta_staleness_fn = meta_staleness or (lambda: None)

    def _get_service() -> EvolutionService:
        if evolution_service is not None:
            return evolution_service
        store = _get_store()
        return EvolutionService(store)

    # Register evolution programs routes
    _register_evolution_programs_routes(
        router,
        read_surface=read_surface,
        get_read_store=_get_store,
        extract_identity=_extract_ident,
        require_read_role=_require_read,
        require_operator_role=_require_op,
        bff_error=_err,
        utc_now=_utc_now,
        page_slice=_page_slice_fn,
        snapshot_meta=_snap_meta,
        dataset_surface_status=_surface_status_fn,
        submit_program_action=submit_program_action,
    )

    # --------------------------------------------------------------------------- #
    # 13 Evolution Domain Routes
    # --------------------------------------------------------------------------- #

    # 1. OODA Packets
    @router.get("/bff/evolution-programs/{program_id}/ooda")
    async def bff_list_evolution_program_ooda_packets(
        program_id: str,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: list OODA packets linked to an evolution program."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        if not ooda_packet_routes_enabled():
            raise _err(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "OODA packet read routes disabled",
                "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
                precondition_failed="ooda_packet_feature_flag",
                suggestion="Re-enable the OODA packet read surface before retrying this route.",
            )
        clean_id = program_id.strip()
        service = _get_service()
        packets = service.list_ooda_packets_for_evolution_program(clean_id)
        snapshot_at = _utc_now()
        return ooda_packet_list_payload(
            packets,
            surface_key="evolution_program_ooda_packets",
            page_token=page_token,
            page_size=page_size,
            related={"type": "EvolutionProgram", "id": clean_id},
            snapshot_at=snapshot_at,
            page_slice_fn=_page_slice_fn,
            read_surface_meta_fn=_read_surface_meta_fn,
        )

    # 2. Evolution Decisions List
    @router.get("/api/v1/evolution-decisions")
    async def list_evolution_decisions(
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """EV-01: Evolution Decision List with optional filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        service = _get_service()
        decisions = service.list_evolution_decisions(
            action_type=action_type,
            risk_level=risk_level,
            status=status,
        )
        items, next_page_token = _page_slice_fn(decisions, page_token, page_size)
        return {
            "items": items,
            "page_info": {
                "next_page_token": next_page_token,
            },
            "meta": _snap_meta(snapshot_at),
        }

    # 3. Evolution Decision Detail
    @router.get("/api/v1/evolution-decisions/{decision_id}")
    async def get_evolution_decision(
        decision_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """EV-02: Evolution Decision Detail."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        service = _get_service()
        payload = service.get_evolution_decision(decision_id.strip())
        if not payload:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Evolution decision not found",
                f"Evolution decision {decision_id} does not exist",
            )
        payload["meta"] = _snap_meta(_utc_now())
        return payload

    # 4. Freeze Orders
    @router.get("/api/v1/freeze-orders")
    async def list_freeze_orders(
        status: Optional[str] = None,
        scope: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """EV-03: Freeze Order List with optional filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        service = _get_service()
        orders = service.list_freeze_orders(status=status, scope=scope)
        return {
            "items": orders,
            "meta": _snap_meta(snapshot_at),
        }

    # 5. Rollbacks
    @router.get("/api/v1/rollbacks")
    async def list_rollbacks(
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """EV-04: Global Rollback List with optional filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        service = _get_service()
        rollbacks = service.list_rollbacks(
            runtime_id=runtime_id,
            action_type=action_type,
            time_range=time_range,
        )
        return {
            "items": rollbacks,
            "meta": _snap_meta(snapshot_at),
        }

    # 6. Lineage List
    @router.get("/api/v1/lineage")
    async def list_lineage(
        artifact_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """LN-01: Aggregated lineage list with optional artifact filter."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        surface = _surface_status_fn("lineage_edges", snapshot_at=snapshot_at)
        service = _get_service()
        items = service.list_lineage(artifact_id=artifact_id)
        if surface.get("status") == "unavailable":
            items = []
            next_page_token = None
        else:
            items, next_page_token = _page_slice_fn(items, page_token, page_size)
        return {
            "items": items,
            "page_info": {
                "next_page_token": next_page_token,
            },
            "meta": _snap_meta(snapshot_at),
        }

    # 7. Lineage Edge Detail
    @router.get("/api/v1/lineage/edges/{edge_id}")
    async def get_lineage_edge(
        edge_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """LN-02: Lineage Edge Detail."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        service = _get_service()
        edge = service.get_lineage_edge(edge_id.strip())
        if not edge:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Lineage edge not found",
                f"Lineage edge {edge_id} does not exist",
            )
        payload = dict(edge)
        payload["meta"] = _snap_meta(_utc_now())
        return payload

    # 8. Lineage Graph
    @router.get("/api/v1/lineage/graph")
    async def get_lineage_graph(
        root_type: Optional[str] = None,
        root_id: str = Query(...),
        depth: int = 3,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """LN-03: Lineage Graph from a root artifact with configurable depth."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        clamped_depth = max(1, min(depth, 10))
        snapshot_at = _utc_now()
        service = _get_service()
        nodes, edges = service.get_lineage_graph(
            root_type=root_type,
            root_id=root_id,
            depth=clamped_depth,
        )
        return {
            "nodes": nodes,
            "edges": [
                {
                    "id": edge.get("id"),
                    "from_artifact_id": edge.get("from_artifact_id"),
                    "to_artifact_id": edge.get("to_artifact_id"),
                    "relationship": edge.get("relationship"),
                }
                for edge in edges
            ],
            "meta": _snap_meta(snapshot_at),
        }

    # 9. Inspiration Graph
    @router.get("/api/v1/lineage/inspiration/{artifact_id}")
    async def get_inspiration_graph(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """EW-04: BFF-composed inspiration graph for a target artifact."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        service = _get_service()
        clean_id = artifact_id.strip()
        projection, artifact_exists = service.get_inspiration_graph(
            clean_id,
            snapshot_at=snapshot_at,
            utc_now=_utc_now,
        )
        if projection is None and not artifact_exists:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Artifact not found",
                f"Artifact {clean_id} does not exist",
            )
        store = _get_store()
        source = getattr(store, "dataset_source", lambda ds: "ok")("inspiration_graphs")
        return ew04_inspiration_payload(
            clean_id,
            projection,
            snapshot_at=snapshot_at,
            artifact_exists=artifact_exists or projection is not None,
            source=source,
        )

    # 10. Telemetry Events List
    @router.get("/api/v1/telemetry")
    async def list_telemetry(
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """TL-01: Telemetry Event List with optional filters."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        snapshot_at = _utc_now()
        service = _get_service()
        source, events = service.list_telemetry_events(
            pool_id=pool_id,
            artifact_id=artifact_id,
            time_range=time_range,
        )
        has_surface_data = source != "missing"
        surface = _surface_status_fn(
            "telemetry_events",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_surface_data,
            missing_message="Telemetry events are unavailable.",
        )
        if source == "telemetry_summary_fallback":
            surface["status"] = "degraded"
            surface["note"] = "Telemetry event store is empty; served synthesized telemetry summary fallback."
            surface["staleness"] = {
                "served_from": "telemetry_summary_fallback",
                "last_known_at": snapshot_at,
            }
        return {
            "data": events,
            "meta": _read_surface_meta_fn(
                "telemetry_events",
                "telemetry",
                snapshot_at=snapshot_at,
                total=len(events),
                surface=surface,
            ),
        }

    # 11. Telemetry Summary
    @router.get("/api/v1/telemetry/{runtime_id}/summary")
    async def get_telemetry_summary(
        runtime_id: str,
        time_range: Optional[str] = None,
        aggregate_by: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """TL-02: Telemetry Summary for a runtime."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        service = _get_service()
        clean_id = runtime_id.strip()
        summary = service.get_telemetry_summary(clean_id)
        if not summary:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Telemetry summary not found",
                f"No telemetry summary for runtime {clean_id}",
            )
        staleness = _meta_staleness_fn()
        meta = {"staleness": staleness} if staleness is not None else _snap_meta(_utc_now())
        return {
            "data": summary,
            "meta": meta,
        }

    # 12. Telemetry Performance
    @router.get("/api/v1/telemetry/{artifact_id}/performance")
    async def get_telemetry_performance(
        artifact_id: str,
        time_range: Optional[str] = None,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """TL-03: Telemetry Performance Chart for an artifact."""
        identity = _extract_ident(authorization)
        _require_read(identity)
        service = _get_service()
        clean_id = artifact_id.strip()
        performance = service.get_telemetry_performance(clean_id)
        if not performance:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Telemetry performance data not found",
                f"No performance data for artifact {clean_id}",
            )
        staleness = _meta_staleness_fn()
        meta = {"staleness": staleness} if staleness is not None else _snap_meta(_utc_now())
        return {
            "data": performance,
            "meta": meta,
        }

    # 13. Management Evolution Journal
    @router.get("/bff/management/evolution-journal")
    async def bff_management_evolution_journal(
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        persona: Optional[str] = None,
        mutation_review: Optional[str] = None,
        decision: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: compose Management Evolution Journal aggregate rows."""
        identity = _extract_ident(authorization)
        _require_read(identity)

        snapshot_at = _utc_now()
        read_store = _get_store()
        surfaces = evolution_journal_surfaces(
            snapshot_at=snapshot_at,
            dataset_surface_status_fn=_surface_status_fn,
        )

        decisions = list(getattr(read_store, "list_evolution_decisions", lambda: [])() or [])
        postmortems = list(getattr(read_store, "list_postmortems", lambda: [])() or [])
        freeze_orders = list(getattr(read_store, "list_freeze_orders", lambda: [])() or [])
        rollbacks = list(getattr(read_store, "list_all_rollbacks", lambda: [])() or [])

        items: List[Dict[str, Any]] = []
        for dec in decisions:
            dec_item = evolution_journal_decision_item(dec)
            if dec_item is not None:
                items.append(dec_item)
            # Mutation review item projection
            dec_id = _management_record_id(dec, "decision_id", "id", "evolution_decision_id")
            if dec_id and any(
                dec.get(field) is not None
                for field in (
                    "approval_decision_id",
                    "proposed_changes",
                    "risk_assessment",
                    "required_approvals",
                    "review_chain",
                    "threshold_snapshots",
                )
            ):
                if mutation_review_inputs is not None and mutation_review_projection is not None:
                    _, app_dec, l_inc, l_pm = mutation_review_inputs(dec_id)
                    proj = mutation_review_projection(
                        dec,
                        approval_decision=app_dec,
                        linked_incident=l_inc,
                        linked_postmortem=l_pm,
                        identity=identity,
                        snapshot_at=snapshot_at,
                    )
                else:
                    app_dec = getattr(read_store, "get_approval_decision_by_id", lambda aid: None)(dec.get("approval_decision_id"))
                    proj = {
                        "decision_id": dec_id,
                        "target_type": dec.get("target_type") or "artifact",
                        "target_id": dec.get("target_id") or dec.get("artifact_id"),
                        "target_version": dec.get("target_version") or dec.get("artifact_version"),
                        "action_type": dec.get("action_type") or "mutation",
                        "decision_state": dec.get("status") or dec.get("decision_state") or "pending",
                        "risk_level": dec.get("risk_level") or "low",
                        "created_at": dec.get("created_at") or snapshot_at,
                        "proposed_changes": dec.get("proposed_changes") or {},
                        "approval_decision": app_dec,
                    }
                for field in ("metadata", "provenance", "origin"):
                    if field in dec and field not in proj:
                        proj[field] = dec[field]

                mr_item = evolution_journal_base_item(
                    entry_type="mutation_review",
                    source_id=dec_id,
                    title=f"Mutation review: {dec_id}",
                    summary=str((proj.get("proposed_changes") or {}).get("summary") or dec.get("rationale") or ""),
                    status=str(proj.get("decision_state") or "unknown").lower(),
                    created_at=proj.get("created_at"),
                    updated_at=dec.get("updated_at"),
                    occurred_at=evolution_journal_timestamp(dec),
                    risk_level=proj.get("risk_level"),
                    action_type=proj.get("action_type"),
                    target=evolution_journal_target(
                        target_type=proj.get("target_type"),
                        target_id=proj.get("target_id"),
                        target_version=proj.get("target_version"),
                    ),
                    route=f"/management/evolution-journal?mutation_review={dec_id}",
                    bff_detail_path=f"/api/v1/operator/mutation-review/{dec_id}",
                )
                mr_item["mutationReview"] = json.loads(json.dumps(proj))
                mr_item["mutation_review"] = mr_item["mutationReview"]
                mr_item["record"] = mr_item["mutationReview"]
                items.append(mr_item)

        for pm in postmortems:
            pm_item = evolution_journal_postmortem_item(pm)
            if pm_item is not None:
                items.append(pm_item)

        for fo in freeze_orders:
            fo_item = evolution_journal_freeze_order_item(fo)
            if fo_item is not None:
                items.append(fo_item)

        for rb in rollbacks:
            rb_item = evolution_journal_rollback_item(rb)
            if rb_item is not None:
                items.append(rb_item)

        for item in items:
            origin_val = None
            for d in (
                item,
                item.get("record"),
                (item.get("record") or {}).get("metadata"),
                (item.get("record") or {}).get("provenance"),
                item.get("decision"),
                (item.get("decision") or {}).get("metadata"),
                item.get("mutation_review"),
                (item.get("mutation_review") or {}).get("metadata"),
                item.get("postmortem"),
                (item.get("postmortem") or {}).get("metadata"),
                item.get("freeze_order"),
                (item.get("freeze_order") or {}).get("metadata"),
                item.get("rollback"),
                (item.get("rollback") or {}).get("metadata"),
            ):
                if isinstance(d, dict) and d.get("origin"):
                    origin_val = str(d.get("origin")).strip().lower()
                    break

            if origin_val in ("seed", "live", "unknown"):
                item["origin"] = origin_val
            else:
                is_seed = (
                    evolution_journal_is_registered_seed_id(item.get("source_id"))
                    or evolution_journal_is_registered_seed_id(item.get("id"))
                )
                target_obj = item.get("target") if isinstance(item.get("target"), dict) else {}
                if not is_seed and evolution_journal_is_registered_seed_id(target_obj.get("id")):
                    is_seed = True
                if not is_seed:
                    for key in ("decision", "mutation_review", "mutationReview", "postmortem", "freeze_order", "freezeOrder", "rollback"):
                        inner = item.get(key)
                        if isinstance(inner, dict):
                            for field in (
                                "id", "decision_id", "source_id", "report_id",
                                "incident_id", "incident_ref", "linked_incident_id",
                                "target_id", "artifact_id", "runtime_id",
                                "runtime_binding_id", "persona_capital_binding_id",
                                "plan_id", "deployment_plan_id",
                            ):
                                if evolution_journal_is_registered_seed_id(inner.get(field)):
                                    is_seed = True
                                    break
                        if is_seed:
                            break
                item["origin"] = "seed" if is_seed else "unknown"

        items.sort(
            key=lambda it: (
                str(it.get("occurred_at") or ""),
                str(it.get("id") or ""),
            ),
            reverse=True,
        )

        filtered = evolution_journal_filter_items(
            items,
            source_type=source_type,
            status=status,
            action_type=action_type,
            risk_level=risk_level,
        )

        if persona:
            p_clean = persona.strip().lower()
            if p_clean:
                for dep_key, label in (
                    ("personas", "Persona"),
                    ("persona_bindings", "Persona-capital binding"),
                    ("runtime_bindings", "Runtime binding"),
                    ("incidents", "Incident"),
                ):
                    _raise_unavailable(surfaces[dep_key], label=label)

                persona_ids = {p_clean}
                runtime_ids = set()
                binding_ids = set()
                plan_ids = set()
                pool_ids = set()
                artifact_ids = set()
                incident_ids = set()

                personas = getattr(read_store, "list_personas", lambda **kw: [])(include_market_persona_defaults=True) or []
                for p in personas:
                    pid = str(p.get("persona_id") or p.get("id") or "").strip().lower()
                    if pid == p_clean:
                        for field, target_set in [
                            ("runtime_id", runtime_ids),
                            ("binding_id", binding_ids),
                            ("persona_capital_binding_id", binding_ids),
                            ("pool_id", pool_ids),
                            ("capital_pool_id", pool_ids),
                            ("plan_id", plan_ids),
                            ("artifact_id", artifact_ids),
                        ]:
                            val = str(p.get(field) or "").strip().lower()
                            if val:
                                target_set.add(val)

                bindings = list(getattr(read_store, "list_runtime_bindings", lambda **kw: [])(include_market_persona_defaults=True) or [])
                bindings += list(getattr(read_store, "list_bindings", lambda **kw: [])(include_market_persona_defaults=True) or [])
                incidents = getattr(read_store, "list_incidents", lambda: [])() or []

                changed = True
                while changed:
                    changed = False
                    for b in bindings:
                        b_pid = str(b.get("persona_id") or b.get("personaId") or "").strip().lower()
                        b_rid = str(b.get("runtime_id") or "").strip().lower()
                        b_bid = str(b.get("binding_id") or b.get("runtime_binding_id") or "").strip().lower()
                        b_pcbid = str(b.get("persona_capital_binding_id") or "").strip().lower()
                        b_plid = str(b.get("plan_id") or b.get("deployment_plan_id") or "").strip().lower()
                        b_pool = str(b.get("pool_id") or b.get("capital_pool_id") or "").strip().lower()

                        owned_match = (
                            (b_pid and b_pid == p_clean) or
                            (b_rid and b_rid in runtime_ids) or
                            (b_bid and b_bid in binding_ids) or
                            (b_pcbid and b_pcbid in binding_ids) or
                            (b_plid and b_plid in plan_ids)
                        )
                        pool_only_match = (not owned_match) and (b_pool and b_pool in pool_ids)
                        if not (owned_match or pool_only_match):
                            continue
                        if pool_only_match and b_pid and b_pid != p_clean:
                            continue
                        for val, target_set in (
                            (b_rid, runtime_ids),
                            (b_bid, binding_ids), (b_pcbid, binding_ids),
                            (b_plid, plan_ids), (b_pool, pool_ids),
                        ):
                            if val and val not in target_set:
                                target_set.add(val)
                                changed = True

                    for inc in incidents:
                        i_id = str(inc.get("incident_id") or inc.get("id") or "").strip().lower()
                        i_rid = str(inc.get("runtime_id") or "").strip().lower()
                        i_bid = str(inc.get("binding_id") or inc.get("persona_capital_binding_id") or "").strip().lower()
                        i_plid = str(inc.get("deployment_plan_id") or "").strip().lower()
                        i_pool = str(inc.get("capital_pool_id") or inc.get("pool_id") or "").strip().lower()

                        is_match = (
                            (i_id and i_id in incident_ids) or
                            (i_rid and i_rid in runtime_ids) or
                            (i_bid and i_bid in binding_ids) or
                            (i_plid and i_plid in plan_ids) or
                            (i_pool and i_pool in pool_ids)
                        )
                        if not is_match:
                            continue
                        for val, target_set in (
                            (i_id, incident_ids), (i_rid, runtime_ids),
                            (i_bid, binding_ids), (i_plid, plan_ids),
                            (i_pool, pool_ids),
                        ):
                            if val and val not in target_set:
                                target_set.add(val)
                                changed = True

                category_sets = {
                    "persona": persona_ids,
                    "runtime": runtime_ids,
                    "binding": binding_ids,
                    "plan": plan_ids,
                    "pool": pool_ids,
                    "artifact": artifact_ids,
                    "incident": incident_ids,
                }

                def _journal_item_matches_persona_lineage(it: Dict[str, Any]) -> bool:
                    target_obj = it.get("target") or {}
                    if isinstance(target_obj, dict):
                        category = _EVOLUTION_JOURNAL_TARGET_TYPE_CATEGORY.get(
                            str(target_obj.get("type") or "").strip().lower()
                        )
                        target_val = str(target_obj.get("id") or "").strip().lower()
                        if category and target_val and target_val in category_sets.get(category, set()):
                            return True
                    record_obj = it.get("record") or {}
                    if isinstance(record_obj, dict):
                        for field, cat in _EVOLUTION_JOURNAL_REFERENCE_FIELD_CATEGORY.items():
                            val = str(record_obj.get(field) or "").strip().lower()
                            if val and val in category_sets.get(cat, set()):
                                return True
                    return False

                filtered = [it for it in filtered if _journal_item_matches_persona_lineage(it)]

        if mutation_review:
            mr_clean = mutation_review.strip().lower()
            if mr_clean:
                filtered = [
                    it for it in filtered
                    if str(it.get("source_id") or "").lower() == mr_clean
                    and it.get("entry_type") == "mutation_review"
                ]

        if decision:
            dec_clean = decision.strip().lower()
            if dec_clean:
                filtered = [
                    it for it in filtered
                    if str(it.get("source_id") or "").lower() == dec_clean
                    and it.get("entry_type") == "evolution_decision"
                ]

        total = len(filtered)
        page_items, next_page_token = _page_slice_fn(filtered, page_token, page_size)
        meta = _snap_meta(snapshot_at)
        meta["surfaces"] = surfaces
        meta["composition_sources"] = [
            "evolution_decisions",
            "postmortems",
            "mutation_review",
            "rollbacks",
            "freeze_orders",
        ]
        summary = _management_prune_camel_aliases(evolution_journal_summary(filtered, len(page_items)))
        canonical_page_items = _management_prune_camel_aliases(page_items)
        return {
            "data": {
                "id": "management_evolution_journal",
                "items": canonical_page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": meta,
        }

    return router

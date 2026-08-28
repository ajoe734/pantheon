"""Evolution Programs canonical router (prepared, not wired into main.py).

Design unit: ACG-01-EVOEXP (docs/04/pantheon_architecture_cleanup_gap_2026-08-27).
Resolves the ACG-01-006 duplicate-route defect: main.py currently registers
this route family twice (a durable ``read_store``-backed block and a later
``_GOV_BFF`` in-memory-overlay block), then strips the durable registrations
at import time via ``_prefer_latest_bff_gap004_routes`` -- so the overlay is
what actually serves traffic today and writes never survive a restart.

``create_evolution_programs_router`` below is the single canonical owner:
every read and write goes through the durable ``read_store`` (no in-process
overlay dict), while the response envelope matches the overlay's *live*
contract (``items``/``page_info``/``meta`` for lists, flat dict for
create/patch) so existing consumers (execute-plans FE, the contract tests
in ../test_bff_evolution_experiment_jobs_events_contract.py and
../tests/test_evolution_programs_population_contract.py) keep working once
this is wired in.

This module is intentionally decoupled from main.py: every main.py-specific
concern (identity extraction, role checks, error construction, pagination,
snapshot metadata, and action-command dispatch through the command-store /
audit pipeline) is injected as a callable. A follow-up cutover task wires
this router in with ``app.include_router(create_evolution_programs_router(...))``
and deletes the two duplicate blocks plus the pruning hack; it does not need
to change any handler body here.

See CHARACTERIZATION.md in this package for the full envelope/filter/
pagination/validation contract this router preserves, and the specific
points where main.py's two existing blocks disagree (role requirement,
runs/candidates pagination) and which one this router follows.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query

from models import ErrorCode, ObjectType

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
# (entity_type_value, entity_id, action_id, identity, payload) -> receipt dict
SubmitAction = Callable[[str, str, str, Any, Dict[str, Any]], Dict[str, Any]]


def _default_page_slice(
    items: List[Dict[str, Any]], page_token: Optional[str], page_size: int
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Opaque numeric-offset pagination, matching main.py's ``_page_slice``."""
    try:
        start = int(page_token) if page_token else 0
    except (TypeError, ValueError):
        start = 0
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _default_snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    return {"snapshot_at": snapshot_at}


def _default_surface_status(dataset: str, *, snapshot_at: str, **_: Any) -> Dict[str, Any]:
    return {"status": "available", "dataset": dataset, "snapshot_at": snapshot_at}


def _filter_by_status_csv(records: List[Dict[str, Any]], status_csv: Optional[str]) -> List[Dict[str, Any]]:
    """Comma-separated, case-insensitive status filter (the overlay's live contract)."""
    if not status_csv:
        return records
    requested = {s.strip().lower() for s in status_csv.split(",") if s.strip()}
    return [r for r in records if str(r.get("status") or "").lower() in requested]


def create_evolution_programs_router(
    *,
    get_read_store: Callable[[], Any],
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    require_operator_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: PageSlice = _default_page_slice,
    snapshot_meta: SnapshotMeta = _default_snapshot_meta,
    dataset_surface_status: SurfaceStatus = _default_surface_status,
    submit_program_action: Optional[SubmitAction] = None,
) -> APIRouter:
    """Build the canonical Evolution Programs router.

    ``get_read_store`` is called per-request (not once at build time) so the
    router observes the same read_store instance main.py swaps in during
    tests via monkeypatching.
    """

    router = APIRouter()

    def _require_program(read_store: Any, program_id: str) -> Dict[str, Any]:
        program = read_store.get_evolution_program(program_id)
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
        read_store = get_read_store()
        snapshot_at = utc_now()
        programs = _filter_by_status_csv(read_store.list_evolution_programs(), status)
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
        read_store = get_read_store()
        snapshot_at = utc_now()
        program_id = str(
            payload.get("program_id")
            or payload.get("id")
            or f"evp-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        )
        return read_store.create_evolution_program(
            program_id=program_id,
            name=name,
            actor_id=identity.operator_id,
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
        read_store = get_read_store()
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
        read_store = get_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        updated = read_store.patch_evolution_program(
            clean_id,
            patch={k: payload[k] for k in ("name", "status", "params") if k in payload},
            actor_id=identity.operator_id,
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
        read_store = get_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        runs = read_store.list_evolution_program_runs(clean_id)
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
        read_store = get_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        snapshot_at = utc_now()
        candidates = read_store.list_evolution_program_candidates(clean_id)
        page_items, next_page_token = page_slice(candidates, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    @router.post("/bff/evolution-programs/{program_id}/actions/{action_id}", status_code=202)
    async def evolution_program_action(
        program_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        read_store = get_read_store()
        clean_id = program_id.strip()
        _require_program(read_store, clean_id)
        if submit_program_action is None:
            raise bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Evolution program actions are not wired",
                "submit_program_action was not injected into create_evolution_programs_router",
            )
        return submit_program_action(ObjectType.EVOLUTION_PROGRAM.value, clean_id, action_id, identity, payload)

    return router

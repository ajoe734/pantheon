"""Research Experiments canonical router (prepared, not wired into main.py).

Design unit: ACG-01-EVOEXP (docs/04/pantheon_architecture_cleanup_gap_2026-08-27).
Resolves the ACG-01-008 duplicate-route defect: main.py registers
``/bff/experiments*`` twice (a durable ``read_store``-backed block and a
later ``_GOV_BFF`` in-memory-overlay block, the latter pruning-preferred and
therefore live today) plus a third, always-live ``/bff/research-experiments``
surface that reads the same underlying overlay-merge helper under a
different URL prefix, plus two generic-alias stubs for
``POST``/``PATCH /bff/research-experiments*`` that do not touch any store at
all.

``create_research_experiments_router`` below is the single canonical owner
for the whole family. All reads and writes go through the durable
``research_experiments`` store that ``read_store.list_research_experiments``
/ ``get_research_experiment`` / ``create_experiment_bff`` /
``get_experiment_logs`` / ``get_experiment_metrics`` / ``get_experiment_artifacts``
already read and write -- these are the same durable functions
``list_experiments_bff`` and friends delegate to, so ``/bff/experiments*``
and ``/bff/research-experiments`` are unified onto one data source with no
in-process overlay. See CHARACTERIZATION.md for the exact envelope shapes,
including the two points (detail-record analysis-link enrichment, and the
``/bff/research-experiments/{id}`` surface-source heuristic) that this
prepared router intentionally approximates pending the cutover task -- both
are documented there rather than silently dropped.

Like ``evolution/router.py``, this module takes every main.py-specific
concern (identity, roles, error construction, pagination, snapshot
metadata, action-command dispatch) as an injected callable so it can be
built and unit-tested standalone before a follow-up task wires it in with
``app.include_router(...)`` and deletes the duplicate blocks, the generic
alias stubs, and the pruning hack for these paths.
"""
from __future__ import annotations

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


def create_research_experiments_router(
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
    submit_experiment_action: Optional[SubmitAction] = None,
) -> APIRouter:
    """Build the canonical Research Experiments router.

    ``get_read_store`` is called per-request so the router observes the same
    read_store instance main.py swaps in during tests via monkeypatching.
    """

    router = APIRouter()

    def _list_experiments(read_store: Any, status: Optional[str]) -> List[Dict[str, Any]]:
        items = _filter_by_status_csv(list(read_store.list_research_experiments()), status)
        return sorted(items, key=lambda e: str(e.get("created_at") or e.get("queued_at") or ""), reverse=True)

    def _get_experiment_with_analysis_links(read_store: Any, experiment_id: str) -> Optional[Dict[str, Any]]:
        experiment = read_store.get_experiment_bff(experiment_id)
        if not experiment:
            return None
        record = dict(experiment)
        analyses = read_store.list_research_analyses(experiment_id=experiment_id)
        analysis_links: List[Dict[str, Any]] = []
        for analysis in analyses:
            analysis_id = str(analysis.get("analysis_id") or analysis.get("id") or "").strip()
            if not analysis_id:
                continue
            analysis_links.append(
                {
                    "analysis_id": analysis_id,
                    "ticket_id": analysis.get("ticket_id"),
                    "status": analysis.get("status"),
                    "detail": f"/bff/research-analyses/{analysis_id}",
                    "api_detail": f"/api/v1/research/analysis/{analysis_id}",
                }
            )
        record["analysis_links"] = analysis_links
        record["analysis_ids"] = [link["analysis_id"] for link in analysis_links]
        return record

    def _require_experiment(read_store: Any, experiment_id: str) -> Dict[str, Any]:
        experiment = _get_experiment_with_analysis_links(read_store, experiment_id)
        if not experiment:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Experiment not found",
                f"Experiment {experiment_id} does not exist",
            )
        return experiment

    @router.get("/bff/experiments")
    async def list_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """List experiments. Filter: comma-separated ``status`` (case-insensitive)."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        snapshot_at = utc_now()
        items = _list_experiments(read_store, status)
        surface = dataset_surface_status("research_experiments", snapshot_at=snapshot_at, has_data=bool(items) or None)
        if surface.get("status") == "unavailable" and not items:
            page_items, next_page_token = [], None
        else:
            page_items, next_page_token = page_slice(items, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {"experiments": surface}
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    @router.post("/bff/experiments", status_code=201)
    async def create_experiment(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Create an experiment. ``name``/``experiment_name`` is required (422 otherwise)."""
        identity = extract_identity(authorization)
        require_operator_role(identity)
        name = str(payload.get("name") or payload.get("experiment_name") or "").strip()
        if not name:
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Experiment name must be a non-empty string",
                precondition_failed="name",
            )
        read_store = get_read_store()
        return read_store.create_experiment_bff(
            name=name,
            actor_id=identity.operator_id,
            created_at=utc_now(),
            params=payload,
        )

    @router.get("/bff/experiments/{experiment_id}")
    async def get_experiment(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        experiment = _require_experiment(read_store, experiment_id.strip())
        snapshot_at = utc_now()
        return {"data": experiment, "meta": snapshot_meta(snapshot_at)}

    @router.post("/bff/experiments/{experiment_id}/actions/{action_id}", status_code=202)
    async def experiment_action(
        experiment_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        read_store = get_read_store()
        clean_id = experiment_id.strip()
        _require_experiment(read_store, clean_id)
        if submit_experiment_action is None:
            raise bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Experiment actions are not wired",
                "submit_experiment_action was not injected into create_research_experiments_router",
            )
        return submit_experiment_action(ObjectType.EXPERIMENT.value, clean_id, action_id, identity, payload)

    @router.get("/bff/experiments/{experiment_id}/logs")
    async def get_experiment_logs(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        clean_id = experiment_id.strip()
        _require_experiment(read_store, clean_id)
        snapshot_at = utc_now()
        return {
            "experiment_id": clean_id,
            "logs": read_store.get_experiment_logs(clean_id),
            "meta": snapshot_meta(snapshot_at),
        }

    @router.get("/bff/experiments/{experiment_id}/metrics")
    async def get_experiment_metrics(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        clean_id = experiment_id.strip()
        _require_experiment(read_store, clean_id)
        snapshot_at = utc_now()
        return {
            "experiment_id": clean_id,
            "metrics": read_store.get_experiment_metrics(clean_id),
            "meta": snapshot_meta(snapshot_at),
        }

    @router.get("/bff/experiments/{experiment_id}/artifacts")
    async def get_experiment_artifacts(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        clean_id = experiment_id.strip()
        _require_experiment(read_store, clean_id)
        snapshot_at = utc_now()
        return {
            "experiment_id": clean_id,
            "artifacts": read_store.get_experiment_artifacts(clean_id),
            "meta": snapshot_meta(snapshot_at),
        }

    @router.get("/bff/research-experiments")
    async def list_research_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Unified research-experiments surface -- same data source as ``/bff/experiments``."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        snapshot_at = utc_now()
        all_items = _list_experiments(read_store, status)
        surface = dataset_surface_status(
            "research_experiments", snapshot_at=snapshot_at, has_data=bool(all_items) or None,
        )
        page_items, next_page_token = page_slice(all_items, page_token, page_size)
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {"research_experiments": surface}
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": len(all_items)},
            "meta": meta,
        }

    @router.get("/bff/research-experiments/{experiment_id}")
    async def get_research_experiment(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        experiment = _require_experiment(read_store, experiment_id.strip())
        snapshot_at = utc_now()
        return {"data": experiment, "meta": snapshot_meta(snapshot_at)}

    return router

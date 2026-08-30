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

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query

from models import ErrorCode, ObjectType

from .service import ResearchNotFoundError, ResearchRouterService, ResearchValidationError

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
# (entity_type_value, entity_id, action_id, resolved_key, identity, payload) -> receipt dict
SubmitAction = Callable[..., Any]

_RESEARCH_EXPERIMENT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}



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
        if hasattr(read_store, "list_experiments_bff"):
            return read_store.list_experiments_bff(status=status)
        raw = read_store.list_research_experiments() if hasattr(read_store, "list_research_experiments") else []
        return _filter_by_status_csv(raw, status)

    def _enrich_experiment_with_analyses(read_store: Any, item: Dict[str, Any], clean_id: str) -> Dict[str, Any]:
        enriched = dict(item)
        analyses = []
        if hasattr(read_store, "list_research_analyses"):
            try:
                analyses = read_store.list_research_analyses(experiment_id=clean_id) or []
            except Exception:
                analyses = []
        analysis_ids = []
        analysis_links = []
        for a in analyses:
            if isinstance(a, dict):
                a_id = str(a.get("analysis_id") or a.get("id") or "")
                if a_id:
                    analysis_ids.append(a_id)
                link_item = dict(a)
                if a_id and "detail" not in link_item:
                    link_item["detail"] = f"/bff/research-analyses/{a_id}"
                analysis_links.append(link_item)
        if "analysis_ids" not in enriched:
            enriched["analysis_ids"] = analysis_ids
        if "analysis_links" not in enriched:
            enriched["analysis_links"] = analysis_links
        return enriched

    def _require_experiment(read_store: Any, clean_id: str) -> Dict[str, Any]:
        if hasattr(read_store, "get_experiment_bff"):
            item = read_store.get_experiment_bff(clean_id)
        elif hasattr(read_store, "get_research_experiment"):
            item = read_store.get_research_experiment(clean_id)
        else:
            item = None
        if not item:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Experiment '{clean_id}' not found",
                f"No experiment exists with id '{clean_id}'",
            )
        return _enrich_experiment_with_analyses(read_store, item, clean_id)

    # ------------------------------------------------------------------
    # Canonical /bff/experiments endpoints
    # ------------------------------------------------------------------

    @router.get("/bff/experiments")
    async def list_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        read_store = get_read_store()
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
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        """Create an experiment. ``name``/``experiment_name`` is required (422 otherwise)."""
        identity = extract_identity(authorization)
        require_operator_role(identity)
        resolved_key = (idempotency_key or x_idempotency_key or "").strip()
        import hashlib
        req_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        if resolved_key:
            existing = _RESEARCH_EXPERIMENT_IDEMPOTENCY.get(resolved_key)
            if existing is not None:
                if existing.get("hash") != req_hash:
                    raise bff_error(
                        409,
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Idempotency key was already used with a different payload",
                        f"Key {resolved_key!r} is bound to a different request hash",
                        precondition_failed="idempotency_conflict",
                        suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                    )
                return existing["result"]

        name = str(payload.get("name") or payload.get("experiment_name") or "").strip()
        if not name:
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Experiment name must be a non-empty string",
                precondition_failed="name",
            )
        read_store = get_read_store()
        result = read_store.create_experiment_bff(
            name=name,
            actor_id=identity.operator_id,
            created_at=utc_now(),
            params=payload,
        )
        if resolved_key:
            _RESEARCH_EXPERIMENT_IDEMPOTENCY[resolved_key] = {"hash": req_hash, "result": result}
        return result

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
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_operator_role(identity)
        resolved_key = (idempotency_key or x_idempotency_key or "").strip()
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
        try:
            res = submit_experiment_action(ObjectType.EXPERIMENT.value, clean_id, action_id, resolved_key, identity, payload)
        except TypeError:
            res = submit_experiment_action(ObjectType.EXPERIMENT.value, clean_id, action_id, identity, payload)
        return res.model_dump(mode="json") if hasattr(res, "model_dump") else res

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
        if hasattr(read_store, "list_research_experiments"):
            try:
                all_items = read_store.list_research_experiments(status=status)
            except TypeError:
                raw = read_store.list_research_experiments()
                all_items = _filter_by_status_csv(raw, status)
        else:
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
        clean_id = experiment_id.strip()
        if hasattr(read_store, "get_research_experiment"):
            experiment = read_store.get_research_experiment(clean_id)
        else:
            experiment = _require_experiment(read_store, clean_id)
        if not experiment:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Experiment '{clean_id}' not found",
                f"No experiment exists with id '{clean_id}'",
            )
        experiment = _enrich_experiment_with_analyses(read_store, experiment, clean_id)
        snapshot_at = utc_now()
        surface = dataset_surface_status(
            "research_experiments", snapshot_at=snapshot_at, has_data=bool(experiment) or None,
        )
        meta = snapshot_meta(snapshot_at)
        meta["surfaces"] = {"research_experiment_detail": surface}
        return {"data": experiment, "meta": meta}

    return router


def create_research_router(
    *,
    get_read_store: Callable[[], Any],
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: PageSlice = _default_page_slice,
    snapshot_meta: SnapshotMeta = _default_snapshot_meta,
    dataset_surface_status: SurfaceStatus = _default_surface_status,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    submit_experiment_action: Optional[SubmitAction] = None,
    include_prepared_subrouters: bool = True,
) -> APIRouter:
    """Build the standalone Research domain router.

    The factory is intentionally not mounted by this preparation task.  A
    composition-root task can inject main.py's existing identity, metadata and
    action seams, then remove the generic aliases it supersedes without a
    circular import back into ``main``.
    """

    router = APIRouter(tags=["research"])
    service = ResearchRouterService(
        port_getter=get_read_store,
        utc_now=utc_now,
        snapshot_meta=snapshot_meta,
        page_slice=page_slice,
    )

    def _raise_service_error(exc: Exception) -> None:
        if isinstance(exc, ResearchNotFoundError):
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"{exc.label} not found",
                str(exc),
            ) from exc
        if isinstance(exc, ResearchValidationError):
            raise bff_error(
                exc.status_code,
                ErrorCode.VALIDATION_FAILED,
                str(exc),
                str(exc),
                precondition_failed=exc.field,
            ) from exc
        raise exc

    async def _list_analyses(
        ticket_id: Optional[str],
        experiment_id: Optional[str],
        status: Optional[str],
        date_range: Optional[str],
        page_token: Optional[str],
        page_size: int,
        authorization: Optional[str],
    ) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.list_analyses(
                ticket_id=ticket_id,
                experiment_id=experiment_id,
                status=status,
                date_range=date_range,
                page_token=page_token,
                page_size=page_size,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    async def _get_analysis(analysis_id: str, authorization: Optional[str]) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.get_analysis(analysis_id)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/analyses")
    async def list_research_analyses(
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_analyses(ticket_id, experiment_id, status, date_range, page_token, page_size, authorization)

    @router.get("/api/v1/research/analyses/{analysis_id}")
    async def get_research_analysis(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_analysis(analysis_id, authorization)

    @router.get("/api/v1/research/analysis")
    async def list_research_analysis_compat(
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_analyses(ticket_id, experiment_id, status, date_range, page_token, page_size, authorization)

    @router.get("/api/v1/research/analysis/{analysis_id}")
    async def get_research_analysis_compat(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_analysis(analysis_id, authorization)

    async def _list_artifacts(
        artifact_type: Optional[str],
        status: Optional[str],
        tags: Optional[str],
        author: Optional[str],
        date_range: Optional[str],
        page_token: Optional[str],
        page_size: int,
        authorization: Optional[str],
    ) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.list_artifacts(
                artifact_type=artifact_type,
                status=status,
                tags=tags,
                author=author,
                date_range=date_range,
                page_token=page_token,
                page_size=page_size,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    async def _get_artifact(artifact_id: str, authorization: Optional[str]) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.get_artifact(artifact_id)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/artifacts")
    async def list_research_artifacts(
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[str] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _list_artifacts(artifact_type, status, tags, author, date_range, page_token, page_size, authorization)

    @router.get("/api/v1/research/artifacts/compare")
    async def compare_research_artifacts(
        artifact_ids: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.compare_artifacts(artifact_ids)
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    @router.get("/api/v1/research/artifacts/{artifact_id}")
    async def get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_artifact(artifact_id, authorization)

    @router.get("/bff/research-analyses")
    async def bff_list_research_analyses(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        result = await _list_analyses(None, None, None, None, page_token, page_size, authorization)
        return {
            "data": result["data"],
            "items": result["data"],
            "page_info": result["page_info"],
            "meta": result["meta"],
        }

    @router.get("/bff/research-analyses/{analysis_id}")
    async def bff_get_research_analysis(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        payload = await _get_analysis(analysis_id, authorization)
        meta = payload.pop("meta")
        return {"data": payload, "meta": meta}

    @router.get("/bff/artifacts")
    async def bff_list_research_artifacts(
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        result = await _list_artifacts(None, None, None, None, None, page_token, page_size, authorization)
        return {
            "data": result["artifacts"],
            "items": result["artifacts"],
            "page_info": {"next_page_token": result["next_page_token"], "total": result["total_count"]},
            "meta": result["meta"],
        }

    @router.get("/bff/artifacts/{artifact_id}")
    async def bff_get_research_artifact(
        artifact_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        payload = await _get_artifact(artifact_id, authorization)
        meta = payload.pop("meta")
        return {"data": payload, "meta": meta}

    if include_prepared_subrouters:
        from console_gap.knowledge import create_knowledge_router

        router.include_router(
            create_knowledge_router(
                extract_identity=extract_identity,
                require_read_role=require_read_role,
                read_store_getter=get_read_store,
                utc_now=utc_now,
                dataset_surface_status=dataset_surface_status,
            )
        )
        if require_operator_role is not None:
            router.include_router(
                create_research_experiments_router(
                    get_read_store=get_read_store,
                    extract_identity=extract_identity,
                    require_read_role=require_read_role,
                    require_operator_role=require_operator_role,
                    bff_error=bff_error,
                    utc_now=utc_now,
                    page_slice=page_slice,
                    snapshot_meta=snapshot_meta,
                    dataset_surface_status=dataset_surface_status,
                    submit_experiment_action=submit_experiment_action,
                )
            )

    return router

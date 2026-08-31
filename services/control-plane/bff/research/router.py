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
import inspect
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query, Request

from models import ErrorCode, ObjectType

from .service import ResearchNotFoundError, ResearchRouterService, ResearchValidationError

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
# (entity_type_value, entity_id, action_id, resolved_key, identity, payload) -> receipt dict
SubmitAction = Callable[..., Any]

_RESEARCH_EXPERIMENT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


# This is the task's source-of-truth migration inventory.  It deliberately
# records the legacy decorators rather than the implementation functions: the
# latter were split in main.py by generic aliases, while the former are the
# public contract the composition-root cutover must preserve.  The typed
# analysis/artifact routes below replace the final generic aliases without
# reintroducing their in-memory fallback behaviour.
RESEARCH_ROUTE_INVENTORY = (
    ("GET", "/api/v1/workbench/knowledge"),
    ("GET", "/api/v1/operator/research/oss-activation-ready"),
    ("GET", "/api/v1/operator/research/oss-preactivation"),
    ("GET", "/api/v1/operator/source/ops"),
    ("GET", "/api/v1/operator/search/ops"),
    ("POST", "/api/v1/operator/source/dlq/replay"),
    ("POST", "/api/v1/operator/source/frontier/{frontier_id}/replay"),
    ("POST", "/api/v1/operator/search/index/refresh"),
    ("POST", "/api/v1/operator/search/index/materialize"),
    ("POST", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets"),
    ("GET", "/api/v1/research/tickets/{ticket_id}"),
    ("PATCH", "/api/v1/research/tickets/{ticket_id}"),
    ("GET", "/api/v1/research/search"),
    ("GET", "/api/v1/research/source-connectors"),
    ("GET", "/api/v1/research/source-change-proposals"),
    ("GET", "/api/v1/research/analysis"),
    ("GET", "/api/v1/research/analysis/{analysis_id}"),
    ("POST", "/api/v1/experiments/launch"),
    ("GET", "/api/v1/experiments"),
    ("GET", "/api/v1/experiments/{experiment_id}"),
    ("POST", "/api/v1/experiments/{experiment_id}/cancel"),
    ("GET", "/api/v1/artifacts"),
    ("GET", "/api/v1/artifacts/compare"),
    ("GET", "/api/v1/artifacts/{artifact_id}"),
    ("POST", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes"),
    ("GET", "/api/v1/knowledge/notes/{note_id}"),
    ("GET", "/api/v1/knowledge/evidence"),
    ("GET", "/api/v1/knowledge/evidence/{ref_id}"),
    ("GET", "/api/v1/knowledge/insights"),
    ("GET", "/api/v1/knowledge/insights/{insight_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/versions"),
    ("GET", "/api/v1/knowledge/strategy-specs/{strategy_id}/compare"),
    ("GET", "/api/v1/knowledge/memory"),
    ("GET", "/api/v1/knowledge/memory/{entry_id}"),
    ("GET", "/bff/synthesis/conflict-logs"),
    ("GET", "/bff/synthesis/conflict-logs/{log_id}"),
    ("GET", "/bff/search"),
    ("GET", "/bff/artifacts"),
    ("GET", "/bff/artifacts/{artifact_id}"),
    ("GET", "/bff/research-analyses"),
    ("GET", "/bff/research-analyses/{analysis_id}"),
    ("PATCH", "/bff/artifacts/{artifact_id}"),
    ("POST", "/bff/artifacts"),
)



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
    build_knowledge_workbench: Optional[Callable[[], Any]] = None,
    build_research_oss_readiness: Optional[Callable[..., Any]] = None,
    submit_source_search_command: Optional[Callable[..., Any]] = None,
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
            error_code = getattr(ErrorCode, exc.error_code, ErrorCode.VALIDATION_FAILED)
            raise bff_error(
                exc.status_code,
                error_code,
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
        detail_path: str = "/api/v1/research/analyses",
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
                detail_path=detail_path,
            )
        except (ResearchNotFoundError, ResearchValidationError) as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable")

    async def _get_analysis(
        analysis_id: str,
        authorization: Optional[str],
        *,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        require_read_role(extract_identity(authorization))
        try:
            return service.get_analysis(analysis_id, detail_path=detail_path)
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
        return await _list_analyses(
            ticket_id,
            experiment_id,
            status,
            date_range,
            page_token,
            page_size,
            authorization,
            detail_path="/api/v1/research/analysis",
        )

    @router.get("/api/v1/research/analysis/{analysis_id}")
    async def get_research_analysis_compat(
        analysis_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        return await _get_analysis(
            analysis_id,
            authorization,
            detail_path="/api/v1/research/analysis",
        )

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

    # ------------------------------------------------------------------
    # Legacy Research route inventory
    # ------------------------------------------------------------------
    #
    # The main.py implementations depended on process-global helpers.  These
    # adapters preserve their public paths and durable source-port calls while
    # making the two genuinely composition-owned concerns explicit injections:
    # workbench/OSS projections and source-search commands.  A later assembly
    # task can therefore mount this router without a reverse import of main.

    def _identity(request: Request, *, operator: bool = False) -> Any:
        identity = extract_identity(request.headers.get("authorization"))
        if operator:
            if require_operator_role is None:
                raise bff_error(
                    501,
                    ErrorCode.NOT_IMPLEMENTED,
                    "Operator route is not wired",
                    "create_research_router needs require_operator_role for this route",
                )
            require_operator_role(identity)
        else:
            require_read_role(identity)
        return identity

    def _query(request: Request, name: str, default: Optional[str] = None) -> Optional[str]:
        value = request.query_params.get(name)
        return default if value is None else value

    async def _body(request: Request) -> Dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _page(records: List[Dict[str, Any]], request: Request, default_size: int = 20) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            size = int(_query(request, "page_size", str(default_size)) or default_size)
        except ValueError:
            size = default_size
        size = max(1, min(size, 200))
        return page_slice(records, _query(request, "page_token"), size)

    def _meta(snapshot_at: str, surface_name: str, dataset: str, has_data: bool) -> Dict[str, Any]:
        port = get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface = dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        result = snapshot_meta(snapshot_at)
        result["surfaces"] = {surface_name: surface}
        return result

    def _port_method(port: Any, name: str) -> Callable[..., Any]:
        method = getattr(port, name, None)
        if not callable(method):
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research source is unavailable",
                f"ResearchKnowledgeSourcePort does not provide {name}",
            )
        return method

    def _call_port(port: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        method = _port_method(port, name)
        try:
            return method(*args, **kwargs)
        except TypeError:
            # A few older source-port adapters intentionally expose a narrower
            # keyword surface.  Falling back to the positional minimum keeps
            # the prepared router compatible without inventing a local store.
            return method(*args)

    def _required_text(payload: Dict[str, Any], field: str) -> str:
        value = str(payload.get(field) or "").strip()
        if not value:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Missing required field: {field}",
                f"{field} must be a non-empty string",
                precondition_failed=field,
            )
        return value

    _TICKET_PRIORITIES = {"low", "normal", "high", "critical"}
    _TICKET_STATUSES = {"open", "in_progress", "closed", "archived"}
    _TICKET_STATUS_TRANSITIONS = {
        "open": {"in_progress", "closed"},
        "in_progress": {"closed"},
        "closed": {"archived"},
        "archived": set(),
    }
    _EXPERIMENT_STATUSES = {"queued", "running", "completed", "failed", "canceled"}
    _EXPERIMENT_EXECUTION_MODES = {"paper", "backtest", "simulation"}
    _EXPERIMENT_PRIORITIES = {"normal", "high"}
    _KW02_ATTACHMENT_TYPES = {"research_ticket", "persona", "strategy_spec", "free_standing"}
    _KW02_ATTACHMENT_ID_PATTERNS = {
        "research_ticket": re.compile(r"^tkt-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
        "persona": re.compile(r"^persona-[A-Za-z0-9][A-Za-z0-9_-]*$"),
        "strategy_spec": re.compile(r"^strat-[A-Za-z0-9-]+$"),
    }
    _KW02_MEMORY_ANCHOR_PATTERN = re.compile(
        r"^mem-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )

    def _kw02_bad_request(message: str, reason: str, field: str) -> None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _kw02_optional_title(payload: Dict[str, Any]) -> Optional[str]:
        title = payload.get("title")
        if title in (None, ""):
            return None
        normalized = str(title).strip()
        if not normalized:
            return None
        if len(normalized) > 256:
            _kw02_bad_request(
                "Invalid title",
                "title must be 256 characters or fewer",
                "title",
            )
        return normalized

    def _kw02_required_body(payload: Dict[str, Any]) -> str:
        body = payload.get("body")
        if body is None or not str(body).strip():
            _kw02_bad_request(
                "Missing required field: body",
                "body must be a non-empty string",
                "body",
            )
        return str(body).strip()

    def _kw02_validate_string_list(value: Any, field: str) -> List[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            _kw02_bad_request(
                f"Invalid {field}",
                f"{field} must be an array of strings",
                field,
            )
        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                _kw02_bad_request(
                    f"Invalid {field} entry",
                    f"{field} entries must be non-empty strings",
                    field,
                )
            normalized.append(text)
        return normalized

    def _kw02_validate_attachment_type(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW02_ATTACHMENT_TYPES:
            _kw02_bad_request(
                "Invalid attachment_type",
                f"attachment_type must be one of {sorted(_KW02_ATTACHMENT_TYPES)}",
                "attachment_type",
            )
        return normalized

    def _kw02_validate_attachment_ref(attachment_type: str, value: Any) -> Optional[str]:
        if attachment_type == "free_standing":
            if value not in (None, ""):
                _kw02_bad_request(
                    "Invalid attachment_ref",
                    "attachment_ref must be null when attachment_type is free_standing",
                    "attachment_ref",
                )
            return None

        ref = str(value or "").strip()
        if not ref:
            _kw02_bad_request(
                "Missing attachment_ref",
                "attachment_ref is required unless attachment_type is free_standing",
                "attachment_ref",
            )
        pattern = _KW02_ATTACHMENT_ID_PATTERNS.get(attachment_type)
        if pattern is not None and not pattern.match(ref):
            _kw02_bad_request(
                "Invalid attachment_ref",
                f"attachment_ref does not match the identity format for {attachment_type}",
                "attachment_ref",
            )
        return ref

    def _kw02_validate_memory_anchors(port: Any, anchor_ids: List[str]) -> List[str]:
        validated: List[str] = []
        for entry_id in anchor_ids:
            if not _KW02_MEMORY_ANCHOR_PATTERN.match(entry_id):
                _kw02_bad_request(
                    "Invalid linked_memory_anchors entry",
                    "linked_memory_anchors items must use the mem-{UUID} format",
                    "linked_memory_anchors",
                )
            if _call_port(port, "get_institutional_memory_entry", entry_id) is None:
                _kw02_bad_request(
                    "Unknown linked_memory_anchors entry",
                    f"linked_memory_anchors entry {entry_id} does not resolve to a known institutional memory entry",
                    "linked_memory_anchors",
                )
            validated.append(entry_id)
        return validated

    def _kw02_attachment_exists(port: Any, attachment_type: str, attachment_ref: Optional[str]) -> bool:
        if attachment_type == "free_standing":
            return True
        method = {
            "research_ticket": "get_research_ticket",
            "persona": "get_persona",
            "strategy_spec": "get_strategy_spec",
        }[attachment_type]
        return _call_port(port, method, attachment_ref) is not None

    def _kw02_operator_display_name(operator_id: str) -> str:
        if operator_id == "op-001":
            return "Alice Chen"
        token = str(operator_id or "").strip()
        if not token:
            return "Operator"
        if token.startswith("op-"):
            return f"Operator {token}"
        return " ".join(part.capitalize() for part in re.split(r"[-_]+", token) if part)

    def _validate_choice(value: Any, *, field: str, label: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Invalid {label}",
                f"{field} must be one of {sorted(allowed)}",
                precondition_failed=field,
            )
        return normalized

    def _validate_ticket_priority(value: Any) -> str:
        return _validate_choice(
            value,
            field="priority",
            label="research ticket priority",
            allowed=_TICKET_PRIORITIES,
        )

    def _validate_ticket_status(value: Any) -> str:
        return _validate_choice(
            value,
            field="status",
            label="research ticket status",
            allowed=_TICKET_STATUSES,
        )

    def _validate_experiment_status(value: Any) -> str:
        return _validate_choice(
            value,
            field="status",
            label="experiment status",
            allowed=_EXPERIMENT_STATUSES,
        )

    def _required_dict(payload: Dict[str, Any], field: str) -> Dict[str, Any]:
        value = payload.get(field)
        if not isinstance(value, dict):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"Missing or invalid field: {field}",
                f"{field} must be an object",
                precondition_failed=field,
            )
        return value

    def _validate_ticket_patch(ticket: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        allowed_fields = {"status", "title", "description", "priority", "owner"}
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid research ticket patch payload",
                f"Unsupported patch fields: {unknown_fields}",
                precondition_failed="payload_shape",
            )

        patch: Dict[str, Any] = {}
        editable = bool((ticket.get("allowedActions") or {}).get("canEdit"))
        for field in ("title", "description", "owner"):
            if field not in payload:
                continue
            value = _required_text(payload, field)
            if not editable:
                raise bff_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    f"{field} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch[field] = value

        if "priority" in payload:
            if not editable:
                raise bff_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    "priority cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch["priority"] = _validate_ticket_priority(payload["priority"])

        if "status" in payload:
            current_status = str(ticket.get("status") or "").strip().lower()
            next_status = _validate_ticket_status(payload["status"])
            if next_status != current_status:
                actions = ticket.get("allowedActions") or {}
                if next_status == "closed" and not actions.get("canClose"):
                    raise bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be closed in its current state",
                        "allowedActions.canClose is false for this ticket.",
                        precondition_failed="allowedActions.canClose",
                    )
                if next_status == "archived" and not actions.get("canArchive"):
                    raise bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be archived in its current state",
                        "allowedActions.canArchive is false for this ticket.",
                        precondition_failed="allowedActions.canArchive",
                    )
                if next_status not in _TICKET_STATUS_TRANSITIONS.get(current_status, set()):
                    raise bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Invalid research ticket lifecycle transition",
                        f"Cannot transition research ticket from {current_status} to {next_status}.",
                        precondition_failed="status_transition",
                    )
            patch["status"] = next_status

        if not patch:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Empty research ticket patch payload",
                "At least one accepted patch field is required.",
                precondition_failed="payload_shape",
            )
        return patch

    def _validate_experiment_launch(payload: Dict[str, Any]) -> Dict[str, Any]:
        run_config = _required_dict(payload, "run_config")
        time_range = _required_dict(run_config, "time_range")
        validated_run_config = {
            "dataset_ref": _required_text(run_config, "dataset_ref"),
            "time_range": {
                "start_at": _required_text(time_range, "start_at"),
                "end_at": _required_text(time_range, "end_at"),
            },
            "execution_mode": _validate_choice(
                run_config.get("execution_mode"),
                field="execution_mode",
                label="execution_mode",
                allowed=_EXPERIMENT_EXECUTION_MODES,
            ),
            "priority": _validate_choice(
                run_config.get("priority", "normal"),
                field="priority",
                label="priority",
                allowed=_EXPERIMENT_PRIORITIES,
            ),
            "requested_by": _required_text(run_config, "requested_by"),
        }
        launch_context_raw = payload.get("launch_context") or {}
        if not isinstance(launch_context_raw, dict):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid launch_context",
                "launch_context must be an object when provided",
                precondition_failed="launch_context",
            )
        analysis_refs = launch_context_raw.get("analysis_refs")
        if analysis_refs is not None and not isinstance(analysis_refs, list):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid launch_context.analysis_refs",
                "analysis_refs must be null or an array of strings",
                precondition_failed="launch_context.analysis_refs",
            )
        return {
            "ticket_id": _required_text(payload, "ticket_id"),
            "experiment_name": _required_text(payload, "experiment_name"),
            "strategy_selector": _required_dict(payload, "strategy_selector"),
            "parameter_set": _required_dict(payload, "parameter_set"),
            "run_config": validated_run_config,
            "launch_context": {"analysis_refs": list(analysis_refs) if analysis_refs is not None else None},
        }

    def _not_found(label: str, identifier: str) -> None:
        raise bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{label} not found",
            f"{label} {identifier} does not exist",
        )

    async def _inventory_route(request: Request) -> Dict[str, Any]:
        name = str(request.scope["route"].name)
        operator = name.startswith("command_") or name in {"bff_patch_artifact", "bff_create_artifact"}
        identity = _identity(request, operator=operator)
        port = get_read_store()
        snapshot_at = utc_now()
        params = request.path_params

        if name == "knowledge_workbench":
            if build_knowledge_workbench is not None:
                result = build_knowledge_workbench()
                return await result if inspect.isawaitable(result) else result
            records = list(_call_port(port, "list_research_notes") or [])
            return {"data": records, "meta": _meta(snapshot_at, "knowledge_workbench", "research_notes", bool(records))}

        if name in {"oss_activation_ready", "oss_preactivation"}:
            if build_research_oss_readiness is not None:
                result = build_research_oss_readiness(
                    activation_ready=name == "oss_activation_ready",
                    activity_limit=int(_query(request, "activity_limit", "20") or 20),
                )
                return await result if inspect.isawaitable(result) else result
            return {
                "data": {"activation_ready": False, "reason": "research OSS readiness projection is not wired"},
                "meta": _meta(snapshot_at, "research_oss", "research_experiments", False),
            }

        if name == "source_ops":
            data = _call_port(
                port,
                "get_source_ops_snapshot",
                crawl_run_limit=int(_query(request, "crawl_run_limit", "50") or 50),
                dlq_status=_query(request, "dlq_status"),
                frontier_status=_query(request, "frontier_status"),
                audit_limit=int(_query(request, "audit_limit", "20") or 20),
            )
            return {"data": data, "meta": _meta(snapshot_at, "source_ops", "source_ops", bool(data))}

        if name == "search_ops":
            data = _call_port(port, "get_search_ops_snapshot", pipeline_run_limit=int(_query(request, "pipeline_run_limit", "50") or 50))
            return {"data": data, "meta": _meta(snapshot_at, "search_ops", "search_ops", bool(data))}

        if name.startswith("command_"):
            if submit_source_search_command is None:
                raise bff_error(
                    501,
                    ErrorCode.NOT_IMPLEMENTED,
                    "Source-search command route is not wired",
                    "The composition root must inject submit_source_search_command",
                )
            payload = await _body(request)
            result = submit_source_search_command(
                name.removeprefix("command_"),
                payload,
                identity,
                request.headers.get("x-idempotency-key"),
                params,
            )
            result = await result if inspect.isawaitable(result) else result
            return result

        if name == "create_ticket":
            payload = await _body(request)
            ticket = _call_port(
                port,
                "create_research_ticket",
                title=_required_text(payload, "title"),
                description=_required_text(payload, "description"),
                priority=_validate_ticket_priority(payload.get("priority")),
                owner=_required_text(payload, "owner"),
                actor_id=str(getattr(identity, "operator_id", "")),
                created_at=snapshot_at,
            )
            return {key: ticket.get(key) for key in ("ticket_id", "status", "created_at", "allowedActions")}

        if name == "list_tickets":
            statuses = [item.strip() for item in str(_query(request, "status", "") or "").split(",") if item.strip()] or None
            if statuses:
                statuses = [_validate_ticket_status(status) for status in statuses]
            records = list(_call_port(port, "list_research_tickets", statuses=statuses, owner=_query(request, "owner"), include_fixture_pack=False) or [])
            items, next_token = _page(records, request)
            return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": _meta(snapshot_at, "ticket_list", "research_tickets", bool(records))}

        if name in {"get_ticket", "patch_ticket"}:
            ticket_id = str(params["ticket_id"])
            ticket = _call_port(port, "get_research_ticket", ticket_id)
            if not ticket:
                _not_found("Research ticket", ticket_id)
            if name == "patch_ticket":
                patch = _validate_ticket_patch(ticket, await _body(request))
                updated = _call_port(port, "patch_research_ticket", ticket_id, patch=patch, actor_id=str(getattr(identity, "operator_id", "")), updated_at=snapshot_at)
                if not updated:
                    raise bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Research ticket store unavailable", "Research ticket update store is unavailable")
                return {key: updated.get(key) for key in ("ticket_id", "status", "updated_at", "allowedActions")}
            payload = dict(ticket)
            payload["links"] = {"self": f"/api/v1/research/tickets/{ticket_id}", "workbench_detail": f"/research/tickets/{ticket_id}"}
            payload["meta"] = _meta(snapshot_at, "ticket_detail", "research_tickets", True)
            return payload

        if name == "research_search":
            query = _required_text({"q": _query(request, "q")}, "q")
            index = _call_port(port, "get_research_search_index")
            if not index:
                raise bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Search results are unavailable", "SEARCH_RESULTS_UNAVAILABLE")
            records = list(_call_port(port, "list_research_search_results", query=query, match_type=_query(request, "match_type", "all"), status=_query(request, "status"), date_range=_query(request, "date_range")) or [])
            items, next_token = _page(records, request, 25)
            meta = _meta(snapshot_at, "search_results", "research_search", bool(records))
            meta["index_adapter"] = index
            return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": meta}

        if name == "source_connectors":
            registry = _call_port(port, "get_source_connector_registry") or {}
            meta = _meta(snapshot_at, "source_connector_registry", "source_connectors", bool(registry.get("connectors")))
            meta.update({"source": registry.get("source", "missing"), "provider_examples": list(registry.get("provider_examples") or []), "policy_registry": registry.get("policy_registry")})
            return {"data": list(registry.get("connectors") or []), "meta": meta}

        if name == "source_change_proposals":
            result = _call_port(port, "get_source_change_proposals", status=_query(request, "status"), proposal_type=_query(request, "proposal_type"), source_kind=_query(request, "source_kind")) or {}
            records = list(result.get("proposals") or [])
            source = str(result.get("source") or "missing")
            meta = snapshot_meta(snapshot_at)
            meta["surfaces"] = {
                "source_change_proposals": "ok" if source == "service_client" else "unavailable"
            }
            meta["source"] = source
            return {"data": records, "meta": meta}

        if name == "launch_experiment":
            payload = _validate_experiment_launch(await _body(request))
            experiment = _call_port(
                port,
                "create_research_experiment",
                ticket_id=payload["ticket_id"],
                experiment_name=payload["experiment_name"],
                strategy_selector=payload["strategy_selector"],
                parameter_set=payload["parameter_set"],
                run_config=payload["run_config"],
                launch_context=payload["launch_context"],
            )
            experiment_id = str(experiment.get("experiment_id") or "")
            return {"experiment_id": experiment_id, "ticket_id": experiment.get("ticket_id"), "status": experiment.get("status"), "queued_at": experiment.get("queued_at"), "allowedActions": {"canCancel": True}, "links": {"self": f"/api/v1/experiments/{experiment_id}", "workbench_detail": f"/research/experiments/{experiment_id}"}}

        if name in {"list_experiments_api", "get_experiment_api", "cancel_experiment_api"}:
            experiment_id = str(params.get("experiment_id") or "")
            if name == "list_experiments_api":
                raw_status = _query(request, "status")
                status = _validate_experiment_status(raw_status) if raw_status is not None else None
                records = list(_call_port(port, "list_research_experiments", ticket_id=_query(request, "ticket_id"), status=status) or [])
                items, next_token = _page(records, request)
                return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": _meta(snapshot_at, "experiment_history", "research_experiments", bool(records))}
            experiment = _call_port(port, "get_research_experiment", experiment_id)
            if not experiment:
                _not_found("Experiment", experiment_id)
            if name == "cancel_experiment_api":
                _required_text(await _body(request), "reason")
                if str(experiment.get("status") or "") not in {"queued", "running"}:
                    raise bff_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Experiment cannot be canceled",
                        f"Experiment {experiment_id} is in terminal state '{experiment.get('status')}' and cannot be canceled",
                    )
                canceled = _call_port(port, "cancel_research_experiment", experiment_id, completed_at=snapshot_at)
                if not canceled:
                    raise bff_error(409, ErrorCode.OPERATION_NOT_ALLOWED, "Experiment cancel rejected", "Experiment could not be canceled")
                return {"experiment_id": experiment_id, "status": canceled.get("status"), "completed_at": canceled.get("completed_at"), "allowedActions": {"canCancel": False}}
            payload = dict(experiment)
            ticket_id = str(payload.get("ticket_id") or "")
            payload["links"] = {"self": f"/api/v1/experiments/{experiment_id}", "workbench_detail": f"/research/experiments/{experiment_id}", "linked_ticket_detail": f"/research/tickets/{ticket_id}"}
            payload["meta"] = _meta(snapshot_at, "experiment_status", "research_experiments", True)
            return payload

        if name in {"list_artifacts_api", "compare_artifacts_api", "get_artifact_api"}:
            artifact_id = str(params.get("artifact_id") or "")
            if name == "list_artifacts_api":
                records = list(_call_port(port, "list_research_artifacts", status=_query(request, "status")) or [])
                items, next_token = _page(records, request)
                return {"artifacts": items, "next_page_token": next_token, "total_count": len(records), "meta": _meta(snapshot_at, "artifact_list", "research_artifacts", bool(records))}
            if name == "compare_artifacts_api":
                return service.compare_artifacts(_required_text({"artifact_ids": _query(request, "artifact_ids")}, "artifact_ids"))
            artifact = _call_port(port, "get_research_artifact", artifact_id)
            if not artifact:
                _not_found("Artifact", artifact_id)
            payload = dict(artifact)
            payload["meta"] = _meta(snapshot_at, "artifact_detail", "research_artifacts", True)
            return payload

        knowledge_operations = {
            "list_notes": ("list_research_notes", "notes"),
            "list_evidence": ("list_evidence_refs", "evidence_refs"),
            "list_insights": ("list_insight_cards", "insight_cards"),
            "list_strategy_specs": ("list_strategy_specs", "strategy_specs"),
            "list_memory": ("list_institutional_memory_entries", "institutional_memory_entries"),
        }
        if name == "create_note":
            body = await _body(request)
            if "owner_ref" in body:
                _kw02_bad_request(
                    "Invalid owner_ref",
                    "owner_ref is server-assigned and must not be supplied by the caller",
                    "owner_ref",
                )
            identity = _identity(request)
            title = _kw02_optional_title(body)
            note_body = _kw02_required_body(body)
            attachment_type = _kw02_validate_attachment_type(body.get("attachment_type"))
            attachment_ref = _kw02_validate_attachment_ref(attachment_type, body.get("attachment_ref"))
            tags = _kw02_validate_string_list(body.get("tags"), "tags")
            linked_evidence_refs = _kw02_validate_string_list(
                body.get("linked_evidence_refs"), "linked_evidence_refs"
            )
            linked_memory_anchors = _kw02_validate_memory_anchors(
                port,
                _kw02_validate_string_list(
                    body.get("linked_memory_anchors"), "linked_memory_anchors"
                ),
            )
            if not _kw02_attachment_exists(port, attachment_type, attachment_ref):
                raise bff_error(
                    422,
                    ErrorCode.PRECONDITION_FAILED,
                    "Attachment target does not exist",
                    f"{attachment_type} target {attachment_ref} could not be resolved",
                    precondition_failed="attachment_ref",
                )

            operator_id = str(getattr(identity, "operator_id", "") or "")
            note_id = f"note-{uuid.uuid4()}"
            note = {
                "note_id": note_id,
                "title": title,
                "body": note_body,
                "attachment_type": attachment_type,
                "attachment_ref": attachment_ref,
                "owner_ref": {
                    "owner_type": "operator",
                    "owner_id": operator_id,
                    "display_name": _kw02_operator_display_name(operator_id),
                },
                "tags": tags,
                "linked_evidence_refs": linked_evidence_refs,
                "linked_memory_anchors": linked_memory_anchors,
                "created_at": snapshot_at,
                "updated_at": snapshot_at,
            }
            created = _call_port(port, "create_research_note", note)
            if created is None:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Research note store unavailable",
                    "Research note creation store is unavailable.",
                )
            return {
                "note_id": note_id,
                "created_at": snapshot_at,
                "route_href": f"/knowledge/notes/{note_id}",
            }
        if name in knowledge_operations:
            method, dataset = knowledge_operations[name]
            kwargs: Dict[str, Any] = {}
            if name == "list_strategy_specs":
                kwargs = {"lifecycle_state": _query(request, "lifecycle_state", "all"), "source_kind": _query(request, "source_kind"), "persona_id": _query(request, "persona_id"), "include_retired": _query(request, "include_retired", "false") == "true", "include_fixture_pack": False}
            records = list(_call_port(port, method, **kwargs) or [])
            items, next_token = _page(records, request)
            return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": _meta(snapshot_at, name, dataset, bool(records))}

        detail_operations = {
            "get_note": ("get_research_note", "note_id", "research_notes"),
            "get_evidence": ("get_evidence_ref_detail", "ref_id", "evidence_refs"),
            "get_insight": ("get_insight_card_detail", "insight_id", "insight_cards"),
            "get_strategy_spec": ("get_strategy_spec_detail", "strategy_id", "strategy_specs"),
            "get_memory": ("get_institutional_memory_entry", "entry_id", "institutional_memory_entries"),
        }
        if name in detail_operations:
            method, param, dataset = detail_operations[name]
            identifier = str(params[param])
            kwargs = {"version_selector": _query(request, "version", "current")} if name == "get_strategy_spec" else {}
            record = _call_port(port, method, identifier, **kwargs)
            if not record:
                _not_found("Research record", identifier)
            payload = dict(record)
            payload.setdefault("meta", _meta(snapshot_at, name, dataset, True))
            return payload

        if name == "strategy_versions":
            strategy_id = str(params["strategy_id"])
            records = list(_call_port(port, "list_strategy_spec_versions", strategy_id) or [])
            return {"data": records, "meta": _meta(snapshot_at, "strategy_versions", "strategy_specs", bool(records))}

        if name == "strategy_compare":
            strategy_id = str(params["strategy_id"])
            left = _query(request, "left_version") or _query(request, "base_version")
            right = _query(request, "right_version") or _query(request, "target_version")
            if not left or not right:
                raise bff_error(422, ErrorCode.VALIDATION_FAILED, "Two strategy versions are required", "left/right (or base/target) selectors are required", precondition_failed="version")
            comparison = _call_port(port, "compare_strategy_spec_versions", strategy_id, left_selector=left, right_selector=right)
            if not comparison:
                _not_found("Strategy specification", strategy_id)
            return comparison

        if name in {"synthesis_conflict_logs", "synthesis_conflict_log"}:
            if name == "synthesis_conflict_logs":
                records = list(_call_port(port, "list_synthesis_conflict_logs") or [])
                items, next_token = _page(records, request)
                return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": _meta(snapshot_at, "synthesis_conflict_logs", "synthesis_conflict_logs", bool(records))}
            log_id = str(params["log_id"])
            record = _call_port(port, "get_synthesis_conflict_log", log_id)
            if not record:
                _not_found("Synthesis conflict log", log_id)
            return {"data": record, "meta": _meta(snapshot_at, "synthesis_conflict_log", "synthesis_conflict_logs", True)}

        if name == "bff_search":
            query = str(_query(request, "q", "") or "").strip()
            records = list(_call_port(port, "list_research_search_results", query=query, match_type="all", status=None, date_range=None) or []) if query else []
            items, next_token = _page(records, request)
            return {"data": items, "items": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": _meta(snapshot_at, "search", "research_search", bool(records))}

        if name == "bff_patch_artifact":
            artifact_id = str(params["artifact_id"])
            if not _call_port(port, "get_research_artifact", artifact_id):
                _not_found("Artifact", artifact_id)
            raise bff_error(409, ErrorCode.OPERATION_NOT_ALLOWED, "Research artifacts are immutable", "Use the owning artifact pipeline; the generic BFF patch alias has no typed replacement")

        if name == "bff_create_artifact":
            raise bff_error(501, ErrorCode.NOT_IMPLEMENTED, "Artifact creation is not exposed by Research", "Use the owning artifact pipeline; the generic BFF create alias has no typed replacement")

        raise RuntimeError(f"Unknown Research route inventory operation: {name}")

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

    # Paths already declared above are typed replacements for the former
    # generic read aliases.  The remaining paths intentionally share the
    # source-port dispatcher, but must not share its ``Request``-only
    # signature: FastAPI derives both request validation and OpenAPI from an
    # endpoint signature at registration time.  Preserve the legacy
    # parameters here so a composition-root cutover does not silently turn
    # public body/query contracts into untyped request blobs.
    def _parameter(
        name: str,
        *,
        annotation: Any,
        default: Any = inspect.Parameter.empty,
    ) -> inspect.Parameter:
        return inspect.Parameter(
            name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=annotation,
            default=default,
        )

    def _path(name: str) -> inspect.Parameter:
        return _parameter(name, annotation=str)

    def _signature_query(
        name: str,
        *,
        annotation: Any = Optional[str],
        default: Any = None,
        **constraints: Any,
    ) -> inspect.Parameter:
        return _parameter(name, annotation=annotation, default=Query(default=default, **constraints))

    def _body_parameter(*, required: bool = True) -> inspect.Parameter:
        body = Body(...) if required else Body(default_factory=dict)
        return _parameter("payload", annotation=Dict[str, Any], default=body)

    def _authorization() -> inspect.Parameter:
        return _parameter("authorization", annotation=Optional[str], default=Header(default=None))

    def _idempotency_key() -> inspect.Parameter:
        return _parameter(
            "x_idempotency_key",
            annotation=Optional[str],
            default=Header(default=None, alias="X-Idempotency-Key"),
        )

    def _signature(*parameters: inspect.Parameter) -> inspect.Signature:
        return inspect.Signature((_parameter("request", annotation=Request), *parameters))

    def _inventory_endpoint(name: str, signature: inspect.Signature) -> Callable[..., Any]:
        async def endpoint(request: Request, **_validated_values: Any) -> Dict[str, Any]:
            return await _inventory_route(request)

        endpoint.__name__ = f"research_inventory_{name}"
        endpoint.__signature__ = signature
        return endpoint

    auth = _authorization()
    _inventory_signatures = {
        "knowledge_workbench": _signature(auth),
        "oss_activation_ready": _signature(_signature_query("activity_limit", annotation=int, default=20, ge=1, le=200), auth),
        "oss_preactivation": _signature(_signature_query("activity_limit", annotation=int, default=20, ge=1, le=200), auth),
        "source_ops": _signature(
            _signature_query("crawl_run_limit", annotation=int, default=50, ge=1, le=200),
            _signature_query("dlq_status"),
            _signature_query("frontier_status"),
            _signature_query("audit_limit", annotation=int, default=20, ge=1, le=200),
            auth,
        ),
        "search_ops": _signature(_signature_query("pipeline_run_limit", annotation=int, default=50, ge=1, le=200), auth),
        "command_source_dlq_replay": _signature(_body_parameter(required=False), auth, _idempotency_key()),
        "command_source_frontier_replay": _signature(_path("frontier_id"), _body_parameter(required=False), auth, _idempotency_key()),
        "command_search_index_refresh": _signature(_body_parameter(required=False), auth, _idempotency_key()),
        "command_search_index_materialize": _signature(auth, _idempotency_key()),
        "create_ticket": _signature(_body_parameter(), auth),
        "list_tickets": _signature(
            _signature_query("status"), _signature_query("owner"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
        ),
        "get_ticket": _signature(_path("ticket_id"), auth),
        "patch_ticket": _signature(_path("ticket_id"), _body_parameter(), auth),
        "research_search": _signature(
            _signature_query("q", annotation=str, default=...),
            _signature_query("match_type", annotation=str, default="all"),
            _signature_query("status"),
            _signature_query("date_range"),
            _signature_query("page_token"),
            _signature_query("page_size", annotation=int, default=25, ge=1, le=100),
            auth,
        ),
        "source_connectors": _signature(auth),
        "source_change_proposals": _signature(_signature_query("status"), _signature_query("proposal_type"), _signature_query("source_kind"), auth),
        "launch_experiment": _signature(_body_parameter(), auth),
        "list_experiments_api": _signature(
            _signature_query("ticket_id"), _signature_query("status"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
        ),
        "get_experiment_api": _signature(_path("experiment_id"), auth),
        "cancel_experiment_api": _signature(_path("experiment_id"), _body_parameter(), auth),
        "compare_artifacts_api": _signature(_signature_query("artifact_ids", annotation=str, default=...), auth),
        "list_artifacts_api": _signature(
            _signature_query("experiment_id"), _signature_query("ticket_id"), _signature_query("lineage_id"), _signature_query("status"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
        ),
        "get_artifact_api": _signature(_path("artifact_id"), auth),
        "create_note": _signature(_body_parameter(), auth),
        "list_notes": _signature(
            _signature_query("owner_ref"), _signature_query("attachment_type"), _signature_query("attachment_ref"), _signature_query("tags"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
        ),
        "get_note": _signature(_path("note_id"), auth),
        "list_evidence": _signature(
            _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"), _signature_query("link_type"), _signature_query("credibility_tier"), _signature_query("verified", annotation=Optional[bool]), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
        ),
        "get_evidence": _signature(_path("ref_id"), auth),
        "list_insights": _signature(
            _signature_query("status", annotation=str, default="active"), _signature_query("tag"), _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"), _signature_query("recency", annotation=str, default="all"), _signature_query("confidence_min", annotation=Optional[float]), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), _signature_query("include_inactive", annotation=bool, default=False), auth,
        ),
        "get_insight": _signature(_path("insight_id"), auth),
        "list_strategy_specs": _signature(
            _signature_query("lifecycle_state", annotation=str, default="all"), _signature_query("source_kind"), _signature_query("persona_id"), _signature_query("include_retired", annotation=bool, default=False), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
        ),
        "strategy_versions": _signature(_path("strategy_id"), auth),
        "strategy_compare": _signature(_path("strategy_id"), _signature_query("left_version"), _signature_query("right_version"), _signature_query("base_version"), _signature_query("target_version"), auth),
        "get_strategy_spec": _signature(_path("strategy_id"), _signature_query("version", annotation=str, default="current"), auth),
        "list_memory": _signature(
            _signature_query("knowledge_type"), _signature_query("scope"), _signature_query("scope_filter"), _signature_query("tags"), _signature_query("page", annotation=int, default=1, ge=1), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
        ),
        "get_memory": _signature(_path("entry_id"), auth),
        "synthesis_conflict_logs": _signature(
            _signature_query("capital_pool_id"), _signature_query("scope_ref"), _signature_query("proposal_id"), _signature_query("sponsor_persona_id"), _signature_query("synthesis_method"), _signature_query("committee_ref"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
        ),
        "synthesis_conflict_log": _signature(_path("log_id"), auth),
        "bff_search": _signature(
            _signature_query("q", annotation=str, default=""), _signature_query("types"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), _signature_query("limit", annotation=Optional[int], default=None, ge=1, le=100), _signature_query("page_token"), auth,
        ),
        "bff_patch_artifact": _signature(_path("artifact_id"), _body_parameter(required=False), auth),
        "bff_create_artifact": _signature(_body_parameter(required=False), auth),
    }

    _inventory_registrations = (
        ("knowledge_workbench", "/api/v1/workbench/knowledge", ("GET",), None),
        ("oss_activation_ready", "/api/v1/operator/research/oss-activation-ready", ("GET",), None),
        ("oss_preactivation", "/api/v1/operator/research/oss-preactivation", ("GET",), None),
        ("source_ops", "/api/v1/operator/source/ops", ("GET",), None),
        ("search_ops", "/api/v1/operator/search/ops", ("GET",), None),
        ("command_source_dlq_replay", "/api/v1/operator/source/dlq/replay", ("POST",), 202),
        ("command_source_frontier_replay", "/api/v1/operator/source/frontier/{frontier_id}/replay", ("POST",), 202),
        ("command_search_index_refresh", "/api/v1/operator/search/index/refresh", ("POST",), 202),
        ("command_search_index_materialize", "/api/v1/operator/search/index/materialize", ("POST",), 202),
        ("create_ticket", "/api/v1/research/tickets", ("POST",), None),
        ("list_tickets", "/api/v1/research/tickets", ("GET",), None),
        ("get_ticket", "/api/v1/research/tickets/{ticket_id}", ("GET",), None),
        ("patch_ticket", "/api/v1/research/tickets/{ticket_id}", ("PATCH",), None),
        ("research_search", "/api/v1/research/search", ("GET",), None),
        ("source_connectors", "/api/v1/research/source-connectors", ("GET",), None),
        ("source_change_proposals", "/api/v1/research/source-change-proposals", ("GET",), None),
        ("launch_experiment", "/api/v1/experiments/launch", ("POST",), None),
        ("list_experiments_api", "/api/v1/experiments", ("GET",), None),
        ("get_experiment_api", "/api/v1/experiments/{experiment_id}", ("GET",), None),
        ("cancel_experiment_api", "/api/v1/experiments/{experiment_id}/cancel", ("POST",), None),
        ("compare_artifacts_api", "/api/v1/artifacts/compare", ("GET",), None),
        ("list_artifacts_api", "/api/v1/artifacts", ("GET",), None),
        ("get_artifact_api", "/api/v1/artifacts/{artifact_id}", ("GET",), None),
        ("create_note", "/api/v1/knowledge/notes", ("POST",), 201),
        ("list_notes", "/api/v1/knowledge/notes", ("GET",), None),
        ("get_note", "/api/v1/knowledge/notes/{note_id}", ("GET",), None),
        ("list_evidence", "/api/v1/knowledge/evidence", ("GET",), None),
        ("get_evidence", "/api/v1/knowledge/evidence/{ref_id}", ("GET",), None),
        ("list_insights", "/api/v1/knowledge/insights", ("GET",), None),
        ("get_insight", "/api/v1/knowledge/insights/{insight_id}", ("GET",), None),
        ("list_strategy_specs", "/api/v1/knowledge/strategy-specs", ("GET",), None),
        ("strategy_versions", "/api/v1/knowledge/strategy-specs/{strategy_id}/versions", ("GET",), None),
        ("strategy_compare", "/api/v1/knowledge/strategy-specs/{strategy_id}/compare", ("GET",), None),
        ("get_strategy_spec", "/api/v1/knowledge/strategy-specs/{strategy_id}", ("GET",), None),
        ("list_memory", "/api/v1/knowledge/memory", ("GET",), None),
        ("get_memory", "/api/v1/knowledge/memory/{entry_id}", ("GET",), None),
        ("synthesis_conflict_logs", "/bff/synthesis/conflict-logs", ("GET",), None),
        ("synthesis_conflict_log", "/bff/synthesis/conflict-logs/{log_id}", ("GET",), None),
        ("bff_search", "/bff/search", ("GET",), None),
        ("bff_patch_artifact", "/bff/artifacts/{artifact_id}", ("PATCH",), None),
        ("bff_create_artifact", "/bff/artifacts", ("POST",), None),
    )
    for route_name, path, methods, status_code in _inventory_registrations:
        router.add_api_route(
            path,
            _inventory_endpoint(route_name, _inventory_signatures[route_name]),
            methods=list(methods),
            name=route_name,
            status_code=status_code,
        )

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

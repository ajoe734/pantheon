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
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Header, Query, Request

from ..models import ErrorCode, ObjectType, SOURCE_TYPE_TO_EVIDENCE_KIND, redact_evidence_refs

from .service import ResearchNotFoundError, ResearchRouterService, ResearchValidationError

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
SurfaceStatus = Callable[..., Dict[str, Any]]
# (entity_type_value, entity_id, action_id, resolved_key, identity, payload) -> receipt dict
SubmitAction = Callable[..., Any]
IdentityCapabilities = Callable[[Any], Optional[List[str]]]
CrossEntitySearch = Callable[..., Any]
ConflictLogList = Callable[..., List[Dict[str, Any]]]
ConflictLogGet = Callable[[str], Optional[Dict[str, Any]]]

_RESEARCH_EXPERIMENT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

_KW03_LINKED_ENTITY_TYPES = {
    "memory_entry", "research_note", "insight_card", "strategy_spec", "experiment", "artifact",
}
_KW03_LINK_TYPES = {
    "supporting_evidence", "counter_evidence", "citation", "provenance", "corroboration",
}
_KW03_CREDIBILITY_TIERS = {"primary", "secondary", "tertiary", "unverified"}
_KW04_STATUSES = {"active", "superseded", "archived", "all"}
_KW04_LINKED_ENTITY_TYPES = {
    "memory_entry", "research_note", "evidence_ref", "strategy_spec", "experiment",
}
_KW04_RECENCY_VALUES = {"7d", "30d", "90d", "all"}
_KW05_LIFECYCLE_STATES = {"draft", "candidate", "approved", "retired", "all"}
_ENTITY_TYPE_EVIDENCE_KIND: Dict[str, str] = {
    "strategy_spec": "strategy",
    "strategy": "strategy",
    "persona": "persona",
    "deployment_plan": "deployment",
    "deployment": "deployment",
    "runtime": "runtime",
    "runtime_binding": "runtime",
    "alert": "alert",
    "incident": "incident",
    "job": "job",
    "audit": "audit",
    "metric": "metric",
    "policy": "policy",
    "approval": "approval",
    "artifact": "artifact",
    "signal": "signal",
    "journal": "journal",
    "postmortem": "postmortem",
}


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
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
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
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
    elif get_read_store is None:
        raise RuntimeError("Neither read_surface nor get_read_store was configured.")

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
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
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
    get_capabilities: Optional[IdentityCapabilities] = None,
    cross_entity_search: Optional[CrossEntitySearch] = None,
    list_synthesis_conflict_logs: Optional[ConflictLogList] = None,
    get_synthesis_conflict_log: Optional[ConflictLogGet] = None,
    include_prepared_subrouters: bool = True,
) -> APIRouter:
    """Build the standalone Research domain router.

    The factory is intentionally not mounted by this preparation task.  A
    composition-root task can inject main.py's existing identity, metadata and
    action seams, then remove the generic aliases it supersedes without a
    circular import back into ``main``.
    """
    if read_surface is not None:
        get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
    elif get_read_store is None:
        raise RuntimeError("Neither read_surface nor get_read_store was configured.")

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
    _ARTIFACT_STATUSES = {"pending", "sealed", "superseded", "failed"}
    _RESEARCH_SEARCH_MATCH_TYPES = {"all", "ticket", "experiment", "artifact"}
    _RESEARCH_SEARCH_DATE_RANGES = {"24h", "7d", "30d", "90d"}
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

    def _kw02_surface_state(port: Any, *, snapshot_at: str, has_data: bool) -> str:
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_notes") or "missing") if callable(source_fn) else "missing"
        surface = dataset_surface_status(
            "research_notes",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        if surface.get("status") == "unavailable":
            return "unavailable"
        if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
            return "degraded"
        return "ok"

    def _kw02_operator_display_name(operator_id: str) -> str:
        if operator_id == "op-001":
            return "Alice Chen"
        token = str(operator_id or "").strip()
        if not token:
            return "Operator"
        if token.startswith("op-"):
            return f"Operator {token}"
        return " ".join(part.capitalize() for part in re.split(r"[-_]+", token) if part)

    def _kw02_strip_markdown(text: str) -> str:
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        plain = re.sub(r"[`*_>#]", " ", plain)
        return re.sub(r"\s+", " ", plain).strip()

    def _kw02_resolve_attachment_target(
        port: Any,
        attachment_type: str,
        attachment_ref: Optional[str],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if attachment_type == "free_standing":
            return True, None, None
        if attachment_type == "research_ticket":
            ticket = _call_port(port, "get_research_ticket", attachment_ref)
            if not ticket:
                return False, None, None
            return True, ticket.get("title"), f"/research/tickets/{attachment_ref}"
        if attachment_type == "persona":
            persona = _call_port(port, "get_persona", attachment_ref)
            if not persona:
                return False, None, None
            return True, persona.get("name"), f"/personas/{attachment_ref}"
        strategy_spec = _call_port(port, "get_strategy_spec", attachment_ref)
        if not strategy_spec:
            return False, None, None
        label = strategy_spec.get("title") or strategy_spec.get("name") or attachment_ref
        return True, label, f"/knowledge/strategy-specs/{attachment_ref}"

    def _kw02_note_list_item(port: Any, note: Dict[str, Any]) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        attachment_exists, attachment_label, _ = _kw02_resolve_attachment_target(
            port,
            attachment_type,
            attachment_ref,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "excerpt": _kw02_strip_markdown(str(note.get("body") or ""))[:280],
            "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
            "attachment": {
                "type": attachment_type,
                "ref": attachment_ref,
                "display_label": attachment_label if attachment_exists else None,
            },
            "tags": list(note.get("tags") or []),
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "route_href": f"/knowledge/notes/{note.get('note_id')}",
        }

    def _kw02_attachment_payload(
        port: Any,
        note: Dict[str, Any],
        *,
        include_route: bool,
    ) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        exists, display_label, route_href = _kw02_resolve_attachment_target(
            port,
            attachment_type,
            attachment_ref,
        )
        payload = {
            "type": attachment_type,
            "ref": attachment_ref,
            "display_label": display_label if exists else None,
        }
        if include_route:
            payload["route_href"] = route_href if exists else None
        return payload

    def _kw02_resolve_evidence_links(
        port: Any,
        ref_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        items: List[Dict[str, Any]] = []
        for ref_id in ref_ids:
            if surface_state == "unavailable":
                items.append({
                    "ref_id": ref_id,
                    "resolution_state": "unavailable",
                    "display_label": None,
                    "route_href": None,
                })
                continue
            evidence_ref = _call_port(port, "get_evidence_ref", ref_id)
            if evidence_ref:
                items.append({
                    "ref_id": ref_id,
                    "resolution_state": "resolved",
                    "display_label": evidence_ref.get("display_label"),
                    "route_href": evidence_ref.get("route_href") or f"/knowledge/evidence/{ref_id}",
                })
                continue
            items.append({
                "ref_id": ref_id,
                "resolution_state": "unresolved",
                "display_label": None,
                "route_href": None,
            })
        return items, surface_state

    def _kw02_resolve_memory_anchors(
        port: Any,
        entry_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = _knowledge_surface_state(
            "institutional_memory_entries", snapshot_at=snapshot_at, has_data=True,
        )
        items: List[Dict[str, Any]] = []
        missing_entries = False
        for entry_id in entry_ids:
            entry = _call_port(port, "get_institutional_memory_entry", entry_id)
            if not entry:
                missing_entries = True
                continue
            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
            items.append({
                "entry_id": entry_id,
                "headline": content.get("headline") or entry.get("headline"),
                "knowledge_type": entry.get("knowledge_type"),
                "lifecycle_status": lifecycle.get("status"),
                "route_href": f"/knowledge/memory/{entry_id}",
            })
        if missing_entries and surface_state == "ok":
            surface_state = "degraded"
        return items, surface_state

    def _research_note_detail_payload(
        port: Any,
        note: Dict[str, Any],
        *,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        evidence_links, evidence_surface = _kw02_resolve_evidence_links(
            port,
            list(note.get("linked_evidence_refs") or []),
            snapshot_at=snapshot_at,
        )
        memory_anchors, memory_surface = _kw02_resolve_memory_anchors(
            port,
            list(note.get("linked_memory_anchors") or []),
            snapshot_at=snapshot_at,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "body": note.get("body"),
            "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
            "attachment": _kw02_attachment_payload(port, note, include_route=True),
            "tags": list(note.get("tags") or []),
            "linked_evidence_refs": evidence_links,
            "linked_memory_anchors": memory_anchors,
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "meta": {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "research_note_detail": _knowledge_surface_state(
                        "research_notes", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "evidence_links": evidence_surface,
                    "memory_anchors": memory_surface,
                },
            },
        }

    def _kw05_bad_request(message: str, reason: str, field: str) -> None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _kw05_surface_state(*, snapshot_at: str, has_data: bool) -> str:
        return _knowledge_surface_state(
            "strategy_specs", snapshot_at=snapshot_at, has_data=has_data,
        )

    def _kw05_validate_lifecycle_state(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW05_LIFECYCLE_STATES:
            _kw05_bad_request(
                "Invalid lifecycle_state",
                f"lifecycle_state must be one of {sorted(_KW05_LIFECYCLE_STATES)}",
                "lifecycle_state",
            )
        return normalized

    def _kw05_compare_selectors(
        *,
        left_version: Optional[str],
        right_version: Optional[str],
        base_version: Optional[str],
        target_version: Optional[str],
    ) -> Tuple[str, str]:
        left = str(left_version or base_version or "").strip()
        right = str(right_version or target_version or "").strip()
        if not left or not right:
            _kw05_bad_request(
                "Missing compare versions",
                "Provide either left_version/right_version or base_version/target_version",
                "left_version",
            )
        if left_version and base_version and str(left_version).strip() != str(base_version).strip():
            _kw05_bad_request(
                "Conflicting compare aliases",
                "left_version and base_version must reference the same version when both are provided",
                "left_version",
            )
        if right_version and target_version and str(right_version).strip() != str(target_version).strip():
            _kw05_bad_request(
                "Conflicting compare aliases",
                "right_version and target_version must reference the same version when both are provided",
                "right_version",
            )
        return left, right

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

    def _validate_artifact_status(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        normalized = str(value).strip().lower()
        if normalized not in _ARTIFACT_STATUSES:
            # The public artifact search contract predates the typed router and
            # uses request-level (400) validation, rather than leaking an
            # invalid filter to the source port.
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Invalid artifact status",
                f"status must be one of {sorted(_ARTIFACT_STATUSES)}",
                precondition_failed="status",
            )
        return normalized

    def _research_search_bad_request(field: str, reason: str) -> None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Invalid research search query",
            reason,
            precondition_failed=field,
        )

    def _validate_research_search_query(value: Optional[str]) -> str:
        query = str(value or "").strip()
        if not query:
            _research_search_bad_request("q", "q is required and must be non-empty")
        return query

    def _validate_research_search_match_type(value: Optional[str]) -> str:
        match_type = str(value or "all").strip().lower()
        if match_type not in _RESEARCH_SEARCH_MATCH_TYPES:
            _research_search_bad_request(
                "match_type",
                f"match_type must be one of {sorted(_RESEARCH_SEARCH_MATCH_TYPES)}",
            )
        return match_type

    def _validate_research_search_status(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        status = str(value).strip().lower()
        if status not in _TICKET_STATUSES:
            _research_search_bad_request(
                "status", f"status must be one of {sorted(_TICKET_STATUSES)}"
            )
        return status

    def _validate_research_search_date_range(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        date_range = str(value).strip().lower()
        if date_range not in _RESEARCH_SEARCH_DATE_RANGES:
            _research_search_bad_request(
                "date_range",
                f"date_range must be one of {sorted(_RESEARCH_SEARCH_DATE_RANGES)}",
            )
        return date_range

    def _legacy_ticket_surface_state(
        *, snapshot_at: str, has_data: Optional[bool] = None
    ) -> str:
        """Return the frozen RW-01 ticket surface string, not a surface object."""
        port = get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_tickets") or "missing") if callable(source_fn) else "missing"
        surface = dataset_surface_status(
            "research_tickets",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        if isinstance(surface, str):
            return surface
        status = str((surface or {}).get("status") or "")
        if status == "unavailable" or source == "missing":
            return "unavailable"
        if source == "local_snapshot":
            return "degraded"
        if status == "degraded":
            return "stale"
        return "fresh"

    def _legacy_experiment_surface_state(
        *, snapshot_at: str, has_data: bool
    ) -> str:
        """Match the API-v1 experiment availability projection from main.py."""
        port = get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_experiments") or "missing") if callable(source_fn) else "missing"
        if source == "missing" and has_data:
            source = "bff_local"
        surface = dataset_surface_status(
            "research_experiments",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        if isinstance(surface, str):
            return surface
        status = str((surface or {}).get("status") or "")
        if status == "unavailable":
            return "unavailable"
        if source == "local_snapshot" or status == "degraded":
            return "degraded"
        return "ok"

    def _legacy_artifact_reference_values(record: Dict[str, Any], field: str) -> set[str]:
        """Read both current source-port and frozen API-v1 linkage spellings."""
        candidates: List[Any] = [record.get(field)]
        linkage = record.get("research_linkage")
        if isinstance(linkage, dict):
            candidates.extend(
                linkage.get(key)
                for key in (field, f"{field}_ref", f"linked_{field}")
            )
        if field == "experiment_id":
            candidates.append(record.get("produced_by_experiment_id"))
            candidates.append(record.get("experiment_refs"))
        if field == "lineage_id":
            lineage = record.get("lineage")
            if isinstance(lineage, dict):
                candidates.append(lineage.get("lineage_id"))

        values: set[str] = set()
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = (
                    candidate.get(field)
                    or candidate.get("id")
                    or candidate.get("ref")
                )
            elif isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, dict):
                        item = item.get(field) or item.get("id") or item.get("ref")
                    if item not in (None, ""):
                        values.add(str(item))
                continue
            if candidate not in (None, ""):
                values.add(str(candidate))
        return values

    def _filter_legacy_artifacts(
        records: List[Dict[str, Any]],
        *,
        experiment_id: Optional[str],
        ticket_id: Optional[str],
        lineage_id: Optional[str],
        status: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Preserve filters unsupported by the predecessor source-port API.

        ``ResearchKnowledgeSourcePort`` deliberately keeps its reusable
        artifact method limited to its original typed filters.  The migration
        must not widen that port, so this compatibility endpoint asks the port
        for supported status filtering and applies the API-v1 linkage filters
        on its durable projections.
        """
        requested = {
            "experiment_id": experiment_id,
            "ticket_id": ticket_id,
            "lineage_id": lineage_id,
        }
        filtered = list(records)
        for field, expected in requested.items():
            if expected not in (None, ""):
                filtered = [
                    record
                    for record in filtered
                    if str(expected) in _legacy_artifact_reference_values(record, field)
                ]
        if status is not None:
            filtered = [
                record
                for record in filtered
                if str(record.get("status") or "").strip().lower() == status
            ]
        return filtered

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

    def _knowledge_bad_request(message: str, reason: str, field: str) -> None:
        raise bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _validate_knowledge_choice(value: Any, *, field: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            _knowledge_bad_request(
                f"Invalid {field}",
                f"{field} must be one of {sorted(allowed)}",
                field,
            )
        return normalized

    def _knowledge_surface_state(
        dataset: str,
        *,
        snapshot_at: str,
        has_data: bool,
        missing_message: Optional[str] = None,
    ) -> str:
        port = get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface = dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
            missing_message=missing_message,
        )
        if isinstance(surface, str):
            return surface
        status = str((surface or {}).get("status") or "")
        if status == "unavailable" or source == "missing":
            return "unavailable"
        if status == "degraded" or (surface or {}).get("source") == "local_snapshot":
            return "degraded"
        return "ok"

    def _evidence_detail_payload(
        evidence_ref: Dict[str, Any],
        *,
        ref_id: str,
        identity: Any,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        """Preserve the KW-03 detail projection at the router boundary.

        The knowledge port may return a fully projected record or a durable
        source record.  Keep the public detail contract stable for both forms,
        including the capability gate for the evidence itself and its linked
        decision records.
        """
        detail_surface = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        capabilities = _capabilities(identity)

        evidence_kind = str(evidence_ref.get("evidence_type") or "").strip()
        if not evidence_kind:
            source_document = evidence_ref.get("source_document")
            if isinstance(source_document, dict):
                evidence_kind = SOURCE_TYPE_TO_EVIDENCE_KIND.get(
                    str(source_document.get("source_type") or "").strip(), "",
                )
        if evidence_kind:
            [processed_self], _ = redact_evidence_refs(
                identity,
                [{"ref_id": ref_id, "evidence_type": evidence_kind}],
                capabilities=capabilities,
            )
            if isinstance(processed_self, dict) and processed_self.get("redacted"):
                return {
                    **processed_self,
                    "meta": {
                        **snapshot_meta(snapshot_at),
                        "surfaces": {
                            "evidence_ref_detail": detail_surface,
                            "resolved_link": detail_surface,
                            "linked_decisions": detail_surface,
                        },
                        "redacted_evidence_count": 1,
                    },
                }

        raw_linked_decisions = json.loads(
            json.dumps(evidence_ref.get("linked_decisions") or [])
        )
        annotated_decisions: List[Any] = []
        for decision in raw_linked_decisions:
            if not isinstance(decision, dict):
                annotated_decisions.append(decision)
                continue
            evidence_kind = _ENTITY_TYPE_EVIDENCE_KIND.get(
                str(decision.get("entity_type") or "").strip(),
            )
            if not evidence_kind:
                annotated_decisions.append(decision)
                continue
            annotated = dict(decision)
            annotated["evidence_type"] = evidence_kind
            if not annotated.get("ref_id") and not annotated.get("id"):
                annotated["ref_id"] = annotated.get("entity_ref") or ""
            annotated_decisions.append(annotated)
        processed_decisions, redacted_count = redact_evidence_refs(
            identity, annotated_decisions, capabilities=capabilities,
        )
        linked_decisions = [
            processed if isinstance(processed, dict) and processed.get("redacted") else original
            for original, processed in zip(raw_linked_decisions, processed_decisions)
        ]

        return {
            "ref_id": evidence_ref.get("ref_id"),
            "source_document": json.loads(json.dumps(evidence_ref.get("source_document") or {})),
            "link_type": evidence_ref.get("link_type"),
            "credibility": json.loads(json.dumps(evidence_ref.get("credibility") or {})),
            "resolved_link": json.loads(json.dumps(evidence_ref.get("resolved_link") or {})),
            "linked_object_summary": json.loads(
                json.dumps(evidence_ref.get("linked_object_summary") or {})
            ),
            "linked_decisions": linked_decisions,
            "source_note_context": json.loads(json.dumps(evidence_ref.get("source_note_context"))),
            "source_memory_context": json.loads(json.dumps(evidence_ref.get("source_memory_context"))),
            "created_at": evidence_ref.get("created_at"),
            "meta": {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "evidence_ref_detail": detail_surface,
                    "resolved_link": detail_surface,
                    "linked_decisions": detail_surface,
                },
                "redacted_evidence_count": redacted_count,
            },
        }

    def _insight_supporting_evidence_surface(
        supporting_evidence_refs: List[Dict[str, Any]], *, snapshot_at: str,
    ) -> str:
        surface_state = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        if surface_state != "ok":
            return surface_state
        if any(
            not item.get("ref_id") or not isinstance(item.get("resolved_link"), dict)
            for item in supporting_evidence_refs
        ):
            return "degraded"
        return "ok"

    def _insight_linked_sources_surface(
        linked_sources: List[Dict[str, Any]], *, snapshot_at: str,
    ) -> str:
        dataset_map = {
            "memory_entry": "institutional_memory_entries",
            "research_note": "research_notes",
            "evidence_ref": "evidence_refs",
            "strategy_spec": "strategy_specs",
            "experiment": "research_experiments",
        }
        overall = "ok"
        for item in linked_sources:
            dataset = dataset_map.get(str(item.get("entity_type") or "").strip())
            if not dataset:
                return "degraded"
            surface_state = _knowledge_surface_state(
                dataset, snapshot_at=snapshot_at, has_data=True,
            )
            if surface_state == "unavailable":
                return "unavailable"
            if surface_state == "degraded":
                overall = "degraded"
            if not item.get("display_label") or "route_href" not in item:
                overall = "degraded"
        return overall

    def _insight_detail_payload(
        insight_card: Dict[str, Any], *, snapshot_at: str,
    ) -> Dict[str, Any]:
        supporting_evidence_refs = list(insight_card.get("supporting_evidence_refs") or [])
        linked_sources = list(insight_card.get("linked_sources") or [])
        return {
            "insight_id": insight_card.get("insight_id"),
            "summary": insight_card.get("summary"),
            "scope": insight_card.get("scope"),
            "scope_context": json.loads(json.dumps(insight_card.get("scope_context") or {})),
            "status": insight_card.get("status"),
            "superseded_by": json.loads(json.dumps(insight_card.get("superseded_by") or {})),
            "confidence": json.loads(json.dumps(insight_card.get("confidence") or {})),
            "tags": list(insight_card.get("tags") or []),
            "source_ref": insight_card.get("source_ref"),
            "supporting_evidence_refs": json.loads(json.dumps(supporting_evidence_refs)),
            "linked_sources": json.loads(json.dumps(linked_sources)),
            "aggregation_provenance": json.loads(
                json.dumps(insight_card.get("aggregation_provenance") or {})
            ),
            "created_at": insight_card.get("created_at"),
            "updated_at": insight_card.get("updated_at"),
            "meta": {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "insight_card_detail": _knowledge_surface_state(
                        "insight_cards", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "supporting_evidence_refs": _insight_supporting_evidence_surface(
                        supporting_evidence_refs, snapshot_at=snapshot_at,
                    ),
                    "linked_sources": _insight_linked_sources_surface(
                        linked_sources, snapshot_at=snapshot_at,
                    ),
                },
            },
        }

    def _memory_detail_payload(entry: Dict[str, Any], *, snapshot_at: str) -> Dict[str, Any]:
        source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
        source_context_available = bool(source_event.get("type")) and bool(source_event.get("id"))
        return {
            **entry,
            "meta": {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "entry_detail": _knowledge_surface_state(
                        "institutional_memory_entries", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "source_context": _knowledge_surface_state(
                        "institutional_memory_entries",
                        snapshot_at=snapshot_at,
                        has_data=source_context_available,
                        missing_message="Institutional memory source context is unavailable.",
                    ),
                },
            },
        }

    def _capabilities(identity: Any) -> Optional[List[str]]:
        if get_capabilities is None:
            return None
        try:
            return get_capabilities(identity)
        except Exception:
            return None

    def _evidence_list_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """Keep KW-03 list responses stable even when a port returns extra fields."""
        return {
            "ref_id": item.get("ref_id"),
            "source_document": json.loads(json.dumps(item.get("source_document") or {})),
            "link_type": item.get("link_type"),
            "credibility": json.loads(json.dumps(item.get("credibility") or {})),
            "linked_object_summary": json.loads(json.dumps(item.get("linked_object_summary") or {})),
            "resolved_link": json.loads(json.dumps(item.get("resolved_link") or {})),
            "route_href": item.get("route_href"),
        }

    def _insight_list_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "insight_id": item.get("insight_id"),
            "summary": item.get("summary"),
            "scope": item.get("scope"),
            "scope_ref": item.get("scope_ref"),
            "status": item.get("status"),
            "superseded_by_id": item.get("superseded_by_id"),
            "confidence": json.loads(json.dumps(item.get("confidence") or {})),
            "tags": list(item.get("tags") or []),
            "evidence_count": item.get("evidence_count"),
            "primary_evidence_count": item.get("primary_evidence_count"),
            "aggregated_at": item.get("aggregated_at")
            or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
            "route_href": item.get("route_href")
            or (f"/knowledge/insights/{item.get('insight_id')}" if item.get("insight_id") else None),
        }

    def _insight_filter_metadata(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        tag_counts: Dict[str, int] = {}
        entity_counts: Dict[str, int] = {}
        for card in cards:
            seen_tags: set[str] = set()
            for raw_tag in card.get("tags") or []:
                tag = str(raw_tag or "").strip()
                if tag and tag not in seen_tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    seen_tags.add(tag)
            seen_entities: set[str] = set()
            for source in card.get("linked_sources") or []:
                if not isinstance(source, dict):
                    continue
                entity_type = str(source.get("entity_type") or "").strip()
                if entity_type and entity_type not in seen_entities:
                    entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
                    seen_entities.add(entity_type)
        labels = {
            "memory_entry": "Institutional Memory",
            "research_note": "Research Note",
            "evidence_ref": "Evidence Reference",
            "strategy_spec": "Strategy Spec",
            "experiment": "Experiment",
        }
        return {
            "tags": [
                {"value": tag, "display_label": tag.replace("-", " ").title(), "count": count}
                for tag, count in sorted(tag_counts.items(), key=lambda value: (-value[1], value[0]))
            ],
            "linked_entity_types": [
                {
                    "value": entity,
                    "display_label": labels.get(entity, entity.replace("_", " ").title()),
                    "count": count,
                }
                for entity, count in sorted(entity_counts.items(), key=lambda value: (-value[1], value[0]))
            ],
            "recency_options": [
                {"value": value, "display_label": {"7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days", "all": "All time"}[value]}
                for value in ("7d", "30d", "90d", "all")
            ],
            "total_active_count": sum(1 for card in cards if str(card.get("status") or "") == "active"),
        }

    def _within_recency(value: Any, recency: str, snapshot_at: str) -> bool:
        if recency == "all":
            return True
        try:
            raw = str(value or "").replace("Z", "+00:00")
            aggregated = datetime.fromisoformat(raw)
            if aggregated.tzinfo is None:
                aggregated = aggregated.replace(tzinfo=timezone.utc)
            snapshot = datetime.fromisoformat(str(snapshot_at).replace("Z", "+00:00"))
            if snapshot.tzinfo is None:
                snapshot = snapshot.replace(tzinfo=timezone.utc)
            return aggregated >= snapshot - timedelta(days={"7d": 7, "30d": 30, "90d": 90}[recency])
        except (TypeError, ValueError, KeyError):
            return False

    def _conflict_view(log: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.loads(json.dumps(log))
        log_id = str(raw.get("log_id") or raw.get("id") or raw.get("conflict_resolution_log_id") or "").strip()
        proposal_ids = [str(value) for value in raw.get("proposal_ids") or [] if str(value).strip()]
        vetoes = {
            str(value.get("proposal_id")): value
            for value in raw.get("vetoed_proposals") or []
            if isinstance(value, dict) and value.get("proposal_id")
        }
        for proposal_id in vetoes:
            if proposal_id not in proposal_ids:
                proposal_ids.append(proposal_id)
        inputs = raw.get("weighting_inputs") if isinstance(raw.get("weighting_inputs"), dict) else {}
        outputs = raw.get("weighting_outputs") if isinstance(raw.get("weighting_outputs"), dict) else {}
        rows = []
        for proposal_id in proposal_ids:
            veto = vetoes.get(proposal_id)
            output = outputs.get(proposal_id)
            state = "vetoed" if veto else ("selected" if output not in (None, 0, "0") else "not_selected")
            row = {
                "proposal_id": proposal_id,
                "state": state,
                "input_weight": inputs.get(proposal_id),
                "output_share": output,
                "is_vetoed": bool(veto),
            }
            if veto:
                row.update({"persona_id": veto.get("persona_id"), "veto_reason": veto.get("reason"), "veto_detail": veto.get("detail")})
            rows.append(row)
        resolution_state = "rejected" if raw.get("rejected_reason") else ("committee_required" if raw.get("committee_ref") else ("resolved_with_veto" if vetoes else "resolved"))
        raw["id"] = log_id
        raw["resolution_state"] = resolution_state
        artifact_id = raw.get("allocation_policy_artifact_id") or raw.get("artifact_id")
        artifact_href = raw.get("allocation_policy_artifact_href") or raw.get("artifact_href")
        governance_approval_id = raw.get("governance_approval_id")
        raw["view"] = {
            "title": f"Synthesis conflict log {log_id}",
            "resolution_state": resolution_state,
            "summary": {
                "proposal_count": len(rows),
                "selected_count": sum(1 for row in rows if row["state"] == "selected"),
                "veto_count": sum(1 for row in rows if row["is_vetoed"]),
                "committee_required": bool(raw.get("committee_ref")),
                "sponsor_persona_id": raw.get("sponsor_persona_id"),
                "synthesis_method": raw.get("synthesis_method"),
                "capital_pool_id": raw.get("capital_pool_id"),
                "scope_ref": raw.get("scope_ref"),
            },
            "proposal_rows": rows,
            "governance": {
                "committee_ref": raw.get("committee_ref"),
                "rejected_reason": raw.get("rejected_reason"),
                "approval_id": governance_approval_id,
                "decision": raw.get("governance_decision"),
                "decision_state": raw.get("governance_decision_state"),
                "can_proceed": raw.get("governance_can_proceed"),
            },
            "links": {
                "allocation_policy_artifact": (
                    {"id": artifact_id, "href": artifact_href} if artifact_id else None
                ),
                "governance_approval": (
                    {"id": governance_approval_id, "href": f"/bff/approvals/{governance_approval_id}"}
                    if governance_approval_id
                    else None
                ),
            },
        }
        return raw

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
            surface_state = _legacy_ticket_surface_state(snapshot_at=snapshot_at)
            if surface_state == "unavailable":
                items, next_token, total = [], None, 0
            else:
                items, next_token, total = *_page(records, request), len(records)
            meta = snapshot_meta(snapshot_at)
            meta["surfaces"] = {"ticket_list": surface_state}
            return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}

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
            payload["meta"] = {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "ticket_detail": _legacy_ticket_surface_state(
                        snapshot_at=snapshot_at, has_data=True
                    ),
                },
            }
            return payload

        if name == "research_search":
            query = _validate_research_search_query(_query(request, "q"))
            match_type = _validate_research_search_match_type(_query(request, "match_type", "all"))
            status = _validate_research_search_status(_query(request, "status"))
            date_range = _validate_research_search_date_range(_query(request, "date_range"))
            index = _call_port(port, "get_research_search_index")
            if not index:
                raise bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Search results are unavailable", "SEARCH_RESULTS_UNAVAILABLE")
            records = list(_call_port(port, "list_research_search_results", query=query, match_type=match_type, status=status, date_range=date_range) or [])
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
                surface_state = _legacy_experiment_surface_state(
                    snapshot_at=snapshot_at, has_data=bool(records)
                )
                if surface_state == "unavailable":
                    items, next_token, total = [], None, 0
                else:
                    page_items, next_token = _page(records, request)
                    items = []
                    for record in page_items:
                        item = dict(record)
                        experiment_ref = str(item.get("experiment_id") or "")
                        item["links"] = {
                            "self": f"/api/v1/experiments/{experiment_ref}",
                            "workbench_detail": f"/research/experiments/{experiment_ref}",
                        }
                        item["allowedActions"] = {
                            "canCancel": bool(
                                (item.get("allowedActions") or {}).get("canCancel", False)
                            ),
                        }
                        items.append(item)
                    total = len(records)
                meta = snapshot_meta(snapshot_at)
                meta["surfaces"] = {"experiment_history": surface_state}
                return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}
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
                experiment_id = _query(request, "experiment_id")
                ticket_id = _query(request, "ticket_id")
                lineage_id = _query(request, "lineage_id")
                status = _validate_artifact_status(_query(request, "status"))
                artifact_reader = _port_method(port, "list_research_artifacts")
                try:
                    records = list(artifact_reader(
                        experiment_id=experiment_id,
                        ticket_id=ticket_id,
                        lineage_id=lineage_id,
                        status=status,
                    ) or [])
                except TypeError:
                    # The preserved source port accepts the status filter but
                    # intentionally predates the API-v1 linkage arguments.
                    records = list(artifact_reader(status=status) or [])
                records = _filter_legacy_artifacts(
                    records,
                    experiment_id=experiment_id,
                    ticket_id=ticket_id,
                    lineage_id=lineage_id,
                    status=status,
                )
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
            "list_strategy_specs": ("list_strategy_specs", "strategy_specs"),
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

        if name == "list_evidence":
            linked_entity_type = _query(request, "linked_entity_type")
            linked_entity_ref = _query(request, "linked_entity_ref")
            link_type = _query(request, "link_type")
            credibility_tier = _query(request, "credibility_tier")
            verified_raw = _query(request, "verified")
            validated_entity_type = (
                _validate_knowledge_choice(
                    linked_entity_type,
                    field="linked_entity_type",
                    allowed=_KW03_LINKED_ENTITY_TYPES,
                )
                if linked_entity_type is not None
                else None
            )
            if linked_entity_ref is not None and validated_entity_type is None:
                _knowledge_bad_request(
                    "Invalid linked_entity_ref filter",
                    "linked_entity_ref requires linked_entity_type to be set",
                    "linked_entity_ref",
                )
            validated_link_type = (
                _validate_knowledge_choice(link_type, field="link_type", allowed=_KW03_LINK_TYPES)
                if link_type is not None
                else None
            )
            validated_tier = (
                _validate_knowledge_choice(credibility_tier, field="credibility_tier", allowed=_KW03_CREDIBILITY_TIERS)
                if credibility_tier is not None
                else None
            )
            verified: Optional[bool] = None
            if verified_raw is not None:
                normalized_verified = str(verified_raw).strip().lower()
                if normalized_verified not in {"true", "false"}:
                    _knowledge_bad_request("Invalid verified", "verified must be a boolean", "verified")
                verified = normalized_verified == "true"

            records = list(_call_port(port, "list_evidence_refs") or [])
            if validated_entity_type:
                records = [
                    item
                    for item in records
                    if str(((item.get("linked_object_summary") or {}).get("entity_type")) or "").lower()
                    == validated_entity_type
                ]
            if linked_entity_ref is not None:
                records = [
                    item
                    for item in records
                    if str(((item.get("linked_object_summary") or {}).get("entity_ref")) or "")
                    == str(linked_entity_ref)
                ]
            if validated_link_type:
                records = [item for item in records if str(item.get("link_type") or "").lower() == validated_link_type]
            if validated_tier:
                records = [
                    item
                    for item in records
                    if str(((item.get("credibility") or {}).get("tier")) or "").lower() == validated_tier
                ]
            if verified is not None:
                records = [item for item in records if bool((item.get("credibility") or {}).get("verified")) is verified]

            available = getattr(port, "dataset_source", lambda _dataset: "missing")("evidence_refs") != "missing"
            surface_state = _knowledge_surface_state(
                "evidence_refs", snapshot_at=snapshot_at, has_data=available,
            )
            if surface_state == "unavailable":
                page_items, next_token, has_more = [], None, False
            else:
                page_items, next_token = _page(records, request)
                has_more = next_token is not None
            processed, redacted_count = redact_evidence_refs(
                identity, page_items, capabilities=_capabilities(identity)
            )
            response_items = [
                item if isinstance(item, dict) and item.get("redacted") else _evidence_list_item(item)
                for item in processed
            ]
            meta = snapshot_meta(snapshot_at)
            meta["surfaces"] = {"evidence_refs_list": surface_state}
            meta["redacted_evidence_count"] = redacted_count
            return {
                "evidence_refs": response_items,
                "pagination": {
                    "page_size": int(_query(request, "page_size", "20") or 20),
                    "next_page_token": next_token,
                    "has_more": has_more,
                },
                "meta": meta,
            }

        if name == "list_insights":
            status = _validate_knowledge_choice(
                _query(request, "status", "active"), field="status", allowed=_KW04_STATUSES,
            )
            recency = _validate_knowledge_choice(
                _query(request, "recency", "all"), field="recency", allowed=_KW04_RECENCY_VALUES,
            )
            linked_entity_type = _query(request, "linked_entity_type")
            linked_entity_ref = _query(request, "linked_entity_ref")
            validated_entity_type = (
                _validate_knowledge_choice(linked_entity_type, field="linked_entity_type", allowed=_KW04_LINKED_ENTITY_TYPES)
                if linked_entity_type is not None
                else None
            )
            if linked_entity_ref is not None and validated_entity_type is None:
                _knowledge_bad_request(
                    "Invalid linked_entity_ref filter",
                    "linked_entity_ref requires linked_entity_type to be set",
                    "linked_entity_ref",
                )
            confidence_raw = _query(request, "confidence_min")
            confidence_min: Optional[float] = None
            if confidence_raw is not None:
                try:
                    confidence_min = float(confidence_raw)
                except (TypeError, ValueError):
                    _knowledge_bad_request("Invalid confidence_min", "confidence_min must be a number between 0.0 and 1.0", "confidence_min")
                if confidence_min < 0.0 or confidence_min > 1.0:
                    _knowledge_bad_request("Invalid confidence_min", "confidence_min must be a number between 0.0 and 1.0", "confidence_min")
            include_inactive = str(_query(request, "include_inactive", "false") or "false").lower() == "true"
            records = list(_call_port(port, "list_insight_cards") or [])
            filter_metadata = _insight_filter_metadata(records)
            filtered = list(records)
            if not include_inactive and status != "all":
                filtered = [item for item in filtered if str(item.get("status") or "") == status]
            if _query(request, "tag") is not None:
                tag = str(_query(request, "tag") or "")
                filtered = [item for item in filtered if tag in set(item.get("tags") or [])]
            if validated_entity_type:
                filtered = [
                    item for item in filtered if any(
                        str((source or {}).get("entity_type") or "") == validated_entity_type
                        for source in item.get("linked_sources") or []
                    )
                ]
            if linked_entity_ref is not None:
                filtered = [
                    item for item in filtered if any(
                        str((source or {}).get("entity_type") or "") == validated_entity_type
                        and str((source or {}).get("entity_ref") or "") == str(linked_entity_ref)
                        for source in item.get("linked_sources") or []
                    )
                ]
            if recency != "all":
                filtered = [
                    item for item in filtered if _within_recency(
                        item.get("aggregated_at") or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
                        recency,
                        snapshot_at,
                    )
                ]
            if confidence_min is not None:
                filtered = [
                    item for item in filtered if float(((item.get("confidence") or {}).get("score")) or 0.0) >= confidence_min
                ]
            available = getattr(port, "dataset_source", lambda _dataset: "missing")("insight_cards") != "missing"
            surface_state = _knowledge_surface_state("insight_cards", snapshot_at=snapshot_at, has_data=available)
            if surface_state == "unavailable":
                page_items, next_token, has_more = [], None, False
            else:
                page_items, next_token = _page(filtered, request)
                has_more = next_token is not None
            meta = snapshot_meta(snapshot_at)
            meta["surfaces"] = {"insight_cards": surface_state}
            return {
                "insight_cards": [_insight_list_item(item) for item in page_items],
                "filter_metadata": filter_metadata if available else {
                    "tags": [], "linked_entity_types": [],
                    "recency_options": _insight_filter_metadata([])["recency_options"],
                    "total_active_count": 0,
                },
                "pagination": {
                    "page_size": int(_query(request, "page_size", "20") or 20),
                    "next_page_token": next_token,
                    "has_more": has_more,
                },
                "meta": meta,
            }

        if name == "list_memory":
            records = list(_call_port(port, "list_institutional_memory_entries") or [])
            knowledge_type = _query(request, "knowledge_type")
            scope = _query(request, "scope")
            scope_filter = _query(request, "scope_filter")
            tags = _query(request, "tags")
            if knowledge_type:
                records = [item for item in records if str(item.get("knowledge_type") or "") == knowledge_type]
            if scope:
                records = [
                    item for item in records
                    if (
                        str(item.get("scope") or "") == scope
                        if not isinstance(item.get("scope"), dict)
                        else str((item.get("scope") or {}).get("type") or "") == scope
                    )
                ]
            if scope_filter:
                records = [
                    item for item in records
                    if str(item.get("scope_filter") or ((item.get("scope") or {}).get("filter") if isinstance(item.get("scope"), dict) else "")) == scope_filter
                ]
            if tags:
                requested_tags = {value.strip() for value in str(tags).split(",") if value.strip()}
                records = [item for item in records if requested_tags.intersection(set(item.get("tags") or []))]
            try:
                page_number = int(_query(request, "page", "1") or 1)
                page_size = int(_query(request, "page_size", "20") or 20)
            except (TypeError, ValueError):
                _knowledge_bad_request("Invalid pagination", "page and page_size must be integers", "page")
            page_size = max(1, min(page_size, 200))
            total_count = len(records)
            start = (page_number - 1) * page_size
            page_items = records[start : start + page_size]
            total_pages = max(1, (total_count + page_size - 1) // page_size)
            available = getattr(port, "dataset_source", lambda _dataset: "missing")("institutional_memory_entries") != "missing"
            surface_state = _knowledge_surface_state(
                "institutional_memory_entries", snapshot_at=snapshot_at, has_data=available,
                missing_message="Institutional memory list is unavailable.",
            )
            if surface_state == "unavailable":
                page_items, total_count, total_pages = [], 0, 0
            entries = []
            for item in page_items:
                if "headline" in item:
                    entries.append(item)
                    continue
                content = item.get("content") if isinstance(item.get("content"), dict) else {}
                scope_value = item.get("scope") if isinstance(item.get("scope"), dict) else {}
                lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
                usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
                entries.append({
                    "entry_id": item.get("entry_id") or item.get("id"),
                    "headline": content.get("headline"),
                    "knowledge_type": item.get("knowledge_type"),
                    "scope": scope_value.get("type"),
                    "scope_filter": scope_value.get("filter"),
                    "tags": list(content.get("tags") or item.get("tags") or []),
                    "reuse_count": int(usage.get("reuse_count") or 0),
                    "is_superseded": bool(lifecycle.get("superseded_by")),
                    "written_at": item.get("written_at"),
                    "write_authority": item.get("write_authority"),
                    "route_href": f"/knowledge/memory/{item.get('entry_id') or item.get('id')}",
                })
            meta = snapshot_meta(snapshot_at)
            meta["surfaces"] = {"memory_list": surface_state}
            return {
                "entries": entries,
                "pagination": {
                    "total_count": total_count,
                    "page": page_number,
                    "page_size": page_size,
                    "total_pages": total_pages,
                },
                "meta": meta,
            }

        if name in knowledge_operations:
            method, dataset = knowledge_operations[name]
            kwargs: Dict[str, Any] = {}
            if name == "list_notes":
                attachment_type = _query(request, "attachment_type")
                attachment_ref = _query(request, "attachment_ref")
                validated_attachment_type = (
                    _kw02_validate_attachment_type(attachment_type)
                    if attachment_type is not None
                    else None
                )
                if attachment_ref is not None and validated_attachment_type is None:
                    _kw02_bad_request(
                        "Invalid attachment_ref filter",
                        "attachment_ref requires attachment_type to be set",
                        "attachment_ref",
                    )
                validated_attachment_ref = (
                    _kw02_validate_attachment_ref(validated_attachment_type, attachment_ref)
                    if validated_attachment_type is not None and attachment_ref is not None
                    else None
                )
                notes = list(_call_port(port, method) or [])
                notes_dataset_available = (
                    getattr(port, "dataset_source", lambda _dataset: "missing")("research_notes")
                    != "missing"
                )
                owner_ref = _query(request, "owner_ref")
                if owner_ref:
                    notes = [
                        note
                        for note in notes
                        if str(((note.get("owner_ref") or {}).get("owner_id")) or "") == owner_ref
                    ]
                if validated_attachment_type:
                    notes = [
                        note
                        for note in notes
                        if str(note.get("attachment_type") or "") == validated_attachment_type
                    ]
                if validated_attachment_type == "free_standing" or validated_attachment_ref is not None:
                    notes = [
                        note
                        for note in notes
                        if note.get("attachment_ref") == validated_attachment_ref
                    ]
                tags = _query(request, "tags")
                if tags:
                    requested_tags = {value.strip() for value in tags.split(",") if value.strip()}
                    notes = [
                        note
                        for note in notes
                        if requested_tags.intersection(set(note.get("tags") or []))
                    ]
                surface_state = _kw02_surface_state(
                    port,
                    snapshot_at=snapshot_at,
                    has_data=notes_dataset_available,
                )
                if surface_state == "unavailable":
                    page_items, next_token, has_more = [], None, False
                else:
                    page_items, next_token = _page(notes, request)
                    has_more = next_token is not None
                meta = snapshot_meta(snapshot_at)
                meta["surfaces"] = {"research_note_list": surface_state}
                return {
                    "notes": [_kw02_note_list_item(port, note) for note in page_items],
                    "pagination": {
                        "page_size": int(_query(request, "page_size", "20") or 20),
                        "next_page_token": next_token,
                        "has_more": has_more,
                    },
                    "meta": meta,
                }
            if name == "list_strategy_specs":
                lifecycle_state = _kw05_validate_lifecycle_state(
                    _query(request, "lifecycle_state", "all")
                )
                kwargs = {
                    "lifecycle_state": lifecycle_state,
                    "source_kind": _query(request, "source_kind"),
                    "persona_id": _query(request, "persona_id"),
                    "include_retired": _query(request, "include_retired", "false") == "true",
                    "include_fixture_pack": False,
                }
                records = list(_call_port(port, method, **kwargs) or [])
                source_fn = getattr(port, "dataset_source", None)
                dataset_available = (
                    str(source_fn("strategy_specs") or "missing") != "missing"
                    if callable(source_fn)
                    else bool(records)
                )
                surface_state = _kw05_surface_state(
                    snapshot_at=snapshot_at,
                    has_data=dataset_available,
                )
                if surface_state == "unavailable":
                    items, next_token, has_more = [], None, False
                else:
                    items, next_token = _page(records, request)
                    has_more = next_token is not None
                meta = snapshot_meta(snapshot_at)
                meta["surfaces"] = {"strategy_spec_list": surface_state}
                return {
                    "items": items,
                    "page_info": {
                        "next_page_token": next_token,
                        "page_size": int(_query(request, "page_size", "20") or 20),
                        "has_more": has_more,
                    },
                    "meta": meta,
                }
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
            if name == "get_strategy_spec" and not _call_port(port, "get_strategy_spec", identifier):
                _not_found("Strategy spec", identifier)
            record = _call_port(port, method, identifier, **kwargs)
            if not record:
                if name == "get_strategy_spec":
                    _not_found("Strategy spec version", identifier)
                _not_found("Research record", identifier)
            if name == "get_evidence":
                return _evidence_detail_payload(
                    record, ref_id=identifier, identity=identity, snapshot_at=snapshot_at,
                )
            if name == "get_insight":
                return _insight_detail_payload(record, snapshot_at=snapshot_at)
            if name == "get_memory":
                return _memory_detail_payload(record, snapshot_at=snapshot_at)
            if name == "get_note":
                return _research_note_detail_payload(
                    port,
                    record,
                    snapshot_at=snapshot_at,
                )
            if name == "get_strategy_spec":
                detail_surface = _kw05_surface_state(
                    snapshot_at=snapshot_at,
                    has_data=True,
                )
                citation_bundle = json.loads(json.dumps(record.get("citation_bundle") or {}))
                citation_surface = "partial" if not any(citation_bundle.values()) else detail_surface
                ancestry_surface = (
                    "degraded"
                    if record.get("parent_spec_version_id") is None
                    and str(_query(request, "version", "current") or "").strip() not in {"", "current"}
                    else detail_surface
                )
                return {
                    "object_ref": json.loads(json.dumps(record.get("object_ref") or {})),
                    "strategy_id": record.get("strategy_id"),
                    "spec_version_id": record.get("spec_version_id"),
                    "spec_version": record.get("spec_version"),
                    "parent_spec_version_id": record.get("parent_spec_version_id"),
                    "derived_from_source_refs": list(record.get("derived_from_source_refs") or []),
                    "lifecycle_state": record.get("lifecycle_state"),
                    "title": record.get("title"),
                    "hypothesis": record.get("hypothesis"),
                    "objective": record.get("objective"),
                    "market_scope": json.loads(json.dumps(record.get("market_scope") or {})),
                    "execution_profile": json.loads(json.dumps(record.get("execution_profile") or {})),
                    "evaluation_plan": json.loads(json.dumps(record.get("evaluation_plan") or {})),
                    "governance": json.loads(json.dumps(record.get("governance") or {})),
                    "citation_bundle": citation_bundle,
                    "allowedActions": json.loads(json.dumps(record.get("allowedActions") or {})),
                    "meta": {
                        **snapshot_meta(snapshot_at),
                        "surfaces": {
                            "strategy_spec_detail": detail_surface,
                            "citation_bundle": citation_surface,
                            "version_ancestry": ancestry_surface,
                        },
                    },
                }
            payload = dict(record)
            payload.setdefault("meta", _meta(snapshot_at, name, dataset, True))
            return payload

        if name == "strategy_versions":
            strategy_id = str(params["strategy_id"])
            records = list(_call_port(port, "list_strategy_spec_versions", strategy_id) or [])
            if not records and not _call_port(port, "get_strategy_spec", strategy_id):
                _not_found("Strategy spec", strategy_id)
            return {
                "strategy_id": strategy_id,
                "versions": records,
                "meta": {
                    **snapshot_meta(snapshot_at),
                    "surfaces": {
                        "version_history": _kw05_surface_state(
                            snapshot_at=snapshot_at,
                            has_data=True,
                        )
                    },
                },
            }

        if name == "strategy_compare":
            strategy_id = str(params["strategy_id"])
            left, right = _kw05_compare_selectors(
                left_version=_query(request, "left_version"),
                right_version=_query(request, "right_version"),
                base_version=_query(request, "base_version"),
                target_version=_query(request, "target_version"),
            )
            if left == right:
                raise bff_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Compare requires two distinct versions",
                    "left_version and right_version must identify different versions",
                    precondition_failed="left_version",
                )
            left_detail = _call_port(
                port,
                "get_strategy_spec_detail",
                strategy_id,
                version_selector=left,
            )
            right_detail = _call_port(
                port,
                "get_strategy_spec_detail",
                strategy_id,
                version_selector=right,
            )
            if not left_detail or not right_detail:
                _not_found("Strategy spec version", strategy_id)
            if not (left_detail.get("allowedActions") or {}).get("canCompare") or not (
                right_detail.get("allowedActions") or {}
            ).get("canCompare"):
                raise bff_error(
                    422,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "One or more versions cannot be compared",
                    "Compare accepts only candidate, approved, or retired strategy spec versions",
                    precondition_failed="lifecycle_state",
                )
            comparison = _call_port(port, "compare_strategy_spec_versions", strategy_id, left_selector=left, right_selector=right)
            if not comparison:
                _not_found("Strategy spec version", strategy_id)
            payload = dict(comparison)
            payload["meta"] = {
                **snapshot_meta(snapshot_at),
                "surfaces": {
                    "strategy_spec_compare": _kw05_surface_state(
                        snapshot_at=snapshot_at,
                        has_data=True,
                    )
                },
            }
            return payload

        if name in {"synthesis_conflict_logs", "synthesis_conflict_log"}:
            raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
            if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Synthesis conflict log view disabled",
                    "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                    precondition_failed="synthesis_conflict_log_feature_flag",
                )
            list_reader = list_synthesis_conflict_logs
            get_reader = get_synthesis_conflict_log
            if name == "synthesis_conflict_logs":
                if list_reader is not None:
                    try:
                        records = list(list_reader(
                            capital_pool_id=_query(request, "capital_pool_id"),
                            scope_ref=_query(request, "scope_ref"),
                            proposal_id=_query(request, "proposal_id"),
                            sponsor_persona_id=_query(request, "sponsor_persona_id"),
                            synthesis_method=_query(request, "synthesis_method"),
                            committee_ref=_query(request, "committee_ref"),
                        ) or [])
                    except TypeError:
                        records = list(list_reader() or [])
                elif callable(getattr(port, "list_synthesis_conflict_logs", None)):
                    records = list(_call_port(
                        port,
                        "list_synthesis_conflict_logs",
                        capital_pool_id=_query(request, "capital_pool_id"),
                        scope_ref=_query(request, "scope_ref"),
                        proposal_id=_query(request, "proposal_id"),
                        sponsor_persona_id=_query(request, "sponsor_persona_id"),
                        synthesis_method=_query(request, "synthesis_method"),
                        committee_ref=_query(request, "committee_ref"),
                    ) or [])
                else:
                    raise bff_error(
                        503,
                        ErrorCode.DEPENDENCY_UNAVAILABLE,
                        "Synthesis conflict logs are unavailable",
                        "Inject list_synthesis_conflict_logs from the synthesis read adapter",
                    )
                items, next_token = _page(records, request)
                projected = [_conflict_view(item) for item in items]
                return {
                    "data": projected,
                    "items": projected,
                    "page_info": {"next_page_token": next_token, "total": len(records)},
                    "meta": _meta(snapshot_at, "synthesis_conflict_logs", "synthesis_conflict_logs", bool(records)),
                }
            log_id = str(params["log_id"])
            if get_reader is not None:
                record = get_reader(log_id)
            elif callable(getattr(port, "get_synthesis_conflict_log", None)):
                record = _call_port(port, "get_synthesis_conflict_log", log_id)
            else:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Synthesis conflict logs are unavailable",
                    "Inject get_synthesis_conflict_log from the synthesis read adapter",
                )
            if not record:
                _not_found("Synthesis conflict log", log_id)
            return {
                "data": _conflict_view(record),
                "meta": _meta(snapshot_at, "synthesis_conflict_log", "synthesis_conflict_logs", True),
            }

        if name == "bff_search":
            query = str(_query(request, "q", "") or "").strip()
            types_raw = _query(request, "types")
            requested_types = {item.strip().lower() for item in str(types_raw).split(",") if item.strip()} if types_raw else None
            effective_page_size = int(_query(request, "limit") or _query(request, "page_size", "20") or 20)
            effective_page_size = max(1, min(effective_page_size, 100))
            if cross_entity_search is not None:
                result = cross_entity_search(
                    query=query,
                    types=requested_types,
                    page_size=effective_page_size,
                    page_token=_query(request, "page_token"),
                    identity=identity,
                )
                result = await result if inspect.isawaitable(result) else result
                if isinstance(result, dict):
                    return result
                records = list(result or [])
            else:
                records = []
                needle = query.lower()

                def _matches(value: Any) -> bool:
                    return not needle or needle in str(value or "").lower()

                if not requested_types or "strategy" in requested_types:
                    strategy_reader = getattr(port, "list_strategies", None) or getattr(port, "list_strategy_summaries", None)
                    if callable(strategy_reader):
                        for raw in strategy_reader() or []:
                            item_id = str(raw.get("strategy_id") or raw.get("id") or "")
                            name_value = raw.get("title") or raw.get("name") or item_id
                            if _matches(item_id) or _matches(name_value):
                                records.append({"id": item_id, "type": "strategy", "name": str(name_value), "state": raw.get("lifecycle_state") or raw.get("status"), "owner": raw.get("owner") or "pantheon-bff", "risk": "medium", "updatedAt": raw.get("updated_at") or raw.get("last_modified_at") or snapshot_at})
                if not requested_types or "persona" in requested_types:
                    persona_reader = getattr(port, "list_personas", None)
                    if callable(persona_reader):
                        for raw in persona_reader() or []:
                            item_id = str(raw.get("persona_id") or raw.get("id") or "")
                            name_value = raw.get("name") or item_id
                            if _matches(item_id) or _matches(name_value):
                                metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                                records.append({"id": item_id, "type": "persona", "name": str(name_value), "state": raw.get("lifecycle_state") or raw.get("status"), "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff", "risk": metadata.get("risk_level") or "medium", "updatedAt": raw.get("updated_at") or raw.get("created_at") or snapshot_at})
                if not requested_types or "capital_pool" in requested_types or "capitalpool" in requested_types:
                    pool_reader = getattr(port, "list_capital_pools", None)
                    if callable(pool_reader):
                        for raw in pool_reader() or []:
                            item_id = str(raw.get("pool_id") or raw.get("id") or "")
                            name_value = raw.get("name") or item_id
                            if _matches(item_id) or _matches(name_value):
                                records.append({"id": item_id, "type": "capital_pool", "name": str(name_value), "state": raw.get("status"), "owner": raw.get("owner") or "pantheon-bff", "risk": raw.get("risk_level") or "medium", "updatedAt": raw.get("updated_at") or raw.get("created_at") or snapshot_at})
            items, next_token = _page(records, request)
            return {"data": items, "items": items, "page_info": {"next_page_token": next_token, "total": len(records), "returned": len(items)}, "meta": _meta(snapshot_at, "search", "personas", bool(records))}

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
        from ..console_gap.knowledge import create_knowledge_router

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

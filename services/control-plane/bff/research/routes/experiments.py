"""Research experiments routes and canonical experiments subrouter."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Header, Query, Request

from .common import (
    PageSlice,
    ResearchRouteContext,
    SnapshotMeta,
    SubmitAction,
    SurfaceStatus,
    _RESEARCH_EXPERIMENT_IDEMPOTENCY,
    _authorization,
    _body_parameter,
    _default_page_slice,
    _default_snapshot_meta,
    _default_surface_status,
    _filter_by_status_csv,
    _path,
    _signature,
    _signature_query,
)

try:
    from services.control_plane.bff.models import ErrorCode, ObjectType
except (ImportError, ValueError):
    from ..models import ErrorCode, ObjectType

_EXPERIMENT_STATUSES = {"queued", "running", "completed", "failed", "canceled"}
_EXPERIMENT_EXECUTION_MODES = {"paper", "backtest", "simulation"}
_EXPERIMENT_PRIORITIES = {"normal", "high"}


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
    """Build the canonical Research Experiments router."""
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
            rks = getattr(read_store, "research_knowledge_source", None)
            if rks and hasattr(rks, "get_research_experiment"):
                item = rks.get_research_experiment(clean_id)
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
        identity = extract_identity(authorization)
        require_operator_role(identity)
        resolved_key = (idempotency_key or x_idempotency_key or "").strip()
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
        if hasattr(read_store, "create_experiment_bff"):
            result = read_store.create_experiment_bff(
                name=name,
                actor_id=identity.operator_id,
                created_at=utc_now(),
                params=payload,
            )
        else:
            rks = getattr(read_store, "research_knowledge_source", None)
            creator = getattr(rks, "create_research_experiment", None) if rks else None
            if creator is None:
                try:
                    from services.research.write_owner import build_research_write_owner
                    creator = getattr(build_research_write_owner(), "create_research_experiment", None)
                except Exception:
                    creator = None
            if creator is None:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Research experiment write owner unavailable",
                    "Cannot execute create_research_experiment mutation",
                )
            result = creator(
                ticket_id=str(payload.get("ticket_id") or ""),
                experiment_name=name,
                strategy_selector=payload.get("strategy_selector") or {},
                parameter_set=payload.get("parameter_set") or {},
                run_config=payload.get("run_config") or {},
                launch_context=payload.get("launch_context") or {"actor_id": identity.operator_id},
                queued_at=utc_now(),
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
        logs_fn = getattr(read_store, "get_experiment_logs", None)
        if logs_fn is None:
            rks = getattr(read_store, "research_knowledge_source", None)
            logs_fn = getattr(rks, "get_experiment_logs", None) if rks else None
        logs = logs_fn(clean_id) if callable(logs_fn) else []
        return {
            "experiment_id": clean_id,
            "logs": logs,
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
        metrics_fn = getattr(read_store, "get_experiment_metrics", None)
        if metrics_fn is None:
            rks = getattr(read_store, "research_knowledge_source", None)
            metrics_fn = getattr(rks, "get_experiment_metrics", None) if rks else None
        metrics = metrics_fn(clean_id) if callable(metrics_fn) else {}
        return {
            "experiment_id": clean_id,
            "metrics": metrics,
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
        artifacts_fn = getattr(read_store, "get_experiment_artifacts", None)
        if artifacts_fn is None:
            rks = getattr(read_store, "research_knowledge_source", None)
            artifacts_fn = getattr(rks, "get_experiment_artifacts", None) if rks else None
        artifacts = artifacts_fn(clean_id) if callable(artifacts_fn) else []
        return {
            "experiment_id": clean_id,
            "artifacts": artifacts,
            "meta": snapshot_meta(snapshot_at),
        }

    @router.get("/bff/research-experiments")
    async def list_research_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
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


def build_experiments_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    def _validate_experiment_status(value: Any) -> str:
        return ctx.validate_choice(
            value,
            field="status",
            label="experiment status",
            allowed=_EXPERIMENT_STATUSES,
        )

    def _legacy_experiment_surface_state(*, snapshot_at: str, has_data: bool) -> str:
        port = ctx.get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_experiments") or "missing") if callable(source_fn) else "missing"
        if source == "missing" and has_data:
            source = "bff_local"
        surface = ctx.dataset_surface_status(
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

    def _validate_experiment_launch(payload: Dict[str, Any]) -> Dict[str, Any]:
        run_config = ctx.required_dict(payload, "run_config")
        time_range = ctx.required_dict(run_config, "time_range")
        validated_run_config = {
            "dataset_ref": ctx.required_text(run_config, "dataset_ref"),
            "time_range": {
                "start_at": ctx.required_text(time_range, "start_at"),
                "end_at": ctx.required_text(time_range, "end_at"),
            },
            "execution_mode": ctx.validate_choice(
                run_config.get("execution_mode"),
                field="execution_mode",
                label="execution_mode",
                allowed=_EXPERIMENT_EXECUTION_MODES,
            ),
            "priority": ctx.validate_choice(
                run_config.get("priority", "normal"),
                field="priority",
                label="priority",
                allowed=_EXPERIMENT_PRIORITIES,
            ),
            "requested_by": ctx.required_text(run_config, "requested_by"),
        }
        launch_context_raw = payload.get("launch_context") or {}
        if not isinstance(launch_context_raw, dict):
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid launch_context",
                "launch_context must be an object when provided",
                precondition_failed="launch_context",
            )
        analysis_refs = launch_context_raw.get("analysis_refs")
        if analysis_refs is not None and not isinstance(analysis_refs, list):
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid launch_context.analysis_refs",
                "analysis_refs must be null or an array of strings",
                precondition_failed="launch_context.analysis_refs",
            )
        return {
            "ticket_id": ctx.required_text(payload, "ticket_id"),
            "experiment_name": ctx.required_text(payload, "experiment_name"),
            "strategy_selector": ctx.required_dict(payload, "strategy_selector"),
            "parameter_set": ctx.required_dict(payload, "parameter_set"),
            "run_config": validated_run_config,
            "launch_context": {"analysis_refs": list(analysis_refs) if analysis_refs is not None else None},
        }

    async def endpoint_launch_experiment(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        payload = _validate_experiment_launch(await ctx.body(request))
        experiment = ctx.call_port(
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

    async def endpoint_list_experiments_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        raw_status = ctx.query(request, "status")
        status = _validate_experiment_status(raw_status) if raw_status is not None else None
        records = list(ctx.call_port(port, "list_research_experiments", ticket_id=ctx.query(request, "ticket_id"), status=status) or [])
        surface_state = _legacy_experiment_surface_state(
            snapshot_at=snapshot_at, has_data=bool(records)
        )
        if surface_state == "unavailable":
            items, next_token, total = [], None, 0
        else:
            page_items, next_token = ctx.page(records, request)
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
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"experiment_history": surface_state}
        return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}

    async def endpoint_get_experiment_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        experiment_id = str(request.path_params.get("experiment_id") or "")
        experiment = ctx.call_port(port, "get_research_experiment", experiment_id)
        if not experiment:
            ctx.not_found("Experiment", experiment_id)
        payload = dict(experiment)
        ticket_id = str(payload.get("ticket_id") or "")
        payload["links"] = {"self": f"/api/v1/experiments/{experiment_id}", "workbench_detail": f"/research/experiments/{experiment_id}", "linked_ticket_detail": f"/research/tickets/{ticket_id}"}
        payload["meta"] = ctx.meta(snapshot_at, "experiment_status", "research_experiments", True)
        return payload

    async def endpoint_cancel_experiment_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        experiment_id = str(request.path_params.get("experiment_id") or "")
        experiment = ctx.call_port(port, "get_research_experiment", experiment_id)
        if not experiment:
            ctx.not_found("Experiment", experiment_id)
        ctx.required_text(await ctx.body(request), "reason")
        if str(experiment.get("status") or "") not in {"queued", "running"}:
            raise ctx.bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Experiment cannot be canceled",
                f"Experiment {experiment_id} is in terminal state '{experiment.get('status')}' and cannot be canceled",
            )
        canceled = ctx.call_port(port, "cancel_research_experiment", experiment_id, completed_at=snapshot_at)
        if not canceled:
            raise ctx.bff_error(409, ErrorCode.OPERATION_NOT_ALLOWED, "Experiment cancel rejected", "Experiment could not be canceled")
        return {"experiment_id": experiment_id, "status": canceled.get("status"), "completed_at": canceled.get("completed_at"), "allowedActions": {"canCancel": False}}

    auth = _authorization()
    endpoint_launch_experiment.__signature__ = _signature(_body_parameter(), auth)
    endpoint_list_experiments_api.__signature__ = _signature(
        _signature_query("ticket_id"), _signature_query("status"), _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_experiment_api.__signature__ = _signature(_path("experiment_id"), auth)
    endpoint_cancel_experiment_api.__signature__ = _signature(_path("experiment_id"), _body_parameter(), auth)

    router.add_api_route("/api/v1/experiments/launch", endpoint_launch_experiment, methods=["POST"], name="launch_experiment")
    router.add_api_route("/api/v1/experiments", endpoint_list_experiments_api, methods=["GET"], name="list_experiments_api")
    router.add_api_route("/api/v1/experiments/{experiment_id}", endpoint_get_experiment_api, methods=["GET"], name="get_experiment_api")
    router.add_api_route("/api/v1/experiments/{experiment_id}/cancel", endpoint_cancel_experiment_api, methods=["POST"], name="cancel_experiment_api")

    return router

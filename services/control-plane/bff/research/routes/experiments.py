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

try:
    from services.control_plane.bff.ports.research_knowledge_source import (
        ResearchWriteOwnerUnavailableError,
    )
except (ImportError, ValueError):
    from ..ports.research_knowledge_source import ResearchWriteOwnerUnavailableError  # type: ignore[no-redef]

try:
    from services.control_plane.bff.research.service import ResearchRouterService
except (ImportError, ValueError):
    from ..service import ResearchRouterService  # type: ignore[no-redef]


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
    service: Optional[ResearchRouterService] = None,
) -> APIRouter:
    """Build the canonical Research Experiments router."""
    if service is None:
        if read_surface is not None:
            get_read_store = (lambda: read_surface() if callable(read_surface) else read_surface)
        elif get_read_store is None:
            raise RuntimeError("Neither read_surface nor get_read_store was configured.")

        service = ResearchRouterService(
            port_getter=get_read_store,
            utc_now=utc_now,
            snapshot_meta=snapshot_meta,
            page_slice=page_slice,
            bff_error=bff_error,
            dataset_surface_status=dataset_surface_status,
        )

    router = APIRouter()

    @router.get("/bff/experiments")
    async def list_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.list_experiments_bff(
            status=status,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=utc_now(),
        )

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
        actor_id = getattr(identity, "operator_id", None) or getattr(identity, "user_id", None) or str(identity)
        tenant_id = getattr(identity, "tenant_id", None)
        return service.create_experiment(
            payload,
            actor_id=str(actor_id),
            tenant_id=str(tenant_id).strip() if tenant_id else None,
            idempotency_key=resolved_key or None,
        )

    @router.get("/bff/experiments/{experiment_id}")
    async def get_experiment(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.get_experiment_bff(experiment_id, snapshot_at=utc_now())

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
        clean_id = experiment_id.strip()
        service.require_experiment(clean_id)
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
        return service.get_experiment_logs(experiment_id, snapshot_at=utc_now())

    @router.get("/bff/experiments/{experiment_id}/metrics")
    async def get_experiment_metrics(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.get_experiment_metrics(experiment_id, snapshot_at=utc_now())

    @router.get("/bff/experiments/{experiment_id}/artifacts")
    async def get_experiment_artifacts(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.get_experiment_artifacts(experiment_id, snapshot_at=utc_now())

    @router.get("/bff/research-experiments")
    async def list_research_experiments(
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.list_research_experiments_bff(
            status=status,
            page_token=page_token,
            page_size=page_size,
            snapshot_at=utc_now(),
        )

    @router.get("/bff/research-experiments/{experiment_id}")
    async def get_research_experiment(
        experiment_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        identity = extract_identity(authorization)
        require_read_role(identity)
        return service.get_research_experiment_bff(experiment_id, snapshot_at=utc_now())

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
        identity = ctx.identity(request)
        idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
        actor_id = (
            getattr(identity, "operator_id", None)
            or getattr(identity, "user_id", None)
            or getattr(identity, "actor_id", None)
            or str(identity)
        )
        tenant_id = getattr(identity, "tenant_id", None)
        payload = _validate_experiment_launch(await ctx.body(request))
        return ctx.service.launch_experiment(
            payload,
            actor_id=str(actor_id).strip() if actor_id else None,
            tenant_id=str(tenant_id).strip() if tenant_id else None,
            idempotency_key=str(idempotency_key).strip() if idempotency_key else None,
        )

    async def endpoint_list_experiments_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        raw_status = ctx.query(request, "status")
        status = _validate_experiment_status(raw_status) if raw_status is not None else None
        return ctx.service.list_experiments_api(
            ticket_id=ctx.query(request, "ticket_id"),
            status=status,
            page_token=ctx.query(request, "page_token"),
            page_size=int(ctx.query(request, "page_size") or 20),
            snapshot_at=ctx.utc_now(),
        )

    async def endpoint_get_experiment_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        experiment_id = str(request.path_params.get("experiment_id") or "")
        return ctx.service.get_experiment_api(experiment_id, snapshot_at=ctx.utc_now())

    async def endpoint_cancel_experiment_api(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        experiment_id = str(request.path_params.get("experiment_id") or "")
        reason = ctx.required_text(await ctx.body(request), "reason")
        return ctx.service.cancel_experiment_api(experiment_id, reason=reason, snapshot_at=ctx.utc_now())

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

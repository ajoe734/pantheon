"""Research orchestrator tasks and runs read-only BFF routes.

Reads research orchestrator tasks and their execution runs over HTTP from the
research orchestrator owner service using the same verified caller auth and
tenant helper as ``ports/job_read.py``. Never reads orchestrator files on disk
and never merges research tasks into ``research_experiments``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Header, Query

from .common import (
    PageSlice,
    SnapshotMeta,
    SurfaceStatus,
    _default_page_slice,
    _default_snapshot_meta,
    _default_surface_status,
)

try:
    from services.control_plane.bff.auth.policy import (
        bff_error as _policy_bff_error,
        extract_identity as _policy_extract_identity,
        require_read_role as _policy_require_read_role,
    )
    from services.control_plane.bff.core.owner_reads import (
        authorization as _owner_authorization,
        selected_tenant as _owner_selected_tenant,
        verified_caller_tenant,
    )
    from services.control_plane.bff.models import ErrorCode
    from services.control_plane.bff.ports.job_read import (
        HttpGetFn,
        _default_http_get,
        _extract_detail,
    )
    from services.control_plane.bff.research.service import ResearchRouterService
except (ImportError, ValueError):
    from ...auth.policy import (
        bff_error as _policy_bff_error,
        extract_identity as _policy_extract_identity,
        require_read_role as _policy_require_read_role,
    )
    from ...core.owner_reads import (
        authorization as _owner_authorization,
        selected_tenant as _owner_selected_tenant,
        verified_caller_tenant,
    )
    from ...models import ErrorCode
    from ...ports.job_read import (
        HttpGetFn,
        _default_http_get,
        _extract_detail,
    )
    from ..service import ResearchRouterService  # type: ignore[no-redef]

_ORCHESTRATOR_URL_ENVS: Tuple[str, ...] = (
    "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
    "RESEARCH_ORCHESTRATOR_URL",
    "RESEARCH_ORCHESTRATOR_API_URL",
)


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_orchestrator_base_url(explicit_base_url: Optional[str] = None) -> Optional[str]:
    if explicit_base_url and str(explicit_base_url).strip():
        return str(explicit_base_url).strip().rstrip("/")
    for env_name in _ORCHESTRATOR_URL_ENVS:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _extract_collection(payload: Any, keys: Tuple[str, ...]) -> Optional[List[Dict[str, Any]]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return None


def _extract_task_tenant(task: Dict[str, Any]) -> Optional[str]:
    top_level = str(task.get("tenant_id") or "").strip()
    if top_level:
        return top_level
    constraints = task.get("constraints")
    if isinstance(constraints, dict):
        nested = str(constraints.get("tenant_id") or "").strip()
        if nested:
            return nested
    return None


def _resolve_task_name(task: Dict[str, Any], task_id: str) -> str:
    for candidate in (task.get("title"), task.get("objective"), task_id):
        text = str(candidate or "").strip()
        if text:
            return text
    return task_id


def _run_sort_key(run: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(run.get("created_at") or ""),
        str(run.get("updated_at") or ""),
        str(run.get("run_id") or run.get("id") or ""),
    )


def _project_latest_run(run: Dict[str, Any]) -> Dict[str, Any]:
    run_id = str(run.get("run_id") or run.get("id") or "").strip()
    return {
        "run_id": run_id,
        "adapter": run.get("adapter"),
        "dispatch_mode": run.get("dispatch_mode"),
        "status": run.get("status"),
        "requested_mode": run.get("requested_mode"),
        "created_at": run.get("created_at"),
    }


def _project_run_detail(run: Dict[str, Any]) -> Dict[str, Any]:
    run_id = str(run.get("run_id") or run.get("id") or "").strip()
    projected = dict(run)
    projected["id"] = str(run.get("id") or run_id).strip()
    projected["run_id"] = run_id
    projected["adapter"] = run.get("adapter")
    projected["dispatch_mode"] = run.get("dispatch_mode")
    projected["status"] = run.get("status")
    return projected


def _project_task_item(
    task: Dict[str, Any],
    task_runs: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("id") or "").strip()
    item_id = str(task.get("id") or task_id).strip()
    ordered_runs = sorted(task_runs, key=_run_sort_key)
    latest_run = _project_latest_run(ordered_runs[-1]) if ordered_runs else None
    return {
        "id": item_id,
        "task_id": task_id,
        "name": _resolve_task_name(task, task_id),
        "title": task.get("title"),
        "objective": task.get("objective"),
        "status": task.get("status"),
        "task_type": task.get("task_type"),
        "created_by": task.get("created_by"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "source_refs": list(task.get("source_refs") or []),
        "run_count": len(ordered_runs),
        "latest_run": latest_run,
        **({"query_plan": task["query_plan"]} if task.get("task_type") == "collector_query_plan" else {}),
    }


def _project_task_detail(
    task: Dict[str, Any],
    task_runs: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    ordered_runs = sorted(task_runs, key=_run_sort_key)
    item = _project_task_item(task, ordered_runs)
    return {
        **item,
        "constraints": task.get("constraints") if isinstance(task.get("constraints"), dict) else {},
        "idempotency_key": task.get("idempotency_key"),
        "runs": [_project_run_detail(run) for run in ordered_runs],
    }


def create_research_tasks_router(
    *,
    service: Optional[ResearchRouterService] = None,
    read_store: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    extract_identity: Callable[..., Any] = _policy_extract_identity,
    require_read_role: Callable[[Any], None] = _policy_require_read_role,
    require_operator_role: Optional[Callable[[Any], None]] = None,
    bff_error: Callable[..., Exception] = _policy_bff_error,
    utc_now: Callable[[], str] = _default_utc_now,
    page_slice: PageSlice = _default_page_slice,
    snapshot_meta: SnapshotMeta = _default_snapshot_meta,
    dataset_surface_status: SurfaceStatus = _default_surface_status,
    build_experiment_summary: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    get_capabilities: Optional[Callable[[Any], Any]] = None,
    http_get: HttpGetFn = _default_http_get,
    base_url: Optional[str] = None,
) -> APIRouter:
    del read_store, get_read_store, require_operator_role, dataset_surface_status, build_experiment_summary, get_capabilities

    router = APIRouter()
    page_slice_fn = service.page_slice if service is not None and hasattr(service, "page_slice") else page_slice
    snapshot_meta_fn = service.snapshot_meta if service is not None and hasattr(service, "snapshot_meta") else snapshot_meta
    utc_now_fn = service.utc_now if service is not None and hasattr(service, "utc_now") else utc_now

    def _build_meta(
        snapshot_at: str,
        *,
        status: str,
        source: str,
        excluded_unscoped_count: int = 0,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        base = dict(snapshot_meta_fn(snapshot_at) or {"snapshot_at": snapshot_at})
        surfaces = dict(base.get("surfaces") or {})
        surface_payload: Dict[str, Any] = {
            "status": status,
            "source": source,
            "snapshot_at": snapshot_at,
            "excluded_unscoped_count": excluded_unscoped_count,
        }
        if message:
            surface_payload["message"] = message
        surfaces["research_tasks"] = surface_payload
        base["surfaces"] = surfaces
        base["status"] = status
        base["excluded_unscoped_count"] = excluded_unscoped_count
        return base

    def _not_found(task_id: str) -> Exception:
        return bff_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            "Research task not found",
            f"Research task {task_id} does not exist",
        )

    @router.get("/bff/research-tasks")
    def list_research_tasks(
        status: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        auth_ctx = (
            _owner_authorization.set(authorization)
            if _owner_authorization.get() is None and authorization is not None
            else None
        )
        tenant_ctx = (
            _owner_selected_tenant.set(x_tenant_id)
            if _owner_selected_tenant.get() is None and x_tenant_id is not None
            else None
        )
        try:
            identity = extract_identity(authorization)
            require_read_role(identity)
            caller_tenant = verified_caller_tenant()
            snapshot_at = utc_now_fn()

            resolved_base_url = _resolve_orchestrator_base_url(base_url)
            if not resolved_base_url:
                return {
                    "data": [],
                    "items": [],
                    "tasks": [],
                    "next_page_token": None,
                    "page_info": {"next_page_token": None, "page_size": page_size, "total": 0},
                    "meta": _build_meta(
                        snapshot_at,
                        status="unavailable",
                        source="unconfigured",
                        excluded_unscoped_count=0,
                        message="Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL).",
                    ),
                }

            tasks_ok, tasks_payload = http_get(f"{resolved_base_url}/api/research-orchestrator/tasks")
            raw_tasks = (
                _extract_collection(tasks_payload, ("items", "data", "tasks", "results"))
                if tasks_ok
                else None
            )
            if not tasks_ok or raw_tasks is None:
                return {
                    "data": [],
                    "items": [],
                    "tasks": [],
                    "next_page_token": None,
                    "page_info": {"next_page_token": None, "page_size": page_size, "total": 0},
                    "meta": _build_meta(
                        snapshot_at,
                        status="unavailable",
                        source="unavailable",
                        excluded_unscoped_count=0,
                        message="Research orchestrator tasks endpoint is unavailable.",
                    ),
                }

            runs_ok, runs_payload = http_get(f"{resolved_base_url}/api/research-orchestrator/runs")
            raw_runs = (
                _extract_collection(runs_payload, ("items", "data", "runs", "results"))
                if runs_ok
                else None
            )
            if not runs_ok or raw_runs is None:
                return {
                    "data": [],
                    "items": [],
                    "tasks": [],
                    "next_page_token": None,
                    "page_info": {"next_page_token": None, "page_size": page_size, "total": 0},
                    "meta": _build_meta(
                        snapshot_at,
                        status="unavailable",
                        source="unavailable",
                        excluded_unscoped_count=0,
                        message="Research orchestrator runs endpoint is unavailable.",
                    ),
                }

            runs_by_task: Dict[str, List[Dict[str, Any]]] = {}
            for raw_run in raw_runs:
                run_task_id = str(raw_run.get("task_id") or "").strip()
                if run_task_id:
                    runs_by_task.setdefault(run_task_id, []).append(raw_run)

            allowed_statuses = (
                {item.strip() for item in str(status).split(",") if item.strip()}
                if status is not None and str(status).strip()
                else None
            )

            excluded_unscoped_count = 0
            scoped_items: List[Dict[str, Any]] = []
            for raw_task in raw_tasks:
                task_id = str(raw_task.get("task_id") or raw_task.get("id") or "").strip()
                if not task_id:
                    continue
                task_tenant = _extract_task_tenant(raw_task)
                if task_tenant is None:
                    excluded_unscoped_count += 1
                    continue
                if task_tenant != caller_tenant:
                    continue
                if allowed_statuses is not None and str(raw_task.get("status") or "") not in allowed_statuses:
                    continue
                scoped_items.append(_project_task_item(raw_task, runs_by_task.get(task_id, ())))

            scoped_items.sort(
                key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")),
                reverse=True,
            )
            page_items, next_page_token = page_slice_fn(scoped_items, page_token, page_size)
            return {
                "data": page_items,
                "items": page_items,
                "tasks": page_items,
                "next_page_token": next_page_token,
                "page_info": {
                    "next_page_token": next_page_token,
                    "page_size": page_size,
                    "total": len(scoped_items),
                },
                "meta": _build_meta(
                    snapshot_at,
                    status="ok",
                    source="research_orchestrator",
                    excluded_unscoped_count=excluded_unscoped_count,
                ),
            }
        finally:
            if tenant_ctx is not None:
                _owner_selected_tenant.reset(tenant_ctx)
            if auth_ctx is not None:
                _owner_authorization.reset(auth_ctx)

    @router.get("/bff/research-tasks/{task_id}")
    def get_research_task(
        task_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        auth_ctx = (
            _owner_authorization.set(authorization)
            if _owner_authorization.get() is None and authorization is not None
            else None
        )
        tenant_ctx = (
            _owner_selected_tenant.set(x_tenant_id)
            if _owner_selected_tenant.get() is None and x_tenant_id is not None
            else None
        )
        try:
            identity = extract_identity(authorization)
            require_read_role(identity)
            caller_tenant = verified_caller_tenant()
            snapshot_at = utc_now_fn()

            clean_task_id = str(task_id or "").strip()
            if not clean_task_id:
                raise _not_found(clean_task_id)

            resolved_base_url = _resolve_orchestrator_base_url(base_url)
            if not resolved_base_url:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Research orchestrator is unavailable",
                    "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL).",
                )

            encoded_task_id = quote(clean_task_id, safe="")
            task_ok, task_payload = http_get(
                f"{resolved_base_url}/api/research-orchestrator/tasks/{encoded_task_id}"
            )
            if not task_ok:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Research orchestrator is unavailable",
                    f"Research orchestrator task read failed for {clean_task_id}.",
                )

            raw_task = _extract_detail(task_payload)
            resolved_id = (
                str(raw_task.get("task_id") or raw_task.get("id") or "").strip()
                if isinstance(raw_task, dict)
                else ""
            )
            if not isinstance(raw_task, dict) or not resolved_id:
                raise _not_found(clean_task_id)

            task_tenant = _extract_task_tenant(raw_task)
            if task_tenant is None or task_tenant != caller_tenant:
                raise _not_found(clean_task_id)

            runs_ok, runs_payload = http_get(
                f"{resolved_base_url}/api/research-orchestrator/runs?task_id={encoded_task_id}"
            )
            raw_runs = (
                _extract_collection(runs_payload, ("items", "data", "runs", "results"))
                if runs_ok
                else None
            )
            if not runs_ok or raw_runs is None:
                raise bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Research orchestrator is unavailable",
                    f"Research orchestrator runs read failed for {clean_task_id}.",
                )

            task_runs = [
                run
                for run in raw_runs
                if str(run.get("task_id") or "").strip() == resolved_id
            ]
            detail = _project_task_detail(raw_task, task_runs)
            return {
                **detail,
                "data": detail,
                "task": detail,
                "meta": _build_meta(
                    snapshot_at,
                    status="ok",
                    source="research_orchestrator",
                ),
            }
        finally:
            if tenant_ctx is not None:
                _owner_selected_tenant.reset(tenant_ctx)
            if auth_ctx is not None:
                _owner_authorization.reset(auth_ctx)

    return router

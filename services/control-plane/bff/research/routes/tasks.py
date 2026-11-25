"""Research orchestrator tasks and runs read-only BFF routes.

Reads research orchestrator tasks and their execution runs over HTTP from the
research orchestrator owner service using the same verified caller auth and
tenant helper as ``ports/job_read.py``. Never reads orchestrator files on disk
and never merges research tasks into ``research_experiments``.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple
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
    from services.control_plane.bff.strategies.routes.common import (
        PersonaStateLookup,
        STUB_TEST_PERSONA_PROBE,
        read_persona_state_lookup,
        strategy_stub_reason,
    )
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
    from ...strategies.routes.common import (  # type: ignore[no-redef]
        PersonaStateLookup,
        STUB_TEST_PERSONA_PROBE,
        read_persona_state_lookup,
        strategy_stub_reason,
    )

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


_NON_REAL_RUN_MODES = {"stub": "stub_run", "manual": "manual_record"}
_NON_REAL_DATA_SCOPES = {"test", "acceptance", "synthetic"}
_COLLECTOR_QUERY_PLAN = "collector_query_plan"


def _is_blank_name(task: Dict[str, Any]) -> bool:
    return not any(str(task.get(k) or "").strip() for k in ("title", "objective"))


def _first_reference(values: Any, *, dataset_only: bool = False) -> Optional[str]:
    if isinstance(values, (list, tuple)):
        for value in values:
            if isinstance(value, dict):
                if dataset_only and value.get("type") not in ("dataset", "dataset_ref", "dataset_version", "dataset_version_id"):
                    continue
                value = value.get("id") or value.get("dataset_id") or value.get("ref") or value.get("uri")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _classify_non_real(
    task: Dict[str, Any],
    latest_run: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Return non_real_reason for acceptance/stub/manual records, else None."""
    title = str(task.get("title") or "").strip()
    if title.upper().startswith("ACCEPTANCE TEST"):
        return "acceptance_test"
    constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {}
    scopes = [task.get("data_scope"), constraints.get("data_scope")]
    if isinstance(latest_run, dict):
        scopes.append(latest_run.get("data_scope"))
        params = latest_run.get("parameters")
        if isinstance(params, dict):
            scopes.append(params.get("data_scope"))
    if any(str(sc or "").strip().lower() in _NON_REAL_DATA_SCOPES for sc in scopes):
        return "acceptance_test"
    if isinstance(latest_run, dict):
        for key in ("adapter", "dispatch_mode"):
            reason = _NON_REAL_RUN_MODES.get(str(latest_run.get(key) or "").strip().lower())
            if reason:
                return reason
    return None


def _build_display_summary(
    task: Dict[str, Any],
    latest_run: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Structured summary from recorded fields only; missing values stay null."""
    constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {}
    params = (
        latest_run.get("parameters")
        if isinstance(latest_run, dict) and isinstance(latest_run.get("parameters"), dict)
        else {}
    )
    symbol = params.get("symbol") or constraints.get("symbol")
    dataset_ref = (
        _first_reference(latest_run.get("input_refs") if isinstance(latest_run, dict) else None, dataset_only=True)
        or (str(constraints.get("dataset_ref")).strip() if constraints.get("dataset_ref") else None)
    )
    return {
        "task_kind": task.get("task_type") or None,
        "subject_ref": _first_reference(task.get("source_refs")),
        "symbol": str(symbol).strip() if symbol else None,
        "dataset_ref": dataset_ref,
    }


_TEMPLATE_SEARCH_ACTION = "template_search"
_RAPID_EVAL = "rapid_eval"
_DEMANDS_PATH = "/api/research/persona-demands?status=open"


def _is_template_search_run(run: Dict[str, Any]) -> bool:
    params = run.get("parameters") if isinstance(run.get("parameters"), dict) else {}
    return params.get("action_type") == _TEMPLATE_SEARCH_ACTION and bool(str(params.get("demand_id") or "").strip())


def _run_reason_code(run: Dict[str, Any]) -> Optional[str]:
    """Reason recorded on a template-search run: the failure detail, else the search outcome."""
    rejection = run.get("rejection") if isinstance(run.get("rejection"), dict) else {}
    result = run.get("template_search") if isinstance(run.get("template_search"), dict) else {}
    for candidate in (rejection.get("detail"), result.get("reason")):
        text = str(candidate or "").strip()
        if text:
            return text
    return None


def _demand_join(
    task_runs: Sequence[Dict[str, Any]],
    all_runs_by_demand: Dict[str, List[Dict[str, Any]]],
    demands_by_id: Optional[Dict[str, Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Demand object for a template-search task, from recorded run fields only."""
    search_runs = sorted((r for r in task_runs if _is_template_search_run(r)), key=_run_sort_key)
    if not search_runs:
        return None
    latest = search_runs[-1]
    params = latest["parameters"]
    demand_id = str(params["demand_id"]).strip()
    attempts = all_runs_by_demand.get(demand_id) or search_runs
    demand = (demands_by_id or {}).get(demand_id)
    return {
        "demand_id": demand_id,
        "persona_id": params.get("persona_id"),
        "attempt_count": len(attempts),
        "last_reason_code": _run_reason_code(latest),
        "short_measure": demand.get("short_measure") if demand else None,
        "status": demand.get("status") if demand else None,
    }


def _project_demand(demand: Dict[str, Any], lookup: Optional[PersonaStateLookup]) -> Dict[str, Any]:
    discovery = demand.get("discovery") if isinstance(demand.get("discovery"), dict) else {}
    template = discovery.get("template_search") if isinstance(discovery.get("template_search"), dict) else {}
    last_run = discovery.get("last_run") if isinstance(discovery.get("last_run"), dict) else {}
    persona_id = demand.get("persona_id")
    names = lookup.display_names_by_id if lookup is not None and lookup.available else {}
    return {
        "demand_id": demand.get("demand_id"),
        "persona_id": persona_id,
        "persona_display_name": names.get(str(persona_id)) or None,
        "short_measure": demand.get("short_measure"),
        "tried_families": list(demand.get("tried_families") or []),
        "missing_datasets": list(demand.get("missing_datasets") or []),
        "mandate": demand.get("mandate"),
        "created_at": demand.get("created_at"),
        "expires_at": demand.get("expires_at"),
        "status": demand.get("status"),
        "evidence_ref": demand.get("evidence_ref"),
        "last_template_search_reason": template.get("reason"),
        "last_discovery_reason": last_run.get("reason"),
    }


def _lineage_persona_ids(task: Dict[str, Any], run: Optional[Dict[str, Any]]) -> List[str]:
    """Personas named by the run's (else the task's) recorded dataset_lineage."""
    for record in (run, task):
        if not isinstance(record, dict):
            continue
        meta = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        lineage = next((v for v in (record.get("dataset_lineage"), meta.get("dataset_lineage"))
                        if isinstance(v, dict)), None)
        if lineage is None:
            continue
        raw = lineage.get("persona_ids")
        raw = raw if isinstance(raw, list) else [lineage.get("persona_id")]
        return sorted({str(p).strip() for p in raw if p is not None and str(p).strip()})
    return []


def _persona_is_test(lookup: PersonaStateLookup, persona_id: str) -> bool:
    """Owner data_scope test, or the existing release-probe predicate on the owner's name."""
    if lookup.is_test_scoped([persona_id]):
        return True
    name = lookup.display_names_by_id.get(persona_id)
    return bool(name) and strategy_stub_reason({"title": name}, None) == STUB_TEST_PERSONA_PROBE


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
    *,
    persona_lookup: Optional[PersonaStateLookup] = None,
    runs_by_demand: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    demands_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("id") or "").strip()
    item_id = str(task.get("id") or task_id).strip()
    ordered_runs = sorted(task_runs, key=_run_sort_key)
    latest_run = _project_latest_run(ordered_runs[-1]) if ordered_runs else None
    latest_raw = ordered_runs[-1] if ordered_runs else None
    non_real_reason = _classify_non_real(task, latest_raw)
    if non_real_reason is None and task.get("task_type") == _RAPID_EVAL:
        # Only an owner-read persona set can prove test scope; an unreadable owner leaves the row visible.
        ids = _lineage_persona_ids(task, latest_raw)
        if ids and persona_lookup is not None and persona_lookup.available \
                and all(_persona_is_test(persona_lookup, pid) for pid in ids):
            non_real_reason = "acceptance_test"
    demand = _demand_join(ordered_runs, runs_by_demand or {}, demands_by_id)
    summary = _build_display_summary(task, latest_raw)
    if demand is not None:
        summary["task_kind"] = summary["task_kind"] or _TEMPLATE_SEARCH_ACTION
        summary["subject_ref"] = summary["subject_ref"] or demand["demand_id"]
    return {
        "is_non_real": non_real_reason is not None,
        "non_real_reason": non_real_reason,
        "name_is_fallback_id": _is_blank_name(task),
        "display_summary": summary,
        "demand": demand,
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
        **({"query_plan": task["query_plan"]} if task.get("task_type") == _COLLECTOR_QUERY_PLAN else {}),
    }


def _project_task_detail(
    task: Dict[str, Any],
    task_runs: Sequence[Dict[str, Any]],
    **join: Any,
) -> Dict[str, Any]:
    ordered_runs = sorted(task_runs, key=_run_sort_key)
    item = _project_task_item(task, ordered_runs, **join)
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
    persona_directory_snapshot: Optional[Callable[..., Any]] = None,
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
        hidden_non_real_count: Optional[int] = None,
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
        if hidden_non_real_count is not None:
            base["hidden_non_real_count"] = hidden_non_real_count
            surface_payload["hidden_non_real_count"] = hidden_non_real_count
        return base

    def _persona_lookup(tenant_id: str, snapshot_at: str) -> PersonaStateLookup:
        """One persona directory read per request; the owner record is the only truth."""
        return read_persona_state_lookup(
            directory_snapshot=persona_directory_snapshot,
            projected_state=lambda _record: "unknown",
            operational_states=(),
            tenant_id=tenant_id,
            snapshot_at=snapshot_at,
        )

    def _read_open_demands(
        resolved_base_url: str, caller_tenant: str
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        """Open demands of the caller tenant from the Research owner, or a visible failure reason."""
        try:
            ok, payload = http_get(f"{resolved_base_url}{_DEMANDS_PATH}")
        except Exception as exc:  # noqa: BLE001 - denied/unauthorised callers stay a visible state
            return None, f"owner_read_failed:{type(exc).__name__}"
        rows = payload.get("demands") if isinstance(payload, dict) else None
        if not ok or not isinstance(rows, list):
            return None, "owner_read_failed" if not ok else "demands_payload_invalid"
        if any(not isinstance(d, dict) or d.get("tenant_id") != caller_tenant or d.get("status") != "open"
               for d in rows):
            return None, "demand_scope_or_status_mismatch"
        return rows, None

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
        include_non_real: bool = Query(default=False),
        view: Literal["all", "research", "collector_checks"] = Query(default="all"),
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
            hidden_non_real_count = 0
            runs_by_demand: Dict[str, List[Dict[str, Any]]] = {}
            for raw_run in raw_runs:
                if _is_template_search_run(raw_run) and str(raw_run.get("tenant_id") or caller_tenant) == caller_tenant:
                    runs_by_demand.setdefault(str(raw_run["parameters"]["demand_id"]).strip(), []).append(raw_run)

            demand_rows: Optional[List[Dict[str, Any]]] = None
            demand_reason: Optional[str] = None
            if view == "research":
                demand_rows, demand_reason = _read_open_demands(resolved_base_url, caller_tenant)
            demands_by_id = (
                {str(d.get("demand_id")): d for d in demand_rows} if demand_rows is not None else None
            )

            # At most one persona directory read per request, only when a persona-bearing row needs it.
            lookup_cache: List[PersonaStateLookup] = []

            def lookup() -> PersonaStateLookup:
                if not lookup_cache:
                    lookup_cache.append(_persona_lookup(caller_tenant, snapshot_at))
                return lookup_cache[0]

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
                is_collector_check = raw_task.get("task_type") == _COLLECTOR_QUERY_PLAN
                if view != "all" and is_collector_check != (view == "collector_checks"):
                    continue
                if allowed_statuses is not None and str(raw_task.get("status") or "") not in allowed_statuses:
                    continue
                task_runs = runs_by_task.get(task_id, ())
                needs_personas = raw_task.get("task_type") == _RAPID_EVAL
                projected = _project_task_item(
                    raw_task, task_runs,
                    persona_lookup=lookup() if needs_personas else None,
                    runs_by_demand=runs_by_demand, demands_by_id=demands_by_id,
                )
                if projected["is_non_real"] and not include_non_real:
                    hidden_non_real_count += 1
                    continue
                scoped_items.append(projected)

            scoped_items.sort(
                key=lambda item: (str(item.get("created_at") or ""), str(item.get("task_id") or "")),
                reverse=True,
            )
            page_items, next_page_token = page_slice_fn(scoped_items, page_token, page_size)
            extra: Dict[str, Any] = {}
            meta = _build_meta(
                snapshot_at,
                status="ok",
                source="research_orchestrator",
                excluded_unscoped_count=excluded_unscoped_count,
                hidden_non_real_count=hidden_non_real_count,
            )
            if view == "research":
                demand_lookup = lookup() if demand_rows else None
                extra["open_demands"] = (
                    [_project_demand(d, demand_lookup) for d in demand_rows] if demand_rows is not None else []
                )
                meta["surfaces"]["research_demands"] = (
                    {"status": "ok", "source": "research_owner", "reason": None, "snapshot_at": snapshot_at}
                    if demand_rows is not None
                    else {"status": "unavailable", "source": "research_owner",
                          "reason": demand_reason, "snapshot_at": snapshot_at}
                )
            return {
                **extra,
                "data": page_items,
                "items": page_items,
                "tasks": page_items,
                "next_page_token": next_page_token,
                "page_info": {
                    "next_page_token": next_page_token,
                    "page_size": page_size,
                    "total": len(scoped_items),
                },
                "meta": meta,
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
            runs_by_demand = {}
            for raw_run in raw_runs:
                if _is_template_search_run(raw_run):
                    runs_by_demand.setdefault(str(raw_run["parameters"]["demand_id"]).strip(), []).append(raw_run)
            detail = _project_task_detail(
                raw_task, task_runs,
                persona_lookup=_persona_lookup(caller_tenant, snapshot_at)
                if raw_task.get("task_type") == _RAPID_EVAL else None,
                runs_by_demand=runs_by_demand,
            )
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

"""Agora research router — agora.research.v1.

Implements the ResearchPlan facade per:
  - docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/MASTER_SD_RESPONSE.md §B
  - services/control-plane/specs/agora/v4/research_plan_execution.schema.json
  - services/control-plane/specs/agora/v4/research_run_projection.schema.json
  - services/control-plane/openapi/agora_v1_3.openapi.yaml (research plan/run routes)

Stage routing per §B3 of MASTER_SD_RESPONSE.md:
  source_discovery          → source_ingestion
  data_validation           → data_validation
  prototype_backtest        → vectorbt
  alpha_training            → qlib
  rolling_oos               → qlib
  econometric_validation    → statsmodels
  derivatives_pricing_risk  → quantlib
  policy_training           → finrl  (activation-gated)
  parameter_search          → ray_tune
  portfolio_synthesis       → optimizer_svc
  robustness_stress         → rllib
  evidence_synthesis        → openclaw_result_synthesis

Governance enforced here:
  - execution_constraints.environments must not include 'live' or 'canary'
  - no_order_route_proof on plans is always 'research_plan_no_order_route'
  - no_order_route_proof on runs  is always 'research_only_not_direct_action'
  - Agora never routes orders, binds capital, or writes RuntimeBinding
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .store import make_research_plan_store


# ---------------------------------------------------------------------------
# Stage → preferred_backend routing policy (MASTER_SD_RESPONSE.md §B3)
# ---------------------------------------------------------------------------

_STAGE_TO_BACKEND: Dict[str, str] = {
    "source_discovery": "source_ingestion",
    "data_validation": "data_validation",
    "prototype_backtest": "vectorbt",
    "alpha_training": "qlib",
    "rolling_oos": "qlib",
    "econometric_validation": "statsmodels",
    "derivatives_pricing_risk": "quantlib",
    "policy_training": "finrl",
    "parameter_search": "ray_tune",
    "portfolio_synthesis": "optimizer_svc",
    "robustness_stress": "rllib",
    "evidence_synthesis": "openclaw_result_synthesis",
}

_VALID_STAGE_TYPES = frozenset(_STAGE_TO_BACKEND.keys())
_FORBIDDEN_ENVIRONMENTS = frozenset({"live", "canary"})
_PLAN_NO_ORDER_ROUTE_PROOF = "research_plan_no_order_route"
_RUN_NO_ORDER_ROUTE_PROOF = "research_only_not_direct_action"
_CAPABILITY = "agora.research.v1"

_PLAN_ALLOWED_ACTIONS: Dict[str, Dict[str, bool]] = {
    "draft":     {"approve": True,  "cancel": True,  "dispatch": False},
    "approved":  {"approve": False, "cancel": True,  "dispatch": True},
    "running":   {"approve": False, "cancel": True,  "dispatch": False},
    "completed": {"approve": False, "cancel": False, "dispatch": False},
    "cancelled": {"approve": False, "cancel": False, "dispatch": False},
}

_RUN_ALLOWED_ACTIONS: Dict[str, Dict[str, bool]] = {
    "queued":      {"cancel": True},
    "dispatching": {"cancel": True},
    "running":     {"cancel": True},
    "succeeded":   {"cancel": False},
    "failed":      {"cancel": False},
    "cancelled":   {"cancel": False},
    "timed_out":   {"cancel": False},
}


# ---------------------------------------------------------------------------
# Request body models
# ---------------------------------------------------------------------------

class _StageRoutingRequest(BaseModel):
    model_config = {"extra": "forbid"}
    preferred_backend: Optional[str] = None
    fallback_policy: Optional[str] = None
    backend_mode: Optional[str] = None
    routing_reason: Optional[str] = None


class _StageRequest(BaseModel):
    model_config = {"extra": "forbid"}
    stage_id: Optional[str] = None
    stage_type: str
    status: Optional[str] = "pending"
    dependencies: List[str] = Field(default_factory=list)
    required_capability: Optional[str] = None
    routing: Optional[_StageRoutingRequest] = None
    input_refs: Optional[List[str]] = None
    output_refs: Optional[List[str]] = None
    parameters: Optional[Dict[str, Any]] = None
    blocking_reasons: Optional[List[str]] = None


class _ExecutionConstraintsRequest(BaseModel):
    model_config = {"extra": "forbid"}
    max_runtime_hours: Optional[float] = None
    compute_tier: Optional[str] = None
    environments: Optional[List[str]] = None


class _PlanBudgetRequest(BaseModel):
    model_config = {"extra": "forbid"}
    compute_tier: Optional[str] = None
    max_runtime_seconds: Optional[int] = None
    max_parallel_stages: Optional[int] = None
    external_data_spend_allowed: Optional[bool] = None


class ResearchPlanCreateRequest(BaseModel):
    """Request body for POST /bff/agora/workshops/{workshop_id}/research-plans."""
    model_config = {"extra": "forbid"}
    spec_version: str
    strategy_id: str = Field(min_length=1)
    strategy_spec_registry_id: str = Field(min_length=1)
    stages: List[_StageRequest] = Field(min_length=1)
    budget: Optional[_PlanBudgetRequest] = None
    execution_constraints: Optional[_ExecutionConstraintsRequest] = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _plan_etag(plan_id: str, lock_version: int) -> str:
    return f'W/"research-plan:{plan_id}:v{lock_version}"'


def _parse_plan_lock_version(if_match: str, plan_id: str) -> int:
    """Extract lock_version from ETag; returns 0 on parse failure (guaranteed conflict)."""
    prefix = f'W/"research-plan:{plan_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _plan_detail_envelope(
    plan: Dict[str, Any],
    utc_now: Callable[[], str],
    scope: Any,
) -> Dict[str, Any]:
    status = plan.get("status", "draft")
    plan_id = plan["plan_id"]
    lock_version = plan.get("lock_version", 1)
    etag = _plan_etag(plan_id, lock_version)
    return {
        "object_ref": {"type": "research_plan", "id": plan_id},
        "status": status,
        "lifecycle_state": status,
        "allowedActions": _PLAN_ALLOWED_ACTIONS.get(status, {}),
        "data": plan,
        "meta": {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            "etag": etag,
        },
        "links": {
            "self": f"/bff/agora/research-plans/{plan_id}",
            "runs": f"/bff/agora/research-plans/{plan_id}/runs",
        },
    }


def _run_projection_with_defaults(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return a ResearchRunProjection-shaped dict with stable optional arrays."""
    projected = dict(run)
    projected.setdefault("metrics", [])
    projected.setdefault("findings", [])
    projected.setdefault("warnings", [])
    projected.setdefault("blocking_reasons", [])
    projected.setdefault("artifact_refs", [])
    projected.setdefault("evidence_refs", [])
    projected.setdefault("lineage_refs", [])
    return projected


def _build_run_projection(
    *,
    plan: Dict[str, Any],
    stage: Dict[str, Any],
    run_id: str,
    now: str,
) -> Dict[str, Any]:
    routing = stage.get("routing", {})
    backend = routing.get("effective_backend") or routing.get("preferred_backend", "")
    run = {
        "spec_version": "1.0",
        "run_id": run_id,
        "plan_id": plan["plan_id"],
        "workshop_id": plan["workshop_id"],
        "strategy_id": plan["strategy_id"],
        "strategy_spec_registry_id": plan["strategy_spec_registry_id"],
        "stage_id": stage["stage_id"],
        "stage_type": stage["stage_type"],
        "execution_status": "queued",
        "outcome": "pending",
        "progress": {
            "phase": "queued",
            "percent": 0,
            "message": "Run queued for dispatch",
            "updated_at": now,
        },
        "backend": {
            "requested": routing.get("preferred_backend", ""),
            "effective": backend,
            "mode": routing.get("backend_mode", "real"),
        },
        "no_order_route_proof": _RUN_NO_ORDER_ROUTE_PROOF,
        "created_at": now,
        "updated_at": now,
    }
    return _run_projection_with_defaults(run)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def publish_research_progress(
    workshop_id: str,
    run_id: str,
    progress_pct: float,
    message: str = "",
    *,
    phase: str = "running",
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    """Publish a canonical research.run.progress event to the workshop SSE stream.

    Imports _ws_publish lazily to avoid a circular import with the workshop router.
    Returns the SSE event_id, or an empty string if the workshop has no active stream.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
        workshop_id,
        "research.run.progress",
        {
            "run_id": run_id,
            "phase": phase,
            "percent": progress_pct,
            "message": message,
        },
        utc_now_fn=utc_now_fn,
    )


def publish_openclaw_degraded(
    workshop_id: str,
    reason: str = "OPENCLAW_UPSTREAM_DEGRADED",
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
) -> str:
    """Publish a workshop.openclaw.degraded event when OpenClaw is unreachable.

    Callers should invoke this whenever they detect OpenClaw degradation while
    serving a request for a specific workshop.  The event carries the canonical
    error_code OPENCLAW_UPSTREAM_DEGRADED so the frontend can show a graceful
    degraded state.
    """
    from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)
    return _ws_publish(
        workshop_id,
        "workshop.openclaw.degraded",
        {
            "error_code": "OPENCLAW_UPSTREAM_DEGRADED",
            "reason": reason,
        },
        utc_now_fn=utc_now_fn,
    )


def create_research_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    research_plan_store: Any = None,
) -> APIRouter:
    """Build and return the Agora research APIRouter.

    ``research_plan_store`` may be injected for tests.
    When omitted the store is constructed from AGORA_RESEARCH_PLAN_STORE_BACKEND env.
    """
    store = research_plan_store if research_plan_store is not None else make_research_plan_store()
    router = APIRouter(tags=["agora-research"])

    def _scope(authorization: Optional[str], x_tenant_id: Optional[str] = None) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

        identity = extract_identity(authorization)
        require_read_role(identity)
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code, code, exc.message, exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _require_idempotency_key(key: Optional[str]) -> None:
        if key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )

    def _check_idempotency(scope: Any, endpoint: str, key: str) -> None:
        scope_str = f"{scope.user_id}:{scope.tenant_id}:{endpoint}"
        if store.check_and_record_idempotency_key(scope_str, key):
            from models import ErrorCode
            raise bff_error(409, ErrorCode.IDEMPOTENCY_CONFLICT, "Duplicate Idempotency-Key", key)

    def _require_if_match(header: Optional[str]) -> None:
        if header is None:
            from models import ErrorCode
            raise bff_error(
                428, ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required",
                "missing_if_match",
                suggestion="GET the plan first and supply the returned ETag in If-Match",
            )

    def _check_plan_if_match(plan: Dict[str, Any], if_match: str) -> None:
        lock_version = plan.get("lock_version", 1)
        provided = _parse_plan_lock_version(if_match, plan["plan_id"])
        if provided != lock_version:
            from models import ErrorCode
            raise bff_error(
                412, ErrorCode.PRECONDITION_FAILED,
                "ETag mismatch; plan was modified since it was last read",
                f"expected v{lock_version}, supplied ETag resolved to v{provided}",
            )

    def _validate_create_body(body: ResearchPlanCreateRequest, workshop_id: str) -> None:
        from models import ErrorCode
        if body.spec_version != "1.0":
            raise bff_error(
                422, ErrorCode.VALIDATION_FAILED,
                "spec_version must be '1.0'",
                f"received: {body.spec_version!r}",
            )
        # Governance gate: environments must not include live or canary
        if body.execution_constraints and body.execution_constraints.environments:
            forbidden = _FORBIDDEN_ENVIRONMENTS & set(body.execution_constraints.environments)
            if forbidden:
                raise bff_error(
                    422, ErrorCode.VALIDATION_FAILED,
                    f"execution_constraints.environments must not include: {', '.join(sorted(forbidden))}",
                    "forbidden_environments",
                )
        # Validate each stage type
        for i, stage in enumerate(body.stages):
            if stage.stage_type not in _VALID_STAGE_TYPES:
                raise bff_error(
                    422, ErrorCode.VALIDATION_FAILED,
                    f"stages[{i}].stage_type '{stage.stage_type}' is not a recognised stage type",
                    stage.stage_type,
                )

    def _build_plan(
        body: ResearchPlanCreateRequest,
        workshop_id: str,
        plan_id: str,
        now: str,
    ) -> Dict[str, Any]:
        """Construct a normalised ResearchPlanExecution dict from the request."""
        stages: List[Dict[str, Any]] = []
        for stage in body.stages:
            canonical_backend = _STAGE_TO_BACKEND[stage.stage_type]
            routing_in = stage.routing
            routing: Dict[str, Any] = {
                "preferred_backend": (
                    routing_in.preferred_backend if routing_in and routing_in.preferred_backend
                    else canonical_backend
                ),
                "fallback_policy": (
                    routing_in.fallback_policy if routing_in and routing_in.fallback_policy
                    else "fail_closed"
                ),
            }
            if routing_in and routing_in.backend_mode:
                routing["backend_mode"] = routing_in.backend_mode
            if routing_in and routing_in.routing_reason:
                routing["routing_reason"] = routing_in.routing_reason

            normalized: Dict[str, Any] = {
                "stage_id": stage.stage_id or str(uuid.uuid4()),
                "stage_type": stage.stage_type,
                "status": stage.status or "pending",
                "dependencies": stage.dependencies or [],
                "required_capability": stage.required_capability or canonical_backend,
                "routing": routing,
            }
            if stage.input_refs is not None:
                normalized["input_refs"] = stage.input_refs
            if stage.output_refs is not None:
                normalized["output_refs"] = stage.output_refs
            if stage.parameters is not None:
                normalized["parameters"] = stage.parameters
            if stage.blocking_reasons is not None:
                normalized["blocking_reasons"] = stage.blocking_reasons
            stages.append(normalized)

        plan: Dict[str, Any] = {
            "spec_version": "1.0",
            "plan_id": plan_id,
            "workshop_id": workshop_id,
            "strategy_id": body.strategy_id,
            "strategy_spec_registry_id": body.strategy_spec_registry_id,
            "status": "draft",
            "stages": stages,
            "no_order_route_proof": _PLAN_NO_ORDER_ROUTE_PROOF,
            "created_at": now,
            "lock_version": 1,
            "run_ids": [],
        }
        if body.budget:
            plan["budget"] = body.budget.model_dump(exclude_none=True)
        if body.execution_constraints:
            plan["execution_constraints"] = body.execution_constraints.model_dump(exclude_none=True)
        return plan

    def _publish_research_event(workshop_id: str, event_type: str, data: Dict[str, Any]) -> None:
        from agora.strategy_workshop.router import _ws_publish  # noqa: PLC0415 (lazy import)

        _ws_publish(workshop_id, event_type, data, utc_now_fn=utc_now)

    # -------------------------------------------------------------------
    # GET /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.get("/bff/agora/workshops/{workshop_id}/research-plans")
    def list_workshop_research_plans(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plans = store.list_plans_for_workshop(workshop_id)
        return {
            "items": plans,
            "page_info": {
                "next_page_token": None,
                "page_size": len(plans),
                "has_more": False,
                "total": len(plans),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/workshops/{workshop_id}/research-plans
    # -------------------------------------------------------------------
    @router.post("/bff/agora/workshops/{workshop_id}/research-plans", status_code=201)
    def create_workshop_research_plan(
        workshop_id: str,
        body: ResearchPlanCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/workshops/{workshop_id}/research-plans",
            idempotency_key,  # type: ignore[arg-type]
        )
        _validate_create_body(body, workshop_id)
        now = utc_now()
        plan_id = str(uuid.uuid4())
        plan = _build_plan(body, workshop_id, plan_id, now)
        plan = store.create_plan(plan)
        _publish_research_event(
            workshop_id,
            "research.plan.created",
            {"plan_id": plan_id, "status": plan["status"]},
        )
        return _plan_detail_envelope(plan, utc_now, scope)

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}")
    def get_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        return _plan_detail_envelope(plan, utc_now, scope)

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/approve
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/approve")
    def approve_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/approve",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "draft":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan cannot be approved from status '{plan['status']}'",
                f"expected status 'draft', got '{plan['status']}'",
            )
        now = utc_now()
        store.update_plan(plan_id, {
            "status": "approved",
            "approved_at": now,
            "approval": {
                "state": "approved",
                "decided_by": scope.user_id,
                "decided_at": now,
            },
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.plan.approved",
            {"plan_id": plan_id, "status": "approved"},
        )
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "approved"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/cancel")
    def cancel_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        cancellable = {"draft", "approved", "running"}
        if plan["status"] not in cancellable:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Plan in status '{plan['status']}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable)}",
            )
        now = utc_now()
        store.update_plan(plan_id, {
            "status": "cancelled",
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.plan.cancelled",
            {"plan_id": plan_id, "status": "cancelled"},
        )
        return {
            "status": "completed",
            "data": {"plan_id": plan_id, "status": "cancelled"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-plans/{plan_id}/runs
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-plans/{plan_id}/runs")
    def list_agora_research_plan_runs(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        runs = store.list_runs_for_plan(plan_id)
        return {
            "items": runs,
            "page_info": {
                "next_page_token": None,
                "page_size": len(runs),
                "has_more": False,
                "total": len(runs),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # POST /bff/agora/research-plans/{plan_id}/runs  (dispatch)
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-plans/{plan_id}/runs", status_code=202)
    def dispatch_agora_research_plan(
        plan_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _require_if_match(if_match)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-plans/{plan_id}/runs",
            idempotency_key,  # type: ignore[arg-type]
        )
        plan = store.get_plan(plan_id)
        if plan is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research plan not found", plan_id)
        _check_plan_if_match(plan, if_match)  # type: ignore[arg-type]
        if plan["status"] != "approved":
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Only approved plans may be dispatched; current status: '{plan['status']}'",
                f"expected 'approved', got '{plan['status']}'",
            )
        # Select the first pending or ready stage
        dispatch_stage: Optional[Dict[str, Any]] = None
        for stage in plan.get("stages", []):
            if stage.get("status") in ("pending", "ready"):
                dispatch_stage = stage
                break
        if dispatch_stage is None:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "No pending or ready stages to dispatch",
                "all_stages_dispatched_or_blocked",
            )
        now = utc_now()
        run_id = str(uuid.uuid4())
        run = _build_run_projection(
            plan=plan,
            stage=dispatch_stage,
            run_id=run_id,
            now=now,
        )
        store.create_run(run)
        # Update plan: mark dispatched stage as queued, record run_id, advance to running
        updated_stages = [
            {**s, "status": "queued"} if s["stage_id"] == dispatch_stage["stage_id"] else s
            for s in plan.get("stages", [])
        ]
        plan_run_ids = list(plan.get("run_ids") or [])
        plan_run_ids.append(run_id)
        store.update_plan(plan_id, {
            "stages": updated_stages,
            "run_ids": plan_run_ids,
            "status": "running",
            "lock_version": plan.get("lock_version", 1) + 1,
            "updated_at": now,
        })
        _publish_research_event(
            plan["workshop_id"],
            "research.run.queued",
            {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
                "percent": 0,
            },
        )
        return {
            "status": "queued",
            "data": {
                "run_id": run_id,
                "plan_id": plan_id,
                "stage_id": dispatch_stage["stage_id"],
                "stage_type": dispatch_stage["stage_type"],
            },
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}")
    def get_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        _scope(authorization, x_tenant_id)
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        return _run_projection_with_defaults(run)

    # -------------------------------------------------------------------
    # POST /bff/agora/research-runs/{run_id}/cancel
    # -------------------------------------------------------------------
    @router.post("/bff/agora/research-runs/{run_id}/cancel", status_code=202)
    def cancel_agora_research_run(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        _require_idempotency_key(idempotency_key)
        _check_idempotency(
            scope,
            f"POST:/bff/agora/research-runs/{run_id}/cancel",
            idempotency_key,  # type: ignore[arg-type]
        )
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        cancellable_statuses = {"queued", "dispatching", "running"}
        current = run.get("execution_status")
        if current not in cancellable_statuses:
            from models import ErrorCode
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                f"Run in status '{current}' cannot be cancelled",
                f"cancellable statuses: {sorted(cancellable_statuses)}",
            )
        now = utc_now()
        store.update_run(run_id, {
            "execution_status": "cancelled",
            "progress": {
                **run.get("progress", {}),
                "phase": "cancelled",
                "message": "Run cancellation accepted",
                "updated_at": now,
            },
            "completed_at": now,
            "updated_at": now,
        })
        publish_research_progress(
            run["workshop_id"],
            run_id,
            float(run.get("progress", {}).get("percent", 0)),
            "Run cancellation accepted",
            phase="cancelled",
            utc_now_fn=utc_now,
        )
        return {
            "status": "accepted",
            "data": {"run_id": run_id, "execution_status": "cancelled"},
            "meta": {
                "snapshot_at": now,
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # -------------------------------------------------------------------
    # GET /bff/agora/research-runs/{run_id}/artifacts
    # -------------------------------------------------------------------
    @router.get("/bff/agora/research-runs/{run_id}/artifacts")
    def list_agora_research_run_artifacts(
        run_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        run = store.get_run(run_id)
        if run is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Research run not found", run_id)
        artifact_refs = run.get("artifact_refs") or []
        evidence_refs = run.get("evidence_refs") or []
        items: List[Dict[str, Any]] = (
            [{"ref_type": "experiment_artifact", "ref_id": a} for a in artifact_refs]
            + list(evidence_refs)
        )
        return {
            "items": items,
            "page_info": {
                "next_page_token": None,
                "page_size": len(items),
                "has_more": False,
                "total": len(items),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": _CAPABILITY,
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    return router

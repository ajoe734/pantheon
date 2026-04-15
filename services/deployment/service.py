"""
BP5-SVC-004: Deployable DeploymentPlan and stage-transition planner API.

This service wraps the canonical control-plane deployment-plan domain with a
file-backed FastAPI surface so callers can create, validate, list, and read
deployment plans without importing platform objects directly.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from deployment_plan import (  # type: ignore
    DeploymentPlan,
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentScale,
    DeploymentStage,
    PlanStatus,
    RollbackRef,
    ScheduleWindow,
    StagePlanner,
)

try:
    from .models import (
        CreateDeploymentPlanRequest,
        DeploymentPlanBody,
        DeploymentPlanSummary,
        PlanStatusBody,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )
except ImportError:
    from models import (  # type: ignore
        CreateDeploymentPlanRequest,
        DeploymentPlanBody,
        DeploymentPlanSummary,
        PlanStatusBody,
        StrategyReadModelResponse,
        UpdatePlanStatusRequest,
        ValidateDeploymentPlanResponse,
    )

log = logging.getLogger(__name__)


def _iso_sort_key(plan: DeploymentPlan) -> str:
    return str(plan.created_at or "")


def _resolve_governance_dir() -> Path:
    base = (
        os.getenv("DEPLOYMENT_DATA_DIR")
        or os.getenv("PANTHEON_GOVERNANCE_DATA_DIR")
        or "/tmp/pantheon/governance"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_DIR = _resolve_governance_dir()
PLAN_STORE_PATH = DATA_DIR / "deployment_plans.json"
APPROVAL_STORE_PATH = DATA_DIR / "approval_decisions.json"
_REGISTRY_SNAPSHOT_ENV = os.getenv("PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH", "").strip()
REGISTRY_SNAPSHOT_PATH = Path(_REGISTRY_SNAPSHOT_ENV).expanduser() if _REGISTRY_SNAPSHOT_ENV else None

store = DeploymentPlanStore(str(PLAN_STORE_PATH))


class DeploymentPlannerService:
    """Thin service layer over StagePlanner + file-backed stores."""

    def __init__(
        self,
        *,
        plan_store: DeploymentPlanStore,
        approval_store_path: Path,
        registry_snapshot_path: Path | None = None,
    ) -> None:
        self.plan_store = plan_store
        self.approval_store_path = approval_store_path
        self.registry_snapshot_path = registry_snapshot_path
        self.planner = StagePlanner()

    def create_plan(self, request: CreateDeploymentPlanRequest, *, persist: bool) -> DeploymentPlan:
        registry_entry = self._resolve_registry_entry(request)
        approval_decision = self._resolve_approval_decision(request)
        capital_pool_id = request.capital_pool_id or str(approval_decision.get("capital_pool_id") or "").strip()
        if not capital_pool_id:
            raise DeploymentPlanError(
                "capital_pool_id is required unless approval_decision.capital_pool_id is present"
            )
        if request.plan_id and self.plan_store.get(request.plan_id) is not None:
            raise DeploymentPlanError(f"DeploymentPlan '{request.plan_id}' already exists")

        schedule_window = (
            ScheduleWindow(**request.schedule_window.model_dump())
            if request.schedule_window is not None
            else None
        )
        scale = (
            DeploymentScale(**request.scale.model_dump())
            if request.scale is not None
            else None
        )
        rollback = (
            RollbackRef(**request.rollback.model_dump())
            if request.rollback is not None
            else None
        )

        plan = self.planner.create_plan(
            plan_id=request.plan_id or f"plan-{uuid.uuid4().hex[:12]}",
            approval_decision_id=request.approval_decision_id,
            approval_decision=approval_decision,
            registry_entry=registry_entry,
            capital_pool_id=capital_pool_id,
            target_stage=request.target_stage.value,
            current_stage=request.current_stage.value if request.current_stage else None,
            created_by=request.created_by,
            sponsor_persona_id=request.sponsor_persona_id,
            runtime_config_ref=request.runtime_config_ref,
            binding_id=request.binding_id,
            schedule_window=schedule_window,
            scale=scale,
            rollback=rollback,
            pre_checks=list(request.pre_checks),
            post_checks=list(request.post_checks),
            metadata=request.metadata,
            supersedes_plan_id=request.supersedes_plan_id,
            status=request.status.value,
        )
        if persist:
            self.plan_store.put(plan)
        return plan

    def list_plans(
        self,
        *,
        strategy_id: str | None = None,
        capital_pool_id: str | None = None,
        target_stage: str | None = None,
        status: str | None = None,
    ) -> list[DeploymentPlan]:
        plans = self.plan_store.list_all()
        if strategy_id:
            plans = [plan for plan in plans if plan.strategy_id == strategy_id]
        if capital_pool_id:
            plans = [plan for plan in plans if plan.capital_pool_id == capital_pool_id]
        if target_stage:
            plans = [
                plan for plan in plans
                if str(plan.target_stage) == target_stage
                or getattr(plan.target_stage, "value", None) == target_stage
            ]
        if status:
            plans = [
                plan for plan in plans
                if str(plan.status) == status or getattr(plan.status, "value", None) == status
            ]
        return sorted(plans, key=_iso_sort_key, reverse=True)

    def get_plan(self, plan_id: str) -> DeploymentPlan:
        plan = self.plan_store.get(plan_id)
        if plan is None:
            raise DeploymentPlanError(f"DeploymentPlan '{plan_id}' not found")
        return plan

    def update_status(self, plan_id: str, target_status: PlanStatusBody) -> DeploymentPlan:
        plan = self.get_plan(plan_id)
        current_status = PlanStatus(plan.status)
        next_status = PlanStatus(target_status.value)
        if next_status == current_status:
            return plan

        allowed = {
            PlanStatus.DRAFT: {PlanStatus.APPROVED, PlanStatus.REJECTED, PlanStatus.ABORTED},
            PlanStatus.APPROVED: {PlanStatus.EXECUTING, PlanStatus.REJECTED, PlanStatus.ABORTED},
            PlanStatus.EXECUTING: {PlanStatus.EXECUTED, PlanStatus.FAILED, PlanStatus.ABORTED},
            PlanStatus.EXECUTED: set(),
            PlanStatus.ABORTED: set(),
            PlanStatus.REJECTED: set(),
            PlanStatus.FAILED: set(),
        }
        if next_status not in allowed[current_status]:
            allowed_values = ", ".join(sorted(status.value for status in allowed[current_status])) or "<none>"
            raise DeploymentPlanError(
                f"Invalid plan status transition: {current_status.value} -> {next_status.value} "
                f"(expected one of: {allowed_values})"
            )

        plan.status = next_status
        self.plan_store.put(plan)
        return plan

    def strategy_read_model(
        self,
        *,
        strategy_id: str,
        capital_pool_id: str | None = None,
    ) -> StrategyReadModelResponse:
        plans = self.list_plans(strategy_id=strategy_id, capital_pool_id=capital_pool_id)
        latest = plans[0] if plans else None
        executed = next(
            (plan for plan in plans if PlanStatus(plan.status) == PlanStatus.EXECUTED),
            None,
        )
        active = next(
            (
                plan
                for plan in plans
                if PlanStatus(plan.status) in {PlanStatus.APPROVED, PlanStatus.EXECUTING}
            ),
            None,
        )

        current_stage = DeploymentStage.NONE.value
        if executed is not None:
            current_stage = _enum_value(executed.target_stage)
        elif latest is not None:
            current_stage = _enum_value(latest.current_stage)

        plan_summaries = [_plan_summary(plan) for plan in plans]
        return StrategyReadModelResponse(
            strategy_id=strategy_id,
            capital_pool_id=capital_pool_id,
            current_stage=current_stage,
            latest_plan_id=latest.plan_id if latest else None,
            active_plan_id=active.plan_id if active else None,
            latest_target_stage=_enum_value(latest.target_stage) if latest else None,
            latest_transition_type=_enum_value(latest.transition_type) if latest else None,
            latest_status=_enum_value(latest.status) if latest else None,
            plan_count=len(plans),
            plans=plan_summaries,
        )

    def _resolve_registry_entry(self, request: CreateDeploymentPlanRequest) -> Mapping[str, Any]:
        if request.registry_entry is not None:
            return request.registry_entry
        if request.registry_id and self.registry_snapshot_path and self.registry_snapshot_path.exists():
            record = _load_record(
                self.registry_snapshot_path,
                key_candidates=("registry_id", "id"),
                target_key=request.registry_id,
            )
            if record is not None:
                return record
        raise DeploymentPlanError(
            "registry_entry payload is required unless registry_id resolves from "
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        )

    def _resolve_approval_decision(self, request: CreateDeploymentPlanRequest) -> Mapping[str, Any]:
        if request.approval_decision is not None:
            return request.approval_decision
        if self.approval_store_path.exists():
            record = _load_record(
                self.approval_store_path,
                key_candidates=("decision_id", "id"),
                target_key=request.approval_decision_id,
            )
            if record is not None:
                return record
        raise DeploymentPlanError(
            "approval_decision payload is required unless approval_decision_id resolves from "
            f"{self.approval_store_path}"
        )


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _load_record(path: Path, *, key_candidates: Iterable[str], target_key: str) -> Optional[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    payload = json.loads(text)
    if isinstance(payload, dict):
        if target_key in payload and isinstance(payload[target_key], dict):
            return payload[target_key]
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            for key in key_candidates:
                if str(item.get(key) or "") == target_key:
                    return item
        return None
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            for key in key_candidates:
                if str(item.get(key) or "") == target_key:
                    return item
    return None


def _plan_body(plan: DeploymentPlan) -> DeploymentPlanBody:
    return DeploymentPlanBody(**plan.to_dict())


def _plan_summary(plan: DeploymentPlan) -> DeploymentPlanSummary:
    payload = plan.to_dict()
    return DeploymentPlanSummary(
        plan_id=payload["plan_id"],
        artifact_id=payload["artifact_id"],
        artifact_version=payload["artifact_version"],
        strategy_id=payload["strategy_id"],
        capital_pool_id=payload["capital_pool_id"],
        current_stage=payload["current_stage"],
        target_stage=payload["target_stage"],
        transition_type=payload["transition_type"],
        runtime_action=payload["runtime_action"],
        status=payload["status"],
        created_at=payload["created_at"],
        approval_decision_id=payload["approval_decision_id"],
    )


planner_service = DeploymentPlannerService(
    plan_store=store,
    approval_store_path=APPROVAL_STORE_PATH,
    registry_snapshot_path=REGISTRY_SNAPSHOT_PATH,
)

app = FastAPI(
    title="Pantheon Deployment Service",
    description="DeploymentPlan and stage-transition planner API per BP5-SVC-004",
    version="0.1.0",
)


@app.exception_handler(DeploymentPlanError)
async def deployment_plan_error_handler(request: Request, exc: DeploymentPlanError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.post("/api/deployment/plans", response_model=DeploymentPlanBody, status_code=201)
async def create_deployment_plan(body: CreateDeploymentPlanRequest):
    try:
        plan = planner_service.create_plan(body, persist=True)
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _plan_body(plan)


@app.post("/api/deployment/plans/validate", response_model=ValidateDeploymentPlanResponse)
async def validate_deployment_plan(body: CreateDeploymentPlanRequest):
    try:
        plan = planner_service.create_plan(body, persist=False)
    except DeploymentPlanError as exc:
        return ValidateDeploymentPlanResponse(ok=False, errors=[str(exc)])
    return ValidateDeploymentPlanResponse(ok=True, plan=_plan_body(plan), errors=[])


@app.get("/api/deployment/plans", response_model=List[DeploymentPlanBody])
async def list_deployment_plans(
    strategy_id: str | None = Query(default=None),
    capital_pool_id: str | None = Query(default=None),
    target_stage: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    plans = planner_service.list_plans(
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
        target_stage=target_stage,
        status=status,
    )
    return [_plan_body(plan) for plan in plans]


@app.get("/api/deployment/plans/{plan_id}", response_model=DeploymentPlanBody)
async def get_deployment_plan(plan_id: str):
    try:
        return _plan_body(planner_service.get_plan(plan_id))
    except DeploymentPlanError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/deployment/plans/{plan_id}/status", response_model=DeploymentPlanBody)
async def update_deployment_plan_status(plan_id: str, body: UpdatePlanStatusRequest):
    try:
        return _plan_body(planner_service.update_status(plan_id, body.status))
    except DeploymentPlanError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message.lower() else 400
        raise HTTPException(status_code=status_code, detail=message)


@app.get(
    "/api/deployment/strategies/{strategy_id}/read-model",
    response_model=StrategyReadModelResponse,
)
async def get_strategy_read_model(strategy_id: str, capital_pool_id: str | None = Query(default=None)):
    return planner_service.strategy_read_model(
        strategy_id=strategy_id,
        capital_pool_id=capital_pool_id,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pantheon-deployment"}

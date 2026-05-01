from __future__ import annotations

import copy
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.promotion.pg_store import (
    build_promotion_approval_store,
    build_promotion_deployment_store,
    build_promotion_extension_store,
)

ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from approval_decision import (  # type: ignore
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    RiskLevel,
    TargetType,
)
from deployment_plan import (  # type: ignore
    DeploymentPlan,
    DeploymentPlanError,
    DeploymentPlanStore,
    DeploymentScale,
    PlanStatus,
    RollbackRef,
    ScheduleWindow,
    StagePlanner,
)

PORT = int(os.getenv("PORT", "8089"))
DATA_DIR = Path(os.getenv("PROMOTION_DATA_DIR", "/tmp/pantheon/promotion"))
APPROVAL_STORE_PATH = DATA_DIR / "approval_decisions.json"
DEPLOYMENT_STORE_PATH = DATA_DIR / "deployment_plans.json"
DEPLOYMENT_EXT_PATH = DATA_DIR / "deployment_plan_extensions.json"
STORE_BACKEND = os.getenv("PROMOTION_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("promotion")


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: str | None = None
    target_type: TargetType
    target_id: str
    target_version: str
    risk_level: RiskLevel = RiskLevel.LOW
    capital_pool_id: str | None = None
    persona_id: str | None = None


class ApprovalDecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approved", "rejected", "approved_with_conditions"] = "approved"
    rationale: str = "approved via API"
    actor_role: Literal[
        "governance_reviewer", "risk_owner", "governance_committee", "automated_gate"
    ] = "automated_gate"
    actor_id: str = "promotion-svc"
    conditions: list[str] = []


class RollbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_artifact_id: str
    target_version: str
    action_type: Literal["replace", "pause_then_replace", "liquidate_then_replace"] = "replace"
    reason: str | None = None
    verified_at: str | None = None


class ScheduleWindowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: str
    end_at: str | None = None


class ScalePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capital_scale_pct: float
    gross_scale_pct: float
    ramp_schedule: list[str] = []


class DeploymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str | None = None
    approval_decision_id: str
    artifact_id: str
    artifact_version: str
    artifact_type: str
    strategy_id: str
    capital_pool_id: str
    target_stage: Literal["paper", "canary", "live", "frozen"]
    current_stage: Literal["none", "paper", "canary", "live", "frozen"] = "none"
    status: Literal["draft", "approved", "executing", "executed", "aborted", "rejected", "failed"] = "approved"
    created_by: str | None = None
    sponsor_persona_id: str | None = None
    runtime_config_ref: str | None = None
    binding_id: str | None = None
    persona_capital_binding_id: str | None = None
    persona_capital_binding_status: str | None = None
    allowed_deployment_scope: Literal["none", "paper", "canary", "live"] | None = None
    loader_checks_passed: bool | None = None
    schedule_window: ScheduleWindowPayload | None = None
    scale: ScalePayload | None = None
    rollback: RollbackPayload | None = None
    pre_checks: list[str] = []
    post_checks: list[str] = []
    metadata: dict[str, Any] | None = None
    supersedes_plan_id: str | None = None


class ExtensionStore:
    def __init__(self, path: Path):
        self._path = path
        self._items: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            text = self._path.read_text(encoding="utf-8")
            if text.strip():
                self._items = json.loads(text)

    def get(self, key: str) -> dict[str, Any]:
        return dict(self._items.get(key, {}))

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = dict(value)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._items, indent=2), encoding="utf-8")


approval_store = build_promotion_approval_store(APPROVAL_STORE_PATH)
deployment_store = build_promotion_deployment_store(DEPLOYMENT_STORE_PATH)
deployment_ext_store = build_promotion_extension_store(DEPLOYMENT_EXT_PATH)
stage_planner = StagePlanner()


def _deployment_response(plan: DeploymentPlan) -> dict[str, Any]:
    payload = plan.to_dict()
    payload.update(deployment_ext_store.get(plan.plan_id))
    payload["plan_status"] = payload["status"]
    return payload


def _serialize_approval_decision(decision: ApprovalDecision) -> dict[str, Any]:
    payload = decision.to_dict()
    payload["decision_state"] = getattr(decision.decision_state, "value", decision.decision_state)
    payload["decision"] = getattr(decision.decision, "value", decision.decision)
    payload["target_type"] = getattr(decision.target_type, "value", decision.target_type)
    payload["risk_level"] = getattr(decision.risk_level, "value", decision.risk_level)
    payload["actor_role"] = getattr(decision.actor_role, "value", decision.actor_role)
    return payload


def _validated_approval_decision(
    decision_id: str,
    *,
    artifact_id: str,
    artifact_version: str,
    capital_pool_id: str,
    sponsor_persona_id: str | None,
) -> dict[str, Any]:
    decision = approval_store.get(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"ApprovalDecision '{decision_id}' not found")

    if decision.decision_state != DecisionState.DECIDED:
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' must be in decided state before deployment planning",
        )

    if decision.decision not in {
        DecisionOutcome.APPROVED,
        DecisionOutcome.APPROVED_WITH_CONDITIONS,
    }:
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' must be approved before deployment planning",
        )

    if decision.target_id != artifact_id:
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' target_id does not match artifact_id",
        )

    if decision.target_version != artifact_version:
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' target_version does not match artifact_version",
        )

    if decision.capital_pool_id not in (None, "", capital_pool_id):
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' capital_pool_id does not match deployment capital_pool_id",
        )

    if sponsor_persona_id and decision.persona_id not in (None, "", sponsor_persona_id):
        raise HTTPException(
            status_code=422,
            detail=f"ApprovalDecision '{decision_id}' persona_id does not match sponsor_persona_id",
        )

    return _serialize_approval_decision(decision)


def create_app() -> FastAPI:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="Pantheon Promotion Service", version="0.1.0")
    register_fastapi_health_routes(
        app,
        "promotion-svc",
        dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
        metrics=lambda: {
            "approval_count": len(approval_store.list_all()),
            "deployment_count": len(deployment_store.list_all()),
        },
        details=lambda: {
            "port": PORT,
            "data_dir": str(DATA_DIR),
            "store_backend": STORE_BACKEND,
            "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
        },
    )

    @app.get("/__health__")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "promotion-svc",
            "port": PORT,
            "data_dir": str(DATA_DIR),
        }

    @app.post("/api/v1/approvals", status_code=status.HTTP_201_CREATED)
    async def create_approval(body: ApprovalCreateRequest) -> dict[str, Any]:
        decision_id = body.decision_id or f"apv-{uuid.uuid4().hex[:12]}"
        if approval_store.get(decision_id):
            raise HTTPException(status_code=409, detail=f"ApprovalDecision '{decision_id}' already exists")

        decision = ApprovalDecision.create_proposed(
            decision_id=decision_id,
            target_type=body.target_type.value,
            target_id=body.target_id,
            target_version=body.target_version,
            risk_level=body.risk_level.value,
            capital_pool_id=body.capital_pool_id,
            persona_id=body.persona_id,
        )
        errors = decision.validate()
        if errors:
            raise HTTPException(status_code=422, detail={"validation_errors": errors})

        approval_store.put(decision)
        return decision.to_dict()

    @app.get("/api/v1/approvals")
    async def list_approvals(
        target_type: str | None = Query(default=None),
        target_id: str | None = Query(default=None),
        decision_state: str | None = Query(default=None),
        risk_level: str | None = Query(default=None),
    ) -> dict[str, Any]:
        approvals = approval_store.list_all()

        def _enum_value(value: Any) -> Any:
            return getattr(value, "value", value)

        if target_type:
            approvals = [item for item in approvals if _enum_value(item.target_type) == target_type]
        if target_id:
            approvals = [item for item in approvals if item.target_id == target_id]
        if decision_state:
            approvals = [item for item in approvals if _enum_value(item.decision_state) == decision_state]
        if risk_level:
            approvals = [item for item in approvals if _enum_value(item.risk_level) == risk_level]

        approvals.sort(key=lambda item: item.created_at, reverse=True)
        return {"count": len(approvals), "items": [item.to_dict() for item in approvals]}

    @app.post("/api/v1/approvals/{decision_id}/decide", status_code=status.HTTP_200_OK)
    async def decide_approval(decision_id: str, body: ApprovalDecideRequest) -> dict[str, Any]:
        decision = approval_store.get(decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail=f"ApprovalDecision '{decision_id}' not found")
        if decision.decision_state not in (DecisionState.PROPOSED, DecisionState.UNDER_REVIEW):
            raise HTTPException(
                status_code=409,
                detail=f"ApprovalDecision '{decision_id}' is already in '{decision.decision_state}' state",
            )
        # Work on a deep copy so partial mutations (accept_review -> under_review) never
        # escape to the store if the subsequent decide() call raises ValueError.
        working = copy.deepcopy(decision)
        if working.decision_state == DecisionState.PROPOSED:
            try:
                working.accept_review(body.actor_role, body.actor_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            working.decide(
                outcome=DecisionOutcome(body.outcome),
                rationale=body.rationale,
                actor_role=body.actor_role,
                actor_id=body.actor_id,
                conditions=body.conditions or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        approval_store.put(working)
        return _serialize_approval_decision(working)

    @app.post("/api/v1/deployments", status_code=status.HTTP_201_CREATED)
    async def create_deployment(body: DeploymentCreateRequest) -> JSONResponse:
        plan_id = body.plan_id or f"dp-{uuid.uuid4().hex[:12]}"
        if deployment_store.get(plan_id):
            raise HTTPException(status_code=409, detail=f"DeploymentPlan '{plan_id}' already exists")

        approval_decision = _validated_approval_decision(
            body.approval_decision_id,
            artifact_id=body.artifact_id,
            artifact_version=body.artifact_version,
            capital_pool_id=body.capital_pool_id,
            sponsor_persona_id=body.sponsor_persona_id,
        )

        rollback = RollbackRef(**body.rollback.model_dump()) if body.rollback else None
        schedule_window = (
            ScheduleWindow(**body.schedule_window.model_dump()) if body.schedule_window else None
        )
        scale = DeploymentScale(**body.scale.model_dump()) if body.scale else None
        binding_id = body.binding_id or body.persona_capital_binding_id
        metadata = dict(body.metadata or {})
        if body.loader_checks_passed is not None:
            metadata.setdefault("loader_checks_passed", body.loader_checks_passed)
        if body.persona_capital_binding_status:
            metadata.setdefault("persona_capital_binding_status", body.persona_capital_binding_status)
        if body.allowed_deployment_scope:
            metadata.setdefault("allowed_deployment_scope", body.allowed_deployment_scope)

        try:
            plan = stage_planner.create_plan(
                plan_id=plan_id,
                approval_decision_id=body.approval_decision_id,
                approval_decision=approval_decision,
                registry_entry={
                    "registry_id": body.artifact_id,
                    "version": body.artifact_version,
                    "artifact_type": body.artifact_type,
                    "strategy_id": body.strategy_id,
                    "artifact_state": "approved",
                },
                capital_pool_id=body.capital_pool_id,
                target_stage=body.target_stage,
                current_stage=body.current_stage,
                created_by=body.created_by,
                sponsor_persona_id=body.sponsor_persona_id,
                runtime_config_ref=body.runtime_config_ref,
                binding_id=binding_id,
                schedule_window=schedule_window,
                scale=scale,
                rollback=rollback,
                pre_checks=body.pre_checks,
                post_checks=body.post_checks,
                metadata=metadata or None,
                supersedes_plan_id=body.supersedes_plan_id,
                status=PlanStatus(body.status),
            )
        except DeploymentPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        deployment_store.put(plan)
        deployment_ext_store.put(
            plan.plan_id,
            {
                "persona_capital_binding_id": body.persona_capital_binding_id or binding_id,
                "persona_capital_binding_status": body.persona_capital_binding_status,
                "allowed_deployment_scope": body.allowed_deployment_scope,
                "loader_checks_passed": body.loader_checks_passed,
                "approval_decision": approval_decision,
            },
        )

        response = _deployment_response(plan)
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=response)

    @app.get("/api/v1/deployments")
    async def list_deployments(
        status_value: str | None = Query(default=None, alias="status"),
        capital_pool_id: str | None = Query(default=None),
        approval_decision_id: str | None = Query(default=None),
        target_stage: str | None = Query(default=None),
    ) -> dict[str, Any]:
        plans = deployment_store.list_all()

        def _enum_value(value: Any) -> Any:
            return getattr(value, "value", value)

        if status_value:
            plans = [item for item in plans if _enum_value(item.status) == status_value]
        if capital_pool_id:
            plans = [item for item in plans if item.capital_pool_id == capital_pool_id]
        if approval_decision_id:
            plans = [item for item in plans if item.approval_decision_id == approval_decision_id]
        if target_stage:
            plans = [item for item in plans if _enum_value(item.target_stage) == target_stage]

        plans.sort(key=lambda item: item.created_at, reverse=True)
        return {"count": len(plans), "items": [_deployment_response(item) for item in plans]}

    return app


app = create_app()

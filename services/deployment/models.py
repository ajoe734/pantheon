"""
Wire models for the deployable DeploymentPlan service.

These models deliberately mirror the canonical control-plane deployment-plan
domain without importing pydantic into the platform-domain module.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DeploymentStageBody(str, Enum):
    NONE = "none"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    FROZEN = "frozen"


class TransitionTypeBody(str, Enum):
    ACTIVATE = "activate"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    FREEZE = "freeze"
    RESUME = "resume"


class RuntimeActionBody(str, Enum):
    DEPLOY_NEW_BINDING = "deploy_new_binding"
    REPLACE_BINDING = "replace_binding"
    FREEZE_BINDING = "freeze_binding"
    RESUME_BINDING = "resume_binding"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


class RollbackActionTypeBody(str, Enum):
    REPLACE = "replace"
    PAUSE_THEN_REPLACE = "pause_then_replace"
    LIQUIDATE_THEN_REPLACE = "liquidate_then_replace"


class PlanStatusBody(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    ABORTED = "aborted"
    REJECTED = "rejected"
    FAILED = "failed"


class ScheduleWindowBody(BaseModel):
    start_at: str
    end_at: Optional[str] = None


class DeploymentScaleBody(BaseModel):
    capital_scale_pct: float
    gross_scale_pct: float
    ramp_schedule: List[str] = Field(default_factory=list)


class RollbackRefBody(BaseModel):
    target_artifact_id: str
    target_version: str
    action_type: RollbackActionTypeBody = RollbackActionTypeBody.REPLACE
    reason: Optional[str] = None
    verified_at: Optional[str] = None


class DeploymentPlanBody(BaseModel):
    plan_id: str
    approval_decision_id: str
    artifact_id: str
    artifact_version: str
    artifact_type: str
    strategy_id: str
    capital_pool_id: str
    current_stage: str
    target_stage: str
    transition_type: str
    runtime_action: str
    status: str
    created_at: str
    created_by: Optional[str] = None
    sponsor_persona_id: Optional[str] = None
    runtime_config_ref: Optional[str] = None
    binding_id: Optional[str] = None
    schedule_window: Optional[ScheduleWindowBody] = None
    scale: Optional[DeploymentScaleBody] = None
    rollback: Optional[RollbackRefBody] = None
    pre_checks: List[str] = Field(default_factory=list)
    post_checks: List[str] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    supersedes_plan_id: Optional[str] = None


class CreateDeploymentPlanRequest(BaseModel):
    plan_id: Optional[str] = None
    approval_decision_id: str
    capital_pool_id: Optional[str] = None
    target_stage: DeploymentStageBody
    registry_id: Optional[str] = None
    registry_entry: Optional[Dict[str, Any]] = None
    approval_decision: Optional[Dict[str, Any]] = None
    current_stage: Optional[DeploymentStageBody] = None
    created_by: Optional[str] = None
    sponsor_persona_id: Optional[str] = None
    runtime_config_ref: Optional[str] = None
    binding_id: Optional[str] = None
    schedule_window: Optional[ScheduleWindowBody] = None
    scale: Optional[DeploymentScaleBody] = None
    rollback: Optional[RollbackRefBody] = None
    pre_checks: List[str] = Field(default_factory=list)
    post_checks: List[str] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    supersedes_plan_id: Optional[str] = None
    status: PlanStatusBody = PlanStatusBody.APPROVED


class ValidateDeploymentPlanResponse(BaseModel):
    ok: bool
    plan: Optional[DeploymentPlanBody] = None
    errors: List[str] = Field(default_factory=list)


class UpdatePlanStatusRequest(BaseModel):
    status: PlanStatusBody


class DeploymentPlanSummary(BaseModel):
    plan_id: str
    artifact_id: str
    artifact_version: str
    strategy_id: str
    capital_pool_id: str
    current_stage: str
    target_stage: str
    transition_type: str
    runtime_action: str
    status: str
    created_at: str
    approval_decision_id: str


class StrategyReadModelResponse(BaseModel):
    strategy_id: str
    capital_pool_id: Optional[str] = None
    current_stage: str
    latest_plan_id: Optional[str] = None
    active_plan_id: Optional[str] = None
    latest_target_stage: Optional[str] = None
    latest_transition_type: Optional[str] = None
    latest_status: Optional[str] = None
    plan_count: int
    plans: List[DeploymentPlanSummary] = Field(default_factory=list)

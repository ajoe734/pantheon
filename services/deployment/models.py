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


class SagaStatusBody(str, Enum):
    AWAITING_BINDING = "awaiting_binding"
    AWAITING_RUNTIME_LOAD = "awaiting_runtime_load"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    FAILED = "failed"
    ABORTED = "aborted"


class SagaStepBody(str, Enum):
    BINDING_REQUESTED = "binding_requested"
    RUNTIME_LOAD_REQUESTED = "runtime_load_requested"
    RUNTIME_ACTIVE = "runtime_active"
    COMPENSATION_REQUESTED = "compensation_requested"
    COMPENSATED = "compensated"


class OutboxStatusBody(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class ReceiptStatusBody(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"


class CompensationCommandBody(str, Enum):
    ABORT_PLAN = "abort_plan"
    MARK_BINDING_FAILED_INACTIVE = "mark_binding_failed_inactive"
    REQUEST_ROLLBACK = "request_rollback"
    ENTER_SAFE_MODE_AND_RAISE_INCIDENT = "enter_safe_mode_and_raise_incident"


class SagaStepRecordBody(BaseModel):
    step: SagaStepBody
    status: SagaStatusBody
    event_id: str
    sequence_no: int
    emitted_at: str
    note: Optional[str] = None


class SagaEventEnvelopeBody(BaseModel):
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    causal_parent_id: Optional[str] = None
    event_time: str
    emitted_at: str
    trace_id: str
    idempotency_key: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class OutboxRecordBody(BaseModel):
    owner_service: str
    event: SagaEventEnvelopeBody
    status: OutboxStatusBody
    delivery_attempts: int
    published_at: Optional[str] = None
    last_error: Optional[str] = None


class InboxReceiptBody(BaseModel):
    consumer_name: str
    event_id: str
    idempotency_key: str
    aggregate_type: str
    aggregate_id: str
    sequence_no: int
    trace_id: str
    status: ReceiptStatusBody
    processed_at: str
    notes: Optional[str] = None


class CompensationDecisionBody(BaseModel):
    command_type: CompensationCommandBody
    owner_service: str
    plan_status: str
    binding_status: str
    write_scope: str
    runtime_action: Optional[str] = None
    incident_required: bool = False
    safe_mode: bool = False
    reason: Optional[str] = None


class DeploymentSagaBody(BaseModel):
    saga_id: str
    plan_id: str
    approval_decision_id: str
    strategy_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    current_stage: str
    target_stage: str
    runtime_action: str
    rollback_action_type: Optional[str] = None
    status: SagaStatusBody
    current_step: SagaStepBody
    trace_id: str
    created_at: str
    updated_at: str
    binding_id: Optional[str] = None
    runtime_id: Optional[str] = None
    last_sequence_no: int
    last_event_id: Optional[str] = None
    failure_reason: Optional[str] = None
    compensation: Optional[CompensationDecisionBody] = None
    metadata: Optional[Dict[str, Any]] = None
    history: List[SagaStepRecordBody] = Field(default_factory=list)


class DeploymentSagaBootstrapBody(BaseModel):
    saga: DeploymentSagaBody
    outbox_event: OutboxRecordBody


class DeploymentExecutionProjectionBody(BaseModel):
    metadata_key: str
    artifact_key: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeploymentProjectionReadModelResponse(BaseModel):
    projection_contract: str = "DEP-003"
    derived_only: bool = True
    plan_id: str
    strategy_id: str
    artifact_id: str
    artifact_version: str
    capital_pool_id: str
    approval_decision_id: str
    current_stage: str
    target_stage: str
    projected_stage: str
    actual_stage: str
    plan_status: str
    approval_outcome: Optional[str] = None
    approval_state: Optional[str] = None
    runtime_binding_id: Optional[str] = None
    runtime_id: Optional[str] = None
    runtime_status: Optional[str] = None
    deployment_saga_id: Optional[str] = None
    deployment_saga_status: Optional[str] = None
    lifecycle_state: str
    source_status: Dict[str, str] = Field(default_factory=dict)
    summary: Dict[str, Any] = Field(default_factory=dict)
    plan: DeploymentPlanBody
    approval_decision: Optional[Dict[str, Any]] = None
    runtime_binding: Optional[Dict[str, Any]] = None
    deployment_saga: Optional[DeploymentSagaBody] = None
    execution_projection: Optional[DeploymentExecutionProjectionBody] = None


class DispatchDeploymentPlanRequest(BaseModel):
    trace_id: Optional[str] = None
    correlation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    actor_id: Optional[str] = None
    saga_id: Optional[str] = None
    source_task_id: Optional[str] = None
    workflow_id: Optional[str] = None
    registry_entry: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class DeploymentDispatchResponse(BaseModel):
    plan: DeploymentPlanBody
    strategy_id: str
    version: str
    target_stage: str
    execution_context: str
    artifact_loader_contract: str
    deployment_contract: str
    consistency_contract: str
    execution_projection: DeploymentExecutionProjectionBody
    deployment_saga: DeploymentSagaBootstrapBody
    replayed: bool = False


class RecordBindingCreatedRequest(BaseModel):
    binding_id: str
    runtime_id: Optional[str] = None
    note: Optional[str] = None


class RecordRuntimeActiveRequest(BaseModel):
    binding_id: Optional[str] = None
    runtime_id: Optional[str] = None
    note: Optional[str] = None


class RecordSagaFailureRequest(BaseModel):
    reason: str
    failed_step: Optional[SagaStepBody] = None


class FinalizeCompensationRequest(BaseModel):
    note: Optional[str] = None
    terminal_status: Optional[SagaStatusBody] = None


class ConsumeOutboxEventRequest(BaseModel):
    consumer_name: str


class PoolCompatibilityRequest(BaseModel):
    capital_pool_id: str
    target_stage: DeploymentStageBody
    sponsor_persona_id: Optional[str] = None


class PoolCompatibilityResponse(BaseModel):
    ok: bool
    capital_pool_id: str
    target_stage: str
    pool_found: Optional[bool] = None
    pool_status: Optional[str] = None
    pool_active: Optional[bool] = None
    single_runtime_enforced: Optional[bool] = None
    persona_binding_found: Optional[bool] = None
    persona_scope_ok: Optional[bool] = None
    persona_binding_id: Optional[str] = None
    allowed_deployment_scope: Optional[str] = None
    active_runtime_binding_count: Optional[int] = None
    active_runtime_binding_ids: List[str] = Field(default_factory=list)
    single_runtime_ok: Optional[bool] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

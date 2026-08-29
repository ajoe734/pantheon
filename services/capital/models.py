"""
Wire models for the deployable capital service boundary.

These models wrap the canonical governance objects:
- CapitalPool
- PersonaCapitalBinding
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapitalPoolBody(BaseModel):
    pool_id: str
    name: str
    owner_id: str
    owner_type: str
    status: str
    created_at: str
    description: Optional[str] = None
    currency: str = "USD"
    budget: Optional[float] = None
    risk_policy_ref: Optional[str] = None
    single_runtime_enforced: bool = True
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    idempotent_replay: bool = False


class RiskPolicyBody(BaseModel):
    risk_policy_id: str
    version: str = "v1"
    name: Optional[str] = None
    status: str = "active"
    gross_limit: Optional[float] = None
    net_limit: Optional[float] = None
    max_single_name_weight: Optional[float] = None
    max_sector_exposure: Optional[Dict[str, float]] = None
    max_factor_exposure: Optional[Dict[str, float]] = None
    max_leverage: Optional[float] = None
    turnover_limit: Optional[float] = None
    liquidity_constraints: Dict[str, Any] = Field(default_factory=dict)
    drawdown_actions: Dict[str, float] = Field(default_factory=dict)
    pause_rules: Dict[str, Any] = Field(default_factory=dict)
    liquidation_rules: Dict[str, Any] = Field(default_factory=dict)
    allowed_order_types: List[str] = Field(default_factory=list)
    allowed_time_in_force: List[str] = Field(default_factory=list)
    allowed_asset_classes: List[str] = Field(default_factory=list)
    forbidden_asset_classes: List[str] = Field(default_factory=list)
    allowed_strategy_families: List[str] = Field(default_factory=list)
    forbidden_strategy_families: List[str] = Field(default_factory=list)
    max_strategy_family_concentration: Optional[Union[Dict[str, float], float]] = None
    max_target_overlap: Optional[float] = None
    max_signal_correlation: Optional[float] = None
    allowed_stages: List[str] = Field(default_factory=list)
    max_canary_capital_scale_pct: Optional[float] = None
    max_canary_gross_scale_pct: Optional[float] = None
    kill_switch_triggers: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RiskPolicyEvaluationContextBody(BaseModel):
    target_type: str
    target_id: str
    capital_pool_id: str
    stage: Optional[str] = None
    risk_policy_ref: Optional[str] = None
    target_weights: Dict[str, float] = Field(default_factory=dict)
    gross_exposure: Optional[float] = None
    net_exposure: Optional[float] = None
    leverage: Optional[float] = None
    turnover: Optional[float] = None
    asset_classes: List[str] = Field(default_factory=list)
    strategy_family: Optional[str] = None
    strategy_family_concentration: Dict[str, float] = Field(default_factory=dict)
    target_overlap: Optional[float] = None
    signal_correlation: Optional[float] = None
    sector_exposures: Dict[str, float] = Field(default_factory=dict)
    factor_exposures: Dict[str, float] = Field(default_factory=dict)
    liquidity: Dict[str, Any] = Field(default_factory=dict)
    order_type: Optional[str] = None
    time_in_force: Optional[str] = None
    drawdown_pct: Optional[float] = None
    capital_scale_pct: Optional[float] = None
    gross_scale_pct: Optional[float] = None
    runtime_action: Optional[str] = None
    kill_switch_trigger: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace_id: Optional[str] = None


class RiskPolicyEvaluationResponse(BaseModel):
    risk_policy_id: str
    risk_policy_version: str
    capital_pool_id: str
    target_type: str
    target_id: str
    decision: str
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    blocking_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    evaluated_at: str
    trace_id: str


class PersonaCapitalBindingBody(BaseModel):
    binding_id: str
    persona_id: str
    capital_pool_id: str
    capital_sleeve_id: Optional[str] = None
    role: str
    allowed_deployment_scope: str
    status: str
    created_at: str
    mandate: Optional[str] = None
    budget: Optional[float] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    approval_decision_id: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    idempotent_replay: bool = False


class CreateCapitalPoolRequest(BaseModel):
    actor_id: str
    actor_role: str
    idempotency_key: Optional[str] = None
    request_hash: Optional[str] = None
    pool_id: Optional[str] = None
    name: str
    owner_id: str
    owner_type: str
    status: str = "active"
    description: Optional[str] = None
    currency: str = "USD"
    budget: Optional[float] = None
    risk_policy_ref: Optional[str] = None
    single_runtime_enforced: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UpdateCapitalPoolStatusRequest(BaseModel):
    actor_id: str
    actor_role: str
    status: str


class PatchCapitalPoolRequest(BaseModel):
    """Canonical Capital owner patch; omitted and explicit-null are distinct."""

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1)
    actor_role: str = Field(min_length=1)
    name: Optional[str] = Field(default=None, min_length=1)
    status: Optional[str] = None
    risk_policy_ref: Optional[str] = None
    params: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def require_patch_field(self) -> "PatchCapitalPoolRequest":
        if not (self.model_fields_set - {"actor_id", "actor_role"}):
            raise ValueError("at least one CapitalPool patch field is required")
        return self


class CreateBindingRequest(BaseModel):
    actor_id: str
    actor_role: str
    idempotency_key: Optional[str] = None
    request_hash: Optional[str] = None
    binding_id: Optional[str] = None
    persona_id: str
    capital_pool_id: str
    capital_sleeve_id: Optional[str] = None
    role: str
    allowed_deployment_scope: str
    mandate: Optional[str] = None
    budget: Optional[float] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    created_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActivateBindingRequest(BaseModel):
    actor_id: str
    actor_role: str
    approval_decision_id: str


class UpdateBindingStatusRequest(BaseModel):
    actor_id: str
    actor_role: str
    status: str


class BindingAdmissibilityResponse(BaseModel):
    persona_id: str
    capital_pool_id: str
    target_stage: str
    permitted: bool
    pool_status: str
    single_runtime_enforced: bool
    binding_id: Optional[str] = None
    binding_role: Optional[str] = None
    binding_status: Optional[str] = None
    allowed_deployment_scope: Optional[str] = None
    active_live_owner_binding_id: Optional[str] = None
    reason: Optional[str] = None


class WriteAuthorityEntry(BaseModel):
    resource_type: str
    operation: str
    authorized_roles: List[str]


class WriteAuthorityResponse(BaseModel):
    matrix: List[WriteAuthorityEntry]
    description: str


class RebalanceAllocationLine(BaseModel):
    model_config = ConfigDict(extra="allow")

    ranking_snapshot_id: str = Field(min_length=1)
    allocation_evaluation_id: str = Field(min_length=1)
    allocation_line_digest: str = Field(min_length=1)
    allocation_policy_version: str = Field(min_length=1)
    persona_id: str
    stage: str
    capital_scope: str = "pool"
    capital_pool_id: Optional[str] = None
    capital_sleeve_id: Optional[str] = None
    current_weight: float
    target_weight: float
    delta: Optional[float] = None
    cap_reasons: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)


class CreateRebalanceRequest(BaseModel):
    actor_id: str
    actor_role: str
    idempotency_key: str
    request_hash: str
    rebalance_id: Optional[str] = None
    capital_pool_id: str
    ranking_snapshot_id: str = Field(min_length=1)
    allocation_evaluation_id: str = Field(min_length=1)
    allocation_policy_version: str = Field(min_length=1)
    reason: str = ""
    lines: List[RebalanceAllocationLine]
    simulation: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    rollback_target: Dict[str, Any] = Field(default_factory=dict)
    audit_refs: List[str] = Field(default_factory=list)


class ApplyRebalanceRequest(BaseModel):
    actor_id: str
    actor_role: str
    idempotency_key: str
    request_hash: str
    command_id: str
    approval_ref: Optional[str] = None
    receipt_ref: Optional[str] = None
    audit_ref: Optional[str] = None


class AllocationBody(BaseModel):
    allocation_id: str
    capital_pool_id: str
    persona_id: str
    capital_scope: str
    binding_id: Optional[str] = None
    current_weight: float
    target_weight: float
    allocation_version: int
    binding_state: str
    capital_sleeve_id: Optional[str] = None
    stage: Optional[str] = None
    containment_state: Optional[str] = None
    last_rebalance_id: Optional[str] = None
    updated_at: str
    authoritative_capital_readback: bool = True
    canonical_write_authority: str = "capital_service"


class AllocationListResponse(BaseModel):
    items: List[AllocationBody]
    count: int
    snapshot_at: str
    source: str = "capital_service"
    authoritative_capital_readback: bool = True


class RebalanceBody(BaseModel):
    id: str
    rebalance_id: str
    capital_pool_id: str
    status: str
    applied: bool
    lines: List[Dict[str, Any]]
    reason: str = ""
    ranking_snapshot_id: Optional[str] = None
    allocation_evaluation_id: Optional[str] = None
    allocation_policy_version: Optional[str] = None
    simulation: Dict[str, Any] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    rollback_target: Dict[str, Any] = Field(default_factory=dict)
    audit_refs: List[str] = Field(default_factory=list)
    approval_ref: Optional[str] = None
    apply_command_id: Optional[str] = None
    apply_receipt_ref: Optional[str] = None
    apply_audit_ref: Optional[str] = None
    apply_receipt: Optional[Dict[str, Any]] = None
    applied_at: Optional[str] = None
    failure: Optional[Dict[str, Any]] = None
    request_hash: str
    created_at: str
    updated_at: str
    created_by: str
    canonical_write_authority: str = "capital_service"
    persistence_mode: str = "owner_store"


class RebalanceApplyReceipt(BaseModel):
    status: str
    rebalance_id: str
    capital_pool_id: str
    command_id: str
    approval_ref: Optional[str] = None
    receipt_ref: str
    audit_ref: str
    request_hash: str
    payload_hash: str
    applied_at: str
    allocation_readback: List[AllocationBody]
    authoritative_capital_readback: bool
    authoritative_capital_state_applied: bool
    live_capital_side_effects: bool
    canonical_write_authority: str
    audit_delivery_status: str = "pending"
    audit_delivery_attempts: int = 0
    audit_delivery_error: Optional[str] = None
    audit_event_id: Optional[str] = None
    audit_delivered_at: Optional[str] = None
    idempotent_replay: bool = False


class CreateContainmentRequest(BaseModel):
    actor_id: str
    actor_role: str
    idempotency_key: str
    request_hash: str
    persona_id: str
    action: str
    trigger: str
    evidence_refs: List[str]
    containment_id: Optional[str] = None
    capital_pool_id: Optional[str] = None
    current_weight: Optional[float] = None
    target_weight: Optional[float] = None
    target_stage: Optional[str] = None
    allocation_increase: bool = False
    command_id: Optional[str] = None
    approval_ref: Optional[str] = None
    two_man_signature_id: Optional[str] = None
    receipt_ref: Optional[str] = None
    audit_ref: Optional[str] = None


class ContainmentBody(BaseModel):
    containment_id: str
    persona_id: str
    action: str
    state: str
    containment_state: str
    status: str
    trigger: str
    evidence_refs: List[str]
    baseline_weight: float
    current_weight: float
    target_weight: float
    command_id: str
    receipt_ref: str
    audit_ref: str
    request_hash: str
    payload_hash: str
    executed_at: str
    capital_pool_id: Optional[str] = None
    approval_ref: Optional[str] = None
    two_man_signature_id: Optional[str] = None
    authoritative_containment_readback: bool
    authoritative_capital_readback: bool
    authoritative_capital_state_applied: bool
    live_capital_side_effects: bool
    canonical_write_authority: str
    audit_delivery_status: str = "pending"
    audit_delivery_attempts: int = 0
    audit_delivery_error: Optional[str] = None
    audit_event_id: Optional[str] = None
    audit_delivered_at: Optional[str] = None
    idempotent_replay: bool = False

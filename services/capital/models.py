"""
Wire models for the deployable capital service boundary.

These models wrap the canonical governance objects:
- CapitalPool
- PersonaCapitalBinding
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


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


class CreateCapitalPoolRequest(BaseModel):
    actor_id: str
    actor_role: str
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


class CreateBindingRequest(BaseModel):
    actor_id: str
    actor_role: str
    binding_id: Optional[str] = None
    persona_id: str
    capital_pool_id: str
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

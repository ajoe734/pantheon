"""
Wire models for the deployable capital service boundary.

These models wrap the canonical governance objects:
- CapitalPool
- PersonaCapitalBinding
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

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

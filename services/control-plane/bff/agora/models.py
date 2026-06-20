"""Agora BFF shared models — envelope, typed errors, and capability scope.

§18 envelope: {data: T, meta: AgoraMeta} — the standard Agora response shape.
Typed errors: AgoraErrorCode + AgoraError for domain-specific failure modes.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Response envelope (§18)
# --------------------------------------------------------------------------- #

class AgoraMeta(BaseModel):
    """Metadata attached to every Agora response."""
    snapshot_at: str
    capability: Optional[str] = None
    audience: Optional[str] = None


class AgoraListMeta(AgoraMeta):
    total: int = 0
    page_size: int = 0
    next_page_token: Optional[str] = None


class AgoraEnvelope(BaseModel, Generic[T]):
    """Standard single-resource Agora response envelope."""
    data: T
    meta: AgoraMeta


class AgoraListEnvelope(BaseModel, Generic[T]):
    """Standard list Agora response envelope."""
    data: List[T]
    meta: AgoraListMeta


# --------------------------------------------------------------------------- #
# Identity / scope models
# --------------------------------------------------------------------------- #


class AgoraReadPredicate(BaseModel):
    """Mandatory backend predicate for user-private Agora read models."""
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    required_fields: List[str] = Field(default_factory=lambda: ["tenant_id", "user_id"])
    fail_closed: bool = True


class AgoraServantPolicy(BaseModel):
    """Phase-1 servant persona metadata/policy carried in existing Persona records."""
    persona_class: Literal["agora_servant"] = "agora_servant"
    owner_scope: Literal["user_private"] = "user_private"
    visibility_scope: Literal["private", "redacted_management"] = "private"
    memory_scope: Literal["private_user"] = "private_user"
    persona_registry_backed: bool = True
    execution_authority: Literal["none"] = "none"
    prohibited_authority: List[str] = Field(
        default_factory=lambda: ["runtime_binding", "broker_order", "capital_binding"]
    )


class AgoraCapabilityScope(BaseModel):
    """Operator-level Agora capability scope returned by /bff/agora/me."""
    spec_version: Literal["1.0"] = "1.0"
    scope_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    granted_capabilities: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    denied_capabilities: List[str] = Field(default_factory=list)
    surfaces: List[str] = Field(default_factory=lambda: ["agora"])
    persona_ids: List[str] = Field(default_factory=list)
    read_predicate: AgoraReadPredicate
    servant_policy: AgoraServantPolicy = Field(default_factory=AgoraServantPolicy)
    created_at: str
    expires_at: Optional[str] = None
    policy_refs: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ServantCapabilitySummary(BaseModel):
    """Operator-visible high-level servant capability flags."""
    can_ask: bool
    can_research: bool
    can_workshop: bool
    can_shadow: bool = False
    asset_classes: List[str] = Field(default_factory=list)
    strategy_families: List[str] = Field(default_factory=list)
    allowed_agora_capabilities: List[str] = Field(default_factory=list)


class ServantProfile(BaseModel):
    """User-private Agora servant profile backed by the existing Persona Registry."""
    spec_version: Literal["1.0"] = "1.0"
    persona_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status: Literal["active", "suspended", "paper_only", "shadow_only", "retired"]
    tenant_id: str = Field(min_length=1)
    agora_user_id: str = Field(min_length=1)
    persona_class: Literal["agora_servant"] = "agora_servant"
    owner_scope: Literal["user_private"] = "user_private"
    visibility_scope: Literal["private", "redacted_management"] = "private"
    memory_scope: Literal["private_user"] = "private_user"
    capability_summary: ServantCapabilitySummary
    policy: AgoraServantPolicy = Field(default_factory=AgoraServantPolicy)
    description: Optional[str] = None
    avatar_ref: Optional[str] = None
    last_active_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Typed error codes
# --------------------------------------------------------------------------- #

class AgoraErrorCode(str, Enum):
    NOT_IMPLEMENTED = "AGORA_NOT_IMPLEMENTED"
    CAPABILITY_DENIED = "AGORA_CAPABILITY_DENIED"
    AUDIENCE_MISMATCH = "AGORA_AUDIENCE_MISMATCH"
    LIVE_ORDER_ROUTE_FORBIDDEN = "AGORA_LIVE_ORDER_ROUTE_FORBIDDEN"
    SERVANT_PROVISION_FAILED = "AGORA_SERVANT_PROVISION_FAILED"
    WORKSHOP_CONFLICT = "AGORA_WORKSHOP_CONFLICT"
    REDACTION_FAILED = "AGORA_REDACTION_FAILED"
    RAW_PRIVATE_CONTENT_FORBIDDEN = "RAW_PRIVATE_CONTENT_FORBIDDEN"
    SESSION_NOT_FOUND = "AGORA_SESSION_NOT_FOUND"
    INSIGHT_NOT_FOUND = "AGORA_INSIGHT_NOT_FOUND"
    MEMORY_NOT_FOUND = "AGORA_MEMORY_NOT_FOUND"
    SHADOW_LIVE_ROUTE_REJECTED = "AGORA_SHADOW_LIVE_ROUTE_REJECTED"


class AgoraError(Exception):
    """Typed Agora domain error.  Handlers convert this to HTTPException."""
    def __init__(self, code: AgoraErrorCode, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# Capability constants (frozen with AG-XR-001)
# --------------------------------------------------------------------------- #

AGORA_CAPABILITIES: tuple[str, ...] = (
    "agora.identity.v1",
    "agora.session.v1",
    "agora.workshop.v1",
    "agora.research.v1",
    "agora.trading.v1",
    "agora.dashboard.v1",
    "agora.personalization.v1",
)

# All capabilities require operator-level role (see capability_manifest.json §auth_level)
AGORA_REQUIRED_ROLES = frozenset({"operator", "approver", "admin", "reviewer"})

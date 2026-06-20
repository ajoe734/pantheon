"""Agora BFF shared models — envelope, typed errors, and capability scope.

§18 envelope: {data: T, meta: AgoraMeta} — the standard Agora response shape.
Typed errors: AgoraErrorCode + AgoraError for domain-specific failure modes.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Generic, List, Optional, TypeVar

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
# Identity / scope models (skeleton — AG-BE-ID-001 fills the real fields)
# --------------------------------------------------------------------------- #

class AgoraCapabilityScope(BaseModel):
    """Operator-level Agora capability scope returned by /bff/agora/me."""
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)


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

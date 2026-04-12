from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CommandType(str, Enum):
    APPROVE_DEPLOYMENT = "ApproveDeployment"
    PAUSE_RUNTIME = "PauseRuntime"
    EXECUTE_ROLLBACK = "ExecuteRollback"
    ACTIVATE_KILL_SWITCH = "ActivateKillSwitch"
    APPROVE_EVOLUTION_DECISION = "ApproveEvolutionDecision"
    EXECUTE_EVOLUTION_ACTION = "ExecuteEvolutionAction"


class ObjectType(str, Enum):
    DEPLOYMENT_PLAN = "DeploymentPlan"
    RUNTIME_BINDING = "RuntimeBinding"
    KILL_SWITCH_ORDER = "KillSwitchOrder"
    EVOLUTION_DECISION = "EvolutionDecision"
    CAPITAL_POOL = "CapitalPool"
    PERSONA_CAPITAL_BINDING = "PersonaCapitalBinding"


class CommandStatus(str, Enum):
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    EXECUTED = "executed"
    FAILED = "failed"
    TIMEOUT = "timeout"


# --------------------------------------------------------------------------- #
# Error codes (§5.3 of APP-002-OPERATOR-ACTION-CONTRACT)
# --------------------------------------------------------------------------- #

class ErrorCode(str, Enum):
    INVALID_TOKEN = "INVALID_TOKEN"
    INSUFFICIENT_ROLE = "INSUFFICIENT_ROLE"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
    DOWNSTREAM_UNAVAILABLE = "DOWNSTREAM_UNAVAILABLE"
    PRECONDITION_NOT_MET = "PRECONDITION_NOT_MET"
    MFA_REQUIRED = "MFA_REQUIRED"
    INVALID_PARAMS = "INVALID_PARAMS"


class ErrorDetail(BaseModel):
    reason: str
    precondition_failed: Optional[str] = None
    suggestion: Optional[str] = None


class BFFError(BaseModel):
    code: ErrorCode
    message: str
    details: Optional[ErrorDetail] = None


class ErrorResponse(BaseModel):
    error: BFFError


# --------------------------------------------------------------------------- #
# Core request/response models
# --------------------------------------------------------------------------- #

class TargetObject(BaseModel):
    type: ObjectType
    id: str


class AuditContext(BaseModel):
    reason: str
    timestamp: str = Field(default_factory=utc_now)


class OperatorCommand(BaseModel):
    command: CommandType
    target: TargetObject
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    audit_context: AuditContext


class CommandReceipt(BaseModel):
    command_id: str
    command_type: CommandType
    target: TargetObject
    submitted_at: str
    status: CommandStatus
    tracking_url: str


class CommandResultMeta(BaseModel):
    estimated_processing_time_ms: int = 2000
    next_poll_after_ms: int = 500


class StalenessWarning(BaseModel):
    """Present when the command was submitted against stale read surface data."""
    read_surface_state: str  # "degraded" | "unavailable"
    message: str


class CommandSubmissionResponse(BaseModel):
    receipt: CommandReceipt
    meta: CommandResultMeta = Field(default_factory=CommandResultMeta)
    staleness_warning: Optional[StalenessWarning] = None


class CommandStatusResponse(BaseModel):
    command_id: str
    type: CommandType
    target: TargetObject
    submitted_at: str
    status: CommandStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    audit: Optional[Dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Operator token / identity (extracted from Bearer token in real deployments)
# --------------------------------------------------------------------------- #

class OperatorIdentity(BaseModel):
    operator_id: str
    roles: List[str]
    mfa_verified: bool = False

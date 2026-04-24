from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CommandType(str, Enum):
    APPROVE_DEPLOYMENT = "ApproveDeployment"
    APPROVE_DECISION = "ApproveDecision"
    REJECT_DECISION = "RejectDecision"
    REQUEST_APPROVAL_REVISION = "RequestApprovalRevision"
    PAUSE_RUNTIME = "PauseRuntime"
    PAUSE_EXECUTION = "PauseExecution"
    ESCALATE_DIFF = "EscalateDiff"
    ISSUE_RISK_OFF = "IssueRiskOff"
    LIQUIDATE_ALL = "LiquidateAll"
    HARD_ROLLBACK = "HardRollback"
    ISSUE_SAFE_MODE = "IssueSafeMode"
    EXECUTE_ROLLBACK = "ExecuteRollback"
    APPROVE_ROLLBACK = "ApproveRollback"
    REJECT_ROLLBACK = "RejectRollback"
    ACTIVATE_KILL_SWITCH = "ActivateKillSwitch"
    APPROVE_EVOLUTION_DECISION = "ApproveEvolutionDecision"
    EXECUTE_EVOLUTION_ACTION = "ExecuteEvolutionAction"
    APPROVE_MUTATION = "ApproveMutation"
    REJECT_MUTATION = "RejectMutation"
    RECORD_SPONSOR_DECISION = "RecordSponsorDecision"


class ObjectType(str, Enum):
    DEPLOYMENT_PLAN = "DeploymentPlan"
    APPROVAL_DECISION = "ApprovalDecision"
    RUNTIME = "Runtime"
    RUNTIME_BINDING = "RuntimeBinding"
    ROLLBACK = "Rollback"
    KILL_SWITCH_ORDER = "KillSwitchOrder"
    EVOLUTION_DECISION = "EvolutionDecision"
    CAPITAL_POOL = "CapitalPool"
    PERSONA_CAPITAL_BINDING = "PersonaCapitalBinding"
    COMMITTEE_BOARD = "CommitteeBoard"


class CommandStatus(str, Enum):
    SUBMITTED = "submitted"
    PROCESSING = "processing"
    EXECUTED = "executed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class CommandReceiptStatus(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    FAILED = "failed"


class CommandRoutingPath(str, Enum):
    DIRECT = "direct"
    FALLBACK = "fallback"


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
    incident_id: Optional[str] = None


class OperatorCommand(BaseModel):
    command: CommandType
    target: TargetObject
    action: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    audit_context: AuditContext


class ApproveMutationCommandPayload(BaseModel):
    command_type: Literal["ApproveMutation"]
    decision_id: str
    note: Optional[str] = None


class RejectMutationCommandPayload(BaseModel):
    command_type: Literal["RejectMutation"]
    decision_id: str
    note: Optional[str] = None


class RecordSponsorDecisionCommandPayload(BaseModel):
    command_type: Literal["RecordSponsorDecision"]
    committee_id: str
    sponsor_decision: Literal["approved", "rejected", "conditional"]
    rationale_ref: str
    note: Optional[str] = None


class CommandReceipt(BaseModel):
    receipt_id: str
    command_id: Optional[str] = None
    command: str
    status: CommandReceiptStatus
    accepted_at: str
    routing_path: CommandRoutingPath
    expected_completion_at: Optional[str] = None
    error_message: Optional[str] = None


class CommandResultMeta(BaseModel):
    estimated_processing_time_ms: int = 2000
    next_poll_after_ms: int = 500


class StalenessWarning(BaseModel):
    """Present when the command was submitted against stale read surface data."""
    read_surface_state: str  # "degraded" | "unavailable"
    message: str


class CommandSubmissionResponse(BaseModel):
    receipt_id: str
    command: str
    status: CommandReceiptStatus
    accepted_at: str
    routing_path: CommandRoutingPath
    expected_completion_at: Optional[str] = None
    error_message: Optional[str] = None
    staleness_warning: Optional[StalenessWarning] = None
    receipt: Optional[CommandReceipt] = None


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

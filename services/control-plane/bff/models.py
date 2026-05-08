from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field


T = TypeVar("T")


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
    REMEDIATE_SENTINEL_INTERVENTION = "RemediateSentinelIntervention"
    CAPITAL_POOL_ACTION = "CapitalPoolAction"
    RANKING_FORMULA_ACTION = "RankingFormulaAction"
    REBALANCE_ACTION = "RebalanceAction"
    RANKING_ACTION = "RankingAction"
    STRATEGY_ACTION = "StrategyAction"
    PERSONA_ACTION = "PersonaAction"
    AGORA_SIGNAL_FEEDBACK = "AgoraSignalFeedback"
    AGORA_MESSAGE_ACTION = "AgoraMessageAction"
    AGORA_INSIGHT_ACTION = "AgoraInsightAction"
    AGORA_MEMORY_ACTION = "AgoraMemoryAction"
    TOOL_ACTION = "ToolAction"
    MCP_SERVER_ACTION = "McpServerAction"
    SKILL_ACTION = "SkillAction"


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
    SENTINEL_INTERVENTION = "SentinelIntervention"
    RANKING_FORMULA = "RankingFormula"
    REBALANCE = "Rebalance"
    RANKING = "Ranking"
    STRATEGY = "Strategy"
    PERSONA = "Persona"
    AGORA_SIGNAL = "AgoraSignal"
    AGORA_MESSAGE = "AgoraMessage"
    AGORA_INSIGHT = "AgoraInsight"
    AGORA_MEMORY = "AgoraMemory"
    TOOL = "Tool"
    MCP_SERVER = "McpServer"
    SKILL = "Skill"


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


class ActionCommandStatus(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    COMPLETED = "completed"


class CommandRoutingPath(str, Enum):
    DIRECT = "direct"
    FALLBACK = "fallback"


# --------------------------------------------------------------------------- #
# Error codes (§5.3 of APP-002-OPERATOR-ACTION-CONTRACT)
# --------------------------------------------------------------------------- #

class ErrorCode(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_TOKEN = "INVALID_TOKEN"
    INSUFFICIENT_ROLE = "INSUFFICIENT_ROLE"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    CONCURRENT_MODIFICATION = "CONCURRENT_MODIFICATION"
    DOWNSTREAM_UNAVAILABLE = "DOWNSTREAM_UNAVAILABLE"
    PRECONDITION_NOT_MET = "PRECONDITION_NOT_MET"
    CONFIRM_TOKEN_REQUIRED = "CONFIRM_TOKEN_REQUIRED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TWO_MAN_REQUIRED = "TWO_MAN_REQUIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SSE_REPLAY_UNAVAILABLE = "SSE_REPLAY_UNAVAILABLE"
    MFA_REQUIRED = "MFA_REQUIRED"
    INVALID_PARAMS = "INVALID_PARAMS"


class ErrorDetail(BaseModel):
    reason: str
    precondition_failed: Optional[str] = None
    suggestion: Optional[str] = None


class BffErrorPayload(BaseModel):
    code: ErrorCode
    message: str
    details: Optional[ErrorDetail] = None


class BffErrorEnvelope(BaseModel):
    error: BffErrorPayload


class BFFError(BffErrorPayload):
    pass


class ErrorResponse(BffErrorEnvelope):
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


class CommandResponse(BaseModel, Generic[T]):
    status: ActionCommandStatus
    data: T
    meta: Optional[Dict[str, Any]] = None


class DecisionJournalEntryDTO(BaseModel):
    id: str
    title: str
    body: str = ""
    tags: List[str] = Field(default_factory=list)
    linkedStrategyIds: List[str] = Field(default_factory=list)
    linkedPersonaIds: List[str] = Field(default_factory=list)
    visibility: str = "private"
    createdAt: str = Field(default_factory=utc_now)
    updatedAt: str = Field(default_factory=utc_now)
    version: int = 1
    canonicalWriteAuthority: str = "agora_journal_service"
    persistenceMode: str = "bff_local_dev_store"


class JournalEntryMergePatch(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[List[str]] = None
    linkedStrategyIds: Optional[List[str]] = None
    linkedPersonaIds: Optional[List[str]] = None
    visibility: Optional[str] = None


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

class SseEventEnvelope(BaseModel, Generic[T]):
    id: str
    type: str
    timestamp: str = Field(default_factory=utc_now)
    data: T


class ApprovalCreatedPayload(BaseModel):
    approval_id: str
    target_type: ObjectType
    target_id: str
    requester_id: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApprovalStageChangedPayload(BaseModel):
    approval_id: str
    previous_stage: str
    current_stage: str
    actor_id: str


class ApprovalDecidedPayload(BaseModel):
    approval_id: str
    outcome: str
    decided_by: str
    reason: Optional[str] = None


class ApprovalSlaEscalatedPayload(BaseModel):
    approval_id: str
    severity: str
    message: str


class AskSessionStartedPayload(BaseModel):
    session_id: str
    persona_id: str
    context: Dict[str, Any] = Field(default_factory=dict)


class AskMessageDeltaPayload(BaseModel):
    session_id: str
    message_id: str
    delta: str


class AskToolCalledPayload(BaseModel):
    session_id: str
    tool_name: str
    call_id: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class AskMessageCompletedPayload(BaseModel):
    session_id: str
    message_id: str
    full_content: str


class AskSessionCompletedPayload(BaseModel):
    session_id: str
    outcome: str = "success"


class AskSessionFailedPayload(BaseModel):
    session_id: str
    error_code: str
    error_message: str


class OperatorIdentity(BaseModel):
    operator_id: str
    roles: List[str]
    mfa_verified: bool = False
    claims: Dict[str, Any] = Field(default_factory=dict)
    token_kind: str = "stub"


# --------------------------------------------------------------------------- #
# Action catalog models (BFF-FINAL-004)
# --------------------------------------------------------------------------- #

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BffActionCatalogEntry(BaseModel):
    action_id: str
    entity_type: str
    endpoint: str
    method: str = "POST"
    risk_level: RiskLevel
    requires_approval: bool = False
    requires_confirm_token: bool = False
    requires_two_man: bool = False
    cooldown_seconds: int = 0
    idempotency_required: bool = True
    required_roles: List[str] = Field(default_factory=list)
    description: str = ""


class BffActionCatalogResponse(BaseModel):
    catalog: List[BffActionCatalogEntry]
    version: str = "v1"
    generated_at: str = Field(default_factory=utc_now)


class McpToolClass(str, Enum):
    research = "research"
    status = "status"
    monitoring = "monitoring"
    execution_signal = "execution_signal"
    governance = "governance"
    deployment = "deployment"
    lean_direct = "lean_direct"


class McpToolActionVerb(str, Enum):
    GRANT = "grant"
    REVOKE = "revoke"
    DISABLE = "disable"
    TEST = "test"


class McpToolLifecycleStatus(str, Enum):
    IMPORTED = "imported"
    GRANTED = "granted"
    REVOKED = "revoked"
    DISABLED = "disabled"
    TESTED = "tested"


class McpToolActionDescriptor(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action_id: str = Field(alias="actionId")
    action_type: str = Field(default="invoke", alias="actionType")
    description: str = ""
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, alias="riskLevel")
    requires_approval: bool = Field(default=False, alias="requiresApproval")
    allow_standalone_create: bool = Field(default=False, alias="allowStandaloneCreate")
    governance_flag: Optional[str] = Field(default=None, alias="governanceFlag")


class McpToolDescriptor(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    tool_id: str = Field(alias="toolId")
    name: str
    description: str = ""
    tool_class: McpToolClass = Field(alias="toolClass")
    input_schema: Dict[str, Any] = Field(default_factory=dict, alias="inputSchema")
    output_schema: Dict[str, Any] = Field(default_factory=dict, alias="outputSchema")
    schema_url: Optional[str] = Field(default=None, alias="schemaUrl")
    actions: List[McpToolActionDescriptor] = Field(default_factory=list)


class McpToolImportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    server_name: Optional[str] = Field(default=None, alias="serverName")
    server_version: Optional[str] = Field(default=None, alias="serverVersion")
    schema_url: Optional[str] = Field(default=None, alias="schemaUrl")
    governance: Dict[str, Any] = Field(default_factory=dict)
    tools: List[McpToolDescriptor]


class McpImportedTool(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(alias="toolId")
    server_id: str = Field(alias="serverId")
    name: str
    tool_class: McpToolClass = Field(alias="toolClass")
    status: McpToolLifecycleStatus
    schema_url: Optional[str] = Field(default=None, alias="schemaUrl")
    action_count: int = Field(alias="actionCount")
    standalone_create_enabled: bool = Field(default=False, alias="standaloneCreateEnabled")


class McpRejectedTool(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: Optional[str] = Field(default=None, alias="toolId")
    reason: str
    precondition_failed: str = Field(alias="preconditionFailed")


class McpToolImportData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    import_id: str = Field(alias="importId")
    server_id: str = Field(alias="serverId")
    imported_tools: List[McpImportedTool] = Field(default_factory=list, alias="importedTools")
    rejected_tools: List[McpRejectedTool] = Field(default_factory=list, alias="rejectedTools")
    replayed: bool = False


class McpToolActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str
    scope: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(default=False, alias="dryRun")


class McpToolActionData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(alias="toolId")
    server_id: str = Field(alias="serverId")
    action: McpToolActionVerb
    status: McpToolLifecycleStatus
    admitted: bool
    replayed: bool = False


class EvidenceKind(str, Enum):
    alert = "alert"
    incident = "incident"
    job = "job"
    audit = "audit"
    metric = "metric"
    strategy = "strategy"
    persona = "persona"
    deployment = "deployment"
    runtime = "runtime"
    policy = "policy"
    approval = "approval"
    artifact = "artifact"
    signal = "signal"
    journal = "journal"
    postmortem = "postmortem"


# Backend capability map for evidence kinds
EVIDENCE_CAPABILITY_MAP: Dict[str, str] = {
    "alert": "risk.alert.read",
    "incident": "risk.incident.read",
    "job": "job.read",
    "audit": "audit.read",
    "metric": "metric.read",
    "strategy": "strategy.view",
    "persona": "persona.view",
    "deployment": "deployment.read",
    "runtime": "runtime.read",
    "policy": "policy.read",
    "approval": "approval.read",
    "artifact": "artifact.read",
    "signal": "agora.signal.read",
    "journal": "agora.journal.read",
    "postmortem": "postmortem.read",
}


# Maps source_document.source_type values to EvidenceKind strings so refs
# that carry no explicit evidence_type still get capability-gated.
SOURCE_TYPE_TO_EVIDENCE_KIND: Dict[str, str] = {
    "postmortem": "postmortem",
    "incident_report": "incident",
    "audit_log": "audit",
    "experiment_artifact": "artifact",
    "alert": "alert",
    "metric": "metric",
    "internal_metric": "metric",
    "runtime_snapshot": "runtime",
    "deployment_log": "deployment",
    "strategy_spec": "strategy",
    "journal_entry": "journal",
    "agora_signal": "signal",
    "policy_document": "policy",
}


class RedactedEvidenceRef(BaseModel):
    ref_id: str
    kind: Optional[EvidenceKind] = None
    required_capability: str
    reason: str = "insufficient_capability"
    redacted: bool = True
    display_label: Optional[str] = None
    redacted_count: Optional[int] = None


# --------------------------------------------------------------------------- #
# v5 Interventions — HIQ Sentinel remediation (BFF-FINAL-009)
# --------------------------------------------------------------------------- #

class InterventionStatus(str, Enum):
    PENDING = "pending"
    REMEDIATED = "remediated"
    DISMISSED = "dismissed"
    ESCALATED = "escalated"


class InterventionKind(str, Enum):
    HIQ_SENTINEL = "hiq_sentinel"
    RISK_BREACH = "risk_breach"
    STRATEGY_DRIFT = "strategy_drift"
    LOOP_ANOMALY = "loop_anomaly"


class InterventionRecord(BaseModel):
    intervention_id: str
    kind: InterventionKind
    status: InterventionStatus
    target_type: str
    target_id: str
    triggered_at: str
    triggered_by: str = "sentinel"
    remediation_action: Optional[str] = None
    remediated_at: Optional[str] = None
    two_man_signature_id: Optional[str] = None
    correlation_id: Optional[str] = None
    description: str = ""


class InterventionListResponse(BaseModel):
    items: List[InterventionRecord]
    count: int
    generated_at: str = Field(default_factory=utc_now)

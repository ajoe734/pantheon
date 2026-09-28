from __future__ import annotations

import copy as _copy
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Generic, List, Literal, Optional, TypeVar

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
    REVIEW_MUTATION = "ReviewMutation"
    EXECUTE_MUTATION = "ExecuteMutation"
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
    REVIEW_ACTION = "ReviewAction"
    DEPLOYMENT_ACTION = "DeploymentAction"
    DEPLOYMENT_CREATE = "CreateDeployment"
    DEPLOYMENT_PATCH = "PatchDeployment"
    RUNTIME_ACTION = "RuntimeAction"
    RISK_ALERT_ACTION = "RiskAlertAction"
    INCIDENT_ACTION = "IncidentAction"
    EVOLUTION_PROGRAM_ACTION = "EvolutionProgramAction"
    EXPERIMENT_ACTION = "ExperimentAction"
    JOB_ACTION = "JobAction"
    REBALANCE_PATCH = "PatchRebalance"
    AUDIT_EXPORT = "AuditExport"
    CONFIRM_TOKEN_CREATE = "CreateConfirmToken"
    CONFIRM_TOKEN_DELETE = "DeleteConfirmToken"
    CONFIRM_TOKEN_REDEEM = "RedeemConfirmToken"
    V5_INTERVENTION_ACTION = "V5InterventionAction"
    DECIDE_V5_INTERVENTION = "DecideV5Intervention"
    SENTINEL_FINDING_STATUS = "SentinelFindingStatus"
    SENTINEL_REMEDIATION_BUILD = "SentinelRemediationBuild"
    SENTINEL_REMEDIATION_EXECUTE = "SentinelRemediationExecute"
    ALERT_ACKNOWLEDGE = "AlertAcknowledge"
    HUMAN_GATE_APPROVE = "HumanGateApprove"
    HUMAN_GATE_REJECT = "HumanGateReject"
    HUMAN_GATE_REQUEST_MORE_EVIDENCE = "HumanGateRequestMoreEvidence"
    HUMAN_GATE_REVOKE = "HumanGateRevoke"
    HUMAN_GATE_EXTEND_TTL = "HumanGateExtendTtl"
    QUARTERLY_RANKING_RECOMMENDATION_SUBMIT = "QuarterlyRankingRecommendationSubmit"
    # BFF-WRITE-P0-LIFECYCLE: P0-1/2/3 lifecycle action types
    ADVANCE_LIFECYCLE = "AdvanceLifecycle"
    APPROVE_POOL = "ApprovePool"
    START_RUNTIME = "StartRuntime"
    RESTART_PAPER_RUNTIME = "RestartPaperRuntime"
    RESTART_TELEMETRY_BRIDGE = "RestartTelemetryBridge"
    TERMINATE_STALE_PAPER_MONITORING_SESSION = "TerminateStalePaperMonitoringSession"
    START_PAPER_MONITORING_SESSION = "StartPaperMonitoringSession"
    PROBE_TELEMETRY_INGEST = "ProbeTelemetryIngest"
    OBSERVE = "Observe"
    REQUEST_REVIEW = "RequestReview"
    PAUSE_PAPER_RUNTIME = "PausePaperRuntime"
    RESUME_PAPER_RUNTIME = "ResumePaperRuntime"
    DEMOTE = "Demote"
    PROMOTE_CANDIDATE = "PromoteCandidate"
    REBALANCE_PROPOSAL = "RebalanceProposal"
    REBALANCE_APPROVAL = "RebalanceApproval"
    REBALANCE_TWO_MAN_SIGN = "RebalanceTwoManSign"
    APPROVED_APPLY = "ApprovedApply"
    EMERGENCY_CONTAINMENT = "EmergencyContainment"


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
    REVIEW = "Review"
    DEPLOYMENT = "Deployment"
    RISK_ALERT = "RiskAlert"
    INCIDENT = "Incident"
    EVOLUTION_PROGRAM = "EvolutionProgram"
    EXPERIMENT = "Experiment"
    JOB = "Job"
    AUDIT_EXPORT = "AuditExport"
    CONFIRM_TOKEN = "ConfirmToken"
    SENTINEL_FINDING = "SentinelFinding"
    SENTINEL_REMEDIATION = "SentinelRemediation"
    HUMAN_GATE_ITEM = "HumanGateItem"


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
# Error codes (Pack D §D21 canonical allowlist)
# --------------------------------------------------------------------------- #

class ErrorCode(str, Enum):
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    BUSINESS_RULE_VIOLATION = "BUSINESS_RULE_VIOLATION"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    TWO_MAN_SIGNATURE_REQUIRED = "TWO_MAN_SIGNATURE_REQUIRED"
    HUMAN_GATE_PENDING = "HUMAN_GATE_PENDING"
    HUMAN_GATE_REJECTED = "HUMAN_GATE_REJECTED"
    HUMAN_GATE_EXPIRED = "HUMAN_GATE_EXPIRED"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    MAINTENANCE_MODE = "MAINTENANCE_MODE"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    SAFE_MODE_ACTIVE = "SAFE_MODE_ACTIVE"
    DEGRADED_READ_ONLY = "DEGRADED_READ_ONLY"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"


class ErrorDetail(BaseModel):
    reason: str
    precondition_failed: Optional[str] = None
    suggestion: Optional[str] = None


class BffErrorPayload(BaseModel):
    code: ErrorCode
    i18nKey: str
    message: str
    retryable: bool
    userActionable: bool
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


class ReviewMutationCommandPayload(BaseModel):
    command_type: Literal["ReviewMutation"]
    decision_id: str
    approval_decision_id: str
    note: Optional[str] = None


class ExecuteMutationCommandPayload(BaseModel):
    command_type: Literal["ExecuteMutation"]
    decision_id: str
    has_active_runtime: bool = False
    active_binding_id: Optional[str] = None
    freeze_mode: str = "governance_only"
    rollback_action_type: Optional[str] = None
    fallback_artifact_id: Optional[str] = None
    fallback_artifact_version: Optional[str] = None
    force_stage_freeze: bool = False
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
    "deployment_plan": "deployment",
    "strategy_spec": "strategy",
    "journal_entry": "journal",
    "agora_signal": "signal",
    "policy_document": "policy",
    # Generic knowledge-source document types (KW03 evidence refs): no
    # dedicated EvidenceKind, so they gate on the generic artifact
    # capability rather than falling through unresolved.
    "external_paper": "artifact",
    "research_note": "artifact",
    # Generic knowledge-linkage entity types (KW03/KW04 linked_decisions and
    # linked_object_summary.entity_type): no dedicated EvidenceKind, so they
    # gate on the generic artifact capability rather than falling through
    # unresolved.
    "memory_entry": "artifact",
    "experiment": "artifact",
    # Governance review-queue evidence ref "type" values (PKT001):
    "IncidentReport": "incident",
    "BacktestResult": "artifact",
    # Management-console live-evidence workflow artifact (BB3):
    "workflow_artifact": "artifact",
}


# Maps URI scheme aliases to EvidenceKind strings so authoritative references
# resolve directly without being misclassified by incidental path keywords.
URI_SCHEME_TO_EVIDENCE_KIND: Dict[str, str] = {
    # Broker / audit aliases:
    "broker": "audit",
    "broker-evidence": "audit",
    "paper-broker": "audit",
    "broker-adapter": "audit",
    "broker-sandbox": "audit",
    "broker-subaccount": "audit",
    "audit-log": "audit",
    # Metric / telemetry aliases:
    "telemetry-event": "metric",
    "telemetry": "metric",
    # Signal aliases:
    "agora-signal": "signal",
    "signal-inference": "signal",
    # Journal aliases:
    "agora-journal": "journal",
    # Policy aliases:
    "policy-decision": "policy",
    "risk-policy": "policy",
    "risk-adjudication": "policy",
    "persona-policy": "policy",
    # Approval aliases:
    "approval-decision": "approval",
    # Incident aliases:
    "incident-report": "incident",
    # Artifact aliases:
    "research-artifact": "artifact",
    # Strategy aliases:
    "strategy-spec": "strategy",
}


class RedactedEvidenceRef(BaseModel):
    ref_id: str
    kind: Optional[EvidenceKind] = None
    required_capability: str
    reason: str = "insufficient_capability"
    redacted: bool = True
    display_label: Optional[str] = None
    redacted_count: Optional[int] = None


def _resolve_evidence_kind_and_capability(
    ref: Any,
    *,
    default_kind: Optional[str] = None,
    kind_map: Optional[Mapping[str, str]] = None,
) -> tuple[str, Optional[EvidenceKind], Optional[str]]:
    """Resolve an evidence ref's kind key, ``EvidenceKind``, and required capability.

    Shared by ``redact_evidence_refs`` (normal capability-gated redaction) and
    the fail-closed fallback below, so both paths report the same
    ``required_capability`` for a ref whose kind is known, rather than one of
    them silently dropping it. Supports fallback to ``default_kind`` and ref_id
    lookup in ``kind_map`` when an item is a string reference.
    """
    kind_key = ""
    ref_id = ""
    if isinstance(ref, dict):
        ref_id = str(
            ref.get("ref_id") or ref.get("id") or ref.get("artifact_ref") or ref.get("entity_ref") or ""
        ).strip()
        raw_kind = (
            str(ref.get("evidence_type") or "").strip()
            or str(ref.get("type") or "").strip()
            or str(ref.get("ref_type") or "").strip()
            or str(ref.get("link_type") or "").strip()
        )
        if raw_kind in SOURCE_TYPE_TO_EVIDENCE_KIND:
            kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND[raw_kind]
        elif raw_kind in EVIDENCE_CAPABILITY_MAP:
            kind_key = raw_kind
        elif raw_kind in URI_SCHEME_TO_EVIDENCE_KIND:
            kind_key = URI_SCHEME_TO_EVIDENCE_KIND[raw_kind]

        if not kind_key:
            source_document = ref.get("source_document")
            if isinstance(source_document, dict):
                source_type = str(source_document.get("source_type") or "").strip()
                if source_type in SOURCE_TYPE_TO_EVIDENCE_KIND:
                    kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND[source_type]
                elif source_type in EVIDENCE_CAPABILITY_MAP:
                    kind_key = source_type
                elif source_type in URI_SCHEME_TO_EVIDENCE_KIND:
                    kind_key = URI_SCHEME_TO_EVIDENCE_KIND[source_type]

        if not kind_key:
            # linked-decision/linked-object-summary entity type (KW03/KW04):
            # a decision or summary item may identify its own object type
            # here instead of via an explicit evidence/ref/link type field.
            # Checked after (not folded into) the raw_kind chain above, since
            # a present-but-unresolvable link_type (e.g. "supporting_evidence")
            # must not shadow a resolvable entity_type.
            entity_type = str(ref.get("entity_type") or "").strip()
            if entity_type in SOURCE_TYPE_TO_EVIDENCE_KIND:
                kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND[entity_type]
            elif entity_type in EVIDENCE_CAPABILITY_MAP:
                kind_key = entity_type
            elif entity_type in URI_SCHEME_TO_EVIDENCE_KIND:
                kind_key = URI_SCHEME_TO_EVIDENCE_KIND[entity_type]
    else:
        ref_id = str(ref).strip()

    if not kind_key and kind_map and ref_id in kind_map:
        mapped_kind = str(kind_map[ref_id]).strip()
        if mapped_kind in SOURCE_TYPE_TO_EVIDENCE_KIND:
            kind_key = SOURCE_TYPE_TO_EVIDENCE_KIND[mapped_kind]
        elif mapped_kind in EVIDENCE_CAPABILITY_MAP:
            kind_key = mapped_kind
        elif mapped_kind in URI_SCHEME_TO_EVIDENCE_KIND:
            kind_key = URI_SCHEME_TO_EVIDENCE_KIND[mapped_kind]

    # 1. Authoritative URI scheme check: if ref_id (or candidate ref strings in dict) has a scheme (e.g. audit://...),
    # resolve directly to the scheme's evidence kind or alias. Authoritative URI
    # schemes must not be downgraded by incidental path keywords.
    if not kind_key:
        candidates = [ref_id]
        if isinstance(ref, dict):
            for f in ("artifact_ref", "link", "id", "ref_id"):
                val = str(ref.get(f) or "").strip()
                if val and val not in candidates:
                    candidates.append(val)
        for cand in candidates:
            if "://" in cand:
                scheme = cand.split("://", 1)[0].strip().lower()
                if scheme in EVIDENCE_CAPABILITY_MAP:
                    kind_key = scheme
                    break
                elif scheme in URI_SCHEME_TO_EVIDENCE_KIND:
                    kind_key = URI_SCHEME_TO_EVIDENCE_KIND[scheme]
                    break

    # 2. Authoritative field kind: if the container field specifies a domain-specific
    # default_kind (audit_refs -> audit, incident_refs -> incident, postmortem_refs -> postmortem,
    # policy_decision_refs -> policy, broker_evidence_refs -> audit, etc.),
    # that field assignment is authoritative over incidental substring keywords.
    if not kind_key and default_kind:
        dk = str(default_kind).strip()
        if dk in EVIDENCE_CAPABILITY_MAP:
            if dk != "artifact":
                kind_key = dk
        elif dk in URI_SCHEME_TO_EVIDENCE_KIND:
            kind_key = URI_SCHEME_TO_EVIDENCE_KIND[dk]

    # 3. Fallback keyword matching: for generic bundles (default_kind='artifact'
    # or None) and non-URI references, resolve specific evidence kinds by keyword pattern.
    if not kind_key and ref_id:
        lower = ref_id.lower()
        if "approval" in lower:
            kind_key = "approval"
        elif "postmortem" in lower:
            kind_key = "postmortem"
        elif "incident" in lower:
            kind_key = "incident"
        elif "audit" in lower:
            kind_key = "audit"
        elif "metric" in lower:
            kind_key = "metric"
        elif "policy" in lower:
            kind_key = "policy"
        elif "alert" in lower:
            kind_key = "alert"

    # 4. Final fallback to default_kind (e.g. 'artifact' for remaining bundle items).
    if not kind_key and default_kind:
        dk = str(default_kind).strip()
        if dk in EVIDENCE_CAPABILITY_MAP:
            kind_key = dk
        elif dk in URI_SCHEME_TO_EVIDENCE_KIND:
            kind_key = URI_SCHEME_TO_EVIDENCE_KIND[dk]

    required_capability = EVIDENCE_CAPABILITY_MAP.get(kind_key) if kind_key else None
    try:
        evidence_kind = EvidenceKind(kind_key) if kind_key else None
    except (TypeError, ValueError):
        evidence_kind = None
    return kind_key, evidence_kind, required_capability


def fail_closed_redacted_refs(
    refs: list[Any],
    *,
    default_kind: Optional[str] = None,
    kind_map: Optional[Mapping[str, str]] = None,
) -> tuple[list[dict[str, Any]], int]:
    """Withhold every ref because the redaction policy itself is unavailable.

    Used when a capability lookup or a canonical redact call raises, so no
    individual ref can be verified safe to disclose. Still resolves
    ``required_capability`` from the known evidence-kind map when the ref's
    kind can be determined, instead of dropping that field for every ref.
    """
    redacted: list[dict[str, Any]] = []
    for ref in refs:
        if isinstance(ref, dict):
            ref_id = str(ref.get("ref_id") or ref.get("id") or ref.get("artifact_ref") or ref.get("entity_ref") or "")
        else:
            ref_id = str(ref)
        _, evidence_kind, required_capability = _resolve_evidence_kind_and_capability(
            ref, default_kind=default_kind, kind_map=kind_map
        )
        entry: dict[str, Any] = {
            "ref_id": ref_id,
            "redacted": True,
            "required_capability": required_capability or "unknown",
            "reason": "redaction_policy_unavailable",
        }
        if evidence_kind is not None:
            entry["kind"] = evidence_kind
        redacted.append(entry)
    return redacted, len(redacted)


def redact_evidence_refs(
    identity: OperatorIdentity,
    evidence_refs: list[Any],
    capabilities: Optional[list[str]] = None,
    *,
    default_kind: Optional[str] = None,
    kind_map: Optional[Mapping[str, str]] = None,
) -> tuple[list[Any], int]:
    """Redact evidence references that require an unavailable capability.

    ``identity`` remains part of the route-facing contract even though the
    current policy is expressed entirely by the supplied capability set.
    Supports string references and dicts with optional ``default_kind`` and
    ``kind_map`` overrides.

    Fails closed: a missing capability set (``None``) is treated as an
    identity with no capabilities rather than full visibility, and a ref
    whose kind cannot be resolved is withheld rather than passed through,
    since its ``required_capability`` cannot be verified either way.
    """

    del identity
    capability_set = set(capabilities) if capabilities is not None else set()
    processed: list[Any] = []
    redacted_count = 0

    for ref in evidence_refs:
        ref_id = (
            str(ref.get("ref_id") or ref.get("id") or ref.get("artifact_ref") or ref.get("entity_ref") or "")
            if isinstance(ref, dict)
            else str(ref)
        )
        _, evidence_kind, required_capability = _resolve_evidence_kind_and_capability(
            ref, default_kind=default_kind, kind_map=kind_map
        )
        if not required_capability:
            redacted_count += 1
            redacted = RedactedEvidenceRef(
                ref_id=ref_id,
                kind=evidence_kind,
                required_capability="unknown",
                reason="unresolved_evidence_kind",
            )
            processed.append(redacted.model_dump())
            continue
        if required_capability not in capability_set:
            redacted_count += 1
            redacted = RedactedEvidenceRef(
                ref_id=ref_id,
                kind=evidence_kind,
                required_capability=required_capability,
                reason="insufficient_capability",
            )
            processed.append(redacted.model_dump())
            continue
        processed.append(ref)

    return processed, redacted_count


def safe_redact_evidence_refs(
    identity: Any,
    refs: list[Any],
    *,
    redact_fn: Callable[..., tuple[list[Any], int]],
    capabilities_fn: Callable[[Any], Any],
    default_kind: Optional[str] = None,
    kind_map: Optional[Mapping[str, str]] = None,
) -> tuple[list[Any], int]:
    """Resolve capabilities and redact, failing closed on any error.

    Shared fail-closed wrapper for any BFF read surface that emits
    identity-gated evidence references (``evidence_refs``,
    ``linked_evidence``, ``context_refs``, and similar capability-gated
    reference lists). A capability lookup that raises or returns ``None``
    fails closed with ``reason="redaction_policy_unavailable"`` -- distinct
    from ``redact_evidence_refs``'s own ``capabilities=None`` handling
    (identity with no capabilities, ``reason="insufficient_capability"``),
    because here the caller could not even determine the identity's
    capabilities, so no individual ref's authorization is knowable either
    way.
    """
    try:
        capabilities = capabilities_fn(identity)
    except Exception:
        capabilities = None
    if capabilities is None:
        return fail_closed_redacted_refs(refs, default_kind=default_kind, kind_map=kind_map)
    try:
        kwargs: dict[str, Any] = {"capabilities": capabilities}
        if default_kind is not None:
            kwargs["default_kind"] = default_kind
        if kind_map is not None:
            kwargs["kind_map"] = kind_map
        try:
            return redact_fn(identity, refs, **kwargs)
        except TypeError:
            if "kind_map" in kwargs:
                kwargs.pop("kind_map")
                try:
                    return redact_fn(identity, refs, **kwargs)
                except TypeError:
                    pass
            if "default_kind" in kwargs:
                kwargs.pop("default_kind")
            return redact_fn(identity, refs, capabilities=capabilities)
    except Exception:
        return fail_closed_redacted_refs(refs, default_kind=default_kind, kind_map=kind_map)


def safe_redact_scalar_ref(
    identity: Any,
    ref: Any,
    *,
    redact_fn: Callable[..., tuple[list[Any], int]],
    capabilities_fn: Callable[[Any], Any],
    default_kind: Optional[str] = None,
    kind_map: Optional[Mapping[str, str]] = None,
) -> tuple[Any, int]:
    """Resolve capabilities and redact a scalar evidence reference.

    Preserves the original reference when authorized or when the reference
    requires no capability. Withholds unauthorized references or when
    capabilities cannot be resolved, returning a RedactedEvidenceRef dict and count 1.
    """
    if not ref:
        return ref, 0
    redacted_refs, count = safe_redact_evidence_refs(
        identity,
        [ref],
        redact_fn=redact_fn,
        capabilities_fn=capabilities_fn,
        default_kind=default_kind,
        kind_map=kind_map,
    )
    if count > 0 and redacted_refs:
        return redacted_refs[0], count
    return ref, 0


def redact_evidence_field_items(
    identity: Any,
    items: list[Any],
    *,
    field: str,
    redact_fn: Callable[..., tuple[list[dict[str, Any]], int]],
    capabilities_fn: Callable[[Any], Any],
) -> tuple[list[Any], int]:
    """Redact a top-level evidence-ref list ``field`` on each dict in ``items``.

    Shared by any handler whose response is a flat list of dicts that may
    carry a capability-gated reference list directly on the item.
    Non-dict items and items missing (or with an empty) ``field`` pass
    through unchanged.
    """
    total_redacted = 0
    redacted_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            redacted_items.append(item)
            continue
        item_copy = _copy.deepcopy(item)
        raw_refs = item_copy.get(field)
        if isinstance(raw_refs, list) and raw_refs:
            processed_refs, count = safe_redact_evidence_refs(
                identity, raw_refs, redact_fn=redact_fn, capabilities_fn=capabilities_fn
            )
            item_copy[field] = processed_refs
            total_redacted += count
        redacted_items.append(item_copy)
    return redacted_items, total_redacted


def redact_ooda_packet(
    identity: Any,
    packet: Any,
    *,
    redact_fn: Callable[..., tuple[list[Any], int]],
    capabilities_fn: Callable[[Any], Any],
) -> tuple[Any, int]:
    """Redact capability-gated evidence references across a canonical OODA packet.

    Handles top-level ``audit_refs`` and ``evidence_refs`` as well as nested
    bundles: ObserveBundle (``incident_refs``, ``signal_refs``, and any dict
    items in remaining lists), OrientBundle (``persona_proposal_refs``,
    ``signal_inference_refs``, ``evidence_bundle_refs``, and scalar
    ``risk_adjudication_ref``), DecideBundle (``policy_decision_refs``, scalar
    ``decision_rationale_ref``), ActBundle (``broker_evidence_refs``), and
    LearnBundle (``postmortem_refs``). Preserves full-capability visibility
    when the caller holds required capabilities, withholds unauthorized refs
    with standard RedactedEvidenceRef metadata, resolves repeated evidence
    consistently across canonical packet locations, and fails closed when
    capabilities cannot be resolved.
    """
    if not isinstance(packet, dict):
        return packet, 0

    packet_copy = _copy.deepcopy(packet)
    total_redacted = 0

    try:
        resolved_caps = capabilities_fn(identity)
    except Exception:
        resolved_caps = None

    # Step 1: Collect all references across canonical packet locations and build
    # a unified kind_map so repeated references resolve consistently everywhere.
    ref_kinds: dict[str, set[str]] = {}

    def _inspect_ref(item: Any, default_kind: Optional[str]) -> None:
        if not item:
            return
        ref_id = str(item.get("ref_id") or item.get("id") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not ref_id:
            return
        kind_key, _, _ = _resolve_evidence_kind_and_capability(item, default_kind=default_kind)
        if kind_key:
            ref_kinds.setdefault(ref_id, set()).add(kind_key)

    def _inspect_field(container: Any, field_name: str, default_kind: Optional[str]) -> None:
        if not isinstance(container, dict):
            return
        val = container.get(field_name)
        if isinstance(val, list):
            for elem in val:
                _inspect_ref(elem, default_kind)
        elif isinstance(val, (str, dict)) and val:
            _inspect_ref(val, default_kind)

    _inspect_field(packet_copy, "audit_refs", "audit")
    _inspect_field(packet_copy, "evidence_refs", None)

    obs = packet_copy.get("observe")
    _inspect_field(obs, "incident_refs", "incident")
    _inspect_field(obs, "signal_refs", "signal")
    _inspect_field(obs, "telemetry_refs", "runtime")
    for f in ("source_refs", "market_refs", "human_feedback_refs"):
        _inspect_field(obs, f, "artifact")

    ori = packet_copy.get("orient")
    _inspect_field(ori, "persona_proposal_refs", "persona")
    _inspect_field(ori, "signal_inference_refs", "signal")
    _inspect_field(ori, "evidence_bundle_refs", "artifact")
    _inspect_field(ori, "risk_adjudication_ref", "policy")
    for f in ("allocation_proposal_refs", "regime_state_ref", "universe_selection_ref"):
        _inspect_field(ori, f, "artifact")

    dec = packet_copy.get("decide")
    _inspect_field(dec, "policy_decision_refs", "policy")
    _inspect_field(dec, "decision_rationale_ref", "artifact")

    act = packet_copy.get("act")
    _inspect_field(act, "broker_evidence_refs", "audit")
    for f in ("command_receipt_refs", "rollback_refs", "safe_mode_refs"):
        _inspect_field(act, f, "audit")

    lrn = packet_copy.get("learn")
    _inspect_field(lrn, "postmortem_refs", "postmortem")
    _inspect_field(lrn, "telemetry_refs", "runtime")
    for f in ("evolution_followthrough_refs", "trainer_refs", "retrain_refs"):
        _inspect_field(lrn, f, "artifact")

    packet_kind_map: dict[str, str] = {}
    if resolved_caps is not None:
        cap_set = set(resolved_caps)
        for ref_id, kinds in ref_kinds.items():
            selected = None
            for k in sorted(kinds):
                req_cap = EVIDENCE_CAPABILITY_MAP.get(k)
                if req_cap and req_cap not in cap_set:
                    selected = k
                    break
            packet_kind_map[ref_id] = selected or sorted(kinds)[0]
    else:
        for ref_id, kinds in ref_kinds.items():
            packet_kind_map[ref_id] = "audit" if "audit" in kinds else sorted(kinds)[0]

    def _redact_field(
        container: dict[str, Any], field_name: str, *, default_kind: Optional[str] = None
    ) -> None:
        nonlocal total_redacted
        raw = container.get(field_name)
        if isinstance(raw, list) and raw:
            redacted_refs, count = safe_redact_evidence_refs(
                identity,
                raw,
                redact_fn=redact_fn,
                capabilities_fn=capabilities_fn,
                default_kind=default_kind,
                kind_map=packet_kind_map,
            )
            container[field_name] = redacted_refs
            total_redacted += count
        elif isinstance(raw, (str, dict)) and raw:
            redacted_val, count = safe_redact_scalar_ref(
                identity,
                raw,
                redact_fn=redact_fn,
                capabilities_fn=capabilities_fn,
                default_kind=default_kind,
                kind_map=packet_kind_map,
            )
            if count > 0:
                container[field_name] = redacted_val
                total_redacted += count

    # Top-level refs:
    _redact_field(packet_copy, "audit_refs", default_kind="audit")
    _redact_field(packet_copy, "evidence_refs", default_kind=None)

    # ObserveBundle:
    observe = packet_copy.get("observe")
    if isinstance(observe, dict):
        _redact_field(observe, "incident_refs", default_kind="incident")
        _redact_field(observe, "signal_refs", default_kind="signal")
        _redact_field(observe, "telemetry_refs", default_kind="runtime")
        for other_field in ("source_refs", "market_refs", "human_feedback_refs"):
            _redact_field(observe, other_field, default_kind="artifact")

    # OrientBundle:
    orient = packet_copy.get("orient")
    if isinstance(orient, dict):
        _redact_field(orient, "persona_proposal_refs", default_kind="persona")
        _redact_field(orient, "signal_inference_refs", default_kind="signal")
        _redact_field(orient, "evidence_bundle_refs", default_kind="artifact")
        _redact_field(orient, "risk_adjudication_ref", default_kind="policy")
        for other_field in ("allocation_proposal_refs", "regime_state_ref", "universe_selection_ref"):
            _redact_field(orient, other_field, default_kind="artifact")

    # DecideBundle:
    decide = packet_copy.get("decide")
    if isinstance(decide, dict):
        _redact_field(decide, "policy_decision_refs", default_kind="policy")
        _redact_field(decide, "decision_rationale_ref", default_kind="artifact")

    # ActBundle:
    act = packet_copy.get("act")
    if isinstance(act, dict):
        _redact_field(act, "broker_evidence_refs", default_kind="audit")
        for other_field in ("command_receipt_refs", "rollback_refs", "safe_mode_refs"):
            _redact_field(act, other_field, default_kind="audit")

    # LearnBundle:
    learn = packet_copy.get("learn")
    if isinstance(learn, dict):
        _redact_field(learn, "postmortem_refs", default_kind="postmortem")
        _redact_field(learn, "telemetry_refs", default_kind="runtime")
        for other_field in ("evolution_followthrough_refs", "trainer_refs", "retrain_refs"):
            _redact_field(learn, other_field, default_kind="artifact")

    return packet_copy, total_redacted


def redact_ooda_packet_items(
    identity: Any,
    packets: list[Any],
    *,
    redact_fn: Callable[..., tuple[list[Any], int]],
    capabilities_fn: Callable[[Any], Any],
) -> tuple[list[Any], int]:
    """Redact evidence references across a list of OODA packets for the returned page."""
    total_redacted = 0
    redacted_packets: list[Any] = []
    for packet in packets:
        redacted_packet, count = redact_ooda_packet(
            identity, packet, redact_fn=redact_fn, capabilities_fn=capabilities_fn
        )
        redacted_packets.append(redacted_packet)
        total_redacted += count
    return redacted_packets, total_redacted


def redact_settings_bundle(
    identity: Any,
    bundle: dict[str, Any],
    *,
    redact_fn: Callable[..., tuple[list[Any], int]],
    capabilities_fn: Callable[[Any], Any],
) -> tuple[dict[str, Any], int]:
    """Redact evidence references across all supported locations in a settings bundle.

    Scans top-level and nested sections (e.g. ``risk``, ``trading``, etc.) for
    any capability-gated reference lists (``evidence_refs``, ``linked_evidence``),
    redacts unauthorized entries while preserving authorized visibility, and
    fails closed when capability resolution is unavailable. Returns the
    redacted bundle copy and the total count of redacted references.
    """
    if not isinstance(bundle, dict):
        return bundle, 0

    bundle_copy = _copy.deepcopy(bundle)
    total_redacted = 0

    def _traverse(node: Any) -> None:
        nonlocal total_redacted
        if isinstance(node, dict):
            for field in ("evidence_refs", "linked_evidence"):
                raw = node.get(field)
                if isinstance(raw, list) and raw:
                    redacted_refs, count = safe_redact_evidence_refs(
                        identity,
                        raw,
                        redact_fn=redact_fn,
                        capabilities_fn=capabilities_fn,
                    )
                    node[field] = redacted_refs
                    total_redacted += count
            for val in node.values():
                if isinstance(val, (dict, list)):
                    _traverse(val)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    _traverse(item)

    _traverse(bundle_copy)
    return bundle_copy, total_redacted


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

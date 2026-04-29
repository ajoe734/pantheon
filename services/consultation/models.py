from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --- Enums from SD-05 ---

class ConsultRequestType(str, Enum):
    STRATEGY_REVIEW = "strategy_review"
    REDTEAM = "redteam"
    DATA_LEAKAGE = "data_leakage"
    EXECUTION_RISK = "execution_risk"
    CAPITAL_POOL = "capital_pool"
    INCIDENT = "incident"
    PERSONA_POLICY = "persona_policy"


class ConsultRequestStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    MEMO_PENDING = "memo_pending"
    PUBLISHED = "published"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConsultPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ParticipantType(str, Enum):
    PERSONA = "persona"
    HUMAN_REVIEWER = "human_reviewer"
    COMMITTEE = "committee"
    EXTERNAL_TOOL = "external_tool"


class ParticipantRole(str, Enum):
    PRIMARY_REVIEWER = "primary_reviewer"
    RED_TEAM = "red_team"
    RISK_REVIEWER = "risk_reviewer"
    DATA_REVIEWER = "data_reviewer"
    EXECUTION_REVIEWER = "execution_reviewer"
    OBSERVER = "observer"


class ParticipantStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    COMPLETED = "completed"


class MemoType(str, Enum):
    COMMITTEE_SUMMARY = "committee_summary"
    REDTEAM_REPORT = "redteam_report"
    RISK_REVIEW = "risk_review"
    DATA_REVIEW = "data_review"
    EXECUTION_REVIEW = "execution_review"
    DISSENT = "dissent"


class AuthorType(str, Enum):
    PERSONA = "persona"
    HUMAN = "human"
    COMMITTEE = "committee"
    SYSTEM = "system"


class Recommendation(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REJECT = "reject"
    REQUEST_MORE_RESEARCH = "request_more_research"
    FREEZE = "freeze"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MemoStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class GateHandoffStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"


# --- Shared Objects ---

class EvidenceRef(BaseModel):
    id: str
    evidence_type: str
    artifact_ref: Optional[str] = None
    description: Optional[str] = None
    link: str


class ActorRef(BaseModel):
    actor_type: str
    actor_id: str


# --- Core Models ---

class ConsultFinding(BaseModel):
    severity: FindingSeverity
    category: str
    claim: str
    evidence_refs: List[str] = Field(default_factory=list)
    recommendation: str


class ConsultMemo(BaseModel):
    memo_id: str
    request_id: str
    memo_type: MemoType
    author_type: AuthorType
    author_ref: str
    target_type: str
    target_id: str
    summary: str
    findings: List[ConsultFinding] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: float = 1.0
    status: MemoStatus = MemoStatus.DRAFT
    trace_id: str
    created_at: str = Field(default_factory=utc_now)
    published_at: Optional[str] = None


class TranscriptEvent(BaseModel):
    event_id: str
    session_id: str  # maps to request_id or session_id
    sequence_no: int
    event_type: str
    event_time: str = Field(default_factory=utc_now)
    actor: ActorRef
    content: Dict[str, Any]
    evidence_refs: List[str] = Field(default_factory=list)


class ConsultTranscript(BaseModel):
    transcript_id: str
    session_id: str
    request_id: str
    events: List[TranscriptEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ConsultAuditEvent(BaseModel):
    audit_id: str
    request_id: str
    actor_ref: ActorRef
    service_actor_ref: Optional[ActorRef] = None
    action: str
    before_state: Optional[str] = None
    after_state: Optional[str] = None
    payload_hash: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now)
    trace_id: str


class ConsultParticipant(BaseModel):
    participant_id: str
    request_id: str
    participant_type: ParticipantType
    participant_ref: str
    role: ParticipantRole
    status: ParticipantStatus = ParticipantStatus.PENDING
    assigned_at: str = Field(default_factory=utc_now)


class ConsultRequest(BaseModel):
    request_id: str
    request_type: ConsultRequestType
    requested_by: ActorRef
    from_persona_id: Optional[str] = None
    target_type: str
    target_id: str
    task: Optional[str] = None
    consultation_type: Optional[str] = None
    context_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    priority: ConsultPriority = ConsultPriority.NORMAL
    status: ConsultRequestStatus = ConsultRequestStatus.DRAFT
    policy_id: Optional[str] = None
    linked_session_id: Optional[str] = None
    request_to_session_status: Optional[str] = None
    completed_at: Optional[str] = None
    canceled_at: Optional[str] = None
    session_handoff_note: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace_id: str
    created_at: str = Field(default_factory=utc_now)


class ConsultGateHandoff(BaseModel):
    handoff_id: str
    request_id: str
    target_gate: str
    memo_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    audit_refs: List[str] = Field(default_factory=list)
    trace_id: str
    status: GateHandoffStatus = GateHandoffStatus.PENDING
    created_at: str = Field(default_factory=utc_now)
    sent_at: Optional[str] = None


class ConsultEvidenceAttachment(BaseModel):
    attachment_id: str
    request_id: str
    evidence_ref: EvidenceRef
    attached_by: ActorRef
    trace_id: str
    created_at: str = Field(default_factory=utc_now)


# --- API Request/Response Models ---

class CreateConsultRequest(BaseModel):
    request_type: ConsultRequestType
    requested_by: ActorRef
    from_persona_id: Optional[str] = None
    target_type: str
    target_id: str
    task: Optional[str] = None
    consultation_type: Optional[str] = None
    context_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    priority: ConsultPriority = ConsultPriority.NORMAL
    policy_id: Optional[str] = None
    linked_session_id: Optional[str] = None
    request_to_session_status: Optional[str] = None
    completed_at: Optional[str] = None
    canceled_at: Optional[str] = None
    session_handoff_note: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class CreateGateHandoffRequest(BaseModel):
    request_id: str
    target_gate: str
    memo_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    trace_id: str
    initiated_by: Optional[ActorRef] = None


class CancelConsultRequestRequest(BaseModel):
    actor_ref: ActorRef
    canceled_at: Optional[str] = None
    trace_id: Optional[str] = None


class RecordSponsorDecisionRequest(BaseModel):
    sponsor_decision: str
    rationale_ref: str
    actor_id: str
    recorded_at: Optional[str] = None


class AttachEvidenceRequest(BaseModel):
    evidence_ref: EvidenceRef
    attached_by: ActorRef
    trace_id: str


class AssignParticipantRequest(BaseModel):
    participant_type: ParticipantType
    participant_ref: str
    role: ParticipantRole
    trace_id: str
    initiated_by: Optional[ActorRef] = None


class SubmitMemoRequest(BaseModel):
    request_id: str
    memo_type: MemoType
    author_type: AuthorType
    author_ref: str
    summary: str
    findings: List[ConsultFinding] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: float = 1.0
    trace_id: str


class PostTranscriptEventRequest(BaseModel):
    request_id: str
    event_type: str
    actor: ActorRef
    content: Dict[str, Any]
    evidence_refs: List[str] = Field(default_factory=list)

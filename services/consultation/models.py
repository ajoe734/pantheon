from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from jsonschema import Draft7Validator
from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ConsultValidationError(ValueError):
    """Raised when a consultation contract payload violates the schema."""


def _schema_dir() -> Path:
    return Path(__file__).resolve().parent


def consult_request_schema_path() -> Path:
    return _schema_dir() / "consult_request.schema.json"


def consult_memo_schema_path() -> Path:
    return _schema_dir() / "consult_memo.schema.json"


def load_consult_request_schema(schema_path: Path | None = None) -> Dict[str, Any]:
    return json.loads((schema_path or consult_request_schema_path()).read_text(encoding="utf-8"))


def load_consult_memo_schema(schema_path: Path | None = None) -> Dict[str, Any]:
    return json.loads((schema_path or consult_memo_schema_path()).read_text(encoding="utf-8"))


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.path) or "<root>"


def _validate_payload(payload: Mapping[str, Any], schema: Mapping[str, Any], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise ConsultValidationError(f"{label} payload must be a JSON object")
    validator = Draft7Validator(dict(schema))
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        raise ConsultValidationError(f"{label} schema validation failed at {_location(first)}: {first.message}")


def validate_consult_request_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_consult_request_schema(schema_path), "ConsultRequest")


def validate_consult_memo_payload(payload: Mapping[str, Any], schema_path: Path | None = None) -> None:
    _validate_payload(payload, load_consult_memo_schema(schema_path), "ConsultMemo")


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def validate_consult_memo_against_request(memo: "ConsultMemo", request: "ConsultRequest") -> List[str]:
    """Return cross-object lineage errors between a memo and its parent request."""
    errors: List[str] = []
    if memo.request_id != request.request_id:
        errors.append("memo.request_id must match request.request_id")
    if memo.target_type != request.target_type:
        errors.append("memo.target_type must match request.target_type")
    if memo.target_id != request.target_id:
        errors.append("memo.target_id must match request.target_id")
    if _enum_value(memo.status) == MemoStatus.PUBLISHED.value and not memo.published_at:
        errors.append("published memo requires published_at")
    return errors


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
    id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    artifact_ref: Optional[str] = None
    description: Optional[str] = None
    link: str = Field(min_length=1)


class ActorRef(BaseModel):
    actor_type: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)


class ContextRef(BaseModel):
    type: str = Field(min_length=1)
    id: str = Field(min_length=1)


# --- Core Models ---

class ConsultFinding(BaseModel):
    severity: FindingSeverity
    category: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence_refs: List[str] = Field(default_factory=list)
    recommendation: str = Field(min_length=1)


class ConsultMemo(BaseModel):
    memo_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    memo_type: MemoType
    author_type: AuthorType
    author_ref: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    findings: List[ConsultFinding] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: MemoStatus = MemoStatus.DRAFT
    trace_id: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)
    published_at: Optional[str] = None


class TranscriptEvent(BaseModel):
    event_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)  # maps to request_id or session_id
    sequence_no: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    event_time: str = Field(default_factory=utc_now)
    actor: ActorRef
    content: Dict[str, Any]
    evidence_refs: List[str] = Field(default_factory=list)


class ConsultTranscript(BaseModel):
    transcript_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    events: List[TranscriptEvent] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class ConsultAuditEvent(BaseModel):
    audit_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    actor_ref: ActorRef
    service_actor_ref: Optional[ActorRef] = None
    action: str = Field(min_length=1)
    before_state: Optional[str] = None
    after_state: Optional[str] = None
    payload_hash: Optional[str] = None
    timestamp: str = Field(default_factory=utc_now)
    trace_id: str = Field(min_length=1)


class ConsultParticipant(BaseModel):
    participant_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    participant_type: ParticipantType
    participant_ref: str = Field(min_length=1)
    role: ParticipantRole
    status: ParticipantStatus = ParticipantStatus.PENDING
    assigned_at: str = Field(default_factory=utc_now)


class ConsultRequest(BaseModel):
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(default="default", min_length=1)
    request_type: ConsultRequestType
    requested_by: ActorRef
    from_persona_id: Optional[str] = None
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    task: Optional[str] = None
    consultation_type: Optional[str] = None
    context_refs: List[Union[ContextRef, str]] = Field(default_factory=list)
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
    trace_id: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


class ConsultGateHandoff(BaseModel):
    handoff_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    target_gate: str = Field(min_length=1)
    memo_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    audit_refs: List[str] = Field(default_factory=list)
    trace_id: str = Field(min_length=1)
    status: GateHandoffStatus = GateHandoffStatus.PENDING
    created_at: str = Field(default_factory=utc_now)
    sent_at: Optional[str] = None


class ConsultEvidenceAttachment(BaseModel):
    attachment_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    evidence_ref: EvidenceRef
    attached_by: ActorRef
    trace_id: str = Field(min_length=1)
    created_at: str = Field(default_factory=utc_now)


# --- API Request/Response Models ---

class CreateConsultRequest(BaseModel):
    request_id: Optional[str] = Field(default=None, min_length=1)
    tenant_id: Optional[str] = Field(default=None, min_length=1)
    request_type: ConsultRequestType
    requested_by: ActorRef
    from_persona_id: Optional[str] = None
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    task: Optional[str] = None
    consultation_type: Optional[str] = None
    context_refs: List[Union[ContextRef, str]] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    priority: ConsultPriority = ConsultPriority.NORMAL
    policy_id: Optional[str] = None
    linked_session_id: Optional[str] = None
    request_to_session_status: Optional[str] = None
    completed_at: Optional[str] = None
    canceled_at: Optional[str] = None
    session_handoff_note: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    trace_id: str = Field(min_length=1)


class CreateGateHandoffRequest(BaseModel):
    request_id: str = Field(min_length=1)
    target_gate: str = Field(min_length=1)
    memo_ids: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    trace_id: str = Field(min_length=1)
    initiated_by: Optional[ActorRef] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class AcknowledgeGateHandoffRequest(BaseModel):
    consumer_ref: str = Field(min_length=1)
    acknowledged_at: Optional[str] = None


class CancelConsultRequestRequest(BaseModel):
    actor_ref: ActorRef
    canceled_at: Optional[str] = None
    trace_id: Optional[str] = None


class RecordSponsorDecisionRequest(BaseModel):
    sponsor_decision: str = Field(min_length=1)
    rationale_ref: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    recorded_at: Optional[str] = None


class AttachEvidenceRequest(BaseModel):
    evidence_ref: EvidenceRef
    attached_by: ActorRef
    trace_id: str
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class AssignParticipantRequest(BaseModel):
    participant_type: ParticipantType
    participant_ref: str = Field(min_length=1)
    role: ParticipantRole
    trace_id: str = Field(min_length=1)
    initiated_by: Optional[ActorRef] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class SubmitMemoRequest(BaseModel):
    request_id: str = Field(min_length=1)
    memo_type: MemoType
    author_type: AuthorType
    author_ref: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    findings: List[ConsultFinding] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    trace_id: str = Field(min_length=1)
    idempotency_key: Optional[str] = Field(default=None, min_length=1)


class PostTranscriptEventRequest(BaseModel):
    request_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    actor: ActorRef
    content: Dict[str, Any]
    evidence_refs: List[str] = Field(default_factory=list)
    idempotency_key: Optional[str] = Field(default=None, min_length=1)

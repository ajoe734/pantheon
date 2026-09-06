"""
ApprovalDecision — Governance Contract

First-class governance object that records whether an artifact / strategy /
allocation is approved for the next lifecycle step. Shared by promotion and
evolution planes.

Public API
----------
ApprovalDecision      — dataclass with lifecycle-aware validation
ApprovalDecisionStore — in-memory store (filesystem/JSON persistence)
validate_decision()   — standalone structural validation helper
OwnerMatrix           — risk-level → actor_role authorization matrix

Schema
------
The canonical JSON schema lives at:
    services/control-plane/governance/approval_decision.schema.json
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    APPROVED_WITH_CONDITIONS = "approved_with_conditions"


class DecisionState(str, Enum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    DECIDED = "decided"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class ActorRole(str, Enum):
    GOVERNANCE_REVIEWER = "governance_reviewer"
    RISK_OWNER = "risk_owner"
    GOVERNANCE_COMMITTEE = "governance_committee"
    AUTOMATED_GATE = "automated_gate"


class TargetType(str, Enum):
    REGISTRY_ENTRY = "registry_entry"
    STRATEGY_SPEC = "strategy_spec"
    STRATEGY_WORKSHOP = "strategy_workshop"
    MODEL_ARTIFACT = "model_artifact"
    ALLOCATION_POLICY = "allocation_policy"
    PERSONA_CAPITAL_BINDING = "persona_capital_binding"
    EVOLUTION_PROPOSAL = "evolution_proposal"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceRefType(str, Enum):
    EVALUATOR_RESULT = "evaluator_result"
    CRITIC_FINDING = "critic_finding"
    DRIFT_REPORT = "drift_report"
    TELEMETRY_SUMMARY = "telemetry_summary"
    AUDIT_LOG_ENTRY = "audit_log_entry"
    MANUAL_REVIEW_TICKET = "manual_review_ticket"
    COMMITTEE_MEMO = "committee_memo"
    SERVICE_HANDOFF = "service_handoff"


# ---------------------------------------------------------------------------
# Owner Matrix
# ---------------------------------------------------------------------------

from services.governance.write_authority import (
    WRITE_AUTHORITY_MATRIX, REVOKE_AUTHORITY, is_authorized_to_decide,
)

OWNER_MATRIX = {
    RiskLevel(risk): [ActorRole(role) for role in roles]
    for risk, roles in WRITE_AUTHORITY_MATRIX.items()
}
REVOKE_ROLES = {ActorRole(role) for role in REVOKE_AUTHORITY}


class OwnerMatrix:
    """Encapsulates the risk-level → actor_role authorization matrix."""

    @staticmethod
    def is_authorized(role: ActorRole, risk: RiskLevel) -> bool:
        """Return True if *role* is authorized to decide at *risk* level."""
        return is_authorized_to_decide(role, risk)

    @staticmethod
    def minimum_roles_for(risk: RiskLevel) -> List[ActorRole]:
        """Return the list of authorized roles for a given risk level."""
        return list(OWNER_MATRIX.get(risk, []))


# ---------------------------------------------------------------------------
# Consultation Gate
# ---------------------------------------------------------------------------

CONSULTATION_GATE_TARGET_TYPES = frozenset({TargetType.ALLOCATION_POLICY})
CONSULTATION_GATE_RISK_LEVELS = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})
CONSULTATION_HANDOFF_MAX_AGE_SECONDS = 86400  # 24 hours


class ConsultationGateError(ValueError):
    """Raised when a high-risk allocation approval is missing required consultation evidence."""


def consultation_gate_required(
    target_type: "TargetType | str",
    risk_level: "RiskLevel | str",
) -> bool:
    """Return True if this target_type + risk_level requires consultation handoff evidence."""
    try:
        tt = TargetType(target_type)
        rl = RiskLevel(risk_level)
    except ValueError:
        return False
    return tt in CONSULTATION_GATE_TARGET_TYPES and rl in CONSULTATION_GATE_RISK_LEVELS


def _ref_type_str(ref: Any) -> str:
    if isinstance(ref, dict):
        val = ref.get("ref_type", "")
    else:
        val = getattr(ref, "ref_type", "")
    return val.value if isinstance(val, EvidenceRefType) else str(val)


def _issued_at_from_ref(ref: Any) -> Optional[str]:
    storage_ref = None
    if isinstance(ref, dict):
        storage_ref = ref.get("storage_ref")
    else:
        storage_ref = getattr(ref, "storage_ref", None)
    if isinstance(storage_ref, dict):
        return storage_ref.get("issued_at")
    return None


def validate_consultation_gate(
    evidence_refs: "List[Any]",
    *,
    now_iso: Optional[str] = None,
) -> None:
    """Validate consultation handoff evidence for high-risk allocation_policy approvals.

    Raises ConsultationGateError when:
    - No committee_memo evidence ref is present.
    - No service_handoff evidence ref is present.
    - A service_handoff ref carries an issued_at that is stale beyond
      CONSULTATION_HANDOFF_MAX_AGE_SECONDS.
    """
    memo_refs = [r for r in evidence_refs if _ref_type_str(r) == EvidenceRefType.COMMITTEE_MEMO.value]
    handoff_refs = [r for r in evidence_refs if _ref_type_str(r) == EvidenceRefType.SERVICE_HANDOFF.value]

    if not memo_refs:
        raise ConsultationGateError(
            "committee_memo evidence ref is required for high-risk allocation_policy approval"
        )
    if not handoff_refs:
        raise ConsultationGateError(
            "service_handoff evidence ref is required for high-risk allocation_policy approval"
        )

    now = (
        datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        if now_iso
        else datetime.now(timezone.utc)
    )
    for ref in handoff_refs:
        issued_at_str = _issued_at_from_ref(ref)
        if issued_at_str:
            issued_at = datetime.fromisoformat(issued_at_str.replace("Z", "+00:00"))
            age = (now - issued_at).total_seconds()
            if age > CONSULTATION_HANDOFF_MAX_AGE_SECONDS:
                raise ConsultationGateError(
                    f"service_handoff evidence is stale (age={age:.0f}s, "
                    f"max={CONSULTATION_HANDOFF_MAX_AGE_SECONDS}s)"
                )


# ---------------------------------------------------------------------------
# Evidence Reference
# ---------------------------------------------------------------------------

@dataclass
class EvidenceRef:
    ref_type: EvidenceRefType | str
    ref_id: str
    storage_ref: Optional[Dict[str, str]] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"ref_type": self.ref_type, "ref_id": self.ref_id}
        if self.storage_ref:
            result["storage_ref"] = self.storage_ref
        if self.note:
            result["note"] = self.note
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceRef":
        return cls(
            ref_type=data["ref_type"],
            ref_id=data["ref_id"],
            storage_ref=data.get("storage_ref"),
            note=data.get("note"),
        )


# ---------------------------------------------------------------------------
# ApprovalDecision
# ---------------------------------------------------------------------------

@dataclass
class ApprovalDecision:
    """A first-class governance approval decision."""

    decision_id: str
    target_type: TargetType | str
    target_id: str
    target_version: str
    decision: Optional[DecisionOutcome | str]
    decision_state: DecisionState | str
    actor_role: Optional[ActorRole | str]
    actor_id: Optional[str]
    rationale: Optional[str]
    created_at: str
    decided_at: Optional[str]
    conditions: List[str] = field(default_factory=list)
    risk_level: RiskLevel | str = RiskLevel.LOW
    evidence_refs: List[EvidenceRef] = field(default_factory=list)
    superseded_by: Optional[str] = None
    expires_at: Optional[str] = None
    capital_pool_id: Optional[str] = None
    persona_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    tenant_id: str = ""
    owner_user_id: str = ""
    proposal_id: Optional[str] = None
    proposal_revision: Optional[int] = None
    proposal_content_digest: Optional[str] = None
    validation_result_digest: Optional[str] = None
    revoked_at: Optional[str] = None
    session_id: Optional[str] = None
    candidate_digest: Optional[str] = None
    proof_digest: Optional[str] = None
    controller_record_ref: Optional[str] = None
    recorded_at: Optional[str] = None
    authority_status: Optional[str] = None
    version: int = 0
    event_id: Optional[str] = None

    # -- factory helpers -----------------------------------------------------

    @classmethod
    def create_proposed(
        cls,
        decision_id: str,
        target_type: TargetType | str,
        target_id: str,
        target_version: str,
        risk_level: RiskLevel | str = RiskLevel.LOW,
        capital_pool_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        proposal_revision: Optional[int] = None,
        proposal_content_digest: Optional[str] = None,
        validation_result_digest: Optional[str] = None,
        session_id: Optional[str] = None,
        candidate_digest: Optional[str] = None,
        proof_digest: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> "ApprovalDecision":
        """Create a new decision in the *proposed* state."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(
            decision_id=decision_id,
            target_type=target_type,
            target_id=target_id,
            target_version=target_version,
            decision=None,
            decision_state=DecisionState.PROPOSED,
            actor_role=None,
            actor_id=None,
            rationale=None,
            created_at=now,
            decided_at=None,
            risk_level=risk_level,
            capital_pool_id=capital_pool_id,
            persona_id=persona_id,
            # Legacy internal callers derive a non-ambiguous system scope;
            # wire APIs require both values explicitly.
            tenant_id=tenant_id or os.getenv("PANTHEON_DEFAULT_TENANT_ID", "pantheon-system"),
            owner_user_id=owner_user_id or persona_id or "pantheon-system",
            proposal_id=proposal_id,
            proposal_revision=proposal_revision,
            proposal_content_digest=proposal_content_digest,
            validation_result_digest=validation_result_digest,
            session_id=session_id,
            candidate_digest=candidate_digest,
            proof_digest=proof_digest,
            expires_at=expires_at,
        )

    def accept_review(self, actor_role: ActorRole | str, actor_id: str) -> None:
        """Transition from *proposed* to *under_review*."""
        if self.decision_state != DecisionState.PROPOSED:
            raise ValueError(
                f"Can only accept review from 'proposed' state, got '{self.decision_state}'"
            )
        if not OwnerMatrix.is_authorized(
            ActorRole(actor_role), RiskLevel(self.risk_level)
        ):
            raise ValueError(
                f"Role '{actor_role}' not authorized for risk level '{self.risk_level}'"
            )
        self.decision_state = DecisionState.UNDER_REVIEW
        self.actor_role = actor_role
        self.actor_id = actor_id

    def decide(
        self,
        outcome: DecisionOutcome | str,
        rationale: str,
        actor_role: Optional[ActorRole | str] = None,
        actor_id: Optional[str] = None,
        conditions: Optional[List[str]] = None,
        evidence_refs: Optional[List[EvidenceRef]] = None,
        session_id: Optional[str] = None,
        candidate_digest: Optional[str] = None,
        proof_digest: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> None:
        """Finalize the decision (→ *decided*)."""
        if self.decision_state != DecisionState.UNDER_REVIEW:
            raise ValueError(
                f"Can only decide from 'under_review', got '{self.decision_state}'"
            )
        effective_role = actor_role if actor_role is not None else self.actor_role
        effective_actor_id = actor_id if actor_id is not None else self.actor_id
        if not effective_role:
            raise ValueError("actor_role is required to decide an approval")
        if not effective_actor_id:
            raise ValueError("actor_id is required to decide an approval")
        normalized_role = ActorRole(effective_role)
        normalized_risk = RiskLevel(self.risk_level)
        if not OwnerMatrix.is_authorized(normalized_role, normalized_risk):
            raise ValueError(
                f"Role '{normalized_role.value}' not authorized for risk level '{normalized_risk.value}'"
            )
        if outcome == DecisionOutcome.APPROVED_WITH_CONDITIONS:
            if not conditions:
                raise ValueError(
                    "'approved_with_conditions' requires at least one condition"
                )
            self.conditions = conditions
        effective_refs = evidence_refs if evidence_refs is not None else self.evidence_refs
        if consultation_gate_required(self.target_type, self.risk_level):
            validate_consultation_gate(effective_refs)
        if evidence_refs:
            self.evidence_refs = evidence_refs
        if session_id is not None:
            self.session_id = session_id
        if candidate_digest is not None:
            self.candidate_digest = candidate_digest
        if proof_digest is not None:
            self.proof_digest = proof_digest
        if expires_at is not None:
            self.expires_at = expires_at
        self.decision = outcome
        self.decision_state = DecisionState.DECIDED
        self.rationale = rationale
        self.decided_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.actor_role = normalized_role
        self.actor_id = effective_actor_id
        is_approved = outcome in (
            DecisionOutcome.APPROVED,
            DecisionOutcome.APPROVED_WITH_CONDITIONS,
            "approved",
            "approved_with_conditions",
        )
        if is_approved:
            self.authority_status = "authoritative"
            self.controller_record_ref = self.controller_record_ref or f"governance-controller://approval-{self.decision_id}"
            self.recorded_at = self.decided_at
        else:
            self.authority_status = None
            self.controller_record_ref = None
            self.recorded_at = self.decided_at

    def revoke(self, actor_role: ActorRole | str, actor_id: str) -> None:
        """Revoke a decided decision."""
        if self.decision_state != DecisionState.DECIDED:
            raise ValueError("Can only revoke a 'decided' decision")
        normalized_role = ActorRole(actor_role)
        if normalized_role not in REVOKE_ROLES:
            raise ValueError(
                f"Role '{actor_role}' is not allowed to revoke a decided approval"
            )
        self.decision_state = DecisionState.REVOKED
        self.actor_role = normalized_role
        self.actor_id = actor_id
        self.revoked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def supersede(self, superseded_by: str) -> None:
        """Mark this decision as superseded by a newer one."""
        self.superseded_by = superseded_by
        self.decision_state = DecisionState.SUPERSEDED

    # -- validation ----------------------------------------------------------

    def validate(self) -> List[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: List[str] = []

        if not self.decision_id:
            errors.append("decision_id is required")
        if not self.target_id:
            errors.append("target_id is required")
        if not self.target_version:
            errors.append("target_version is required")
        if not self.tenant_id:
            errors.append("tenant_id is required")
        if not self.owner_user_id:
            errors.append("owner_user_id is required")
        if self.decision_state in (
            DecisionState.UNDER_REVIEW,
            DecisionState.DECIDED,
            DecisionState.REVOKED,
        ):
            if not self.actor_role:
                errors.append("actor_role is required once review has started")
            if not self.actor_id:
                errors.append("actor_id is required once review has started")

        if self.decision_state == DecisionState.DECIDED and not self.decision:
            errors.append("decision is required for decided decisions")
        if self.decision_state == DecisionState.DECIDED and not self.rationale:
            errors.append("rationale is required for decided decisions")

        # Role authorization
        try:
            role = ActorRole(self.actor_role)
            risk = RiskLevel(self.risk_level)
            if self.decision_state in (DecisionState.UNDER_REVIEW, DecisionState.DECIDED):
                if not OwnerMatrix.is_authorized(role, risk):
                    errors.append(
                        f"Role '{role.value}' not authorized for risk level '{risk.value}'"
                    )
        except ValueError:
            pass  # enum validation caught elsewhere

        # Conditions required for approved_with_conditions
        if self.decision == DecisionOutcome.APPROVED_WITH_CONDITIONS:
            if not self.conditions:
                errors.append(
                    "'approved_with_conditions' requires at least one condition"
                )

        # decided_at required for decided state
        if self.decision_state == DecisionState.DECIDED and not self.decided_at:
            errors.append("decided_at is required for 'decided' state")

        return errors

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        # Convert enums to string values
        result["target_type"] = (
            self.target_type.value
            if isinstance(self.target_type, TargetType)
            else self.target_type
        )
        result["decision"] = (
            self.decision.value
            if isinstance(self.decision, DecisionOutcome)
            else self.decision
        )
        result["decision_state"] = (
            self.decision_state.value
            if isinstance(self.decision_state, DecisionState)
            else self.decision_state
        )
        result["actor_role"] = (
            self.actor_role.value
            if isinstance(self.actor_role, ActorRole)
            else self.actor_role
        )
        result["risk_level"] = (
            self.risk_level.value
            if isinstance(self.risk_level, RiskLevel)
            else self.risk_level
        )
        result["evidence_refs"] = [
            ref.to_dict() if isinstance(ref, EvidenceRef) else ref
            for ref in self.evidence_refs
        ]
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalDecision":
        evidence_refs = [
            EvidenceRef.from_dict(r) if isinstance(r, dict) else r
            for r in data.get("evidence_refs", [])
        ]
        return cls(
            decision_id=data["decision_id"],
            target_type=data["target_type"],
            target_id=data["target_id"],
            target_version=data["target_version"],
            decision=data.get("decision"),
            decision_state=data["decision_state"],
            actor_role=data.get("actor_role"),
            actor_id=data.get("actor_id"),
            rationale=data.get("rationale"),
            created_at=data["created_at"],
            decided_at=data.get("decided_at"),
            conditions=data.get("conditions", []),
            risk_level=data.get("risk_level", RiskLevel.LOW),
            evidence_refs=evidence_refs,
            superseded_by=data.get("superseded_by"),
            expires_at=data.get("expires_at"),
            capital_pool_id=data.get("capital_pool_id"),
            persona_id=data.get("persona_id"),
            metadata=data.get("metadata"),
            tenant_id=data.get("tenant_id") or "pantheon-system",
            owner_user_id=data.get("owner_user_id") or data.get("user_id") or data.get("persona_id") or "pantheon-system",
            proposal_id=data.get("proposal_id"),
            proposal_revision=data.get("proposal_revision"),
            proposal_content_digest=data.get("proposal_content_digest"),
            validation_result_digest=data.get("validation_result_digest"),
            revoked_at=data.get("revoked_at"),
            session_id=data.get("session_id"),
            candidate_digest=data.get("candidate_digest"),
            proof_digest=data.get("proof_digest"),
            controller_record_ref=data.get("controller_record_ref"),
            recorded_at=data.get("recorded_at"),
            authority_status=data.get("authority_status"),
            version=data.get("version", 0),
            event_id=data.get("event_id"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ApprovalDecision":
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# Standalone JSON validation (schema-based)
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "approval_decision.schema.json"


def validate_decision_json(data: Dict[str, Any]) -> List[str]:
    """Validate a decision dict against ApprovalDecision structural rules."""
    errors: List[str] = []

    required = [
        "decision_id",
        "target_type",
        "target_id",
        "target_version",
        "decision_state",
        "created_at",
        "tenant_id",
        "owner_user_id",
    ]
    for key in required:
        if key not in data or data[key] is None:
            errors.append(f"Missing required field: {key}")

    valid_target_types = [t.value for t in TargetType]
    if data.get("target_type") and data["target_type"] not in valid_target_types:
        errors.append(
            f"Invalid target_type: {data['target_type']}. "
            f"Must be one of {valid_target_types}"
        )

    valid_decisions = [d.value for d in DecisionOutcome]
    if data.get("decision") and data["decision"] not in valid_decisions:
        errors.append(
            f"Invalid decision: {data['decision']}. Must be one of {valid_decisions}"
        )

    valid_states = [s.value for s in DecisionState]
    if data.get("decision_state") and data["decision_state"] not in valid_states:
        errors.append(
            f"Invalid decision_state: {data['decision_state']}. "
            f"Must be one of {valid_states}"
        )

    valid_roles = [r.value for r in ActorRole]
    if data.get("actor_role") and data["actor_role"] not in valid_roles:
        errors.append(
            f"Invalid actor_role: {data['actor_role']}. Must be one of {valid_roles}"
        )

    valid_risks = [r.value for r in RiskLevel]
    if data.get("risk_level") and data["risk_level"] not in valid_risks:
        errors.append(
            f"Invalid risk_level: {data['risk_level']}. Must be one of {valid_risks}"
        )

    # Consultation gate check for high-risk allocation_policy
    if consultation_gate_required(
        data.get("target_type", ""), data.get("risk_level", "low")
    ) and data.get("decision_state") == DecisionState.DECIDED:
        try:
            validate_consultation_gate(data.get("evidence_refs") or [])
        except ConsultationGateError as exc:
            errors.append(str(exc))

    state = data.get("decision_state")
    if state in (
        DecisionState.UNDER_REVIEW,
        DecisionState.DECIDED,
        DecisionState.REVOKED,
    ):
        if not data.get("actor_role"):
            errors.append("actor_role is required once review has started")
        if not data.get("actor_id"):
            errors.append("actor_id is required once review has started")

    if state == DecisionState.DECIDED:
        if not data.get("decision"):
            errors.append("decision is required for 'decided' state")
        if not data.get("rationale"):
            errors.append("rationale is required for 'decided' state")
        if not data.get("decided_at"):
            errors.append("decided_at is required for 'decided' state")

    # approved_with_conditions requires conditions
    if data.get("decision") == DecisionOutcome.APPROVED_WITH_CONDITIONS:
        if not data.get("conditions") or len(data["conditions"]) == 0:
            errors.append(
                "'approved_with_conditions' requires at least one condition"
            )

    # Role authorization
    if data.get("actor_role") and data.get("risk_level") and state in (
        DecisionState.UNDER_REVIEW,
        DecisionState.DECIDED,
    ):
        try:
            role = ActorRole(data["actor_role"])
            risk = RiskLevel(data["risk_level"])
            if not OwnerMatrix.is_authorized(role, risk):
                errors.append(
                    f"Role '{role.value}' not authorized for risk level '{risk.value}'"
                )
        except ValueError:
            pass

    return errors


def validate_decision(decision: ApprovalDecision) -> List[str]:
    """Validate an ApprovalDecision instance."""
    return decision.validate()


# ---------------------------------------------------------------------------
# In-Memory Store
# ---------------------------------------------------------------------------

class ApprovalDecisionStore:
    """Simple in-memory store for ApprovalDecision objects.

    Persists to JSON file on each write operation.
    """

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = Path(storage_path) if storage_path else None
        self._decisions: Dict[str, ApprovalDecision] = {}
        if self._storage_path and self._storage_path.exists():
            self._load()

    def put(self, decision: ApprovalDecision) -> None:
        """Store or update a decision."""
        self._decisions[decision.decision_id] = decision
        self._save()

    def get(self, decision_id: str) -> Optional[ApprovalDecision]:
        """Retrieve a decision by ID."""
        return self._decisions.get(decision_id)

    def find_by_target(
        self, target_type: str, target_id: str
    ) -> List[ApprovalDecision]:
        """Find all decisions for a given target."""
        return [
            d
            for d in self._decisions.values()
            if d.target_id == target_id
            and (
                d.target_type == target_type
                or (isinstance(d.target_type, TargetType) and d.target_type.value == target_type)
            )
        ]

    def find_latest_approved(
        self, target_type: str, target_id: str
    ) -> Optional[ApprovalDecision]:
        """Return the most recent decided approval for a target."""
        candidates = [
            d
            for d in self.find_by_target(target_type, target_id)
            if d.decision_state == DecisionState.DECIDED
            and d.decision
            in (DecisionOutcome.APPROVED, DecisionOutcome.APPROVED_WITH_CONDITIONS)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda d: d.decided_at, reverse=True)
        return candidates[0]

    def list_all(self) -> List[ApprovalDecision]:
        """Return all decisions."""
        return list(self._decisions.values())

    def _save(self) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            did: d.to_dict() for did, d in self._decisions.items()
        }
        self._storage_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not self._storage_path or not self._storage_path.exists():
            return
        text = self._storage_path.read_text()
        if not text or not text.strip():
            # Empty or newly created storage file — start with empty state
            return
        raw = json.loads(text)
        for did, data in raw.items():
            self._decisions[did] = ApprovalDecision.from_dict(data)


# ---------------------------------------------------------------------------
# Audit log integration helper
# ---------------------------------------------------------------------------

def to_audit_event(
    decision: ApprovalDecision, event_type: str
) -> Dict[str, Any]:
    """Convert a decision to an audit log event payload.

    *event_type* should be one of:
      - approval_decision_created
      - approval_decision_state_changed
      - approval_decision_revoked
    """
    return {
        "event_type": event_type,
        "decision_id": decision.decision_id,
        "target_type": (
            decision.target_type.value
            if isinstance(decision.target_type, TargetType)
            else decision.target_type
        ),
        "target_id": decision.target_id,
        "decision": (
            decision.decision.value
            if isinstance(decision.decision, DecisionOutcome)
            else decision.decision
        ),
        "actor_id": decision.actor_id,
        "actor_role": (
            decision.actor_role.value
            if isinstance(decision.actor_role, ActorRole)
            else decision.actor_role
        ),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

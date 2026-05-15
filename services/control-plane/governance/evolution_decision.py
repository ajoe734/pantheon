"""
EvolutionDecision — First-Class Evolution Governance Contract

Formalizes the canonical evolution-governance object that turns telemetry,
drift, incident, and operator evidence into a governed decision with explicit
review ownership, approval linkage, cooldown windows, and execution metadata.

Public API
----------
EvolutionDecision        — first-class evolution-governance object
EvolutionDecisionStore   — in-memory store with optional JSON persistence
ReviewStep               — reviewer-chain entry for review / approval / execute
ThresholdSnapshot        — normalized threshold breach snapshot
ExecutionResult          — downstream execution result envelope
validate_evolution_decision()
validate_evolution_decision_json()
to_audit_event()

Schema
------
The canonical JSON schema lives at:
    services/control-plane/governance/evolution_decision.schema.json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from approval_decision import EvidenceRef, EvidenceRefType, RiskLevel


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EvolutionDecisionError(ValueError):
    """Raised when an evolution decision is invalid or violates invariants."""


class EvolutionDecisionState(str, Enum):
    PROPOSED = "proposed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CANCELED = "canceled"
    SUPERSEDED = "superseded"


class EvolutionActionType(str, Enum):
    OBSERVE = "observe"
    REVALIDATE = "revalidate"
    RETRAIN = "retrain"
    REQUIRE_MORE_DATA = "require_more_data"
    FLAG_FOR_REVIEW = "flag_for_review"
    REDUCE_BUDGET = "reduce_budget"
    TIGHTEN_RISK_POLICY = "tighten_risk_policy"
    MUTATE_PERSONA_ROUTE_POLICY = "mutate_persona_route_policy"
    MUTATE_CONSULT_POLICY = "mutate_consult_policy"
    FREEZE = "freeze"
    RETIRE = "retire"
    SPLIT_PERSONA = "split_persona"
    MERGE_PERSONA = "merge_persona"
    REMOVE_LIVE_OWNER_ROLE = "remove_live_owner_role"
    RESTRICT_POOL_ELIGIBILITY = "restrict_pool_eligibility"
    FORCE_RISK_OFF = "force_risk_off"
    REVIVE = "revive"


class EvolutionActorRole(str, Enum):
    EVOLUTION_CONTROLLER = "evolution_controller"
    REVIEWER_ON_DUTY = "reviewer_on_duty"
    REVIEWER = "reviewer"
    RISK_OWNER = "risk_owner"
    GOVERNANCE_COMMITTEE = "governance_committee"
    OPERATOR = "operator"
    AUTOMATED_GATE = "automated_gate"


class EvolutionTargetType(str, Enum):
    STRATEGY_SPEC = "strategy_spec"
    ALPHA_TEMPLATE = "alpha_template"
    CANDIDATE_ARTIFACT = "candidate_artifact"
    ALLOCATION_POLICY_ARTIFACT = "allocation_policy_artifact"
    PERSONA = "persona"
    PERSONA_CAPITAL_BINDING = "persona_capital_binding"
    CAPITAL_POOL = "capital_pool"


class ThresholdSignalType(str, Enum):
    PERFORMANCE_DEGRADATION = "performance_degradation"
    EXECUTION_DRIFT = "execution_drift"
    FEATURE_DRIFT = "feature_drift"
    HUMAN_CORRECTION = "human_correction"
    GOVERNANCE_INCIDENT = "governance_incident"
    MANUAL_REVIEW = "manual_review"


class ComparisonOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"


class ReviewStepType(str, Enum):
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXECUTED = "executed"


class ExecutionPlane(str, Enum):
    GOVERNANCE = "governance"
    DEPLOYMENT = "deployment"
    RUNTIME = "runtime"
    RESEARCH = "research"


class ExecutionStatus(str, Enum):
    SUBMITTED = "submitted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOOP = "noop"


REVIEW_OWNER_MATRIX: dict[RiskLevel, set[EvolutionActorRole]] = {
    RiskLevel.LOW: {
        EvolutionActorRole.REVIEWER_ON_DUTY,
        EvolutionActorRole.AUTOMATED_GATE,
    },
    RiskLevel.MEDIUM: {
        EvolutionActorRole.REVIEWER,
        EvolutionActorRole.RISK_OWNER,
    },
    RiskLevel.HIGH: {
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
    },
}

APPROVAL_OWNER_MATRIX: dict[RiskLevel, set[EvolutionActorRole]] = {
    RiskLevel.LOW: {
        EvolutionActorRole.REVIEWER_ON_DUTY,
        EvolutionActorRole.AUTOMATED_GATE,
    },
    RiskLevel.MEDIUM: {
        # Policy (EVOLUTION_REVIEW_AND_THRESHOLDS.md §6.2): Risk Owner is the
        # required approval owner for medium-risk decisions. Operator may be
        # added as a *supplementary* approver when explicitly necessary, but
        # must not be listed here as a standalone approval path — doing so
        # would allow operator to approve without Risk Owner sign-off.
        EvolutionActorRole.RISK_OWNER,
    },
    RiskLevel.HIGH: {
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
    },
}

PROPOSER_ROLES = {
    EvolutionActorRole.EVOLUTION_CONTROLLER,
    EvolutionActorRole.OPERATOR,
}
EXECUTION_ROLES = {
    EvolutionActorRole.EVOLUTION_CONTROLLER,
    EvolutionActorRole.OPERATOR,
}
CANCEL_ROLES = {
    EvolutionActorRole.OPERATOR,
    EvolutionActorRole.RISK_OWNER,
    EvolutionActorRole.GOVERNANCE_COMMITTEE,
}

LOW_RISK_ACTIONS = {
    EvolutionActionType.OBSERVE,
    EvolutionActionType.REVALIDATE,
    EvolutionActionType.RETRAIN,
    EvolutionActionType.REQUIRE_MORE_DATA,
    EvolutionActionType.FLAG_FOR_REVIEW,
}
MEDIUM_RISK_ACTIONS = {
    EvolutionActionType.REDUCE_BUDGET,
    EvolutionActionType.TIGHTEN_RISK_POLICY,
    EvolutionActionType.MUTATE_PERSONA_ROUTE_POLICY,
    EvolutionActionType.MUTATE_CONSULT_POLICY,
}
HIGH_RISK_ACTIONS = {
    EvolutionActionType.RETIRE,
    EvolutionActionType.SPLIT_PERSONA,
    EvolutionActionType.MERGE_PERSONA,
    EvolutionActionType.REMOVE_LIVE_OWNER_ROLE,
    EvolutionActionType.RESTRICT_POOL_ELIGIBILITY,
    EvolutionActionType.FORCE_RISK_OFF,
    EvolutionActionType.REVIVE,
}
VALID_TARGET_STAGES = {"paper", "canary", "live", "frozen"}


def infer_risk_level(
    action_type: EvolutionActionType | str,
    *,
    target_stage: str | None = None,
) -> RiskLevel:
    action = EvolutionActionType(action_type)
    if action in LOW_RISK_ACTIONS:
        return RiskLevel.LOW
    if action in MEDIUM_RISK_ACTIONS:
        return RiskLevel.MEDIUM
    if action == EvolutionActionType.FREEZE:
        if target_stage not in {"paper", "canary", "live"}:
            raise EvolutionDecisionError(
                "freeze requires target_stage to be one of: paper, canary, live"
            )
        if target_stage == "live":
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM
    if action in HIGH_RISK_ACTIONS:
        return RiskLevel.HIGH
    raise EvolutionDecisionError(f"Unsupported action_type: {action_type}")


def _ensure_role_allowed(
    role: EvolutionActorRole | str,
    allowed: set[EvolutionActorRole],
    message: str,
) -> EvolutionActorRole:
    normalized = EvolutionActorRole(role)
    if normalized not in allowed:
        allowed_labels = ", ".join(sorted(item.value for item in allowed))
        raise EvolutionDecisionError(f"{message}. Allowed roles: {allowed_labels}")
    return normalized


@dataclass
class ThresholdSnapshot:
    policy_source: str
    signal_type: ThresholdSignalType | str
    metric_name: str
    comparator: ComparisonOperator | str
    observed_value: Any
    threshold_value: Any
    window: Optional[str] = None
    breached: bool = True
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["signal_type"] = (
            self.signal_type.value
            if isinstance(self.signal_type, ThresholdSignalType)
            else self.signal_type
        )
        payload["comparator"] = (
            self.comparator.value
            if isinstance(self.comparator, ComparisonOperator)
            else self.comparator
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ThresholdSnapshot":
        return cls(
            policy_source=str(data["policy_source"]),
            signal_type=str(data["signal_type"]),
            metric_name=str(data["metric_name"]),
            comparator=str(data["comparator"]),
            observed_value=data.get("observed_value"),
            threshold_value=data.get("threshold_value"),
            window=data.get("window"),
            breached=bool(data.get("breached", True)),
            note=data.get("note"),
        )


@dataclass
class ReviewStep:
    step_type: ReviewStepType | str
    actor_role: EvolutionActorRole | str
    actor_id: str
    timestamp: str
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["step_type"] = (
            self.step_type.value
            if isinstance(self.step_type, ReviewStepType)
            else self.step_type
        )
        payload["actor_role"] = (
            self.actor_role.value
            if isinstance(self.actor_role, EvolutionActorRole)
            else self.actor_role
        )
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewStep":
        return cls(
            step_type=str(data["step_type"]),
            actor_role=str(data["actor_role"]),
            actor_id=str(data["actor_id"]),
            timestamp=str(data["timestamp"]),
            note=data.get("note"),
        )


@dataclass
class ExecutionResult:
    status: ExecutionStatus | str
    plane: ExecutionPlane | str
    executed_at: str
    execution_ref_id: Optional[str] = None
    outcome_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value if isinstance(self.status, ExecutionStatus) else self.status
        payload["plane"] = self.plane.value if isinstance(self.plane, ExecutionPlane) else self.plane
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionResult":
        return cls(
            status=str(data["status"]),
            plane=str(data["plane"]),
            executed_at=str(data["executed_at"]),
            execution_ref_id=data.get("execution_ref_id"),
            outcome_summary=data.get("outcome_summary"),
        )


@dataclass
class EvolutionDecision:
    decision_id: str
    target_type: EvolutionTargetType | str
    target_id: str
    target_version: str
    action_type: EvolutionActionType | str
    decision_state: EvolutionDecisionState | str
    risk_level: RiskLevel | str
    created_at: str
    created_by_role: EvolutionActorRole | str
    created_by_id: str
    rationale: str
    approval_decision_id: Optional[str] = None
    evidence_refs: List[EvidenceRef] = field(default_factory=list)
    threshold_snapshots: List[ThresholdSnapshot] = field(default_factory=list)
    review_chain: List[ReviewStep] = field(default_factory=list)
    linked_postmortem_id: Optional[str] = None
    linked_incident_id: Optional[str] = None
    capital_pool_id: Optional[str] = None
    persona_id: Optional[str] = None
    target_stage: Optional[str] = None
    cooldown_started_at: Optional[str] = None
    cooldown_ends_at: Optional[str] = None
    observation_window_started_at: Optional[str] = None
    observation_window_ends_at: Optional[str] = None
    execution_result: Optional[ExecutionResult] = None
    superseded_by: Optional[str] = None
    supersedes_decision_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def create_proposed(
        cls,
        *,
        decision_id: str,
        target_type: EvolutionTargetType | str,
        target_id: str,
        target_version: str,
        action_type: EvolutionActionType | str,
        rationale: str,
        created_by_id: str,
        created_by_role: EvolutionActorRole | str = EvolutionActorRole.EVOLUTION_CONTROLLER,
        risk_level: RiskLevel | str | None = None,
        evidence_refs: List[EvidenceRef] | None = None,
        threshold_snapshots: List[ThresholdSnapshot] | None = None,
        linked_postmortem_id: str | None = None,
        linked_incident_id: str | None = None,
        capital_pool_id: str | None = None,
        persona_id: str | None = None,
        target_stage: str | None = None,
        metadata: Dict[str, Any] | None = None,
    ) -> "EvolutionDecision":
        proposer = _ensure_role_allowed(
            created_by_role,
            PROPOSER_ROLES,
            "created_by_role is not allowed to propose EvolutionDecision",
        )
        resolved_risk = risk_level or infer_risk_level(action_type, target_stage=target_stage)
        return cls(
            decision_id=decision_id,
            target_type=target_type,
            target_id=target_id,
            target_version=target_version,
            action_type=action_type,
            decision_state=EvolutionDecisionState.PROPOSED,
            risk_level=resolved_risk,
            created_at=utc_now(),
            created_by_role=proposer,
            created_by_id=created_by_id,
            rationale=rationale,
            evidence_refs=list(evidence_refs or []),
            threshold_snapshots=list(threshold_snapshots or []),
            linked_postmortem_id=linked_postmortem_id,
            linked_incident_id=linked_incident_id,
            capital_pool_id=capital_pool_id,
            persona_id=persona_id,
            target_stage=target_stage,
            metadata=metadata,
        )

    def mark_reviewed(
        self,
        actor_role: EvolutionActorRole | str,
        actor_id: str,
        approval_decision_id: str,
        *,
        note: str | None = None,
        reviewed_at: str | None = None,
    ) -> None:
        if self.decision_state != EvolutionDecisionState.PROPOSED:
            raise EvolutionDecisionError(
                f"Can only mark reviewed from proposed, got {self.decision_state!r}"
            )
        role = _ensure_role_allowed(
            actor_role,
            REVIEW_OWNER_MATRIX[RiskLevel(self.risk_level)],
            "actor_role is not authorized to review this risk level",
        )
        self.decision_state = EvolutionDecisionState.REVIEWED
        self.approval_decision_id = approval_decision_id
        self.review_chain.append(
            ReviewStep(
                step_type=ReviewStepType.REVIEWED,
                actor_role=role,
                actor_id=actor_id,
                timestamp=reviewed_at or utc_now(),
                note=note,
            )
        )

    def approve(
        self,
        actor_role: EvolutionActorRole | str,
        actor_id: str,
        *,
        approval_decision_id: str | None = None,
        note: str | None = None,
        approved_at: str | None = None,
    ) -> None:
        if self.decision_state != EvolutionDecisionState.REVIEWED:
            raise EvolutionDecisionError(
                f"Can only approve from reviewed, got {self.decision_state!r}"
            )
        role = _ensure_role_allowed(
            actor_role,
            APPROVAL_OWNER_MATRIX[RiskLevel(self.risk_level)],
            "actor_role is not authorized to approve this risk level",
        )
        if approval_decision_id:
            self.approval_decision_id = approval_decision_id
        self.decision_state = EvolutionDecisionState.APPROVED
        self.review_chain.append(
            ReviewStep(
                step_type=ReviewStepType.APPROVED,
                actor_role=role,
                actor_id=actor_id,
                timestamp=approved_at or utc_now(),
                note=note,
            )
        )

    def reject(
        self,
        actor_role: EvolutionActorRole | str,
        actor_id: str,
        *,
        approval_decision_id: str | None = None,
        note: str,
        rejected_at: str | None = None,
    ) -> None:
        if self.decision_state != EvolutionDecisionState.REVIEWED:
            raise EvolutionDecisionError(
                f"Can only reject from reviewed, got {self.decision_state!r}"
            )
        allowed = REVIEW_OWNER_MATRIX[RiskLevel(self.risk_level)] | APPROVAL_OWNER_MATRIX[RiskLevel(self.risk_level)]
        role = _ensure_role_allowed(
            actor_role,
            allowed,
            "actor_role is not authorized to reject this risk level",
        )
        if approval_decision_id:
            self.approval_decision_id = approval_decision_id
        self.decision_state = EvolutionDecisionState.REJECTED
        self.review_chain.append(
            ReviewStep(
                step_type=ReviewStepType.REJECTED,
                actor_role=role,
                actor_id=actor_id,
                timestamp=rejected_at or utc_now(),
                note=note,
            )
        )

    def execute(
        self,
        actor_role: EvolutionActorRole | str,
        actor_id: str,
        execution_result: ExecutionResult,
        *,
        cooldown_started_at: str | None = None,
        cooldown_ends_at: str,
        observation_window_started_at: str | None = None,
        observation_window_ends_at: str,
        note: str | None = None,
    ) -> None:
        if self.decision_state != EvolutionDecisionState.APPROVED:
            raise EvolutionDecisionError(
                f"Can only execute from approved, got {self.decision_state!r}"
            )
        role = _ensure_role_allowed(
            actor_role,
            EXECUTION_ROLES,
            "actor_role is not allowed to execute EvolutionDecision",
        )
        executed_at = execution_result.executed_at
        self.decision_state = EvolutionDecisionState.EXECUTED
        self.execution_result = execution_result
        self.cooldown_started_at = cooldown_started_at or executed_at
        self.cooldown_ends_at = cooldown_ends_at
        self.observation_window_started_at = observation_window_started_at or executed_at
        self.observation_window_ends_at = observation_window_ends_at
        self.review_chain.append(
            ReviewStep(
                step_type=ReviewStepType.EXECUTED,
                actor_role=role,
                actor_id=actor_id,
                timestamp=executed_at,
                note=note,
            )
        )

    def cancel(
        self,
        actor_role: EvolutionActorRole | str,
        actor_id: str,
        *,
        note: str,
        canceled_at: str | None = None,
    ) -> None:
        if self.decision_state not in {
            EvolutionDecisionState.PROPOSED,
            EvolutionDecisionState.REVIEWED,
            EvolutionDecisionState.APPROVED,
        }:
            raise EvolutionDecisionError(
                f"Can only cancel from proposed/reviewed/approved, got {self.decision_state!r}"
            )
        role = _ensure_role_allowed(
            actor_role,
            CANCEL_ROLES,
            "actor_role is not allowed to cancel EvolutionDecision",
        )
        self.decision_state = EvolutionDecisionState.CANCELED
        self.review_chain.append(
            ReviewStep(
                step_type=ReviewStepType.CANCELED,
                actor_role=role,
                actor_id=actor_id,
                timestamp=canceled_at or utc_now(),
                note=note,
            )
        )

    def supersede(self, superseded_by: str) -> None:
        if self.decision_state == EvolutionDecisionState.SUPERSEDED:
            raise EvolutionDecisionError("Decision is already superseded")
        if superseded_by == self.decision_id:
            raise EvolutionDecisionError("superseded_by cannot equal decision_id")
        self.superseded_by = superseded_by
        self.decision_state = EvolutionDecisionState.SUPERSEDED

    def active_until(self) -> datetime | None:
        bounds = [
            _parse_timestamp(self.cooldown_ends_at),
            _parse_timestamp(self.observation_window_ends_at),
        ]
        active_bounds = [bound for bound in bounds if bound is not None]
        if not active_bounds:
            return None
        return max(active_bounds)

    def is_active(self, *, as_of: str | None = None) -> bool:
        state = EvolutionDecisionState(self.decision_state)
        if state in {
            EvolutionDecisionState.PROPOSED,
            EvolutionDecisionState.REVIEWED,
            EvolutionDecisionState.APPROVED,
        }:
            return True
        if state != EvolutionDecisionState.EXECUTED:
            return False
        active_end = self.active_until()
        if active_end is None:
            return False
        compare_at = _parse_timestamp(as_of) or datetime.now(timezone.utc)
        return compare_at <= active_end

    def validate(self) -> List[str]:
        errors: List[str] = []
        required = {
            "decision_id": self.decision_id,
            "target_id": self.target_id,
            "target_version": self.target_version,
            "created_at": self.created_at,
            "created_by_id": self.created_by_id,
            "rationale": self.rationale,
        }
        for field_name, value in required.items():
            if value in (None, ""):
                errors.append(f"{field_name} is required")

        try:
            EvolutionTargetType(self.target_type)
        except ValueError:
            errors.append(f"Invalid target_type: {self.target_type}")

        action: EvolutionActionType | None
        try:
            action = EvolutionActionType(self.action_type)
        except ValueError:
            errors.append(f"Invalid action_type: {self.action_type}")
            action = None

        state: EvolutionDecisionState | None
        try:
            state = EvolutionDecisionState(self.decision_state)
        except ValueError:
            errors.append(f"Invalid decision_state: {self.decision_state}")
            state = None

        try:
            risk = RiskLevel(self.risk_level)
        except ValueError:
            errors.append(f"Invalid risk_level: {self.risk_level}")
            risk = None

        try:
            proposer_role = EvolutionActorRole(self.created_by_role)
        except ValueError:
            errors.append(f"Invalid created_by_role: {self.created_by_role}")
            proposer_role = None
        else:
            if proposer_role not in PROPOSER_ROLES:
                errors.append(
                    f"created_by_role '{proposer_role.value}' is not allowed to propose EvolutionDecision"
                )

        if self.target_stage:
            if self.target_stage not in VALID_TARGET_STAGES:
                errors.append(
                    f"Invalid target_stage: {self.target_stage}. Must be one of {sorted(VALID_TARGET_STAGES)}"
                )
            if action == EvolutionActionType.FREEZE and self.target_stage == "frozen":
                errors.append("freeze target_stage must be paper, canary, or live")

        if action == EvolutionActionType.FREEZE and not self.target_stage:
            errors.append("freeze action_type requires target_stage")

        if action is not None and risk is not None:
            try:
                inferred = infer_risk_level(action, target_stage=self.target_stage)
            except EvolutionDecisionError as exc:
                errors.append(str(exc))
            else:
                if inferred != risk:
                    errors.append(
                        f"risk_level mismatch: expected {inferred.value} for action_type '{action.value}', got {risk.value}"
                    )

        if not (
            self.evidence_refs
            or self.threshold_snapshots
            or self.linked_postmortem_id
            or self.linked_incident_id
        ):
            errors.append(
                "At least one evidence link is required: evidence_refs, threshold_snapshots, linked_postmortem_id, or linked_incident_id"
            )

        for idx, ref in enumerate(self.evidence_refs):
            if not ref.ref_id:
                errors.append(f"evidence_refs[{idx}].ref_id is required")
            try:
                EvidenceRefType(ref.ref_type)
            except ValueError:
                errors.append(f"evidence_refs[{idx}].ref_type is invalid: {ref.ref_type}")

        for idx, snap in enumerate(self.threshold_snapshots):
            if not snap.policy_source:
                errors.append(f"threshold_snapshots[{idx}].policy_source is required")
            if not snap.metric_name:
                errors.append(f"threshold_snapshots[{idx}].metric_name is required")
            try:
                ThresholdSignalType(snap.signal_type)
            except ValueError:
                errors.append(
                    f"threshold_snapshots[{idx}].signal_type is invalid: {snap.signal_type}"
                )
            try:
                ComparisonOperator(snap.comparator)
            except ValueError:
                errors.append(
                    f"threshold_snapshots[{idx}].comparator is invalid: {snap.comparator}"
                )

        seen_steps: list[ReviewStepType] = []
        for idx, step in enumerate(self.review_chain):
            step_type: ReviewStepType | None = None
            try:
                step_type = ReviewStepType(step.step_type)
                seen_steps.append(step_type)
            except ValueError:
                errors.append(f"review_chain[{idx}].step_type is invalid: {step.step_type}")
            try:
                role = EvolutionActorRole(step.actor_role)
            except ValueError:
                errors.append(f"review_chain[{idx}].actor_role is invalid: {step.actor_role}")
            else:
                if risk is not None and step_type == ReviewStepType.REVIEWED:
                    if role not in REVIEW_OWNER_MATRIX[risk]:
                        errors.append(
                            f"review_chain[{idx}].actor_role '{role.value}' cannot review risk '{risk.value}'"
                        )
                if risk is not None and step_type == ReviewStepType.APPROVED:
                    if role not in APPROVAL_OWNER_MATRIX[risk]:
                        errors.append(
                            f"review_chain[{idx}].actor_role '{role.value}' cannot approve risk '{risk.value}'"
                        )
            if not step.actor_id:
                errors.append(f"review_chain[{idx}].actor_id is required")
            if not step.timestamp:
                errors.append(f"review_chain[{idx}].timestamp is required")

        has_reviewed = ReviewStepType.REVIEWED in seen_steps
        has_approved = ReviewStepType.APPROVED in seen_steps
        has_rejected = ReviewStepType.REJECTED in seen_steps
        has_executed = ReviewStepType.EXECUTED in seen_steps

        if state in {
            EvolutionDecisionState.REVIEWED,
            EvolutionDecisionState.APPROVED,
            EvolutionDecisionState.REJECTED,
            EvolutionDecisionState.EXECUTED,
        }:
            if not self.approval_decision_id:
                errors.append("approval_decision_id is required once review has started")
            if not has_reviewed:
                errors.append("review_chain must contain a reviewed step once state reaches reviewed or beyond")

        if state == EvolutionDecisionState.APPROVED and not has_approved:
            errors.append("approved state requires an approved step in review_chain")

        if state == EvolutionDecisionState.REJECTED and not has_rejected:
            errors.append("rejected state requires a rejected step in review_chain")

        if state == EvolutionDecisionState.EXECUTED:
            if not has_approved:
                errors.append("executed state requires an approved step in review_chain")
            if not has_executed:
                errors.append("executed state requires an executed step in review_chain")
            if self.execution_result is None:
                errors.append("execution_result is required for executed state")
            if not self.cooldown_started_at or not self.cooldown_ends_at:
                errors.append("executed state requires cooldown_started_at and cooldown_ends_at")
            if not self.observation_window_started_at or not self.observation_window_ends_at:
                errors.append(
                    "executed state requires observation_window_started_at and observation_window_ends_at"
                )

        if state != EvolutionDecisionState.EXECUTED and self.execution_result is not None:
            errors.append("execution_result is only allowed when decision_state == executed")

        if self.execution_result is not None:
            try:
                ExecutionStatus(self.execution_result.status)
            except ValueError:
                errors.append(f"Invalid execution_result.status: {self.execution_result.status}")
            try:
                ExecutionPlane(self.execution_result.plane)
            except ValueError:
                errors.append(f"Invalid execution_result.plane: {self.execution_result.plane}")
            if not self.execution_result.executed_at:
                errors.append("execution_result.executed_at is required")

        if any(
            value is not None
            for value in (
                self.cooldown_started_at,
                self.cooldown_ends_at,
                self.observation_window_started_at,
                self.observation_window_ends_at,
            )
        ):
            cooldown_start = _parse_timestamp(self.cooldown_started_at)
            cooldown_end = _parse_timestamp(self.cooldown_ends_at)
            observation_start = _parse_timestamp(self.observation_window_started_at)
            observation_end = _parse_timestamp(self.observation_window_ends_at)
            if cooldown_start is None or cooldown_end is None:
                errors.append("cooldown window must include valid start and end timestamps")
            elif cooldown_end <= cooldown_start:
                errors.append("cooldown_ends_at must be after cooldown_started_at")
            if observation_start is None or observation_end is None:
                errors.append("observation window must include valid start and end timestamps")
            elif observation_end <= observation_start:
                errors.append("observation_window_ends_at must be after observation_window_started_at")

        if state == EvolutionDecisionState.SUPERSEDED and not self.superseded_by:
            errors.append("superseded state requires superseded_by")

        return errors

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["target_type"] = (
            self.target_type.value if isinstance(self.target_type, EvolutionTargetType) else self.target_type
        )
        payload["action_type"] = (
            self.action_type.value if isinstance(self.action_type, EvolutionActionType) else self.action_type
        )
        payload["decision_state"] = (
            self.decision_state.value
            if isinstance(self.decision_state, EvolutionDecisionState)
            else self.decision_state
        )
        payload["risk_level"] = self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level
        payload["created_by_role"] = (
            self.created_by_role.value
            if isinstance(self.created_by_role, EvolutionActorRole)
            else self.created_by_role
        )
        payload["evidence_refs"] = [
            ref.to_dict() if isinstance(ref, EvidenceRef) else ref for ref in self.evidence_refs
        ]
        payload["threshold_snapshots"] = [
            snap.to_dict() if isinstance(snap, ThresholdSnapshot) else snap
            for snap in self.threshold_snapshots
        ]
        payload["review_chain"] = [
            step.to_dict() if isinstance(step, ReviewStep) else step for step in self.review_chain
        ]
        if self.execution_result:
            payload["execution_result"] = (
                self.execution_result.to_dict()
                if isinstance(self.execution_result, ExecutionResult)
                else self.execution_result
            )
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvolutionDecision":
        return cls(
            decision_id=str(data["decision_id"]),
            target_type=str(data["target_type"]),
            target_id=str(data["target_id"]),
            target_version=str(data["target_version"]),
            action_type=str(data["action_type"]),
            decision_state=str(data["decision_state"]),
            risk_level=str(data["risk_level"]),
            created_at=str(data["created_at"]),
            created_by_role=str(data["created_by_role"]),
            created_by_id=str(data["created_by_id"]),
            rationale=str(data["rationale"]),
            approval_decision_id=data.get("approval_decision_id"),
            evidence_refs=[
                EvidenceRef.from_dict(item) if isinstance(item, Mapping) else item
                for item in data.get("evidence_refs", [])
            ],
            threshold_snapshots=[
                ThresholdSnapshot.from_dict(item) if isinstance(item, Mapping) else item
                for item in data.get("threshold_snapshots", [])
            ],
            review_chain=[
                ReviewStep.from_dict(item) if isinstance(item, Mapping) else item
                for item in data.get("review_chain", [])
            ],
            linked_postmortem_id=data.get("linked_postmortem_id"),
            linked_incident_id=data.get("linked_incident_id"),
            capital_pool_id=data.get("capital_pool_id"),
            persona_id=data.get("persona_id"),
            target_stage=data.get("target_stage"),
            cooldown_started_at=data.get("cooldown_started_at"),
            cooldown_ends_at=data.get("cooldown_ends_at"),
            observation_window_started_at=data.get("observation_window_started_at"),
            observation_window_ends_at=data.get("observation_window_ends_at"),
            execution_result=ExecutionResult.from_dict(data["execution_result"])
            if isinstance(data.get("execution_result"), Mapping)
            else None,
            superseded_by=data.get("superseded_by"),
            supersedes_decision_id=data.get("supersedes_decision_id"),
            metadata=data.get("metadata"),
        )

    @classmethod
    def from_json(cls, payload: str) -> "EvolutionDecision":
        return cls.from_dict(json.loads(payload))


_SCHEMA_PATH = Path(__file__).parent / "evolution_decision.schema.json"


def validate_evolution_decision_json(data: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    required = [
        "decision_id",
        "target_type",
        "target_id",
        "target_version",
        "action_type",
        "decision_state",
        "risk_level",
        "created_at",
        "created_by_role",
        "created_by_id",
        "rationale",
    ]
    for key in required:
        if not data.get(key):
            errors.append(f"Missing required field: {key}")
    if errors:
        return errors
    try:
        decision = EvolutionDecision.from_dict(data)
    except Exception as exc:  # pragma: no cover - defensive parse guard
        errors.append(str(exc))
        return errors
    return decision.validate()


def validate_evolution_decision(decision: EvolutionDecision) -> List[str]:
    return decision.validate()


class EvolutionDecisionStore:
    """In-memory store for EvolutionDecision with optional persistence."""

    def __init__(
        self,
        storage_path: Optional[str] = None,
        *,
        incident_store: Any | None = None,
    ) -> None:
        self._storage_path = Path(storage_path) if storage_path else None
        self._incident_store = incident_store
        self._decisions: Dict[str, EvolutionDecision] = {}
        if self._storage_path and self._storage_path.exists():
            self._load()

    def put(self, decision: EvolutionDecision) -> None:
        errors = validate_evolution_decision(decision)
        if errors:
            raise EvolutionDecisionError(f"Invalid EvolutionDecision: {errors}")
        self._enforce_single_active_rule(decision)
        self._decisions[decision.decision_id] = decision
        self._sync_postmortem_link(decision)
        self._save()

    def get(self, decision_id: str) -> Optional[EvolutionDecision]:
        return self._decisions.get(decision_id)

    def list_all(self) -> List[EvolutionDecision]:
        return list(self._decisions.values())

    def find_by_target(
        self,
        target_type: EvolutionTargetType | str,
        target_id: str,
    ) -> List[EvolutionDecision]:
        target_type_value = (
            target_type.value if isinstance(target_type, EvolutionTargetType) else target_type
        )
        return [
            decision
            for decision in self._decisions.values()
            if decision.target_id == target_id
            and decision.to_dict()["target_type"] == target_type_value
        ]

    def find_active_by_target(
        self,
        target_type: EvolutionTargetType | str,
        target_id: str,
        *,
        as_of: str | None = None,
    ) -> List[EvolutionDecision]:
        return [
            decision
            for decision in self.find_by_target(target_type, target_id)
            if decision.is_active(as_of=as_of)
        ]

    def find_by_postmortem(self, postmortem_id: str) -> List[EvolutionDecision]:
        return [
            decision
            for decision in self._decisions.values()
            if decision.linked_postmortem_id == postmortem_id
        ]

    def _enforce_single_active_rule(self, candidate: EvolutionDecision) -> None:
        if not candidate.is_active():
            return
        for other in self._decisions.values():
            if other.decision_id == candidate.decision_id:
                continue
            if other.target_id != candidate.target_id:
                continue
            if other.to_dict()["target_type"] != candidate.to_dict()["target_type"]:
                continue
            if other.is_active():
                raise EvolutionDecisionError(
                    "single-active-rule violated: target already has an active EvolutionDecision "
                    f"({other.decision_id})"
                )

    def _sync_postmortem_link(self, decision: EvolutionDecision) -> None:
        if self._incident_store is None or not decision.linked_postmortem_id:
            return
        self._incident_store.link_evolution_decision(
            decision.linked_postmortem_id,
            decision.decision_id,
        )

    def _save(self) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            decision_id: decision.to_dict()
            for decision_id, decision in self._decisions.items()
        }
        self._storage_path.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not self._storage_path or not self._storage_path.exists():
            return
        text = self._storage_path.read_text()
        if not text.strip():
            return
        raw = json.loads(text)
        for decision_id, data in raw.items():
            self._decisions[decision_id] = EvolutionDecision.from_dict(data)


def to_audit_event(decision: EvolutionDecision, event_type: str) -> Dict[str, Any]:
    latest_actor = decision.created_by_id
    latest_role = (
        decision.created_by_role.value
        if isinstance(decision.created_by_role, EvolutionActorRole)
        else decision.created_by_role
    )
    if decision.review_chain:
        last = decision.review_chain[-1]
        latest_actor = last.actor_id
        latest_role = last.actor_role.value if isinstance(last.actor_role, EvolutionActorRole) else last.actor_role
    return {
        "event_type": event_type,
        "decision_id": decision.decision_id,
        "target_type": (
            decision.target_type.value
            if isinstance(decision.target_type, EvolutionTargetType)
            else decision.target_type
        ),
        "target_id": decision.target_id,
        "action_type": (
            decision.action_type.value
            if isinstance(decision.action_type, EvolutionActionType)
            else decision.action_type
        ),
        "decision_state": (
            decision.decision_state.value
            if isinstance(decision.decision_state, EvolutionDecisionState)
            else decision.decision_state
        ),
        "approval_decision_id": decision.approval_decision_id,
        "actor_id": latest_actor,
        "actor_role": latest_role,
        "timestamp": utc_now(),
    }

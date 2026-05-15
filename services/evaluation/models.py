"""
Dataclass models for evaluation service output contracts.

Two output types (contract: services/evaluation/contracts/contract.md):
  - EvaluatorResult  — scored assessment of a governed artifact
  - CriticResult     — rationale and failure analysis supplementing evaluator scores

Both are governed artifacts stored in the registry; neither triggers promotion directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------

class ArtifactType(str, Enum):
    STRATEGY_SPEC = "strategy_spec"
    MODEL_ARTIFACT = "model_artifact"
    FEATURE_SET = "feature_set"
    PROMPT_BUNDLE = "prompt_bundle"
    SIGNAL_SNAPSHOT = "signal_snapshot"
    EXECUTION_BUNDLE = "execution_bundle"


class PromotionState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    PAPER = "paper"
    LIVE = "live"
    RETIRED = "retired"


# ---------------------------------------------------------------------------
# Evaluator types
# ---------------------------------------------------------------------------

class Recommendation(str, Enum):
    CANDIDATE_TO_PAPER = "candidate_to_paper"
    CANDIDATE_HOLD = "candidate_hold"
    PAPER_TO_LIVE = "paper_to_live"
    PAPER_HOLD = "paper_hold"
    RETIRE = "retire"
    NEEDS_CRITIQUE = "needs_critique"


@dataclass
class ScoreComponent:
    value: Any
    weight: float
    dimension_score: float
    interpretation: str = ""
    confidence: Optional[float] = None
    model_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError(f"weight must be in [0, 1], got {self.weight}")
        if not (0.0 <= self.dimension_score <= 1.0):
            raise ValueError(f"dimension_score must be in [0, 1], got {self.dimension_score}")


@dataclass
class EvaluationDataSnapshot:
    target_artifact_version: str
    target_artifact_checksum: str
    target_created_at: Optional[str] = None
    target_lineage: Optional[Dict[str, Any]] = None
    execution_telemetry_window: Optional[Dict[str, Any]] = None
    feedback_events_considered: Optional[Dict[str, Any]] = None
    preference_model_used: Optional[Dict[str, Any]] = None


@dataclass
class EvaluatorResult:
    registry_id: str
    artifact_type: str  # always "evaluation_result"
    strategy_id: str
    target_artifact_id: str
    target_artifact_type: str
    target_promotion_state: str
    evaluator_id: str
    evaluator_version: str
    evaluation_timestamp: str
    evaluation_data_snapshot: EvaluationDataSnapshot
    score_components: Dict[str, ScoreComponent]
    overall_score: float
    recommendation: str
    confidence: float
    rationale: Optional[str] = None
    auditable_fields: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.artifact_type != "evaluation_result":
            raise ValueError("artifact_type must be 'evaluation_result'")
        if not (0.0 <= self.overall_score <= 1.0):
            raise ValueError(f"overall_score must be in [0, 1], got {self.overall_score}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        valid_artifact_types = {artifact_type.value for artifact_type in ArtifactType}
        if self.target_artifact_type not in valid_artifact_types:
            raise ValueError(f"target_artifact_type must be one of {valid_artifact_types}")
        valid_promotion_states = {promotion_state.value for promotion_state in PromotionState}
        if self.target_promotion_state not in valid_promotion_states:
            raise ValueError(f"target_promotion_state must be one of {valid_promotion_states}")
        valid_recs = {r.value for r in Recommendation}
        if self.recommendation not in valid_recs:
            raise ValueError(f"recommendation must be one of {valid_recs}")
        if not self.score_components:
            raise ValueError("score_components must contain at least one component")
        total_weight = sum(c.weight for c in self.score_components.values())
        if not (0.99 <= total_weight <= 1.01):
            raise ValueError(f"score_components weights must sum to 1.0 ± 0.01, got {total_weight:.4f}")

    def to_dict(self) -> Dict[str, Any]:
        snap = self.evaluation_data_snapshot
        snap_dict: Dict[str, Any] = {
            "target_artifact_version": snap.target_artifact_version,
            "target_artifact_checksum": snap.target_artifact_checksum,
        }
        for key in ("target_created_at", "target_lineage", "execution_telemetry_window",
                    "feedback_events_considered", "preference_model_used"):
            val = getattr(snap, key)
            if val is not None:
                snap_dict[key] = val

        components: Dict[str, Any] = {}
        for name, comp in self.score_components.items():
            c: Dict[str, Any] = {
                "value": comp.value,
                "weight": comp.weight,
                "dimension_score": comp.dimension_score,
            }
            if comp.interpretation:
                c["interpretation"] = comp.interpretation
            if comp.confidence is not None:
                c["confidence"] = comp.confidence
            if comp.model_id is not None:
                c["model_id"] = comp.model_id
            components[name] = c

        result: Dict[str, Any] = {
            "registry_id": self.registry_id,
            "artifact_type": self.artifact_type,
            "strategy_id": self.strategy_id,
            "target_artifact_id": self.target_artifact_id,
            "target_artifact_type": self.target_artifact_type,
            "target_promotion_state": self.target_promotion_state,
            "evaluator_id": self.evaluator_id,
            "evaluator_version": self.evaluator_version,
            "evaluation_timestamp": self.evaluation_timestamp,
            "evaluation_data_snapshot": snap_dict,
            "score_components": components,
            "overall_score": self.overall_score,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
        }
        if self.rationale is not None:
            result["rationale"] = self.rationale
        if self.auditable_fields is not None:
            result["auditable_fields"] = self.auditable_fields
        return result


# ---------------------------------------------------------------------------
# Critic types
# ---------------------------------------------------------------------------

class CritiqueTrigger(str, Enum):
    EVALUATION_DISAGREEMENT = "evaluation_disagreement"
    BORDERLINE_SCORE = "borderline_score"
    FAILURE_FORENSICS = "failure_forensics"
    RISK_FLAGGED = "risk_flagged"
    PROMOTION_OVERRIDE_REQUESTED = "promotion_override_requested"
    BASELINE_DRIFT_DETECTED = "baseline_drift_detected"


class FindingCategory(str, Enum):
    REPLICATION_RISK = "replication_risk"
    EXECUTION_FIDELITY = "execution_fidelity"
    MODEL_DEGRADATION = "model_degradation"
    MARKET_REGIME_SENSITIVITY = "market_regime_sensitivity"
    CORRELATION_CHANGE = "correlation_change"
    SIGNAL_DRIFT = "signal_drift"
    PARAMETER_SENSITIVITY = "parameter_sensitivity"
    DATA_QUALITY = "data_quality"
    OTHER = "other"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Finding:
    finding_id: str
    category: str
    severity: str
    description: str
    evidence: List[str] = field(default_factory=list)
    impact: Optional[str] = None
    recommendation: Optional[str] = None

    def __post_init__(self) -> None:
        valid_cats = {c.value for c in FindingCategory}
        if self.category not in valid_cats:
            raise ValueError(f"category must be one of {valid_cats}")
        valid_sevs = {s.value for s in Severity}
        if self.severity not in valid_sevs:
            raise ValueError(f"severity must be one of {valid_sevs}")


@dataclass
class KeyRisk:
    rank: int
    risk_type: str
    risk_level: str
    description: str
    likelihood: Optional[str] = None
    impact: Optional[str] = None
    mitigation: Optional[str] = None

    VALID_RISK_TYPES = frozenset({
        "model_degradation", "market_regime_sensitivity", "correlation_change",
        "overfitting", "data_leakage", "operational", "liquidity", "counterparty", "other",
    })
    VALID_RISK_LEVELS = frozenset({"critical", "high", "medium", "low"})

    def __post_init__(self) -> None:
        if self.risk_type not in self.VALID_RISK_TYPES:
            raise ValueError(f"risk_type must be one of {self.VALID_RISK_TYPES}")
        if self.risk_level not in self.VALID_RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {self.VALID_RISK_LEVELS}")


@dataclass
class DecisionGuidance:
    recommended_action: str
    confidence_in_recommendation: float
    rationale_summary: str
    conditions_for_approval: List[str] = field(default_factory=list)
    conditions_for_rejection: List[str] = field(default_factory=list)
    escalation_recommendation: Optional[str] = None
    next_evaluation_trigger: Optional[str] = None

    VALID_ACTIONS = frozenset({
        "approve_candidate_to_paper", "reject_candidate", "approve_paper_to_live",
        "reject_paper_to_live", "recommend_retirement", "escalate_for_review",
        "needs_further_evaluation",
    })

    def __post_init__(self) -> None:
        if self.recommended_action not in self.VALID_ACTIONS:
            raise ValueError(f"recommended_action must be one of {self.VALID_ACTIONS}")
        if not (0.0 <= self.confidence_in_recommendation <= 1.0):
            raise ValueError("confidence_in_recommendation must be in [0, 1]")


@dataclass
class CriticResult:
    registry_id: str
    artifact_type: str  # always "critique_result"
    strategy_id: str
    target_artifact_id: str
    target_artifact_type: str
    target_promotion_state: str
    critic_id: str
    critic_version: str
    critique_timestamp: str
    critique_trigger: str
    findings: List[Finding]
    key_risks: List[KeyRisk]
    decision_guidance: DecisionGuidance
    evaluation_context: Optional[Dict[str, Any]] = None
    rationale: Optional[str] = None

    def __post_init__(self) -> None:
        if self.artifact_type != "critique_result":
            raise ValueError("artifact_type must be 'critique_result'")
        if not self.findings:
            raise ValueError("findings must contain at least one entry")
        valid_triggers = {t.value for t in CritiqueTrigger}
        if self.critique_trigger not in valid_triggers:
            raise ValueError(f"critique_trigger must be one of {valid_triggers}")

    def to_dict(self) -> Dict[str, Any]:
        def finding_dict(f: Finding) -> Dict[str, Any]:
            d: Dict[str, Any] = {
                "finding_id": f.finding_id,
                "category": f.category,
                "severity": f.severity,
                "description": f.description,
            }
            if f.evidence:
                d["evidence"] = f.evidence
            if f.impact is not None:
                d["impact"] = f.impact
            if f.recommendation is not None:
                d["recommendation"] = f.recommendation
            return d

        def risk_dict(r: KeyRisk) -> Dict[str, Any]:
            d: Dict[str, Any] = {
                "rank": r.rank,
                "risk_type": r.risk_type,
                "risk_level": r.risk_level,
                "description": r.description,
            }
            for key in ("likelihood", "impact", "mitigation"):
                val = getattr(r, key)
                if val is not None:
                    d[key] = val
            return d

        g = self.decision_guidance
        guidance: Dict[str, Any] = {
            "recommended_action": g.recommended_action,
            "confidence_in_recommendation": g.confidence_in_recommendation,
            "rationale_summary": g.rationale_summary,
        }
        if g.conditions_for_approval:
            guidance["conditions_for_approval"] = g.conditions_for_approval
        if g.conditions_for_rejection:
            guidance["conditions_for_rejection"] = g.conditions_for_rejection
        if g.escalation_recommendation is not None:
            guidance["escalation_recommendation"] = g.escalation_recommendation
        if g.next_evaluation_trigger is not None:
            guidance["next_evaluation_trigger"] = g.next_evaluation_trigger

        result: Dict[str, Any] = {
            "registry_id": self.registry_id,
            "artifact_type": self.artifact_type,
            "strategy_id": self.strategy_id,
            "target_artifact_id": self.target_artifact_id,
            "target_artifact_type": self.target_artifact_type,
            "target_promotion_state": self.target_promotion_state,
            "critic_id": self.critic_id,
            "critic_version": self.critic_version,
            "critique_timestamp": self.critique_timestamp,
            "critique_trigger": self.critique_trigger,
            "findings": [finding_dict(f) for f in self.findings],
            "key_risks": [risk_dict(r) for r in self.key_risks],
            "decision_guidance": guidance,
        }
        if self.evaluation_context is not None:
            result["evaluation_context"] = self.evaluation_context
        if self.rationale is not None:
            result["rationale"] = self.rationale
        return result

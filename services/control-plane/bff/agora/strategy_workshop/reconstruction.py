"""Agent-owned semantic reconstruction; deterministic draft validation only.

No keyword reconstruction, invented StrategySpec, or execution approval lives
here. The agent's interpretation is a proposal, never financial authority.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from services.research.strategy_spec.models import (
    StrategySpec, validate_strategy_spec, validate_strategy_spec_payload,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


CompletenessGrade = Literal["insufficient", "draftable", "researchable", "trading_room_ready"]


class StrategyMapBlock(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["missing", "partial", "confirmed"] = "missing"
    summary: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class StrategyMap(BaseModel):
    model_config = {"extra": "forbid"}
    hypothesis: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    universe: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    data_requirements: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    signal_definition: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    entry_rules: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    exit_rules: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    position_sizing: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    risk_controls: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    cost_liquidity_capacity: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    validation_plan: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    regime_invalidation: StrategyMapBlock = Field(default_factory=StrategyMapBlock)
    governance_constraints: StrategyMapBlock = Field(default_factory=StrategyMapBlock)


class CompletenessAssessment(BaseModel):
    model_config = {"extra": "forbid"}
    grade: CompletenessGrade = "insufficient"
    blockers: List[str] = Field(default_factory=list)
    confirmed_fields: List[str] = Field(default_factory=list)
    unconfirmed_fields: List[str] = Field(default_factory=list)


class NextBestQuestion(BaseModel):
    model_config = {"extra": "forbid"}
    question_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    resolves: List[str] = Field(default_factory=list)
    why_now: str = Field(min_length=1)


class SemanticReconstructionDraft(BaseModel):
    """Data-only provider output: no IDs, grade, provenance or approval authority."""
    model_config = {"extra": "forbid"}
    strategy_map: StrategyMap
    explicit_facts: List[str]
    inferences: List[str]
    assumptions: List[str]
    contradictions: List[str]
    next_best_question: NextBestQuestion
    strategy_spec: Optional[Dict[str, Any]] = None


class StrategyReconstructionResult(BaseModel):
    model_config = {"extra": "forbid"}
    reconstruction_id: str = Field(min_length=1)
    workshop_id: str = Field(min_length=1)
    based_on_sequence_no: int = Field(ge=0)
    strategy_map: StrategyMap = Field(default_factory=StrategyMap)
    explicit_facts: List[str] = Field(default_factory=list)
    inferences: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    completeness: CompletenessAssessment = Field(default_factory=CompletenessAssessment)
    next_best_question: Optional[NextBestQuestion] = None
    draft_proposal: Optional[Dict[str, Any]] = None
    provider_lineage: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)


def reconstruct_strategy_from_events(
    *, workshop_id: str, sequence_no: int, events: List[Dict[str, Any]],
    messages_content: List[str], provider_lineage: Optional[Dict[str, Any]] = None,
    strategy_spec: Optional[StrategySpec | Dict[str, Any]] = None,
    semantic_draft: Optional[SemanticReconstructionDraft] = None,
) -> StrategyReconstructionResult:
    """Validate a proposal, never interpret text with rules or manufacture fields.

    Without a semantic turn this produces an explicitly insufficient result.
    Even a complete typed agent draft is at most draftable, not trade-ready.
    """
    strategy_map = semantic_draft.strategy_map if semantic_draft else StrategyMap()
    confirmed = [name for name in StrategyMap.model_fields
                 if getattr(strategy_map, name).status == "confirmed"]
    unconfirmed = [name for name in StrategyMap.model_fields if name not in confirmed]
    blockers: List[str] = []
    if semantic_draft is None:
        blockers.append("Semantic reconstruction unavailable")
    if unconfirmed:
        blockers.append("Unconfirmed strategy dimensions: " + ", ".join(unconfirmed))
    if semantic_draft and (semantic_draft.contradictions or semantic_draft.assumptions):
        blockers.append("Resolve contradictions and unverified assumptions before drafting")
    candidate = strategy_spec or (semantic_draft.strategy_spec if semantic_draft else None)
    document = None
    if candidate is not None:
        try:
            document = candidate.to_dict() if isinstance(candidate, StrategySpec) else candidate
            validate_strategy_spec_payload(document)
            spec = StrategySpec.from_dict(document, validate_schema=True)
            errors = validate_strategy_spec(spec)
            if errors:
                raise ValueError("; ".join(errors))
            if document.get("lifecycle_state", "draft") != "draft":
                raise ValueError("reconstruction can only propose a draft")
            if document.get("governance", {}).get("approval_required") is not True:
                raise ValueError("reconstruction cannot waive approval")
        except (ValueError, TypeError, KeyError) as exc:
            document = None
            blockers.append(f"StrategySpec validation failed: {exc}")
    if document is None:
        blockers.append("No valid executable StrategySpec provided or reconstructed")
    ready_to_draft = document is not None and not blockers
    digest = hashlib.sha256(f"semantic-v2:{workshop_id}:{sequence_no}".encode()).hexdigest()[:20]
    return StrategyReconstructionResult(
        reconstruction_id=f"recon-{digest}", workshop_id=workshop_id,
        based_on_sequence_no=sequence_no, strategy_map=strategy_map,
        explicit_facts=semantic_draft.explicit_facts if semantic_draft else [],
        inferences=semantic_draft.inferences if semantic_draft else [],
        assumptions=semantic_draft.assumptions if semantic_draft else [],
        contradictions=semantic_draft.contradictions if semantic_draft else [],
        next_best_question=semantic_draft.next_best_question if semantic_draft else NextBestQuestion(
            question_id=f"nbq-{sequence_no}", text="What strategy would you like to explore?",
            resolves=["hypothesis.summary"], why_now="No semantic reconstruction is available.",
        ),
        completeness=CompletenessAssessment(
            grade="draftable" if ready_to_draft else "insufficient", blockers=blockers,
            confirmed_fields=confirmed, unconfirmed_fields=unconfirmed,
        ),
        draft_proposal={"strategy_spec": document} if ready_to_draft else None,
        provider_lineage=provider_lineage or {"engine": "typed_validation_only", "provider": None},
        evidence_refs=[{"ref_type": "conversation_sequence", "ref_id": f"seq-{sequence_no}",
                        "summary": f"Messages up to sequence {sequence_no}"}],
    )

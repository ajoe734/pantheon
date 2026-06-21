"""agora-strategy-completeness skill — Pantheon-side orchestrator.

Implements the agora-strategy-completeness skill per C1 design-closure SPEC:
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/
    skills/agora/strategy-completeness/SPEC.md

Scoring logic (A1):
  services/research/strategy_spec/completeness.py

Hard rules (C1 SPEC §Common Hard Rules):
  - Must not write RuntimeBinding, capital binding, broker order, or live enable.
  - Must not cross user scope.
  - Research / consult / dashboard output is proposal/candidate only.
  - All refs need evidence_ref / source_ref.
  - Output uncertain items as uncertain; never fabricate.

Failure codes (C1 §Common Failure Codes):
  INPUT_SCHEMA_INVALID, POLICY_VERSION_MISMATCH, REGISTRY_VERSION_MISMATCH,
  CAPABILITY_DENIED, NO_ACTIONABLE_CHANGE.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.research.strategy_spec.completeness import (
    QUESTION_SCORING_POLICY_VERSION,
    BlockingItem,
    NextBestQuestion,
    ProvisionalAssumption,
    SuppressedQuestion,
    assess_blocking_items,
    assess_readiness,
    compute_overall_grade,
    select_next_best_question,
)

# ---------------------------------------------------------------------------
# Input / Output models per C1 SPEC
# ---------------------------------------------------------------------------


class CompletenessInput(BaseModel):
    """Input to agora-strategy-completeness (C1 SPEC §Input).

    Maps to CompletenessInput TypeScript interface in SPEC:
      workshopId                   → workshop_id
      strategySpecRef              → strategy_spec_ref
      strategyVersionId            → strategy_version_id
      workshopEventRefs            → workshop_event_refs
      researchCapabilitySnapshotRef → research_capability_snapshot_ref
      questionScoringPolicyVersion  → question_scoring_policy_version
    """

    workshop_id: str = Field(min_length=1, description="Workshop session this assessment is produced in.")
    strategy_spec_ref: str = Field(min_length=1, description="StrategySpec ref being assessed.")
    strategy_version_id: str = Field(min_length=1, description="StrategySpec version being assessed.")
    workshop_event_refs: List[str] = Field(default_factory=list, description="Workshop event refs for context.")
    research_capability_snapshot_ref: str = Field(
        min_length=1,
        description="Research capability snapshot ref (for tool derivability checks).",
    )
    question_scoring_policy_version: Literal["QuestionScoringPolicy.v1"] = "QuestionScoringPolicy.v1"
    recent_questions: List[str] = Field(
        default_factory=list,
        description="Field names asked in recent turns (for non-duplicate gate, A1 §3).",
    )


class BlockingItemOut(BaseModel):
    field: str
    reason: str
    gate: str  # "research" | "validation" | "trading_room"


class NextBestQuestionOut(BaseModel):
    question_id: str
    text: str
    target_fields: List[str]
    score: float
    mandatory: bool
    why_now: str
    answer_mode: str = "free_text"
    options: List[str] = Field(default_factory=list)
    policy_version: str = QUESTION_SCORING_POLICY_VERSION


class SuppressedQuestionOut(BaseModel):
    question_id: str
    field: str
    reason: str


class ProvisionalAssumptionOut(BaseModel):
    field: str
    assumed_value: str
    why: str


class CompletenessOutput(BaseModel):
    """Output of agora-strategy-completeness (C1 SPEC §Output).

    Maps to CompletenessOutput TypeScript interface in SPEC:
      stateMap              → state_map
      blockingItems         → blocking_items
      provisionalAssumptions → provisional_assumptions
      researchReadiness     → research_readiness
      validationReadiness   → validation_readiness
      tradingRoomReadiness  → trading_room_readiness
      nextBestQuestion      → next_best_question
      suppressedQuestions   → suppressed_questions

    Additional common envelope fields (C1 §3):
      status, blocking_reasons, warnings, overall_grade
    """

    state_map: Dict[str, str] = Field(description="Field → FieldState mapping.")
    blocking_items: List[BlockingItemOut] = Field(default_factory=list)
    provisional_assumptions: List[ProvisionalAssumptionOut] = Field(default_factory=list)
    research_readiness: bool = False
    validation_readiness: bool = False
    trading_room_readiness: bool = False
    next_best_question: Optional[NextBestQuestionOut] = None
    suppressed_questions: List[SuppressedQuestionOut] = Field(default_factory=list)
    overall_grade: str = "incomplete"
    status: str = "completed"
    blocking_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core skill function
# ---------------------------------------------------------------------------


def run_strategy_completeness(
    input_data: CompletenessInput,
    *,
    state_map: Optional[Dict[str, str]] = None,
) -> CompletenessOutput:
    """Run the agora-strategy-completeness skill per C1 SPEC.

    In production, state_map is derived by loading the StrategySpec and
    workshop events via skill tools (strategy_spec.read, question_policy.read,
    etc.).  For testing and direct invocation, callers may supply state_map
    directly to bypass the loader layer.

    Failure modes (C1 §Failure behavior):
      - state_map is None and no loader provided → CAPABILITY_DENIED / blocked.
      - Policy version mismatch → REGISTRY_VERSION_MISMATCH.
      - No actionable open fields → next_best_question=None, status=completed,
        suggesting build_research_plan.

    Hard rules (C1 §Rules) enforced here:
      - Does not write RuntimeBinding, capital binding, or broker order.
      - Does not cross user scope (state_map is pre-scoped by caller).
      - All outputs are proposals, not executed governance decisions.
    """
    # Policy version check
    if input_data.question_scoring_policy_version != QUESTION_SCORING_POLICY_VERSION:
        return CompletenessOutput(
            state_map=state_map or {},
            status="blocked",
            blocking_reasons=["REGISTRY_VERSION_MISMATCH"],
            warnings=[
                f"Expected policy version {QUESTION_SCORING_POLICY_VERSION}, "
                f"got {input_data.question_scoring_policy_version}."
            ],
        )

    # State map is required; in production this comes from a spec loader
    if state_map is None:
        return CompletenessOutput(
            state_map={},
            status="blocked",
            blocking_reasons=["CAPABILITY_DENIED"],
            warnings=[
                "No state_map provided and no spec loader available. "
                "Cannot assess completeness without a resolved StrategySpec state."
            ],
        )

    # Compute blocking items and readiness gates
    blocking = assess_blocking_items(state_map)
    research_ready, validation_ready, trading_ready = assess_readiness(blocking)
    overall_grade = compute_overall_grade(state_map)

    # NBQ selection
    nbq, suppressed, provisional = select_next_best_question(
        state_map,
        recent_questions=input_data.recent_questions,
    )

    # Determine output status
    if nbq is not None:
        status = "needs_user"
    else:
        status = "completed"

    return CompletenessOutput(
        state_map=dict(state_map),
        blocking_items=[
            BlockingItemOut(field=b.field, reason=b.reason, gate=b.gate)
            for b in blocking
        ],
        provisional_assumptions=[
            ProvisionalAssumptionOut(
                field=p.field,
                assumed_value=p.assumed_value,
                why=p.why,
            )
            for p in provisional
        ],
        research_readiness=research_ready,
        validation_readiness=validation_ready,
        trading_room_readiness=trading_ready,
        next_best_question=(
            NextBestQuestionOut(
                question_id=nbq.question_id,
                text=nbq.text,
                target_fields=nbq.target_fields,
                score=nbq.score,
                mandatory=nbq.mandatory,
                why_now=nbq.why_now,
                answer_mode=nbq.answer_mode,
                options=nbq.options,
            )
            if nbq is not None
            else None
        ),
        suppressed_questions=[
            SuppressedQuestionOut(
                question_id=s.question_id,
                field=s.field,
                reason=s.reason,
            )
            for s in suppressed
        ],
        overall_grade=overall_grade,
        status=status,
        blocking_reasons=[],
        warnings=[],
    )

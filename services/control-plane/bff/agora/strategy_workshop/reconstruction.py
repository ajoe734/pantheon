"""Agent-owned semantic reconstruction; deterministic draft validation only.

No keyword reconstruction, invented StrategySpec, or execution approval lives
here. The agent's interpretation is a proposal, never financial authority.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from services.research.strategy_spec.models import (
    StrategySpec, validate_strategy_spec, validate_strategy_spec_payload,
)
from .readiness import (
    _spec_for_target,
    build_next_high_value_decision,
    build_twelve_strategy_blocks,
    detect_branch_strategy,
    extract_workshop_decisions,
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


class EnrichedNextBestQuestion(NextBestQuestion):
    """Next-Best-Question enriched with V10 §4.1 selectable options and servant default."""
    model_config = {"extra": "forbid"}
    decision_id: Optional[str] = None
    question: Optional[str] = None
    why_it_matters: Optional[str] = None
    target_blocks: List[str] = Field(default_factory=list)
    options: List[Dict[str, Any]] = Field(default_factory=list)
    answer_options: List[str] = Field(default_factory=list)
    default_option_id: Optional[str] = None
    servant_default: Optional[Dict[str, Any]] = None
    score_total: Optional[float] = None
    freeform_allowed: bool = True
    defer_allowed: bool = True


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
    next_best_question: Optional[EnrichedNextBestQuestion] = None
    blocks: List[Dict[str, Any]] = Field(default_factory=list)
    twelve_blocks: List[Dict[str, Any]] = Field(default_factory=list)
    next_decision: Optional[Dict[str, Any]] = None
    strategy_family: str = "generic"
    is_branch_strategy: bool = False
    draft_proposal: Optional[Dict[str, Any]] = None
    provider_lineage: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)


def validate_semantic_citations(draft: SemanticReconstructionDraft, message_count: int) -> None:
    for name in StrategyMap.model_fields:
        block = getattr(draft.strategy_map, name)
        if block.status == "confirmed":
            citations = block.details.get("message_numbers")
            if (not block.summary or not block.summary.strip() or not isinstance(citations, list)
                    or not citations or any(type(n) is not int or not 1 <= n <= message_count for n in citations)):
                raise ValueError("confirmed claim lacks conversation support")


def reconstruct_strategy_from_events(
    *, workshop_id: str, sequence_no: int, events: List[Dict[str, Any]],
    messages_content: List[str], provider_lineage: Optional[Dict[str, Any]] = None,
    strategy_spec: Optional[StrategySpec | Dict[str, Any]] = None,
    semantic_draft: Optional[SemanticReconstructionDraft] = None,
) -> StrategyReconstructionResult:
    """Validate a proposal, never interpret text with rules or manufacture fields.

    Without a semantic turn this produces an explicitly insufficient result.
    Even a complete typed agent draft is at most draftable, not trade-ready.
    Answered workshop decision events recorded via POST /decisions are folded
    into the reconstructed strategy map and advance the next high-value decision.
    """
    strategy_map = semantic_draft.strategy_map.model_copy(deep=True) if semantic_draft else StrategyMap()
    explicit_facts = list(semantic_draft.explicit_facts) if semantic_draft else []
    decisions = extract_workshop_decisions(events, messages_content)
    msg_cap = max(len(messages_content), 1)
    for decision in decisions:
        decision_summary = f"交易員裁示（{decision['option_id']}）：{decision['option_label']}"
        if decision_summary not in explicit_facts:
            explicit_facts.append(decision_summary)
        target_fields: List[str] = []
        for target in decision.get("resolves") or []:
            if target in StrategyMap.model_fields and target not in target_fields:
                target_fields.append(target)
            spec = _spec_for_target(target)
            if spec is not None and spec.strategy_map_key in StrategyMap.model_fields:
                if spec.strategy_map_key not in target_fields:
                    target_fields.append(spec.strategy_map_key)
        if not target_fields:
            spec = _spec_for_target(str(decision.get("decision_id") or ""))
            if spec is not None and spec.strategy_map_key in StrategyMap.model_fields:
                target_fields.append(spec.strategy_map_key)
        for field_name in target_fields:
            block = getattr(strategy_map, field_name)
            block.status = "confirmed"
            block.summary = (
                f"{block.summary}；{decision_summary}"
                if (block.summary and decision_summary not in block.summary)
                else decision_summary
            )
            msg_nums = [
                n for n in (block.details.get("message_numbers") or [])
                if isinstance(n, int) and 1 <= n <= msg_cap
            ]
            cite_no = min(max(int(decision.get("message_number") or msg_cap), 1), msg_cap)
            if cite_no not in msg_nums:
                msg_nums.append(cite_no)
            block.details = {
                **block.details,
                "message_numbers": msg_nums or [1],
                "decision_id": decision["decision_id"],
                "selected_option_id": decision["option_id"],
            }
            block.details.pop("missing_definitions", None)
            block.details.pop("unresolved", None)
            block.details.pop("open_question", None)

    confirmed = [name for name in StrategyMap.model_fields
                 if getattr(strategy_map, name).status == "confirmed"]
    unconfirmed = [name for name in StrategyMap.model_fields if name not in confirmed]
    blockers: List[str] = []
    if semantic_draft is None:
        blockers.append("Semantic reconstruction unavailable")
    else:
        try:
            validate_semantic_citations(semantic_draft, len(messages_content))
        except ValueError as exc:
            blockers.append(str(exc))
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
            if semantic_draft:
                universe = strategy_map.universe.details
                cadence = strategy_map.exit_rules.details.get("rebalance_cadence")
                if (universe.get("symbols") != document["market_scope"]["symbols"]
                        or universe.get("frequency") != document["market_scope"]["frequency"]
                        or cadence != document["execution_profile"].get("rebalance_cadence")):
                    raise ValueError("StrategySpec disagrees with the semantic map's universe or cadence")
        except (ValueError, TypeError, KeyError) as exc:
            document = None
            blockers.append(f"StrategySpec validation failed: {exc}")
    if document is None:
        blockers.append("No valid executable StrategySpec provided or reconstructed")
    ready_to_draft = document is not None and not blockers

    base_nbq_dict = (
        semantic_draft.next_best_question.model_dump(mode="json")
        if semantic_draft is not None
        else {
            "question_id": f"nbq-{sequence_no}",
            "text": "What strategy would you like to explore?",
            "resolves": ["hypothesis.summary"],
            "why_now": "No semantic reconstruction is available.",
        }
    )
    pseudo_recon = {
        "strategy_map": strategy_map.model_dump(mode="json"),
        "explicit_facts": explicit_facts,
        "inferences": semantic_draft.inferences if semantic_draft else [],
        "assumptions": semantic_draft.assumptions if semantic_draft else [],
        "contradictions": semantic_draft.contradictions if semantic_draft else [],
        "next_best_question": base_nbq_dict,
    }
    pseudo_events = [
        *(events or []),
        *[
            {"event_type": "message", "redacted_summary": msg}
            for msg in (messages_content or [])
        ],
    ]
    is_branch = detect_branch_strategy(
        snapshot=None,
        events=pseudo_events,
        reconstruction=pseudo_recon,
    )
    twelve_blocks = build_twelve_strategy_blocks(
        snapshot=None,
        events=events,
        reconstruction=pseudo_recon,
        is_branch_strategy=is_branch,
    )
    next_decision = build_next_high_value_decision(
        snapshot=None,
        blocks=twelve_blocks,
        events=events,
        reconstruction=pseudo_recon,
        is_branch_strategy=is_branch,
    )
    if semantic_draft is None and not decisions:
        resolves_list = list(next_decision.get("resolves") or [])
        if "hypothesis.summary" not in resolves_list:
            resolves_list.insert(0, "hypothesis.summary")
        nbq = EnrichedNextBestQuestion(
            question_id=f"nbq-{sequence_no}",
            decision_id=f"nbq-{sequence_no}",
            text="What strategy would you like to explore?",
            question=next_decision["question"],
            resolves=resolves_list,
            target_blocks=list(next_decision.get("target_blocks") or []),
            why_now="No semantic reconstruction is available.",
            why_it_matters=next_decision["why_it_matters"],
            options=list(next_decision.get("options") or []),
            answer_options=list(next_decision.get("answer_options") or []),
            default_option_id=next_decision.get("default_option_id"),
            servant_default=next_decision.get("servant_default"),
            score_total=next_decision.get("score_total"),
            freeform_allowed=bool(next_decision.get("freeform_allowed", True)),
            defer_allowed=bool(next_decision.get("defer_allowed", True)),
        )
    else:
        nbq = EnrichedNextBestQuestion(
            question_id=str(next_decision["question_id"]),
            decision_id=str(next_decision["decision_id"]),
            text=str(next_decision["text"]),
            question=str(next_decision["question"]),
            resolves=list(next_decision.get("resolves") or []),
            target_blocks=list(next_decision.get("target_blocks") or []),
            why_now=str(next_decision["why_now"]),
            why_it_matters=str(next_decision["why_it_matters"]),
            options=list(next_decision.get("options") or []),
            answer_options=list(next_decision.get("answer_options") or []),
            default_option_id=next_decision.get("default_option_id"),
            servant_default=next_decision.get("servant_default"),
            score_total=next_decision.get("score_total"),
            freeform_allowed=bool(next_decision.get("freeform_allowed", True)),
            defer_allowed=bool(next_decision.get("defer_allowed", True)),
        )

    identity_payload: Dict[str, Any] = {
        "draft": semantic_draft.model_dump(mode="json") if semantic_draft else None,
        "strategy_spec": document,
    }
    if decisions:
        identity_payload["decisions"] = [
            {
                "decision_id": d["decision_id"],
                "option_id": d["option_id"],
                "sequence_no": d["sequence_no"],
            }
            for d in decisions
        ]
    proposal_identity = json.dumps(
        identity_payload, sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(f"semantic-v2:{workshop_id}:{sequence_no}:{proposal_identity}".encode()).hexdigest()[:20]
    evidence_refs = [
        {
            "ref_type": "conversation_sequence",
            "ref_id": f"seq-{sequence_no}",
            "summary": f"Messages up to sequence {sequence_no}",
        },
        *[
            {
                "ref_type": "workshop_decision",
                "ref_id": str(d["event_id"]),
                "summary": f"Decision {d['decision_id']} -> {d['option_id']}",
            }
            for d in decisions
        ],
    ]
    return StrategyReconstructionResult(
        reconstruction_id=f"recon-{digest}",
        workshop_id=workshop_id,
        based_on_sequence_no=sequence_no,
        strategy_map=strategy_map,
        explicit_facts=explicit_facts,
        inferences=semantic_draft.inferences if semantic_draft else [],
        assumptions=semantic_draft.assumptions if semantic_draft else [],
        contradictions=semantic_draft.contradictions if semantic_draft else [],
        next_best_question=nbq,
        blocks=twelve_blocks,
        twelve_blocks=twelve_blocks,
        next_decision=next_decision,
        strategy_family="winner_branch" if is_branch else "generic",
        is_branch_strategy=is_branch,
        completeness=CompletenessAssessment(
            grade="draftable" if ready_to_draft else "insufficient",
            blockers=blockers,
            confirmed_fields=confirmed,
            unconfirmed_fields=unconfirmed,
        ),
        draft_proposal={"strategy_spec": document} if ready_to_draft else None,
        provider_lineage=provider_lineage or {"engine": "typed_validation_only", "provider": None},
        evidence_refs=evidence_refs,
    )

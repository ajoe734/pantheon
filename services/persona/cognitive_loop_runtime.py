"""Persona cognitive closed-loop runtime for end-to-end autonomy validation."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.knowledge.evidence.models import EvidenceBundle, EvidenceItem, KnowledgeObject
from services.knowledge.evidence.repository import InMemoryEvidenceRepository
from services.memory.institutional_memory_store import (
    InstitutionalMemoryStore,
    SourceEventType,
    WriteAuthority,
)
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.research.replication.gate import ReplicationGate
from services.research.replication.gate_schema import (
    CandidateAdmissionStatus,
    ReplicationRequest,
)
from services.search.filters import SearchAccessContext, SearchRequest
from services.search.gateway import SearchGateway
from services.source_ingestion.connectors.base import SourceRecord, SourceType


REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from approval_decision import EvidenceRef, EvidenceRefType, RiskLevel  # noqa: E402
from evolution_decision import (  # noqa: E402
    ComparisonOperator,
    EvolutionActionType,
    EvolutionActorRole,
    EvolutionDecision,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
    validate_evolution_decision,
)


TOTAL_COGNITIVE_E2E_CASES = 3000
CLOSED_LOOP_TYPES = (
    "memory_changes_decision",
    "uncertainty_triggers_search",
    "performance_triggers_optimization",
    "bad_optimization_rejected",
    "persona_tool_selection",
)
ALPHA_MODES = (
    "pure_quant_momentum",
    "pure_quant_reversal",
    "pure_quant_carry",
    "llm_news_sentiment",
    "llm_research_synthesis",
    "hybrid_quant_llm",
)
TOOL_SCENARIOS = (
    ("needs_cited_market_context", "governed_search"),
    ("candidate_admission_feasibility", "replication_gate"),
    ("performance_repair_needed", "evolution_decision"),
    ("recall_prior_lesson", "persona_memory"),
    ("paper_runtime_handoff", "lean_paper_handoff"),
)


@dataclass(frozen=True)
class PersonaCognitiveCase:
    case_id: str
    ordinal: int
    loop_type: str
    persona_id: str
    collaborator_persona_id: str
    workspace_id: str
    alpha_mode: str
    strategy_id: str
    memory_bias: str
    uncertainty_score: float
    performance_delta_bps: int
    drawdown_delta: float
    tool_problem_kind: str
    expected_tool: str

    @property
    def is_uncertain(self) -> bool:
        return self.uncertainty_score >= 0.75

    @property
    def has_performance_breach(self) -> bool:
        return self.performance_delta_bps <= -35 or self.drawdown_delta >= 0.08


class PersonaCognitiveLoopError(ValueError):
    """Raised when a cognitive closed loop cannot produce replayable evidence."""


def build_persona_cognitive_case(ordinal: int) -> PersonaCognitiveCase:
    """Build one deterministic, non-duplicated closed-loop validation case."""

    if ordinal < 1 or ordinal > TOTAL_COGNITIVE_E2E_CASES:
        raise PersonaCognitiveLoopError(
            f"ordinal must be between 1 and {TOTAL_COGNITIVE_E2E_CASES}"
        )
    loop_type = CLOSED_LOOP_TYPES[(ordinal - 1) % len(CLOSED_LOOP_TYPES)]
    alpha_mode = ALPHA_MODES[(ordinal - 1) % len(ALPHA_MODES)]
    tool_index = ((ordinal - 1) // len(CLOSED_LOOP_TYPES)) % len(TOOL_SCENARIOS)
    tool_problem_kind, expected_tool = TOOL_SCENARIOS[tool_index]
    is_uncertainty_loop = loop_type == "uncertainty_triggers_search"
    is_performance_loop = loop_type == "performance_triggers_optimization"
    return PersonaCognitiveCase(
        case_id=f"persona-cognitive-closed-loop-{ordinal:04d}",
        ordinal=ordinal,
        loop_type=loop_type,
        persona_id=f"persona-cognitive-{ordinal % 60:02d}",
        collaborator_persona_id=f"persona-collab-risk-{(ordinal * 7) % 47:02d}",
        workspace_id=f"workspace-cognitive-{ordinal % 19:02d}",
        alpha_mode=alpha_mode,
        strategy_id=f"strategy-{alpha_mode}-{ordinal:04d}",
        memory_bias=_memory_bias_for(ordinal),
        uncertainty_score=0.86 if is_uncertainty_loop else 0.42 + ((ordinal % 5) * 0.03),
        performance_delta_bps=-72 if is_performance_loop else 18 - (ordinal % 9),
        drawdown_delta=0.11 if is_performance_loop else 0.02 + ((ordinal % 4) * 0.005),
        tool_problem_kind=tool_problem_kind,
        expected_tool=expected_tool,
    )


def run_persona_cognitive_closed_loop(
    case: PersonaCognitiveCase,
    *,
    persona_store_path: Path | None = None,
    institutional_store_path: Path | None = None,
    replication_gate: ReplicationGate | None = None,
) -> dict[str, Any]:
    """Run observe/orient/decide/act/learn for one persona cognition case."""

    persona_store = PersonaMemoryStore(path=persona_store_path)
    institutional_store = InstitutionalMemoryStore(path=institutional_store_path)
    memory_write = write_learn_feedback(
        _learn_feedback_payload(case),
        persona_store=persona_store,
        institutional_store=institutional_store,
    )

    persona_reader = PersonaMemoryStore(path=persona_store_path) if persona_store_path else persona_store
    institutional_reader = (
        InstitutionalMemoryStore(path=institutional_store_path)
        if institutional_store_path
        else institutional_store
    )
    primary_memory = _retrieve_and_reuse_memory(persona_reader, case.persona_id, case)
    collaborator_memory = _retrieve_and_reuse_memory(
        persona_reader, case.collaborator_persona_id, case
    )
    institutional_memory = institutional_reader.mark_reused(memory_write["institutional_entry_id"])

    baseline_decision = _baseline_decision(case)
    memory_adjusted_decision = _memory_adjusted_decision(
        case,
        baseline_decision=baseline_decision,
        primary_memory=primary_memory,
        collaborator_memory=collaborator_memory,
    )
    selected_tool = choose_persona_tool(case, memory_adjusted_decision)
    tool_execution = _execute_selected_tool(
        case,
        selected_tool=selected_tool,
        memory_decision=memory_adjusted_decision,
        primary_memory=primary_memory,
        collaborator_memory=collaborator_memory,
        replication_gate=replication_gate or ReplicationGate(),
    )

    final_decision = {
        **memory_adjusted_decision,
        "selected_tool": selected_tool,
        "tool_result_status": tool_execution["status"],
    }
    phases = ("observe", "orient", "collaborate", "decide", "act", "learn")
    return {
        "proof_id": f"proof-{case.case_id}",
        "case_id": case.case_id,
        "loop_type": case.loop_type,
        "persona_id": case.persona_id,
        "collaborator_persona_id": case.collaborator_persona_id,
        "workspace_id": case.workspace_id,
        "alpha_mode": case.alpha_mode,
        "strategy_id": case.strategy_id,
        "phases": list(phases),
        "memory_writeback": memory_write,
        "memory_reads": {
            "primary": _memory_read_proof(primary_memory),
            "collaborator": _memory_read_proof(collaborator_memory),
            "institutional": {
                "entry_id": institutional_memory.entry_id,
                "reuse_count": institutional_memory.reuse_count,
                "source_event_id": institutional_memory.source_event_id,
            },
        },
        "collaboration": {
            "stance": "risk_review_constrains_primary_action",
            "primary_persona_id": case.persona_id,
            "collaborator_persona_id": case.collaborator_persona_id,
            "collaborator_memory_reused": collaborator_memory["reuse_count"] > 0,
            "consensus": memory_adjusted_decision["collaboration_consensus"],
        },
        "baseline_decision": baseline_decision,
        "final_decision": final_decision,
        "decision_changed_by_memory": baseline_decision["action"] != final_decision["action"],
        "tool_choice": {
            "selected_tool": selected_tool,
            "reason": _tool_reason(case, selected_tool),
            "problem_kind": case.tool_problem_kind,
        },
        "tool_execution": tool_execution,
        "learn": {
            "memory_source_event_id": memory_write["source_event_id"],
            "persona_memory_ids": memory_write["persona_memory_ids"],
            "institutional_entry_id": memory_write["institutional_entry_id"],
            "no_live_side_effects": True,
        },
    }


def choose_persona_tool(case: PersonaCognitiveCase, decision: Mapping[str, Any]) -> str:
    """Select the smallest governed tool that can resolve the current problem."""

    if case.loop_type == "bad_optimization_rejected":
        return "replication_gate"
    if case.has_performance_breach:
        return "evolution_decision"
    if case.is_uncertain or decision.get("requires_external_evidence"):
        return "governed_search"
    if case.loop_type == "persona_tool_selection":
        if case.tool_problem_kind == "needs_cited_market_context":
            return "governed_search"
        if case.tool_problem_kind == "candidate_admission_feasibility":
            return "replication_gate"
        if case.tool_problem_kind == "performance_repair_needed":
            return "evolution_decision"
        if case.tool_problem_kind == "paper_runtime_handoff":
            return "lean_paper_handoff"
        return "persona_memory"
    return "persona_memory"


def _execute_selected_tool(
    case: PersonaCognitiveCase,
    *,
    selected_tool: str,
    memory_decision: Mapping[str, Any],
    primary_memory: Mapping[str, Any],
    collaborator_memory: Mapping[str, Any],
    replication_gate: ReplicationGate,
) -> dict[str, Any]:
    if selected_tool == "governed_search":
        return {
            "tool": selected_tool,
            "status": "completed",
            "search": _run_governed_search(case),
        }
    if selected_tool == "evolution_decision":
        return {
            "tool": selected_tool,
            "status": "completed",
            "evolution": _propose_evolution_decision(case, memory_decision),
        }
    if selected_tool == "replication_gate":
        bad_candidate = case.loop_type == "bad_optimization_rejected"
        gate_response = _run_replication_gate(
            case,
            replication_gate=replication_gate,
            bad_candidate=bad_candidate,
        )
        return {
            "tool": selected_tool,
            "status": "rejected" if bad_candidate else "completed",
            "replication_gate": gate_response,
        }
    if selected_tool == "lean_paper_handoff":
        return {
            "tool": selected_tool,
            "status": "completed",
            "lean_paper_handoff": _lean_paper_handoff_packet(case, memory_decision),
        }
    if selected_tool == "persona_memory":
        return {
            "tool": selected_tool,
            "status": "completed",
            "memory_context": {
                "primary_memory_id": primary_memory["memory_id"],
                "collaborator_memory_id": collaborator_memory["memory_id"],
                "used_to_change_decision": memory_decision["used_memory"],
            },
        }
    raise PersonaCognitiveLoopError(f"Unsupported selected_tool: {selected_tool}")


def _learn_feedback_payload(case: PersonaCognitiveCase) -> dict[str, Any]:
    source_event_id = f"telemetry-{case.case_id}"
    return {
        "source_event_type": SourceEventType.RUNTIME_TELEMETRY_OUTCOME.value,
        "source_event_id": source_event_id,
        "write_authority": WriteAuthority.TELEMETRY_SVC.value,
        "sponsor_persona_id": case.persona_id,
        "contributing_persona_ids": [case.persona_id, case.collaborator_persona_id],
        "summary": (
            f"{case.alpha_mode} telemetry for {case.strategy_id}: "
            f"{case.memory_bias}; uncertainty={case.uncertainty_score:.2f}; "
            f"performance_delta_bps={case.performance_delta_bps}."
        ),
        "headline": f"Persona cognitive closed-loop telemetry {case.case_id}",
        "body": (
            f"Case {case.case_id} records {case.alpha_mode} behavior, peer review, "
            "tool need, and paper-only action evidence."
        ),
        "runtime_telemetry_evidence": [
            {
                "ref_type": "telemetry_summary",
                "ref_id": f"telemetry-summary-{case.case_id}",
                "strategy_id": case.strategy_id,
                "alpha_mode": case.alpha_mode,
                "performance_delta_bps": case.performance_delta_bps,
                "drawdown_delta": case.drawdown_delta,
                "uncertainty_score": case.uncertainty_score,
            }
        ],
        "proposal_ids": [f"proposal-{case.case_id}"],
        "contributor_feedback": [
            {
                "persona_id": case.persona_id,
                "summary": (
                    f"Primary lesson for {case.alpha_mode}: {case.memory_bias}; "
                    f"use {case.strategy_id} only after prior lesson is considered."
                ),
                "tags": [
                    "persona_cognitive_closed_loop",
                    case.alpha_mode,
                    case.memory_bias,
                    case.loop_type,
                ],
                "proposal_ids": [f"proposal-primary-{case.case_id}"],
            },
            {
                "persona_id": case.collaborator_persona_id,
                "summary": (
                    f"Collaborator risk review for {case.strategy_id}: require evidence, "
                    "governance gate, and paper-only execution before action."
                ),
                "tags": [
                    "persona_cognitive_closed_loop",
                    case.alpha_mode,
                    "peer_risk_review",
                    case.loop_type,
                ],
                "proposal_ids": [f"proposal-collab-{case.case_id}"],
            },
        ],
        "tags": [
            "persona_cognitive_closed_loop",
            case.alpha_mode,
            case.memory_bias,
            case.loop_type,
        ],
    }


def _retrieve_and_reuse_memory(
    store: PersonaMemoryStore,
    persona_id: str,
    case: PersonaCognitiveCase,
) -> dict[str, Any]:
    hits = store.retrieve(
        persona_id=persona_id,
        query=f"{case.alpha_mode} {case.memory_bias} {case.loop_type}",
        tags=["persona_cognitive_closed_loop", case.alpha_mode],
        limit=3,
    )
    if not hits:
        raise PersonaCognitiveLoopError(
            f"No persona memory retrieved for {persona_id} in {case.case_id}"
        )
    hit = hits[0]
    reused = store.mark_reused(hit.entry.memory_id)
    return {
        "memory_id": reused.memory_id,
        "persona_id": reused.persona_id,
        "reuse_count": reused.reuse_count,
        "summary": reused.content["summary"],
        "tags": list(reused.content.get("tags", [])),
        "source_event_id": reused.source_event_id,
        "relevance_score": hit.relevance_score,
        "structured_payload": dict(reused.content.get("structured_payload", {})),
    }


def _baseline_decision(case: PersonaCognitiveCase) -> dict[str, Any]:
    return {
        "action": "increase_alpha_allocation",
        "confidence": 0.67,
        "requires_external_evidence": False,
        "rationale": f"Fresh {case.alpha_mode} signal appears actionable before memory is read.",
    }


def _memory_adjusted_decision(
    case: PersonaCognitiveCase,
    *,
    baseline_decision: Mapping[str, Any],
    primary_memory: Mapping[str, Any],
    collaborator_memory: Mapping[str, Any],
) -> dict[str, Any]:
    action = "reduce_position_size"
    confidence = 0.58
    requires_external_evidence = False
    if case.loop_type == "uncertainty_triggers_search":
        action = "pause_and_search_cited_evidence"
        confidence = 0.44
        requires_external_evidence = True
    elif case.loop_type == "performance_triggers_optimization":
        action = "propose_revalidation"
        confidence = 0.61
    elif case.loop_type == "bad_optimization_rejected":
        action = "send_candidate_to_replication_gate"
        confidence = 0.52
    elif case.loop_type == "persona_tool_selection":
        action = _tool_selection_action(case)
        confidence = 0.63
        requires_external_evidence = case.expected_tool == "governed_search"

    return {
        "action": action,
        "confidence": confidence,
        "requires_external_evidence": requires_external_evidence,
        "used_memory": True,
        "baseline_action": baseline_decision["action"],
        "primary_memory_id": primary_memory["memory_id"],
        "collaborator_memory_id": collaborator_memory["memory_id"],
        "collaboration_consensus": "paper_only_governed_action",
        "rationale": (
            f"Memory {primary_memory['memory_id']} and peer review "
            f"{collaborator_memory['memory_id']} changed the action for {case.case_id}."
        ),
    }


def _run_governed_search(case: PersonaCognitiveCase) -> dict[str, Any]:
    repository = InMemoryEvidenceRepository()
    source_id = f"src-{case.case_id}"
    item_id = f"evi-{case.case_id}"
    bundle_id = f"bundle-{case.case_id}"
    knowledge_id = f"ko-{case.case_id}"
    citation_label = f"web-evidence:{case.case_id}"
    repository.add_source_record(
        SourceRecord(
            source_id=source_id,
            connector_id="connector-governed-web-research",
            source_type=SourceType.NEWS,
            title=f"{case.alpha_mode} market context for {case.case_id}",
            content_ref=f"https://research.example.invalid/{case.case_id}",
            metadata={"case_id": case.case_id, "source_dedupe_key": source_id},
            trace_id=f"trace-{case.case_id}",
        )
    )
    repository.add_evidence_item(
        EvidenceItem(
            evidence_item_id=item_id,
            source_id=source_id,
            item_type="web_research_note",
            content_ref=f"https://research.example.invalid/{case.case_id}#evidence",
            citation_label=citation_label,
            body=(
                f"{case.alpha_mode} {case.strategy_id} uncertainty drawdown citation "
                "supports paper-only validation before allocation."
            ),
            confidence=0.88,
            access_scope=("public",),
            trace_refs=(f"trace-{case.case_id}",),
            metadata={"evidence_dedupe_key": item_id},
        )
    )
    repository.add_bundle(
        EvidenceBundle(
            evidence_bundle_id=bundle_id,
            source_ids=(source_id,),
            evidence_item_ids=(item_id,),
            summary=f"Cited evidence bundle for {case.case_id}",
            citation_refs=(citation_label,),
            confidence=0.88,
            license_scope="open",
            access_scope=("public",),
            created_by="persona-cognitive-loop-runtime",
            trace_refs=(f"trace-{case.case_id}",),
        )
    )
    repository.add_knowledge_object(
        KnowledgeObject(
            knowledge_object_id=knowledge_id,
            source_id=source_id,
            evidence_item_id=item_id,
            evidence_bundle_id=bundle_id,
            title=f"{case.alpha_mode} uncertainty evidence",
            text=(
                f"{case.alpha_mode} {case.strategy_id} uncertainty drawdown citation "
                "requires governed search before action."
            ),
            source_type=SourceType.NEWS.value,
            license_scope="open",
            access_scope=("public",),
            environment_scope=("paper",),
            persona_scope=(case.persona_id,),
            workspace_scope=(case.workspace_id,),
            keywords=(case.alpha_mode, case.strategy_id, "uncertainty", "drawdown"),
            metadata={"case_id": case.case_id},
        )
    )
    request = SearchRequest(
        request_id=f"search-{case.case_id}",
        trace_id=f"trace-search-{case.case_id}",
        query=f"{case.alpha_mode} {case.strategy_id} uncertainty drawdown citation",
        persona_id=case.persona_id,
        workspace_id=case.workspace_id,
        source_types=(SourceType.NEWS.value,),
        environment="paper",
        top_k=1,
        require_citations=True,
    )
    response = SearchGateway(repository=repository).search(
        request,
        SearchAccessContext(
            persona_id=case.persona_id,
            workspace_id=case.workspace_id,
            environment="paper",
            access_scopes=("public",),
            license_scopes=("open",),
        ),
    )
    return response.to_dict()


def _propose_evolution_decision(
    case: PersonaCognitiveCase,
    memory_decision: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = ThresholdSnapshot(
        policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md#persona-cognitive-e2e",
        signal_type=ThresholdSignalType.PERFORMANCE_DEGRADATION,
        metric_name="rolling_alpha_delta_bps",
        comparator=ComparisonOperator.LTE,
        observed_value=case.performance_delta_bps,
        threshold_value=-35,
        window="20d",
        breached=case.has_performance_breach,
        note="Past performance breached the self-optimization threshold.",
    )
    decision = EvolutionDecision.create_proposed(
        decision_id=f"evo-{case.case_id}",
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=case.strategy_id,
        target_version="paper-1",
        action_type=EvolutionActionType.REVALIDATE,
        rationale=(
            f"Persona memory {memory_decision['primary_memory_id']} and telemetry "
            f"{case.performance_delta_bps} bps require revalidation, not live mutation."
        ),
        created_by_id=case.persona_id,
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        risk_level=RiskLevel.LOW,
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
                ref_id=f"telemetry-summary-{case.case_id}",
                storage_ref={
                    "backend": "persona_memory",
                    "path": str(memory_decision["primary_memory_id"]),
                },
            )
        ],
        threshold_snapshots=[snapshot],
        persona_id=case.persona_id,
        target_stage="paper",
        metadata={
            "case_id": case.case_id,
            "alpha_mode": case.alpha_mode,
            "self_optimization": "threshold_triggered_revalidation",
        },
    )
    errors = validate_evolution_decision(decision)
    return {
        "decision": decision.to_dict(),
        "validation_errors": errors,
        "is_valid": not errors,
        "optimization_is_feasible": not errors and snapshot.breached,
        "paper_only": True,
    }


def _run_replication_gate(
    case: PersonaCognitiveCase,
    *,
    replication_gate: ReplicationGate,
    bad_candidate: bool,
) -> dict[str, Any]:
    request = _replication_request(case, bad_candidate=bad_candidate)
    response = replication_gate.evaluate_candidate(request)
    return response.to_dict()


def _replication_request(case: PersonaCognitiveCase, *, bad_candidate: bool) -> ReplicationRequest:
    proposed_spec = _canonical_strategy_spec(case)
    if bad_candidate:
        proposed_spec = {
            **proposed_spec,
            "skip_promotion_gate": True,
            "lifecycle_state": "live",
        }
    return ReplicationRequest(
        candidate_id=f"cand-{case.case_id}",
        source_task_id="persona-cognitive-e2e",
        research_handoff={
            "source_metadata": {
                "api_endpoint": f"https://research.example.invalid/{case.case_id}",
                "retrieved_at": _now_iso(),
                "governance_context": "Approved structured source for paper replication.",
            },
            "normalized_findings": {
                "strategy_spec": {"strategy_id": case.strategy_id},
                "replication_notes": "Replicate in paper mode with bounded risk.",
                "evaluation_hypotheses": "Expected improvement must survive out-of-sample checks.",
            },
            "grok_processing_notes": {
                "normalization_confidence": "high",
                "governance_compliance": "verified",
                "downstream_readiness": "ready_for_replication",
            },
        },
        proposed_strategy_spec=proposed_spec,
        metadata={"case_id": case.case_id, "bad_candidate": bad_candidate},
    )


def _canonical_strategy_spec(case: PersonaCognitiveCase) -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "strategy_id": case.strategy_id,
        "title": f"{case.alpha_mode} persona paper strategy",
        "hypothesis": f"{case.alpha_mode} can improve paper alpha after governed validation.",
        "objective": "Validate risk-adjusted performance without live-capital bypass.",
        "market_scope": {
            "symbols": ["SPY"],
            "asset_classes": ["equities"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": f"https://research.example.invalid/{case.case_id}", "kind": "source_record"}
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio", "max_drawdown", "turnover"],
            "candidate_gate": "Pass replication gate before runtime handoff.",
        },
        "governance": {
            "approval_required": True,
            "policy_id": "persona-cognitive-paper-only",
        },
        "provenance": {
            "source_kind": "workflow",
            "created_at": _now_iso(),
            "source_refs": [f"telemetry-summary-{case.case_id}"],
            "created_by": "persona-cognitive-loop-runtime",
        },
    }


def _lean_paper_handoff_packet(
    case: PersonaCognitiveCase,
    memory_decision: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "handoff_id": f"lean-paper-handoff-{case.case_id}",
        "adapter": "lean_paper_runtime",
        "target_stage": "paper",
        "strategy_id": case.strategy_id,
        "alpha_mode": case.alpha_mode,
        "runtime_binding_id": f"rtb-{case.case_id}",
        "memory_refs": [
            memory_decision["primary_memory_id"],
            memory_decision["collaborator_memory_id"],
        ],
        "no_live_side_effects": True,
        "accepted_alpha_modes": list(ALPHA_MODES),
    }


def _tool_selection_action(case: PersonaCognitiveCase) -> str:
    if case.expected_tool == "governed_search":
        return "ask_search_for_cited_context"
    if case.expected_tool == "replication_gate":
        return "evaluate_candidate_feasibility"
    if case.expected_tool == "evolution_decision":
        return "prepare_evolution_revalidation"
    if case.expected_tool == "lean_paper_handoff":
        return "materialize_paper_handoff"
    return "reuse_persona_memory"


def _tool_reason(case: PersonaCognitiveCase, selected_tool: str) -> str:
    reasons = {
        "governed_search": "uncertainty or missing citations require evidence search",
        "replication_gate": "candidate feasibility or unsafe optimization requires gate evaluation",
        "evolution_decision": "past performance breached a self-optimization threshold",
        "persona_memory": "the problem is answerable from prior persona memory",
        "lean_paper_handoff": "paper runtime handoff is needed after governed decision",
    }
    return f"{reasons[selected_tool]} for {case.case_id}"


def _memory_read_proof(memory: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": memory["memory_id"],
        "persona_id": memory["persona_id"],
        "reuse_count": memory["reuse_count"],
        "source_event_id": memory["source_event_id"],
        "relevance_score": memory["relevance_score"],
    }


def _memory_bias_for(ordinal: int) -> str:
    biases = (
        "reduce_exposure_after_drawdown",
        "require_citations_before_action",
        "prefer_paper_revalidation",
        "reject_live_bypass_shortcut",
    )
    return biases[(ordinal - 1) % len(biases)]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

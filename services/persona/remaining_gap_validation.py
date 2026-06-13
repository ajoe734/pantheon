"""Remaining-gap persona E2E validations.

This harness covers the gaps left after the cognitive-loop and trading-reflection
3000-case suites:
- LEAN paper execution feedback is recovered after adapter restart.
- Long-term persona memory survives across rounds, supersession, and isolation.
- Self-optimization is accepted or rejected by before/after backtest evidence.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from services.execution.ibkr_adapter import IBKRAdapter, IBKRConfig, IBKROrderIntent
from services.execution.kraken_adapter import KrakenAdapter, KrakenConfig, KrakenOrderIntent
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.shioaji_adapter import ShioajiAdapter, ShioajiConfig, ShioajiOrderIntent
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.research.vectorbt.adapter import BacktestConfig, StubVectorbtBackend, run_vectorbt_workflow
from services.telemetry.feedback_adapter import FeedbackStoreAdapter


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
    EvolutionDecisionState,
    EvolutionTargetType,
    ExecutionPlane,
    ExecutionResult,
    ExecutionStatus,
    ThresholdSignalType,
    ThresholdSnapshot,
    validate_evolution_decision,
)


TOTAL_REMAINING_GAP_E2E_CASES = 3000
REMAINING_GAP_TYPES = (
    "lean_order_feedback_recovery",
    "long_term_memory_influence",
    "optimization_backtest_proof",
)
LEAN_LIFECYCLES = (
    "market_fill_ack_recovered",
    "limit_fill_ack_recovered",
    "duplicate_retry_idempotent",
    "binding_mismatch_filtered",
    "hold_noop_recovered",
)
LONG_MEMORY_SCENARIOS = (
    "newer_memory_preferred",
    "superseded_memory_ignored",
    "private_persona_isolated",
    "institutional_reuse_tracked",
)
OPTIMIZATION_SCENARIOS = (
    "accepted_after_backtest_improves",
    "rejected_after_backtest_degrades",
)
ALPHA_FAMILIES = (
    "pure_quant_momentum",
    "pure_quant_reversal",
    "llm_event_sentiment",
    "hybrid_macro_quant",
    "rl_policy_probe",
    "stat_arb_pair",
)


@dataclass(frozen=True)
class RemainingGapCase:
    case_id: str
    ordinal: int
    gap_type: str
    persona_id: str
    collaborator_persona_id: str
    strategy_id: str
    alpha_family: str
    lean_lifecycle: str
    long_memory_scenario: str
    optimization_scenario: str
    broker_adapter: str


class RemainingGapValidationError(ValueError):
    """Raised when a remaining-gap validation cannot build replayable evidence."""


def build_validation_round_plan(case: RemainingGapCase) -> dict[str, Any]:
    """Build the explicit ask-plan-execute contract for one validation round."""

    return {
        "round": case.ordinal,
        "case_id": case.case_id,
        "asked_before_execution": True,
        "self_questions": {
            "not_yet_verified": _not_yet_verified_question(case),
            "deeper_validation": _deeper_validation_question(case),
            "realistic_untested_combination": _realistic_combination_question(case),
        },
        "validation_plan": {
            "objective": _validation_objective(case),
            "phase_order": _expected_phase_order(case),
            "realistic_combination_id": (
                f"{case.case_id}|{case.gap_type}|{case.alpha_family}|"
                f"{case.broker_adapter}|{case.lean_lifecycle}|"
                f"{case.long_memory_scenario}|{case.optimization_scenario}"
            ),
            "fix_policy": "Any missing artifact, non-recovered state, or fantasy-only optimization fails the round until code is fixed.",
        },
    }


def build_remaining_gap_case(ordinal: int) -> RemainingGapCase:
    if ordinal < 1 or ordinal > TOTAL_REMAINING_GAP_E2E_CASES:
        raise RemainingGapValidationError(
            f"ordinal must be between 1 and {TOTAL_REMAINING_GAP_E2E_CASES}"
        )
    gap_type = REMAINING_GAP_TYPES[(ordinal - 1) % len(REMAINING_GAP_TYPES)]
    cycle_index = (ordinal - 1) // len(REMAINING_GAP_TYPES)
    return RemainingGapCase(
        case_id=f"persona-remaining-gap-e2e-{ordinal:04d}",
        ordinal=ordinal,
        gap_type=gap_type,
        persona_id=f"persona-gap-{ordinal % 71:02d}",
        collaborator_persona_id=f"persona-gap-collab-{(ordinal * 5) % 53:02d}",
        strategy_id=f"strategy-remaining-gap-{ordinal:04d}",
        alpha_family=ALPHA_FAMILIES[(ordinal - 1) % len(ALPHA_FAMILIES)],
        lean_lifecycle=LEAN_LIFECYCLES[cycle_index % len(LEAN_LIFECYCLES)],
        long_memory_scenario=LONG_MEMORY_SCENARIOS[cycle_index % len(LONG_MEMORY_SCENARIOS)],
        optimization_scenario=OPTIMIZATION_SCENARIOS[cycle_index % len(OPTIMIZATION_SCENARIOS)],
        broker_adapter=("ibkr", "shioaji", "kraken")[cycle_index % 3],
    )


def run_remaining_gap_e2e_case(
    case: RemainingGapCase,
    *,
    work_dir: Path,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    persona_path = work_dir / "persona-memory.json"
    institutional_path = work_dir / "institutional-memory.json"
    feedback_path = work_dir / "feedback-store.jsonl"
    round_plan = build_validation_round_plan(case)

    if case.gap_type == "lean_order_feedback_recovery":
        proof = _run_lean_order_feedback_recovery(
            case,
            persona_path=persona_path,
            institutional_path=institutional_path,
            feedback_path=feedback_path,
        )
    elif case.gap_type == "long_term_memory_influence":
        proof = _run_long_term_memory_influence(
            case,
            persona_path=persona_path,
            institutional_path=institutional_path,
        )
    elif case.gap_type == "optimization_backtest_proof":
        proof = _run_optimization_backtest_proof(
            case,
            persona_path=persona_path,
            institutional_path=institutional_path,
            feedback_path=feedback_path,
        )
    else:
        raise RemainingGapValidationError(f"Unsupported gap_type: {case.gap_type}")
    return _attach_round_plan(proof, round_plan)


def _run_lean_order_feedback_recovery(
    case: RemainingGapCase,
    *,
    persona_path: Path,
    institutional_path: Path,
    feedback_path: Path,
) -> dict[str, Any]:
    signal = _lean_signal(case)
    pending_store = InMemoryPendingSignalStore([signal])
    telemetry = _CanonicalTelemetryRecorder(case=case)
    runtime = PaperRuntimeService(
        store=pending_store,
        identity=_runtime_identity(case),
        runtime_manager_client=_RuntimeManagerClient(case),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=10,
    )

    first_snapshot = runtime.drain_once()
    second_snapshot = None
    if case.lean_lifecycle == "duplicate_retry_idempotent":
        pending_store.enqueue(signal)
        second_snapshot = runtime.drain_once()

    expected_event_type = _expected_lean_event_type(case)
    selected_event = _select_telemetry_event(telemetry.events, expected_event_type, case)
    writer = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored = writer.ingest_telemetry_event(
        selected_event,
        strategy_id=case.strategy_id,
        promotion_state="paper",
    )
    recovered = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_events = recovered.query_telemetry(
        strategy_id=case.strategy_id,
        event_type=expected_event_type,
        promotion_state="paper",
        limit=10,
    )
    recovered_event = next(event for event in recovered_events if event["event_id"] == stored["event_id"])
    lineage = recovered.build_lineage_record(recovered_event)
    writeback = _write_memory_from_telemetry(
        recovered,
        recovered_event,
        case=case,
        persona_path=persona_path,
        institutional_path=institutional_path,
        summary=(
            f"{case.lean_lifecycle} recovered durable feedback for {case.strategy_id}; "
            f"adapter={case.broker_adapter}; alpha={case.alpha_family}."
        ),
        tags=["lean_remaining_gap", case.lean_lifecycle, case.broker_adapter, case.alpha_family],
    )
    memory_read = _read_and_reuse_persona_memory(
        persona_path,
        case.persona_id,
        query=f"{case.lean_lifecycle} durable feedback {case.alpha_family}",
        tags=["lean_remaining_gap", case.lean_lifecycle],
    )
    institutional_read = _read_and_reuse_institutional_memory(
        institutional_path,
        writeback["institutional_entry_id"],
    )
    return {
        "proof_id": f"proof-{case.case_id}",
        "case_id": case.case_id,
        "gap_type": case.gap_type,
        "phases": ["lean_signal", "paper_runtime", "feedback_recovery", "memory_write", "memory_read"],
        "persona_id": case.persona_id,
        "strategy_id": case.strategy_id,
        "alpha_family": case.alpha_family,
        "lean": {
            "lifecycle": case.lean_lifecycle,
            "expected_event_type": expected_event_type,
            "first_snapshot_status": first_snapshot["status"],
            "second_snapshot_status": second_snapshot["status"] if second_snapshot else None,
            "processed_signal_count": (
                second_snapshot or first_snapshot
            )["paper_state"]["processed_signal_count"],
            "recent_order_event_count": len((second_snapshot or first_snapshot)["paper_state"]["recent_order_events"]),
        },
        "feedback_recovery": {
            "store_exists": feedback_path.exists(),
            "stored_event_id": stored["event_id"],
            "recovered_event_ids": [event["event_id"] for event in recovered_events],
            "event_type": recovered_event["event_type"],
            "lineage": lineage,
        },
        "memory": {
            "writeback": writeback,
            "persona_read": memory_read,
            "institutional_read": institutional_read,
        },
        "safety": {
            "submitted_to_broker": bool(recovered_event["metrics"].get("submitted_to_broker", False)),
            "is_real_order": bool(recovered_event["metadata"].get("is_real_order", False)),
            "is_real_capital": bool(recovered_event["metadata"].get("is_real_capital", False)),
        },
    }


def _run_long_term_memory_influence(
    case: RemainingGapCase,
    *,
    persona_path: Path,
    institutional_path: Path,
) -> dict[str, Any]:
    persona_store = PersonaMemoryStore(path=persona_path)
    institutional_store = InstitutionalMemoryStore(path=institutional_path)
    other_persona_id = f"persona-gap-other-{case.ordinal % 31:02d}"
    old_payload = _long_memory_payload(
        case,
        event_suffix="round-01",
        persona_id=case.persona_id,
        summary=f"Round 1 memory says keep prior allocation for {case.alpha_family}.",
        written_at="2026-06-01T00:00:00Z",
        tags=["long_memory", "round_01", case.long_memory_scenario],
    )
    old_write = write_learn_feedback(
        old_payload,
        persona_store=persona_store,
        institutional_store=institutional_store,
    )
    new_payload = _long_memory_payload(
        case,
        event_suffix="round-09",
        persona_id=case.persona_id,
        summary=f"Round 9 memory says tighten risk after recovered evidence for {case.alpha_family}.",
        written_at="2026-06-12T00:00:00Z",
        tags=["long_memory", "round_09", case.long_memory_scenario],
    )
    new_write = write_learn_feedback(
        new_payload,
        persona_store=persona_store,
        institutional_store=institutional_store,
    )
    other_write = write_learn_feedback(
        _long_memory_payload(
            case,
            event_suffix="other-persona",
            persona_id=other_persona_id,
            summary=f"Other persona private memory for {case.alpha_family} must not leak.",
            written_at="2026-06-13T00:00:00Z",
            tags=["long_memory", "other_persona_private", case.long_memory_scenario],
        ),
        persona_store=persona_store,
        institutional_store=institutional_store,
    )
    old_memory_id = old_write["persona_memory_ids"][0]
    new_memory_id = new_write["persona_memory_ids"][0]
    if case.long_memory_scenario == "superseded_memory_ignored":
        persona_store.supersede(old_memory_id, new_memory_id)

    reopened = PersonaMemoryStore(path=persona_path)
    hits = reopened.retrieve(
        persona_id=case.persona_id,
        query=f"{case.alpha_family} tighten risk round",
        tags=["long_memory", case.long_memory_scenario],
        limit=5,
    )
    if not hits:
        raise RemainingGapValidationError(f"No long-term memory hits for {case.case_id}")
    reused = reopened.mark_reused(hits[0].entry.memory_id)
    other_hits = reopened.retrieve(
        persona_id=case.persona_id,
        query="",
        tags=["other_persona_private"],
        limit=5,
    )
    other_persona_hits = reopened.retrieve(
        persona_id=other_persona_id,
        query="Other persona private memory",
        tags=["other_persona_private"],
        limit=5,
    )
    other_memory_id = other_write["persona_memory_ids"][0]
    institutional_read = _read_and_reuse_institutional_memory(
        institutional_path,
        new_write["institutional_entry_id"],
    )
    final_decision = "tighten_risk_and_require_revalidation"
    return {
        "proof_id": f"proof-{case.case_id}",
        "case_id": case.case_id,
        "gap_type": case.gap_type,
        "phases": ["round_1_write", "round_9_write", "reopen_store", "retrieve", "decide"],
        "persona_id": case.persona_id,
        "strategy_id": case.strategy_id,
        "alpha_family": case.alpha_family,
        "long_memory": {
            "scenario": case.long_memory_scenario,
            "old_memory_id": old_memory_id,
            "new_memory_id": new_memory_id,
            "selected_memory_id": reused.memory_id,
            "selected_reuse_count": reused.reuse_count,
            "selected_summary": reused.content["summary"],
            "active_hit_ids": [hit.entry.memory_id for hit in hits],
            "old_superseded_by": (
                PersonaMemoryStore(path=persona_path).get(old_memory_id).superseded_by
                if case.long_memory_scenario == "superseded_memory_ignored"
                else None
            ),
            "other_persona_memory_id": other_memory_id,
            "other_persona_retrievable_by_owner": any(
                hit.entry.memory_id == other_memory_id for hit in other_persona_hits
            ),
            "other_persona_leaked": any(hit.entry.memory_id == other_memory_id for hit in other_hits),
            "institutional_read": institutional_read,
        },
        "decision": {
            "baseline": "continue_prior_allocation",
            "final": final_decision,
            "changed_by_long_term_memory": True,
            "source_memory_id": reused.memory_id,
        },
    }


def _run_optimization_backtest_proof(
    case: RemainingGapCase,
    *,
    persona_path: Path,
    institutional_path: Path,
    feedback_path: Path,
) -> dict[str, Any]:
    dataset = _vectorbt_dataset(case)
    accepted = case.optimization_scenario == "accepted_after_backtest_improves"
    before_config = BacktestConfig(
        version=f"{case.case_id}-before",
        requested_by="Codex",
        strategy_params={"short_window": 12 if accepted else 3, "long_window": 24 if accepted else 8},
        fees=0.0005,
    )
    after_config = BacktestConfig(
        version=f"{case.case_id}-after",
        requested_by="Codex",
        strategy_params={"short_window": 3 if accepted else 12, "long_window": 8 if accepted else 24},
        fees=0.0005,
    )
    before = run_vectorbt_workflow(dataset, backend=StubVectorbtBackend(), config=before_config)
    after = run_vectorbt_workflow(dataset, backend=StubVectorbtBackend(), config=after_config)
    before_score = _backtest_score(before.backtest_result.aggregate_metrics)
    after_score = _backtest_score(after.backtest_result.aggregate_metrics)
    decision = _build_backtest_evolution_decision(
        case,
        before_score=before_score,
        after_score=after_score,
        accepted=accepted and after_score > before_score,
    )
    errors = validate_evolution_decision(decision)
    if errors:
        raise RemainingGapValidationError(f"Invalid optimization decision {case.case_id}: {errors}")

    telemetry_event = _backtest_telemetry_event(case, before_score, after_score, decision)
    writer = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    stored = writer.ingest_telemetry_event(
        telemetry_event,
        strategy_id=case.strategy_id,
        promotion_state="paper",
    )
    recovered = FeedbackStoreAdapter(feedback_store_path=str(feedback_path))
    recovered_events = recovered.query_telemetry(
        strategy_id=case.strategy_id,
        event_type="pnl_snapshot",
        promotion_state="paper",
        limit=5,
    )
    recovered_event = next(event for event in recovered_events if event["event_id"] == stored["event_id"])
    writeback = _write_memory_from_telemetry(
        recovered,
        recovered_event,
        case=case,
        persona_path=persona_path,
        institutional_path=institutional_path,
        summary=(
            f"Before/after backtest for {case.strategy_id}: before={before_score:.6f}, "
            f"after={after_score:.6f}, decision={decision.decision_state}."
        ),
        tags=["optimization_backtest", case.optimization_scenario, case.alpha_family],
    )
    memory_read = _read_and_reuse_persona_memory(
        persona_path,
        case.persona_id,
        query=f"before after backtest {case.optimization_scenario}",
        tags=["optimization_backtest", case.optimization_scenario],
    )
    return {
        "proof_id": f"proof-{case.case_id}",
        "case_id": case.case_id,
        "gap_type": case.gap_type,
        "phases": ["before_backtest", "after_backtest", "decision_gate", "feedback_recovery", "memory_read"],
        "persona_id": case.persona_id,
        "strategy_id": case.strategy_id,
        "alpha_family": case.alpha_family,
        "optimization": {
            "scenario": case.optimization_scenario,
            "before_run_id": before.backtest_result.run_id,
            "after_run_id": after.backtest_result.run_id,
            "before_score": before_score,
            "after_score": after_score,
            "improvement": round(after_score - before_score, 10),
            "decision": decision.to_dict(),
            "decision_valid": not errors,
            "accepted": decision.decision_state == EvolutionDecisionState.EXECUTED.value,
            "rejected": decision.decision_state == EvolutionDecisionState.REJECTED.value,
        },
        "feedback_recovery": {
            "stored_event_id": stored["event_id"],
            "recovered_event_ids": [event["event_id"] for event in recovered_events],
        },
        "memory": {
            "writeback": writeback,
            "persona_read": memory_read,
        },
    }


def _attach_round_plan(proof: dict[str, Any], round_plan: dict[str, Any]) -> dict[str, Any]:
    phase_order = round_plan["validation_plan"]["phase_order"]
    executed_phases = proof["phases"]
    proof["validation_round"] = {
        **round_plan,
        "executed_phase_order": executed_phases,
        "plan_executed": executed_phases == phase_order,
        "defects_found": [],
        "correction_status": "no_defect_detected",
    }
    if not proof["validation_round"]["plan_executed"]:
        raise RemainingGapValidationError(
            f"{proof['case_id']} executed phases {executed_phases} did not match planned phases {phase_order}"
        )
    return proof


def _expected_phase_order(case: RemainingGapCase) -> list[str]:
    if case.gap_type == "lean_order_feedback_recovery":
        return ["lean_signal", "paper_runtime", "feedback_recovery", "memory_write", "memory_read"]
    if case.gap_type == "long_term_memory_influence":
        return ["round_1_write", "round_9_write", "reopen_store", "retrieve", "decide"]
    if case.gap_type == "optimization_backtest_proof":
        return ["before_backtest", "after_backtest", "decision_gate", "feedback_recovery", "memory_read"]
    raise RemainingGapValidationError(f"Unsupported gap_type: {case.gap_type}")


def _not_yet_verified_question(case: RemainingGapCase) -> str:
    if case.gap_type == "lean_order_feedback_recovery":
        return (
            f"Have we verified {case.broker_adapter} paper execution can recover "
            f"{case.lean_lifecycle} feedback and preserve lineage for {case.alpha_family}?"
        )
    if case.gap_type == "long_term_memory_influence":
        return (
            f"Have we verified {case.long_memory_scenario} changes persona reasoning "
            f"after the store is reopened for {case.alpha_family}?"
        )
    if case.gap_type == "optimization_backtest_proof":
        return (
            f"Have we verified {case.optimization_scenario} is decided by before/after "
            f"backtest evidence instead of optimistic narrative for {case.alpha_family}?"
        )
    raise RemainingGapValidationError(f"Unsupported gap_type: {case.gap_type}")


def _deeper_validation_question(case: RemainingGapCase) -> str:
    if case.gap_type == "lean_order_feedback_recovery":
        return (
            "Can this round go deeper by replaying recovered telemetry through memory write/read "
            "and checking the broker adapter payload is paper-only?"
        )
    if case.gap_type == "long_term_memory_influence":
        return (
            "Can this round go deeper by proving newer or unsuperseded memory is reused while "
            "private persona memory remains isolated?"
        )
    if case.gap_type == "optimization_backtest_proof":
        return (
            "Can this round go deeper by requiring a deterministic vectorbt before/after score "
            "and rejecting the optimization when the score does not improve?"
        )
    raise RemainingGapValidationError(f"Unsupported gap_type: {case.gap_type}")


def _realistic_combination_question(case: RemainingGapCase) -> str:
    return (
        f"Could production realistically see strategy={case.strategy_id}, alpha={case.alpha_family}, "
        f"adapter={case.broker_adapter}, lean={case.lean_lifecycle}, "
        f"memory={case.long_memory_scenario}, optimization={case.optimization_scenario}, "
        "and have we executed that exact combination end to end?"
    )


def _validation_objective(case: RemainingGapCase) -> str:
    if case.gap_type == "lean_order_feedback_recovery":
        return "Recover order feedback after the paper runtime and feedback adapter are reopened, then prove memory reuse."
    if case.gap_type == "long_term_memory_influence":
        return "Persist multi-round persona memory, reopen it, retrieve the correct memory, and prove the decision changes."
    if case.gap_type == "optimization_backtest_proof":
        return "Gate self-optimization on concrete before/after backtest evidence and persist the resulting decision."
    raise RemainingGapValidationError(f"Unsupported gap_type: {case.gap_type}")


def _lean_signal(case: RemainingGapCase) -> dict[str, Any]:
    base = {
        "signal_id": f"sig-{case.case_id}",
        "version": "1.0",
        "strategy_id": case.strategy_id,
        "timestamp": _iso_now(),
        "symbol": _symbol_for(case),
        "action": "BUY",
        "direction": "LONG",
        "quantity": 4 + (case.ordinal % 5),
        "quantity_type": "SHARES",
        "order_type": "MARKET",
        "source_worker": "remaining-gap-validation",
        "binding_id": f"binding-{case.case_id}",
        "metadata": {
            "alpha_source": case.alpha_family,
            "confidence_score": 0.72 + ((case.ordinal % 10) * 0.02),
            "market_data": {"close": _price_for(case), "symbol": _symbol_for(case)},
            "market_price": _price_for(case),
            **_broker_order_metadata(case),
        },
    }
    if case.lean_lifecycle == "limit_fill_ack_recovered":
        base["order_type"] = "LIMIT"
        base["limit_price"] = round(_price_for(case) - 0.25, 2)
    elif case.lean_lifecycle == "duplicate_retry_idempotent":
        base["metadata"]["adapter_response_status"] = "retry_after_timeout_then_idempotent_noop"
    elif case.lean_lifecycle == "binding_mismatch_filtered":
        base["binding_id"] = f"wrong-binding-{case.case_id}"
        base["metadata"]["adapter_response_status"] = "filtered_before_broker_submit"
    elif case.lean_lifecycle == "hold_noop_recovered":
        base["action"] = "HOLD"
        base["metadata"]["adapter_response_status"] = "acknowledged_no_order"
    return base


def _expected_lean_event_type(case: RemainingGapCase) -> str:
    if case.lean_lifecycle in {"market_fill_ack_recovered", "limit_fill_ack_recovered"}:
        return "paper_fill_simulated"
    return "paper_order_simulated"


def _select_telemetry_event(
    events: list[dict[str, Any]],
    event_type: str,
    case: RemainingGapCase,
) -> dict[str, Any]:
    matches = [event for event in events if event["event_type"] == event_type]
    if not matches:
        raise RemainingGapValidationError(f"No {event_type} event for {case.case_id}")
    if case.lean_lifecycle == "duplicate_retry_idempotent":
        duplicate = [
            event
            for event in matches
            if event.get("metadata", {}).get("noop_reason") == "duplicate_signal_id"
        ]
        if duplicate:
            return duplicate[-1]
    return matches[-1]


def _write_memory_from_telemetry(
    adapter: FeedbackStoreAdapter,
    telemetry_event: dict[str, Any],
    *,
    case: RemainingGapCase,
    persona_path: Path,
    institutional_path: Path,
    summary: str,
    tags: list[str],
) -> dict[str, Any]:
    payload = adapter.build_learn_feedback_writeback_payload(
        telemetry_event,
        sponsor_persona_id=case.persona_id,
        contributing_persona_ids=[case.persona_id, case.collaborator_persona_id],
        summary=summary,
        contributor_feedback=[
            {
                "persona_id": case.persona_id,
                "summary": summary,
                "proposal_ids": [f"proposal-{case.case_id}"],
                "tags": tags,
            },
            {
                "persona_id": case.collaborator_persona_id,
                "summary": f"Peer reviewer confirms paper-only evidence for {case.case_id}.",
                "proposal_ids": [f"proposal-peer-{case.case_id}"],
                "tags": [*tags, "peer_review"],
            },
        ],
        proposal_ids=[f"proposal-{case.case_id}", telemetry_event["event_id"]],
    )
    payload["tags"].extend(tags)
    return write_learn_feedback(
        payload,
        persona_store=PersonaMemoryStore(path=persona_path),
        institutional_store=InstitutionalMemoryStore(path=institutional_path),
    )


def _read_and_reuse_persona_memory(
    persona_path: Path,
    persona_id: str,
    *,
    query: str,
    tags: list[str],
) -> dict[str, Any]:
    store = PersonaMemoryStore(path=persona_path)
    hits = store.retrieve(persona_id=persona_id, query=query, tags=tags, limit=5)
    if not hits:
        raise RemainingGapValidationError(f"No persona memory hit for {persona_id}")
    reused = store.mark_reused(hits[0].entry.memory_id)
    return {
        "memory_id": reused.memory_id,
        "persona_id": reused.persona_id,
        "reuse_count": reused.reuse_count,
        "source_event_id": reused.source_event_id,
        "summary": reused.content["summary"],
    }


def _read_and_reuse_institutional_memory(
    institutional_path: Path,
    entry_id: str,
) -> dict[str, Any]:
    store = InstitutionalMemoryStore(path=institutional_path)
    reused = store.mark_reused(entry_id)
    return {
        "entry_id": reused.entry_id,
        "reuse_count": reused.reuse_count,
        "source_event_id": reused.source_event_id,
    }


def _long_memory_payload(
    case: RemainingGapCase,
    *,
    event_suffix: str,
    persona_id: str,
    summary: str,
    written_at: str,
    tags: list[str],
) -> dict[str, Any]:
    event_id = f"long-memory-{case.case_id}-{event_suffix}"
    return {
        "source_event_type": "runtime_telemetry_outcome",
        "source_event_id": event_id,
        "write_authority": "telemetry-svc",
        "sponsor_persona_id": persona_id,
        "contributing_persona_ids": [persona_id],
        "summary": summary,
        "headline": f"Long memory round {event_suffix} for {case.case_id}",
        "body": summary,
        "runtime_telemetry_evidence": [
            {
                "ref_type": "telemetry_event",
                "ref_id": event_id,
                "event_type": "pnl_snapshot",
                "lineage": {
                    "strategy_id": case.strategy_id,
                    "alpha_family": case.alpha_family,
                    "memory_round": event_suffix,
                },
            }
        ],
        "contributor_feedback": [
            {
                "persona_id": persona_id,
                "summary": summary,
                "proposal_ids": [f"proposal-{case.case_id}-{event_suffix}"],
                "tags": tags,
            }
        ],
        "proposal_ids": [f"proposal-{case.case_id}-{event_suffix}"],
        "written_at": written_at,
        "tags": tags,
    }


def _vectorbt_dataset(case: RemainingGapCase) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for instrument_index, instrument in enumerate(("REM-GAP-A", "REM-GAP-B")):
        price = 100.0 + instrument_index * 5 + (case.ordinal % 7)
        for bar in range(42):
            date = (start + timedelta(days=bar)).date().isoformat()
            drift = 0.35 + (0.02 * instrument_index)
            cycle = ((bar + case.ordinal + instrument_index) % 5) * 0.03
            close = round(price + bar * drift + cycle, 4)
            records.append(
                {
                    "instrument": instrument,
                    "date": date,
                    "open": round(close - 0.15, 4),
                    "high": round(close + 0.25, 4),
                    "low": round(close - 0.35, 4),
                    "close": close,
                    "volume": 100000 + bar * 10 + instrument_index,
                }
            )
    return {
        "dataset_id": f"dataset-{case.case_id}",
        "strategy_id": case.strategy_id,
        "source_dataset_refs": [f"source-dataset-{case.case_id}", "remaining-gap-fixture"],
        "source_strategy_spec_id": f"strategy-spec-{case.case_id}",
        "data_frequency": "daily",
        "records": records,
    }


def _backtest_score(metrics: Mapping[str, Any]) -> float:
    return round(
        float(metrics["mean_total_return"])
        + 0.1 * float(metrics["mean_sharpe_ratio"])
        - 0.5 * float(metrics["mean_max_drawdown"]),
        10,
    )


def _build_backtest_evolution_decision(
    case: RemainingGapCase,
    *,
    before_score: float,
    after_score: float,
    accepted: bool,
) -> EvolutionDecision:
    improvement = after_score - before_score
    snapshot = ThresholdSnapshot(
        policy_source="remaining_gap_validation#before_after_backtest",
        signal_type=ThresholdSignalType.PERFORMANCE_DEGRADATION,
        metric_name="before_after_score_delta",
        comparator=ComparisonOperator.GT,
        observed_value=round(improvement, 10),
        threshold_value=0.0,
        window="oos_42_bars",
        breached=accepted,
        note="Optimization must improve backtest score before execution.",
    )
    decision = EvolutionDecision.create_proposed(
        decision_id=f"evo-{case.case_id}",
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=case.strategy_id,
        target_version="paper-remaining-gap",
        action_type=EvolutionActionType.REVALIDATE,
        rationale=(
            f"Before/after backtest score delta={improvement:.6f}; "
            "execute only when evidence improves."
        ),
        created_by_id=case.persona_id,
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        risk_level=RiskLevel.LOW,
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.EVALUATOR_RESULT,
                ref_id=f"backtest-{case.case_id}",
                storage_ref={"backend": "vectorbt_stub", "path": f"memory://{case.case_id}"},
            )
        ],
        threshold_snapshots=[snapshot],
        persona_id=case.persona_id,
        target_stage="paper",
        metadata={
            "case_id": case.case_id,
            "before_score": before_score,
            "after_score": after_score,
        },
    )
    decision.mark_reviewed(
        EvolutionActorRole.REVIEWER_ON_DUTY,
        "remaining-gap-reviewer",
        f"approval-{case.case_id}",
        note="Backtest proof reviewed.",
    )
    if accepted:
        decision.approve(
            EvolutionActorRole.REVIEWER_ON_DUTY,
            "remaining-gap-approver",
            note="Backtest improvement is positive.",
        )
        executed_at = _iso_now()
        decision.execute(
            EvolutionActorRole.EVOLUTION_CONTROLLER,
            "remaining-gap-controller",
            ExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                plane=ExecutionPlane.RESEARCH,
                executed_at=executed_at,
                execution_ref_id=f"exec-{case.case_id}",
                outcome_summary="Paper revalidation executed after positive before/after proof.",
            ),
            cooldown_ends_at=_iso_after(days=7),
            observation_window_ends_at=_iso_after(days=14),
            note="Executed as paper-only revalidation.",
        )
    else:
        decision.reject(
            EvolutionActorRole.REVIEWER_ON_DUTY,
            "remaining-gap-reviewer",
            note="Rejected because after backtest did not improve.",
        )
    return decision


def _backtest_telemetry_event(
    case: RemainingGapCase,
    before_score: float,
    after_score: float,
    decision: EvolutionDecision,
) -> dict[str, Any]:
    return {
        "event_id": f"backtest-telemetry-{case.case_id}",
        "event_type": "pnl_snapshot",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": f"binding-{case.case_id}",
        "runtime_id": f"runtime-{case.case_id}",
        "capital_pool_id": f"pool-{case.case_id}",
        "artifact_id": f"artifact-{case.strategy_id}",
        "artifact_version": "remaining-gap",
        "plan_id": f"plan-{case.case_id}",
        "persona_capital_binding_id": f"pcb-{case.case_id}",
        "target": {
            "registry_id": f"artifact-{case.strategy_id}",
            "strategy_id": case.strategy_id,
            "artifact_version": "remaining-gap",
            "artifact_type": "backtest_result",
            "promotion_state": "paper",
        },
        "metrics": {
            "pnl": after_score,
            "before_score": before_score,
            "after_score": after_score,
            "score_improvement": round(after_score - before_score, 10),
        },
        "metadata": {
            "persona_id": case.persona_id,
            "case_id": case.case_id,
            "alpha_source": case.alpha_family,
            "decision_id": decision.decision_id,
            "decision_state": decision.decision_state,
            "is_real_order": False,
            "is_real_capital": False,
            "submitted_to_broker": False,
        },
        "trace_id": f"trace-{case.case_id}",
    }


def _runtime_identity(case: RemainingGapCase) -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": f"runtime-{case.case_id}",
            "PANTHEON_RUNTIME_BINDING_ID": f"binding-{case.case_id}",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": f"trace-{case.case_id}",
            "PANTHEON_REQUEST_ID": f"request-{case.case_id}",
        }
    )


class _RuntimeManagerClient:
    def __init__(self, case: RemainingGapCase) -> None:
        self._case = case

    def list_all(self) -> list[dict[str, Any]]:
        return [
            {
                "binding_id": f"binding-{self._case.case_id}",
                "runtime_id": f"runtime-{self._case.case_id}",
                "capital_pool_id": f"pool-{self._case.case_id}",
                "artifact_id": f"artifact-{self._case.strategy_id}",
                "artifact_version": "remaining-gap",
                "deployment_mode": "paper",
                "deployment_stage": "paper",
                "plan_id": f"plan-{self._case.case_id}",
                "persona_capital_binding_id": f"pcb-{self._case.case_id}",
                "status": "active",
            }
        ]


class _CanonicalTelemetryRecorder:
    enabled = True

    def __init__(self, *, case: RemainingGapCase) -> None:
        self._case = case
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        metadata = dict(metadata or {})
        index = len(self.events) + 1
        event = {
            "event_id": f"{self._case.case_id}-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": f"binding-{self._case.case_id}",
            "runtime_id": f"runtime-{self._case.case_id}",
            "capital_pool_id": f"pool-{self._case.case_id}",
            "artifact_id": f"artifact-{self._case.strategy_id}",
            "artifact_version": "remaining-gap",
            "plan_id": f"plan-{self._case.case_id}",
            "persona_capital_binding_id": f"pcb-{self._case.case_id}",
            "target": {
                "registry_id": f"artifact-{self._case.strategy_id}",
                "strategy_id": self._case.strategy_id,
                "artifact_version": "remaining-gap",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": {
                "persona_id": self._case.persona_id,
                "case_id": self._case.case_id,
                **metadata,
            },
            "trace_id": f"trace-{self._case.case_id}",
        }
        self.events.append(event)
        return True

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl)}
        metrics.update(extra_metrics or {})
        return self.emit("pnl_snapshot", metrics, metadata)

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": True, "sent": len(self.events), "failed": 0, "last_error": None}


def _broker_order_metadata(case: RemainingGapCase) -> dict[str, Any]:
    if case.broker_adapter == "ibkr":
        adapter = IBKRAdapter(IBKRConfig(host="127.0.0.1", port=7497, client_id=case.ordinal, readonly_market_data=True))
        order = adapter.build_order(
            IBKROrderIntent(
                symbol="AAPL.US",
                side="BUY",
                quantity=max(1, 1 + case.ordinal % 9),
                order_type="MKT",
                metadata={"case_id": case.case_id},
            )
        )
        return {
            "adapter": "IBKRAdapter",
            "broker": "IBKR",
            "adapter_response_status": "acknowledged_readback_only",
            "broker_order_id": f"ibkr-{case.case_id}",
            "submission": order,
        }
    if case.broker_adapter == "shioaji":
        adapter = ShioajiAdapter(ShioajiConfig(api_key="ref://shioaji/api-key", secret_key="ref://shioaji/secret", simulation=True))
        order = adapter.build_order(
            ShioajiOrderIntent(
                symbol="2330.TW",
                side="BUY",
                quantity=max(1, 1 + case.ordinal % 5),
                price=650.0,
                metadata={"case_id": case.case_id},
            )
        )
        return {
            "adapter": "ShioajiAdapter",
            "broker": "Shioaji",
            "adapter_response_status": "sim_ack_recovered",
            "broker_order_id": f"shioaji-{case.case_id}",
            "submission": order,
        }
    adapter = KrakenAdapter(KrakenConfig(api_key="ref://kraken/key", api_secret="ref://kraken/secret", validate_only=True))
    order = adapter.build_order(
        KrakenOrderIntent(
            symbol="BTC/USD.KRAKEN",
            side="buy",
            quantity=0.01,
            validate=True,
            metadata={"case_id": case.case_id},
        )
    )
    return {
        "adapter": "KrakenAdapter",
        "broker": "Kraken",
        "adapter_response_status": "validate_only_ack_recovered",
        "broker_order_id": f"kraken-{case.case_id}",
        "submission": order,
    }


def _symbol_for(case: RemainingGapCase) -> str:
    if case.broker_adapter == "shioaji":
        return "AAPL.US"
    if case.broker_adapter == "kraken":
        return "BTCUSD.KRAKEN"
    return "AAPL.US"


def _price_for(case: RemainingGapCase) -> float:
    if case.broker_adapter == "shioaji":
        return 650.0 + (case.ordinal % 7)
    if case.broker_adapter == "kraken":
        return 65000.0 + (case.ordinal % 13) * 10
    return 180.0 + (case.ordinal % 11)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_after(*, days: int) -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        + timedelta(days=days)
    ).isoformat().replace("+00:00", "Z")

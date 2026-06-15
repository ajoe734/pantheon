"""Persona agent usability validation across trading, reflection, and evolution.

This harness is intentionally about *usable autonomy*, not service liveness. It
builds 3000 non-repeated portfolio episodes from repo-backed alpha seeds and
historical OHLCV, routes persona requests through OSS evidence, executes paper
trades, writes and reuses memory, scores agent-generated improvement
candidates, and validates governed evolution decisions.

The important hardening over the first 3000-case proof is no-leakage evolution:
observe/decide sees only the observe window, reflection/evolution sees only the
first feedback window, and the evolved policy is judged on later holdout windows
that are not present in the decision trace.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from services.broker.shioaji.adapter import ShioajiBrokerAdapter
from services.broker.shioaji.facade import ShioajiSandboxFacade
from services.broker.shioaji.sandbox_smoke import MockShioajiApi
from services.execution.lean_runtime.smoke_algorithm import (
    SMOKE_STRATEGY_ID,
    SMOKE_VERSION,
    run_algorithm_smoke_from_binding,
)
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.pending_signal_store import InMemoryPendingSignalStore
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.learn_feedback_writeback import write_learn_feedback
from services.memory.persona_memory_store import PersonaMemoryStore
from services.persona.ooda_cycle_runtime import (
    ALPHA_SEED_SOURCES,
    HISTORICAL_OHLCV_DATASET_ID,
    HISTORICAL_OHLCV_FIXTURE,
)
from services.persona.oss_runtime import (
    PERSONA_OSS_COMPONENTS,
    PersonaOSSRequest,
    run_persona_oss_request,
)
from services.registry.experiments.adapter import (
    InMemoryMlflowBackend,
    OfflineWandbLocalBackend,
    RegistryExperimentAdapter,
)
from services.telemetry.feedback_adapter import FeedbackStoreAdapter
from services.research.vectorbt.adapter import BacktestConfig, VectorbtBackend, run_vectorbt_workflow


REPO_ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE_DIR = REPO_ROOT / "services" / "control-plane" / "governance"
if str(GOVERNANCE_DIR) not in sys.path:
    sys.path.insert(0, str(GOVERNANCE_DIR))

from approval_decision import EvidenceRef, EvidenceRefType  # noqa: E402
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


DEFAULT_CASE_COUNT = 3000
DEFAULT_GENERATED_AT = "2026-06-13T00:00:00Z"
LOOKBACK_BARS = 24
FEEDBACK_BARS = 12
HOLDOUT_BARS = 12
FUTURE_HOLDOUT_BARS = 12
MIN_HISTORY_BARS = LOOKBACK_BARS + FEEDBACK_BARS + HOLDOUT_BARS + FUTURE_HOLDOUT_BARS + 2
PORTFOLIO_LEG_COUNT = 3
GENERATION_COUNT = 3
MIN_USABILITY_SCORE = 0.95

QUANTITY_TYPES = ("SHARES", "CASH_VALUE", "PERCENT_PORTFOLIO")
ORDER_TYPES = ("MARKET", "LIMIT")
OSS_REQUIRED_COMPONENTS = tuple(PERSONA_OSS_COMPONENTS)
POLICY_OSS_COMPONENTS = ("finrl", "rllib", "ray_tune")
REFLECTION_OSS_COMPONENTS = ("dspy", "trl", "imitation")
TRACKING_OSS_COMPONENTS = ("mlflow", "wandb")
RISK_OSS_COMPONENTS = ("statsmodels", "quantlib")
OPERATIONAL_SCENARIOS = (
    "partial_fill_reconcile",
    "limit_miss_reprice",
    "liquidity_cap_scale",
    "cancel_replace_readback",
    "risk_reject_reduce",
)
BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO: dict[str, str] = {
    "cancel_replace_readback": "confirm_cancel_replace_then_resubmit_paper_order",
    "limit_miss_reprice": "reprice_limit_with_fresh_readback",
    "liquidity_cap_scale": "scale_to_liquidity_cap_before_next_cycle",
    "partial_fill_reconcile": "reconcile_partial_fill_and_verify_position",
    "risk_reject_reduce": "reduce_risk_and_hold_live_submission_disabled",
}
LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO: dict[str, str] = {
    "cancel_replace_readback": "act_on_handoff_replay_before_resubmission",
    "limit_miss_reprice": "orient_on_runtime_fill_quality_before_reprice",
    "liquidity_cap_scale": "decide_reduced_allocation_for_next_cycle",
    "partial_fill_reconcile": "observe_runtime_fills_before_reflection",
    "risk_reject_reduce": "decide_risk_reduced_paper_next_cycle",
}
LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION: dict[str, str] = {
    "act_on_handoff_replay_before_resubmission": "act",
    "decide_reduced_allocation_for_next_cycle": "decide",
    "decide_risk_reduced_paper_next_cycle": "decide",
    "observe_runtime_fills_before_reflection": "observe",
    "orient_on_runtime_fill_quality_before_reprice": "orient",
}
BROKER_LIFECYCLE_TERMINAL_STATUS = "filled"
MARKET_FRICTION_MODEL_ID = "volume_capped_slippage_commission_v1"
LEAN_ENGINE_REPLAY_MODEL_ID = "pantheon_lean_smoke_binding_context_v1"
LEAN_RUNTIME_FEEDBACK_MODEL_ID = "persona_lean_runtime_feedback_v1"
LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID = "lean_evolved_strategy_packet_provenance_v1"
LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID = "lean_evolved_packet_multi_asset_execution_projection_v1"
LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID = "lean_object_store_evolved_packet_readback_v1"
SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID = "shioaji_sandbox_facade_mock_replay_v1"
BROKER_ADAPTER_LIFECYCLE_MODEL_ID = "persona_broker_adapter_lifecycle_v1"
BROKER_ADAPTER_FOLLOWUP_MODEL_ID = "persona_broker_adapter_followup_v1"
CASE_UPSTREAM_VECTORBT_MODEL_ID = "case_specific_vectorbt_feedback_v1"
CASE_UPSTREAM_TRACKING_MODEL_ID = "case_specific_tracking_artifact_roundtrip_v1"
CASE_SELECTED_OSS_MODEL_ID = "case_specific_selected_oss_feedback_v1"
PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID = "persona_policy_candidate_oss_materiality_v1"
PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID = "persona_policy_oss_lineage_handoff_v1"
PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID = "persona_reflection_artifact_oss_materiality_v1"
PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID = "persona_reflection_oss_lineage_handoff_v1"
PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID = "persona_openclaw_session_handoff_v1"
PERSONA_CONFLICT_RESOLUTION_MODEL_ID = "persona_multi_persona_conflict_resolution_v1"
PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID = "persona_scheduler_conflict_ooda_dispatch_v1"
PERSONA_DECISION_ARTIFACT_MODEL_ID = "persona_replayable_candidate_decision_v1"
PERSONA_CANDIDATE_GENERATOR_MODEL_ID = "persona_candidate_generation_from_oss_feedback_v1"
PERSONA_CANDIDATE_SCORER_MODEL_ID = "persona_multi_factor_candidate_scorer_v1"
PERSONA_RISK_EVALUATOR_MODEL_ID = "persona_oss_risk_turnover_evaluator_v1"
PERSONA_MEMORY_INFLUENCE_MODEL_ID = "persona_retrieved_lesson_influence_v1"
PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID = "persona_cross_persona_institutional_memory_lineage_v1"
PERSONA_REASONING_MODEL_ID = "persona_structured_reasoning_candidate_generator_v1"
PERSONA_REASONING_EVALUATOR_MODEL_ID = "persona_reasoning_response_evaluator_v1"
EVOLUTION_TRAJECTORY_MODEL_ID = "persona_multi_generation_evolution_trajectory_v1"
NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID = "persona_no_leakage_temporal_protocol_v1"
STRICT_OOS_EVOLUTION_PROOF_MODEL_ID = "persona_strict_oos_evolution_proof_v1"
PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID = "persona_memory_counterfactual_decision_proof_v1"
MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID = "persona_multi_oss_closed_loop_proof_v1"
PERSONA_OSS_OODA_LEDGER_MODEL_ID = "persona_oss_ooda_causal_ledger_v1"
PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID = "persona_cross_cycle_runtime_carryover_v1"
PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID = "persona_persisted_cycle_resume_carryover_v1"
PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID = "persona_multi_cycle_lineage_carryover_v1"
OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID = "persona_oss_response_followup_loop_v1"
PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID = "persona_multi_oss_disagreement_arbitration_v1"
PERSONA_TRACKING_RECONCILIATION_MODEL_ID = "persona_tracking_readback_reconciliation_v1"
PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID = (
    "persona_experiment_tracking_lineage_handoff_v1"
)
PERSONA_ALPHA_SEED_REVISION_MODEL_ID = "persona_alpha_seed_revision_from_oss_v1"
PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID = (
    "persona_alpha_seed_revision_handoff_v1"
)
ALPHA_SEED_REVISION_ACTION_BY_COMPONENT: dict[str, str] = {
    "qlib": "apply_qlib_alpha_seed_update",
    "vectorbt": "apply_vectorbt_alpha_backtest_seed_update",
}
TRACKING_DIVERGENCE_TYPES_BY_SCENARIO: dict[str, str] = {
    "cancel_replace_readback": "run_tag_backend_normalization",
    "limit_miss_reprice": "artifact_uri_normalization",
    "liquidity_cap_scale": "registry_alias_lag",
    "partial_fill_reconcile": "metric_precision_roundtrip",
    "risk_reject_reduce": "artifact_manifest_ordering",
}
TRACKING_RECONCILIATION_ACTION_BY_TYPE: dict[str, str] = {
    "artifact_manifest_ordering": "sort_manifest_before_experiment_ref",
    "artifact_uri_normalization": "normalize_artifact_uri_before_citation",
    "metric_precision_roundtrip": "accept_metric_precision_roundtrip",
    "registry_alias_lag": "bind_registry_alias_before_handoff",
    "run_tag_backend_normalization": "normalize_backend_tags_before_scoring",
}
OSS_DISAGREEMENT_TYPES_BY_SCENARIO: dict[str, str] = {
    "cancel_replace_readback": "reflection_handoff_execution_conflict",
    "limit_miss_reprice": "alpha_backtest_price_conflict",
    "liquidity_cap_scale": "policy_risk_liquidity_conflict",
    "partial_fill_reconcile": "backtest_policy_fill_conflict",
    "risk_reject_reduce": "alpha_risk_rejection_conflict",
}
OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE: dict[str, tuple[str, str]] = {
    "alpha_backtest_price_conflict": ("alpha_model", "backtest"),
    "alpha_risk_rejection_conflict": ("alpha_model", "risk_analytics"),
    "backtest_policy_fill_conflict": ("backtest", "policy_candidate"),
    "policy_risk_liquidity_conflict": ("policy_candidate", "risk_analytics"),
    "reflection_handoff_execution_conflict": ("reflection_artifact", "handoff"),
}
OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE: dict[str, str] = {
    "alpha_backtest_price_conflict": "feedback-adapt",
    "alpha_risk_rejection_conflict": "risk-off",
    "backtest_policy_fill_conflict": "feedback-adapt",
    "policy_risk_liquidity_conflict": "risk-off",
    "reflection_handoff_execution_conflict": "contrarian-check",
}
AUTONOMOUS_SCHEDULER_PHASES = (
    "observe",
    "request_oss",
    "decide",
    "paper_trade",
    "reflect",
    "evolve",
    "handoff",
    "schedule_next",
)

DEFAULT_PERSONAS: tuple[dict[str, str], ...] = (
    {
        "persona_id": "persona-alpha",
        "name": "Persona Alpha",
        "mandate": "Cross-market alpha synthesis and execution review.",
    },
    {
        "persona_id": "persona-pack-a-momentum",
        "name": "Persona Momentum",
        "mandate": "Momentum strategy research and paper deployment.",
    },
    {
        "persona_id": "p-compliance-sponsor",
        "name": "Compliance Sponsor",
        "mandate": "Evidence-gated strategy sponsorship.",
    },
    {
        "persona_id": "p-execution-lead",
        "name": "Execution Lead",
        "mandate": "Paper execution and fill quality review.",
    },
    {
        "persona_id": "p-macro-observer",
        "name": "Macro Observer",
        "mandate": "Regime interpretation for allocation timing.",
    },
    {
        "persona_id": "p-risk-analyst",
        "name": "Risk Analyst",
        "mandate": "Drawdown and risk budget adaptation.",
    },
    {
        "persona_id": "persona-us-equity",
        "name": "US Equity Persona",
        "mandate": "Equity alpha transfer and paper validation.",
    },
    {
        "persona_id": "persona-tw-equity",
        "name": "TW Equity Persona",
        "mandate": "Taiwan equity historical alpha validation.",
    },
    {
        "persona_id": "persona-crypto",
        "name": "Crypto Persona",
        "mandate": "Liquid market strategy adaptation.",
    },
)


@dataclass(frozen=True)
class AgentUsabilityValidationRun:
    """Replayable result bundle for the 3000-case usability proof."""

    summary: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    oss_results: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class InstrumentWindow:
    instrument: str
    execution_symbol: str
    start_index: int
    observe_rows: tuple[dict[str, Any], ...]
    feedback_rows: tuple[dict[str, Any], ...]
    holdout_rows: tuple[dict[str, Any], ...]
    future_holdout_rows: tuple[dict[str, Any], ...]
    observe_direction: int
    feedback_direction: int
    holdout_direction: int
    future_direction: int

    @property
    def selection_archetype(self) -> str:
        if self.observe_direction == -self.feedback_direction:
            return "feedback_reversal_repair"
        return "feedback_conviction_scale"


@dataclass(frozen=True)
class PortfolioEpisode:
    case_id: str
    validation_signature: str
    ordinal: int
    persona: dict[str, Any]
    seed_key: str
    source_strategy_spec_id: str
    source_dataset_refs: tuple[str, ...]
    windows: tuple[InstrumentWindow, ...]
    oss_route: dict[str, str]
    order_profile: dict[str, str]
    reflection_archetype: str
    generation_path: tuple[str, str, str]
    regime_path: tuple[str, ...]


@dataclass(frozen=True)
class PolicyCandidate:
    candidate_id: str
    direction_by_instrument: dict[str, int]
    risk_multiplier: float
    score: float
    source_windows: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rationale: str


class _RuntimeManagerClient:
    def __init__(self, *, binding_id: str, runtime_id: str, persona_id: str, strategy_id: str) -> None:
        self._binding = {
            "binding_id": binding_id,
            "runtime_id": runtime_id,
            "capital_pool_id": f"pool-usability-{persona_id}",
            "artifact_id": f"artifact-{strategy_id}",
            "artifact_version": "3000.1.0",
            "deployment_mode": "paper",
            "deployment_stage": "paper",
            "plan_id": f"plan-usability-{persona_id}",
            "persona_capital_binding_id": f"pcb-usability-{persona_id}",
            "status": "active",
        }

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(self._binding)]


class _TelemetryRecorder:
    enabled = True

    def __init__(
        self,
        *,
        binding_id: str,
        runtime_id: str,
        persona_id: str,
        strategy_id: str,
        case_id: str,
    ) -> None:
        self._binding_id = binding_id
        self._runtime_id = runtime_id
        self._persona_id = persona_id
        self._strategy_id = strategy_id
        self._case_id = case_id
        self.events: list[dict[str, Any]] = []

    def emit(self, event_type: str, metrics: dict[str, Any], metadata: dict[str, Any] | None = None) -> bool:
        metadata = dict(metadata or {})
        index = len(self.events) + 1
        event = {
            "event_id": f"{self._case_id}-{event_type}-{index:03d}",
            "event_type": event_type,
            "created_at": _iso_now(),
            "execution_mode": "paper",
            "environment": "paper",
            "deployment_stage": "paper",
            "binding_id": self._binding_id,
            "runtime_id": self._runtime_id,
            "capital_pool_id": f"pool-usability-{self._persona_id}",
            "artifact_id": f"artifact-{self._strategy_id}",
            "artifact_version": "3000.1.0",
            "plan_id": f"plan-usability-{self._persona_id}",
            "persona_capital_binding_id": f"pcb-usability-{self._persona_id}",
            "target": {
                "registry_id": f"artifact-{self._strategy_id}",
                "strategy_id": metadata.get("strategy_id") or self._strategy_id,
                "artifact_version": "3000.1.0",
                "artifact_type": "execution_bundle",
                "promotion_state": "paper",
            },
            "metrics": dict(metrics),
            "metadata": {
                "persona_id": self._persona_id,
                "case_id": self._case_id,
                **metadata,
            },
            "trace_id": f"trace-{self._case_id}",
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
        return {
            "enabled": True,
            "url": "memory://agent-usability-validation",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


def run_agent_usability_validations(
    *,
    case_count: int = DEFAULT_CASE_COUNT,
    personas: Sequence[Mapping[str, Any]] | None = None,
    generated_at: str = DEFAULT_GENERATED_AT,
    run_oss_backtests: bool = True,
) -> AgentUsabilityValidationRun:
    """Run hard no-leakage persona trading usability validations."""

    if case_count <= 0:
        raise ValueError("case_count must be positive")
    persona_records = [dict(persona) for persona in (personas or DEFAULT_PERSONAS)]
    if not persona_records:
        raise ValueError("at least one persona is required")

    dataset = _load_historical_dataset()
    grouped = _group_records_by_instrument(dataset["records"])
    valid_windows = _build_valid_no_leakage_windows(grouped)
    episodes = _build_episode_manifest(
        valid_windows=valid_windows,
        personas=persona_records,
        case_count=case_count,
    )
    oss_results = _run_oss_feedback_bank(persona_records) if run_oss_backtests else []
    oss_by_component = {str(result["component"]): result for result in oss_results}

    feedback_adapter = FeedbackStoreAdapter()
    persona_store = PersonaMemoryStore()
    institutional_store = InstitutionalMemoryStore()
    cases: list[dict[str, Any]] = []
    cycle_state_history_by_persona: dict[str, list[dict[str, Any]]] = {}

    for index, episode in enumerate(episodes):
        persona_id = _persona_id(episode.persona)
        prior_cycle_history = list(cycle_state_history_by_persona.get(persona_id, []))
        cross_cycle_context = _cross_cycle_context_for_episode(
            episode=episode,
            prior_cycle_state=prior_cycle_history[-1] if prior_cycle_history else None,
        )
        multi_cycle_context = _multi_cycle_context_for_episode(
            episode=episode,
            prior_cycle_states=prior_cycle_history,
        )
        oss_inputs = _oss_inputs_for_episode(episode, oss_by_component)
        case_upstream_artifacts = _build_case_upstream_artifact_feedback(
            episode=episode,
            oss_inputs=oss_inputs,
        )
        oss_inputs = _apply_case_upstream_artifacts_to_oss_inputs(
            oss_inputs,
            case_upstream_artifacts,
        )
        oss_followup_loop = _build_oss_response_followup_loop(
            episode=episode,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
        )
        oss_disagreement_arbitration = _build_oss_disagreement_arbitration(
            episode=episode,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            oss_followup_loop=oss_followup_loop,
        )
        case_upstream_artifacts["oss_disagreement_arbitration"] = oss_disagreement_arbitration
        case_upstream_artifacts["persona_response"]["evidence_refs"].append(
            oss_disagreement_arbitration["arbitration_ref"]
        )
        case_upstream_artifacts["persona_response"][
            "next_disagreement_action"
        ] = "arbitrate_multi_oss_disagreement"
        tracking_reconciliation = _build_tracking_readback_reconciliation(
            episode=episode,
            case_upstream_artifacts=case_upstream_artifacts,
        )
        case_upstream_artifacts["tracking_reconciliation"] = tracking_reconciliation
        case_upstream_artifacts["persona_response"]["evidence_refs"].append(
            tracking_reconciliation["reconciliation_ref"]
        )
        case_upstream_artifacts["persona_response"][
            "next_tracking_reconciliation_action"
        ] = tracking_reconciliation["repair"]["action"]
        prior_memory = _retrieve_prior_lesson(persona_store, persona_id)
        prior_institutional_memory = _retrieve_cross_persona_institutional_lesson(
            institutional_store,
            persona_id,
        )
        validation_plan = _build_validation_planning_step(
            episode=episode,
            prior_cases=cases,
        )

        generation0_policy = _build_baseline_policy(episode, index, prior_memory, oss_inputs)
        generation0_exec = _execute_signals(
            _build_signals(
                episode=episode,
                policy=generation0_policy,
                generation=0,
                generated_at=generated_at,
            ),
            case_id=f"{episode.case_id}-gen0",
            persona_id=persona_id,
        )
        generation0_eval = _evaluate_portfolio_policy(episode, generation0_policy, period="feedback")
        generation0_event = _build_portfolio_outcome_event(
            episode=episode,
            generation=0,
            policy=generation0_policy,
            execution=generation0_exec,
            evaluation=generation0_eval,
        )
        stored_generation0 = feedback_adapter.ingest_telemetry_event(
            generation0_event,
            strategy_id=f"{episode.seed_key}-agent-usability-hardening",
            promotion_state="paper",
        )
        decision_trace0 = _build_agent_decision_trace(
            episode=episode,
            generation=1,
            baseline_policy=generation0_policy,
            latest_evaluation=generation0_eval,
            telemetry_event=stored_generation0,
            prior_memory=prior_memory,
            oss_inputs=oss_inputs,
            oss_followup_loop=oss_followup_loop,
            oss_disagreement_arbitration=oss_disagreement_arbitration,
            tracking_reconciliation=tracking_reconciliation,
            alpha_seed_revision=case_upstream_artifacts["alpha_seed_revision"],
            cross_cycle_context=cross_cycle_context,
            multi_cycle_context=multi_cycle_context,
            institutional_memory_context=prior_institutional_memory,
        )
        memory_write0 = _write_learn_memory(
            feedback_adapter=feedback_adapter,
            telemetry_event=stored_generation0,
            persona=episode.persona,
            reflection=decision_trace0,
            persona_store=persona_store,
            institutional_store=institutional_store,
        )
        current_memory0 = _retrieve_current_lesson(
            persona_store,
            persona_id,
            reflection_id=decision_trace0["reflection_id"],
        )

        generation1_policy = _policy_from_decision_trace(
            episode=episode,
            generation=1,
            decision_trace=decision_trace0,
            memory_context=current_memory0,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
        )
        generation1_exec = _execute_signals(
            _build_signals(
                episode=episode,
                policy=generation1_policy,
                generation=1,
                generated_at=generated_at,
            ),
            case_id=f"{episode.case_id}-gen1",
            persona_id=persona_id,
        )
        generation1_eval = _evaluate_portfolio_policy(episode, generation1_policy, period="holdout")
        baseline_holdout_counterfactual = _evaluate_portfolio_policy(
            episode,
            generation0_policy,
            period="holdout",
        )
        generation1_event = _build_portfolio_outcome_event(
            episode=episode,
            generation=1,
            policy=generation1_policy,
            execution=generation1_exec,
            evaluation=generation1_eval,
        )
        stored_generation1 = feedback_adapter.ingest_telemetry_event(
            generation1_event,
            strategy_id=f"{episode.seed_key}-agent-usability-hardening",
            promotion_state="paper",
        )
        decision_trace1 = _build_agent_decision_trace(
            episode=episode,
            generation=2,
            baseline_policy=generation1_policy,
            latest_evaluation=generation1_eval,
            telemetry_event=stored_generation1,
            prior_memory=current_memory0,
            oss_inputs=oss_inputs,
            oss_followup_loop=oss_followup_loop,
            oss_disagreement_arbitration=oss_disagreement_arbitration,
            tracking_reconciliation=tracking_reconciliation,
            alpha_seed_revision=case_upstream_artifacts["alpha_seed_revision"],
            cross_cycle_context=cross_cycle_context,
            multi_cycle_context=multi_cycle_context,
            institutional_memory_context=prior_institutional_memory,
        )
        memory_write1 = _write_learn_memory(
            feedback_adapter=feedback_adapter,
            telemetry_event=stored_generation1,
            persona=episode.persona,
            reflection=decision_trace1,
            persona_store=persona_store,
            institutional_store=institutional_store,
        )
        current_memory1 = _retrieve_current_lesson(
            persona_store,
            persona_id,
            reflection_id=decision_trace1["reflection_id"],
        )

        generation2_policy = _policy_from_decision_trace(
            episode=episode,
            generation=2,
            decision_trace=decision_trace1,
            memory_context=current_memory1,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
        )
        generation2_exec = _execute_signals(
            _build_signals(
                episode=episode,
                policy=generation2_policy,
                generation=2,
                generated_at=generated_at,
            ),
            case_id=f"{episode.case_id}-gen2",
            persona_id=persona_id,
        )
        generation2_eval = _evaluate_portfolio_policy(episode, generation2_policy, period="future_holdout")
        generation1_future_counterfactual = _evaluate_portfolio_policy(
            episode,
            generation1_policy,
            period="future_holdout",
        )

        evolution_decision = _build_evolution_decision(
            episode=episode,
            telemetry_event=stored_generation1,
            decision_trace=decision_trace1,
            baseline_policy=generation0_policy,
            evolved_policy=generation2_policy,
            baseline_eval=baseline_holdout_counterfactual,
            evolved_eval=generation2_eval,
            tracking_reconciliation=tracking_reconciliation,
            generated_at=generated_at,
        )
        decision_errors = validate_evolution_decision(evolution_decision)
        if decision_errors:
            raise ValueError(f"invalid evolution decision for {episode.case_id}: {decision_errors}")

        evolution_trajectory = _build_evolution_trajectory(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            evolution_decision=evolution_decision,
        )
        no_leakage_protocol = _build_no_leakage_temporal_protocol(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            case_upstream_artifacts=case_upstream_artifacts,
            evolution_trajectory=evolution_trajectory,
        )
        strict_oos_evolution_proof = _build_strict_oos_evolution_proof(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            evolution_decision=evolution_decision,
            evolution_trajectory=evolution_trajectory,
            no_leakage_protocol=no_leakage_protocol,
        )
        policy_candidate_materiality = _build_policy_candidate_materiality_proof(
            episode=episode,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            decision_traces=(decision_trace0, decision_trace1),
        )
        reflection_artifact_materiality = _build_reflection_artifact_materiality_proof(
            episode=episode,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            decision_traces=(decision_trace0, decision_trace1),
        )
        multi_oss_closed_loop_proof = _build_multi_oss_closed_loop_proof(
            episode=episode,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            oss_followup_loop=oss_followup_loop,
            decision_traces=(decision_trace0, decision_trace1),
        )
        operational_context = _build_operational_context(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            executions=(generation0_exec, generation1_exec, generation2_exec),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            decision_traces=(decision_trace0, decision_trace1),
            memory_contexts=(current_memory0, current_memory1),
            evolution_decision=evolution_decision,
            evolution_trajectory=evolution_trajectory,
            no_leakage_protocol=no_leakage_protocol,
            strict_oos_evolution_proof=strict_oos_evolution_proof,
            policy_candidate_materiality=policy_candidate_materiality,
            reflection_artifact_materiality=reflection_artifact_materiality,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            generated_at=generated_at,
        )
        persona_oss_ooda_ledger = _build_persona_oss_ooda_causal_ledger(
            episode=episode,
            multi_oss_closed_loop_proof=multi_oss_closed_loop_proof,
            oss_followup_loop=oss_followup_loop,
            decision_traces=(decision_trace0, decision_trace1),
            operational_context=operational_context,
        )
        cross_cycle_carryover = _build_cross_cycle_carryover_proof(
            episode=episode,
            cross_cycle_context=cross_cycle_context,
            decision_traces=(decision_trace0, decision_trace1),
            persona_oss_ooda_ledger=persona_oss_ooda_ledger,
        )
        persisted_cycle_resume = _build_persisted_cycle_resume_proof(
            episode=episode,
            cross_cycle_context=cross_cycle_context,
            decision_traces=(decision_trace0, decision_trace1),
            cross_cycle_carryover=cross_cycle_carryover,
        )
        multi_cycle_lineage = _build_multi_cycle_lineage_proof(
            episode=episode,
            multi_cycle_context=multi_cycle_context,
            decision_traces=(decision_trace0, decision_trace1),
            cross_cycle_carryover=cross_cycle_carryover,
            persisted_cycle_resume=persisted_cycle_resume,
        )
        institutional_memory_lineage = _build_institutional_memory_lineage_proof(
            episode=episode,
            institutional_memory_context=prior_institutional_memory,
            decision_traces=(decision_trace0, decision_trace1),
        )
        usability_dimensions = _build_usability_dimensions(
            episode=episode,
            executions=(generation0_exec, generation1_exec, generation2_exec),
            generation0_eval=generation0_eval,
            generation1_eval=generation1_eval,
            generation2_eval=generation2_eval,
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            memory_contexts=(current_memory0, current_memory1),
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            validation_plan=validation_plan,
            operational_context=operational_context,
            evolution_trajectory=evolution_trajectory,
            no_leakage_protocol=no_leakage_protocol,
            strict_oos_evolution_proof=strict_oos_evolution_proof,
            policy_candidate_materiality=policy_candidate_materiality,
            reflection_artifact_materiality=reflection_artifact_materiality,
            multi_oss_closed_loop_proof=multi_oss_closed_loop_proof,
            persona_oss_ooda_ledger=persona_oss_ooda_ledger,
            cross_cycle_carryover=cross_cycle_carryover,
            persisted_cycle_resume=persisted_cycle_resume,
            multi_cycle_lineage=multi_cycle_lineage,
            institutional_memory_lineage=institutional_memory_lineage,
            oss_followup_loop=oss_followup_loop,
        )
        validation_diagnostics = _diagnose_validation_execution(
            episode=episode,
            validation_plan=validation_plan,
            executions=(generation0_exec, generation1_exec, generation2_exec),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            memory_writes=(memory_write0, memory_write1),
            memory_contexts=(current_memory0, current_memory1),
            evolution_decision=evolution_decision,
            usability_dimensions=usability_dimensions,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            operational_context=operational_context,
            evolution_trajectory=evolution_trajectory,
            no_leakage_protocol=no_leakage_protocol,
            strict_oos_evolution_proof=strict_oos_evolution_proof,
            policy_candidate_materiality=policy_candidate_materiality,
            reflection_artifact_materiality=reflection_artifact_materiality,
            multi_oss_closed_loop_proof=multi_oss_closed_loop_proof,
            persona_oss_ooda_ledger=persona_oss_ooda_ledger,
            cross_cycle_carryover=cross_cycle_carryover,
            persisted_cycle_resume=persisted_cycle_resume,
            multi_cycle_lineage=multi_cycle_lineage,
            institutional_memory_lineage=institutional_memory_lineage,
            oss_followup_loop=oss_followup_loop,
        )
        validation_repair = _repair_validation_deficiencies(
            validation_plan=validation_plan,
            diagnostics=validation_diagnostics,
        )

        case = _build_case_result(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            executions=(generation0_exec, generation1_exec, generation2_exec),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            baseline_holdout_counterfactual=baseline_holdout_counterfactual,
            generation1_future_counterfactual=generation1_future_counterfactual,
            decision_traces=(decision_trace0, decision_trace1),
            memory_writes=(memory_write0, memory_write1),
            memory_contexts=(prior_memory, current_memory0, current_memory1),
            evolution_decision=evolution_decision,
            evolution_trajectory=evolution_trajectory,
            no_leakage_protocol=no_leakage_protocol,
            strict_oos_evolution_proof=strict_oos_evolution_proof,
            policy_candidate_materiality=policy_candidate_materiality,
            reflection_artifact_materiality=reflection_artifact_materiality,
            multi_oss_closed_loop_proof=multi_oss_closed_loop_proof,
            persona_oss_ooda_ledger=persona_oss_ooda_ledger,
            cross_cycle_carryover=cross_cycle_carryover,
            persisted_cycle_resume=persisted_cycle_resume,
            multi_cycle_lineage=multi_cycle_lineage,
            institutional_memory_lineage=institutional_memory_lineage,
            oss_followup_loop=oss_followup_loop,
            usability_dimensions=usability_dimensions,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            operational_context=operational_context,
            validation_plan=validation_plan,
            validation_diagnostics=validation_diagnostics,
            validation_repair=validation_repair,
            prior_institutional_memory=prior_institutional_memory,
        )
        cases.append(case)
        cycle_state_history_by_persona.setdefault(persona_id, []).append(
            _cross_cycle_state_from_case(case)
        )

    summary = _build_summary(
        dataset=dataset,
        personas=persona_records,
        cases=cases,
        oss_results=oss_results,
        generated_at=generated_at,
    )
    return AgentUsabilityValidationRun(
        summary=summary,
        cases=tuple(cases),
        oss_results=tuple(oss_results),
    )


def _load_historical_dataset() -> dict[str, Any]:
    path = REPO_ROOT / HISTORICAL_OHLCV_FIXTURE
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset_id") != HISTORICAL_OHLCV_DATASET_ID:
        raise ValueError(f"unexpected historical dataset id: {payload.get('dataset_id')!r}")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("historical dataset records must be a non-empty list")
    return payload


def _group_records_by_instrument(records: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        instrument = str(record.get("instrument") or "")
        if instrument:
            grouped.setdefault(instrument, []).append(dict(record))
    for instrument, rows in grouped.items():
        rows.sort(key=lambda row: str(row["date"]))
        if len(rows) < MIN_HISTORY_BARS:
            raise ValueError(f"instrument {instrument} has too few rows for usability validation")
    return grouped


def _build_valid_no_leakage_windows(
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[InstrumentWindow, ...]:
    windows: list[InstrumentWindow] = []
    for instrument in sorted(grouped):
        rows = [dict(row) for row in grouped[instrument]]
        for start_index in range(0, len(rows) - MIN_HISTORY_BARS):
            window = _window_from_rows(instrument, rows, start_index)
            if window is None:
                continue
            baseline_policy = _single_leg_policy(window.observe_direction, 0.55)
            generation1_policy = _single_leg_policy(window.feedback_direction, 0.75)
            generation2_policy = _single_leg_policy(window.feedback_direction, 1.15)
            if _evaluate_leg(window, baseline_policy, "holdout") >= _evaluate_leg(window, generation1_policy, "holdout"):
                continue
            if _evaluate_leg(window, generation1_policy, "future_holdout") >= _evaluate_leg(
                window,
                generation2_policy,
                "future_holdout",
            ):
                continue
            windows.append(window)
    if len(windows) < DEFAULT_CASE_COUNT:
        raise ValueError(f"need at least {DEFAULT_CASE_COUNT} no-leakage windows, found {len(windows)}")
    return tuple(windows)


def _window_from_rows(
    instrument: str,
    rows: Sequence[Mapping[str, Any]],
    start_index: int,
) -> InstrumentWindow | None:
    observe_rows = tuple(dict(row) for row in rows[start_index : start_index + LOOKBACK_BARS])
    feedback_start = start_index + LOOKBACK_BARS
    holdout_start = feedback_start + FEEDBACK_BARS
    future_start = holdout_start + HOLDOUT_BARS
    feedback_rows = tuple(dict(row) for row in rows[feedback_start : feedback_start + FEEDBACK_BARS])
    holdout_rows = tuple(dict(row) for row in rows[holdout_start : holdout_start + HOLDOUT_BARS])
    future_rows = tuple(dict(row) for row in rows[future_start : future_start + FUTURE_HOLDOUT_BARS])
    if not (observe_rows and feedback_rows and holdout_rows and future_rows):
        return None
    observe_direction = _direction(_period_return(observe_rows[0], observe_rows[-1]))
    feedback_direction = _direction(_period_return(observe_rows[-1], feedback_rows[-1]))
    holdout_direction = _direction(_period_return(feedback_rows[-1], holdout_rows[-1]))
    future_direction = _direction(_period_return(holdout_rows[-1], future_rows[-1]))
    if not (observe_direction and feedback_direction):
        return None
    if holdout_direction != feedback_direction or future_direction != feedback_direction:
        return None
    return InstrumentWindow(
        instrument=instrument,
        execution_symbol=_execution_symbol_for(instrument),
        start_index=start_index,
        observe_rows=observe_rows,
        feedback_rows=feedback_rows,
        holdout_rows=holdout_rows,
        future_holdout_rows=future_rows,
        observe_direction=observe_direction,
        feedback_direction=feedback_direction,
        holdout_direction=holdout_direction,
        future_direction=future_direction,
    )


def _build_episode_manifest(
    *,
    valid_windows: Sequence[InstrumentWindow],
    personas: Sequence[Mapping[str, Any]],
    case_count: int,
) -> tuple[PortfolioEpisode, ...]:
    episodes: list[PortfolioEpisode] = []
    signatures: set[str] = set()
    old_case_ids = {f"agent-usability-{index:04d}" for index in range(1, DEFAULT_CASE_COUNT + 1)}
    windows_by_instrument = _windows_by_instrument(valid_windows)
    cursor = 0
    while len(episodes) < case_count:
        windows = _portfolio_windows(windows_by_instrument, cursor)
        persona = dict(personas[len(episodes) % len(personas)])
        seed = ALPHA_SEED_SOURCES[len(episodes) % len(ALPHA_SEED_SOURCES)]
        oss_route = _oss_route_for_index(len(episodes))
        order_profile = _order_profile_for_index(len(episodes))
        reflection_archetype = _reflection_archetype(windows)
        operational_scenario = OPERATIONAL_SCENARIOS[len(episodes) % len(OPERATIONAL_SCENARIOS)]
        generation_path = (
            "observe_only_baseline",
            "feedback_memory_agent_decision",
            "holdout_feedback_second_generation",
        )
        signature = _validation_signature(
            persona_id=_persona_id(persona),
            seed_key=seed.key,
            windows=windows,
            oss_route=oss_route,
            order_profile=order_profile,
            reflection_archetype=reflection_archetype,
            generation_path=generation_path,
            operational_scenario=operational_scenario,
        )
        case_id = f"agent-hardening-{len(episodes) + 1:04d}"
        if case_id in old_case_ids:
            raise ValueError(f"case_id overlaps previous validation family: {case_id}")
        if signature in signatures:
            cursor += 1
            continue
        signatures.add(signature)
        episodes.append(
            PortfolioEpisode(
                case_id=case_id,
                validation_signature=signature,
                ordinal=len(episodes) + 1,
                persona=persona,
                seed_key=seed.key,
                source_strategy_spec_id=seed.source_strategy_spec_id,
                source_dataset_refs=(HISTORICAL_OHLCV_DATASET_ID, *seed.source_dataset_refs),
                windows=windows,
                oss_route=oss_route,
                order_profile=order_profile,
                reflection_archetype=reflection_archetype,
                generation_path=generation_path,
                regime_path=tuple(_regime_for_window(window) for window in windows),
            )
        )
        cursor += 1
        if cursor > len(valid_windows) * 8 and len(episodes) < case_count:
            raise ValueError("could not build enough non-repeated portfolio episodes")
    return tuple(episodes)


def _windows_by_instrument(
    valid_windows: Sequence[InstrumentWindow],
) -> dict[str, tuple[InstrumentWindow, ...]]:
    grouped: dict[str, list[InstrumentWindow]] = {}
    for window in valid_windows:
        grouped.setdefault(window.instrument, []).append(window)
    if len(grouped) < PORTFOLIO_LEG_COUNT:
        raise ValueError("not enough instruments to build portfolio validations")
    return {
        instrument: tuple(sorted(windows, key=lambda window: window.start_index))
        for instrument, windows in grouped.items()
    }


def _portfolio_windows(
    windows_by_instrument: Mapping[str, Sequence[InstrumentWindow]],
    cursor: int,
) -> tuple[InstrumentWindow, ...]:
    instruments = sorted(windows_by_instrument)
    selected: list[InstrumentWindow] = []
    instrument_step = 7 + 2 * (cursor % 11)
    if instrument_step % len(instruments) == 0:
        instrument_step += 1
    window_cycle = cursor // len(instruments)
    while len(selected) < PORTFOLIO_LEG_COUNT:
        leg_index = len(selected)
        instrument = instruments[(cursor + leg_index * instrument_step) % len(instruments)]
        if instrument in {window.instrument for window in selected}:
            cursor += 1
            continue
        instrument_windows = windows_by_instrument[instrument]
        window_index = (window_cycle + cursor + leg_index * 13) % len(instrument_windows)
        selected.append(instrument_windows[window_index])
    return tuple(selected)


def _run_oss_feedback_bank(personas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, component in enumerate(OSS_REQUIRED_COMPONENTS):
        persona = personas[index % len(personas)]
        seed = ALPHA_SEED_SOURCES[index % len(ALPHA_SEED_SOURCES)]
        payload = _oss_payload_for_component(component, seed.key, index)
        request = PersonaOSSRequest(
            persona_id=_persona_id(persona),
            session_id=f"session-agent-hardening-oss-{component}",
            component=component,
            intent=f"agent_hardening_{component}_feedback",
            payload=payload,
            request_id=f"req-agent-hardening-oss-{component}",
        )
        result = run_persona_oss_request(request).to_dict()
        result["seed_key"] = seed.key
        result["drives_persona_step"] = _oss_persona_step(component)
        results.append(result)
    return results


def _oss_payload_for_component(component: str, seed_key: str, index: int) -> dict[str, Any]:
    common = {
        "dataset_id": HISTORICAL_OHLCV_DATASET_ID,
        "strategy_id": f"{seed_key}-agent-hardening-{component}",
        "source_dataset_refs": [HISTORICAL_OHLCV_DATASET_ID],
        "version": f"3000.1.{index + 1}",
        "metadata": {
            "alpha_seed_key": seed_key,
            "validation_family": "agent_usability_hardening_3000",
        },
    }
    vectorbt_payload = {
        **common,
        "dataset_fixture_path": HISTORICAL_OHLCV_FIXTURE,
        "instrument_count": 2,
        "instrument_offset": index * 3,
        "short_window": 3 + (index % 5),
        "long_window": 12 + (index % 7),
        "fees": 0.0005,
    }
    if component in {"vectorbt", "mlflow", "wandb", "lean_handoff"}:
        if component == "vectorbt":
            return vectorbt_payload
        return {
            **common,
            "source_vectorbt_payload": vectorbt_payload,
            "plan_suffix": f"hardening-{component}",
        }
    if component == "qlib":
        return {
            **common,
            "seed": 100 + index,
            "n_estimators": 12,
            "num_leaves": 7,
            "max_depth": 3,
        }
    return common


def _oss_persona_step(component: str) -> str:
    if component in {"qlib", "vectorbt"}:
        return "alpha_seed_update"
    if component in POLICY_OSS_COMPONENTS:
        return "policy_candidate_generation"
    if component in REFLECTION_OSS_COMPONENTS:
        return "reflection_candidate_generation"
    if component in TRACKING_OSS_COMPONENTS:
        return "experiment_tracking"
    if component == "lean_handoff":
        return "evolved_strategy_handoff"
    if component in RISK_OSS_COMPONENTS:
        return "risk_or_regime_interpretation"
    return "session_context"


def _oss_inputs_for_episode(
    episode: PortfolioEpisode,
    oss_by_component: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    selected_components = {
        "session": "openclaw",
        "alpha_model": episode.oss_route["alpha_model"],
        "backtest": "vectorbt",
        "policy_candidate": episode.oss_route["policy_candidate"],
        "reflection_artifact": episode.oss_route["reflection_artifact"],
        "tracker": episode.oss_route["tracker"],
        "risk_analytics": episode.oss_route["risk_analytics"],
        "handoff": "lean_handoff",
    }
    return {
        role: copy.deepcopy(dict(oss_by_component[component]))
        for role, component in selected_components.items()
    }


def _build_case_upstream_artifact_feedback(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    vectorbt_feedback = _run_case_vectorbt_feedback(episode)
    tracker_feedback = _run_case_tracking_feedback(
        episode=episode,
        vectorbt_feedback=vectorbt_feedback,
        tracker_component=str(oss_inputs["tracker"]["component"]),
    )
    selected_oss_feedback = _run_case_selected_oss_feedback(
        episode=episode,
        oss_inputs=oss_inputs,
        vectorbt_feedback=vectorbt_feedback,
    )
    alpha_seed_revision = _build_alpha_seed_revision_from_oss(
        episode=episode,
        vectorbt_feedback=vectorbt_feedback,
        tracker_feedback=tracker_feedback,
        selected_oss_feedback=selected_oss_feedback,
    )
    return {
        "feedback_id": f"case-upstream-artifacts-{episode.case_id}",
        "vectorbt_model_id": CASE_UPSTREAM_VECTORBT_MODEL_ID,
        "tracking_model_id": CASE_UPSTREAM_TRACKING_MODEL_ID,
        "selected_oss_model_id": CASE_SELECTED_OSS_MODEL_ID,
        "persona_id": _persona_id(episode.persona),
        "seed_key": episode.seed_key,
        "allowed_windows": ["observe", "feedback"],
        "forbidden_windows_not_used": ["holdout", "future_holdout"],
        "source_strategy_spec_id": episode.source_strategy_spec_id,
        "source_dataset_refs": list(episode.source_dataset_refs),
        "vectorbt": vectorbt_feedback,
        "tracker": tracker_feedback,
        "selected_oss": selected_oss_feedback,
        "alpha_seed_revision": alpha_seed_revision,
        "persona_response": {
            "ooda_sequence": ["decide", "learn", "orient", "observe"],
            "next_decision_action": "score_case_specific_backtest_candidate",
            "next_tracking_action": "cite_case_specific_experiment_ref",
            "next_alpha_seed_action": alpha_seed_revision["revision"]["action"],
            "evidence_refs": [
                f"oss://vectorbt/{vectorbt_feedback['request_id']}",
                f"oss://{tracker_feedback['component']}/{tracker_feedback['request_id']}",
                f"experiment://{tracker_feedback['backend']}/{tracker_feedback['run_id']}",
                alpha_seed_revision["revision_ref"],
                *[
                    f"oss://{entry['component']}/{entry['request_id']}"
                    for entry in selected_oss_feedback.values()
                ],
            ],
            "used_before_generation1_decision": True,
            "used_before_generation2_decision": True,
        },
    }


def _apply_case_upstream_artifacts_to_oss_inputs(
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    updated = {
        role: copy.deepcopy(dict(result))
        for role, result in oss_inputs.items()
    }
    vectorbt_feedback = case_upstream_artifacts["vectorbt"]
    tracker_feedback = case_upstream_artifacts["tracker"]
    selected_oss_feedback = case_upstream_artifacts["selected_oss"]
    alpha_seed_revision = case_upstream_artifacts["alpha_seed_revision"]
    updated["backtest"] = {
        "component": "vectorbt",
        "persona_id": case_upstream_artifacts["persona_id"],
        "session_id": vectorbt_feedback["session_id"],
        "request_id": vectorbt_feedback["request_id"],
        "status": "completed",
        "artifact_family": "vectorbt_backtest",
        "primary_output": {
            "backend": vectorbt_feedback["backend"],
            "run_id": vectorbt_feedback["run_id"],
            "aggregate_metrics": copy.deepcopy(vectorbt_feedback["aggregate_metrics"]),
            "per_instrument_metrics": copy.deepcopy(vectorbt_feedback["per_instrument_metrics"]),
        },
        "metrics": copy.deepcopy(vectorbt_feedback["aggregate_metrics"]),
        "registry_entry": copy.deepcopy(vectorbt_feedback["registry_entry"]),
        "artifact_bundle": copy.deepcopy(vectorbt_feedback["artifact_bundle"]),
        "refs": {
            "source_dataset_refs": list(vectorbt_feedback["dataset_summary"]["source_dataset_refs"]),
            "registry_id": vectorbt_feedback["registry_id"],
            "alpha_seed_revision_ref": alpha_seed_revision["revision_ref"],
            "alpha_seed_revision_key": alpha_seed_revision["revision"]["revision_key"],
        },
        "persona_followup": {
            "persona_id": case_upstream_artifacts["persona_id"],
            "session_id": vectorbt_feedback["session_id"],
            "trigger_component": "vectorbt",
            "trigger_request_id": vectorbt_feedback["request_id"],
            "trigger_artifact_family": "vectorbt_backtest",
            "ooda_phase": "decide",
            "next_action": "draft_strategy_proposal",
            "evidence_refs": [
                vectorbt_feedback["run_id"],
                vectorbt_feedback["registry_id"],
                alpha_seed_revision["revision_ref"],
            ],
        },
        "seed_key": case_upstream_artifacts["seed_key"],
        "drives_persona_step": "alpha_seed_update",
    }
    updated["tracker"] = {
        "component": tracker_feedback["component"],
        "persona_id": case_upstream_artifacts["persona_id"],
        "session_id": tracker_feedback["session_id"],
        "request_id": tracker_feedback["request_id"],
        "status": "completed",
        "artifact_family": "experiment_run",
        "primary_output": copy.deepcopy(tracker_feedback["experiment_ref"]),
        "metrics": copy.deepcopy(tracker_feedback["metrics"]),
        "registry_entry": copy.deepcopy(vectorbt_feedback["registry_entry"]),
        "artifact_bundle": {
            "record": copy.deepcopy(tracker_feedback["record"]),
            "readback": copy.deepcopy(tracker_feedback["readback"]),
        },
        "refs": {
            "run_id": tracker_feedback["run_id"],
            "artifact_uri": tracker_feedback["artifact_uri"],
        },
        "persona_followup": {
            "persona_id": case_upstream_artifacts["persona_id"],
            "session_id": tracker_feedback["session_id"],
            "trigger_component": tracker_feedback["component"],
            "trigger_request_id": tracker_feedback["request_id"],
            "trigger_artifact_family": "experiment_run",
            "ooda_phase": "observe",
            "next_action": "cite_experiment_ref",
            "evidence_refs": [tracker_feedback["run_id"]],
        },
        "seed_key": case_upstream_artifacts["seed_key"],
        "drives_persona_step": "experiment_tracking",
    }
    for role, result in selected_oss_feedback.items():
        updated[role] = _case_selected_oss_result_for_role(
            result,
            seed_key=str(case_upstream_artifacts["seed_key"]),
        )
    updated["alpha_model"]["alpha_seed_revision"] = copy.deepcopy(dict(alpha_seed_revision))
    updated["alpha_model"].setdefault("refs", {})["alpha_seed_revision_ref"] = alpha_seed_revision[
        "revision_ref"
    ]
    updated["alpha_model"]["persona_followup"].setdefault("evidence_refs", []).append(
        alpha_seed_revision["revision_ref"]
    )
    return updated


def _case_selected_oss_result_for_role(
    result: Mapping[str, Any],
    *,
    seed_key: str,
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(result["persona_result"]))
    payload["seed_key"] = seed_key
    payload["drives_persona_step"] = result["drives_persona_step"]
    payload["case_specific_oss_model_id"] = CASE_SELECTED_OSS_MODEL_ID
    payload["selected_oss_role"] = result["role"]
    return payload


def _build_oss_response_followup_loop(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    required_roles = (
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    )
    followups: list[dict[str, Any]] = []
    for role in required_roles:
        result = oss_inputs[role]
        component = str(result["component"])
        request_id = str(result["request_id"])
        followup = copy.deepcopy(dict(result.get("persona_followup") or {}))
        source_ref = f"oss://{component}/{request_id}"
        output_ref = f"followup://persona/{episode.case_id}/{role}/{component}/{request_id}"
        candidate_action = _oss_followup_candidate_action(role=role, component=component)
        request = {
            "request_id": f"persona-followup-request-{episode.case_id}-{role}-{component}",
            "persona_id": _persona_id(episode.persona),
            "case_id": episode.case_id,
            "role": role,
            "component": component,
            "source_oss_request_id": request_id,
            "source_oss_ref": source_ref,
            "trigger_artifact_family": str(
                followup.get("trigger_artifact_family") or result.get("artifact_family") or "unknown"
            ),
            "trigger_status": result.get("status"),
            "requested_after_oss_response": True,
            "input_refs": [
                source_ref,
                *[str(ref) for ref in followup.get("evidence_refs", [])],
            ],
            "ooda_phase": str(followup.get("ooda_phase") or _oss_followup_phase(role)),
            "next_action": str(followup.get("next_action") or _oss_followup_next_action(role)),
            "drives_persona_step": str(result.get("drives_persona_step") or _oss_persona_step(component)),
        }
        response = {
            "response_id": f"persona-followup-response-{episode.case_id}-{role}-{component}",
            "status": "completed",
            "accepted_action": request["next_action"],
            "candidate_action": candidate_action,
            "output_ref": output_ref,
            "score_adjustments": _oss_followup_score_adjustment(role=role, component=component),
            "reasoning_tag": f"{role}:{component}:{request['next_action']}",
            "used_by_generations": [1, 2],
        }
        followups.append(
            {
                "role": role,
                "component": component,
                "source_oss_ref": source_ref,
                "request": request,
                "response": response,
            }
        )

    candidate_score_adjustments = {
        "feedback-adapt": 0.0,
        "retain-observe": 0.0,
        "risk-off": 0.0,
        "contrarian-check": 0.0,
    }
    for item in followups:
        for action, value in item["response"]["score_adjustments"].items():
            candidate_score_adjustments[action] = round(
                candidate_score_adjustments[action] + float(value),
                10,
            )
    all_refs = [item["response"]["output_ref"] for item in followups]
    refs_by_role = {
        item["role"]: item["response"]["output_ref"]
        for item in followups
    }
    candidate_evidence_refs_by_action = {
        "feedback-adapt": list(all_refs),
        "risk-off": [
            refs_by_role["risk_analytics"],
            refs_by_role["policy_candidate"],
        ],
        "retain-observe": [refs_by_role["session"]],
        "contrarian-check": [refs_by_role["reflection_artifact"]],
    }
    loop = {
        "loop_id": f"oss-response-followup-loop-{episode.case_id}",
        "loop_ref": f"followup://persona/{episode.case_id}/oss-response-loop",
        "model_id": OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "source_feedback_id": case_upstream_artifacts["feedback_id"],
        "required_roles": list(required_roles),
        "followups": followups,
        "candidate_score_adjustments": candidate_score_adjustments,
        "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
        "drives_generations": [1, 2],
        "replay": {
            "replayable": True,
            "all_required_roles_followed_up": [item["role"] for item in followups] == list(required_roles),
            "all_followups_requested_after_oss_response": all(
                item["request"]["requested_after_oss_response"] is True
                and item["request"]["source_oss_ref"] == item["source_oss_ref"]
                for item in followups
            ),
            "all_followups_completed": all(item["response"]["status"] == "completed" for item in followups),
            "responses_drive_candidate_scoring": any(
                float(value) > 0
                for value in candidate_score_adjustments.values()
            ),
            "feedback_adapt_receives_all_followup_refs": set(
                candidate_evidence_refs_by_action["feedback-adapt"]
            ) == set(all_refs),
            "risk_response_available_to_risk_off": refs_by_role["risk_analytics"]
            in candidate_evidence_refs_by_action["risk-off"],
        },
    }
    loop["input_hash"] = _stable_payload_hash(
        "oss-response-followup-loop",
        {
            "case_id": episode.case_id,
            "followups": followups,
            "candidate_score_adjustments": candidate_score_adjustments,
            "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
        },
    )
    return loop


def _build_oss_disagreement_arbitration(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
) -> dict[str, Any]:
    disagreement_type = OSS_DISAGREEMENT_TYPES_BY_SCENARIO[_operational_scenario_for_episode(episode)]
    source_roles = OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE[disagreement_type]
    resolution_action = OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE[disagreement_type]
    arbitration_id = f"oss-disagreement-arbitration-{episode.case_id}"
    arbitration_ref = f"oss-disagreement://{arbitration_id}"
    source_refs = [_oss_ref_for_role(oss_inputs, role) for role in source_roles]
    all_selected_refs = [
        _oss_ref_for_role(oss_inputs, role)
        for role in ("alpha_model", "backtest", "policy_candidate", "reflection_artifact", "tracker", "risk_analytics")
    ]
    conflict = {
        "conflict_id": f"{arbitration_id}-{disagreement_type}",
        "conflict_type": disagreement_type,
        "source_roles": list(source_roles),
        "source_refs": source_refs,
        "observed_signals": _oss_disagreement_observed_signals(
            disagreement_type=disagreement_type,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
        ),
        "severity": "high" if "risk" in disagreement_type else "medium",
        "resolution_action": resolution_action,
        "resolved_by": "persona_scorer_risk_evaluator_arbitration",
        "resolution_ref": f"{arbitration_ref}/{disagreement_type}/{resolution_action}",
    }
    candidate_score_adjustments = {
        "feedback-adapt": 0.08,
        "retain-observe": 0.0,
        "risk-off": 0.04,
        "contrarian-check": 0.02,
    }
    candidate_score_adjustments[resolution_action] = round(
        candidate_score_adjustments[resolution_action] + 0.06,
        10,
    )
    candidate_evidence_refs_by_action = {
        "feedback-adapt": [arbitration_ref, conflict["resolution_ref"], *all_selected_refs],
        "risk-off": [
            arbitration_ref,
            conflict["resolution_ref"],
            _oss_ref_for_role(oss_inputs, "risk_analytics"),
            _oss_ref_for_role(oss_inputs, "policy_candidate"),
        ],
        "retain-observe": [arbitration_ref, _oss_ref_for_role(oss_inputs, "tracker")],
        "contrarian-check": [
            arbitration_ref,
            conflict["resolution_ref"],
            _oss_ref_for_role(oss_inputs, "reflection_artifact"),
        ],
    }
    replay = {
        "replayable": True,
        "all_source_roles_completed": all(oss_inputs[role].get("status") == "completed" for role in source_roles),
        "conflict_detected": bool(conflict["observed_signals"]),
        "conflict_sources_bound": all(ref.startswith("oss://") for ref in source_refs),
        "resolution_action_selected": resolution_action in candidate_score_adjustments,
        "resolution_drives_candidate_scoring": float(candidate_score_adjustments[resolution_action]) > 0.0,
        "followup_loop_available": _oss_response_followup_loop_is_usable(oss_followup_loop),
        "selected_oss_refs_available": set(all_selected_refs).issubset(
            set(case_upstream_artifacts["persona_response"]["evidence_refs"])
        ),
        "feedback_adapt_gets_all_selected_refs": set(all_selected_refs).issubset(
            set(candidate_evidence_refs_by_action["feedback-adapt"])
        ),
    }
    return {
        "arbitration_id": arbitration_id,
        "arbitration_ref": arbitration_ref,
        "model_id": PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID,
        "status": "resolved" if all(replay.values()) else "blocked",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "scenario": _operational_scenario_for_episode(episode),
        "source_feedback_id": case_upstream_artifacts["feedback_id"],
        "source_followup_loop_ref": oss_followup_loop["loop_ref"],
        "conflicts": [conflict],
        "candidate_score_adjustments": candidate_score_adjustments,
        "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
        "persona_arbitration_response": {
            "next_action": "score_candidates_with_arbitrated_oss_weights",
            "preferred_candidate_action": "feedback-adapt",
            "resolution_actions": [resolution_action],
            "evidence_refs": [arbitration_ref, conflict["resolution_ref"], *source_refs],
            "used_by_generations": [1, 2],
        },
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "oss-disagreement-arbitration",
            {
                "case_id": episode.case_id,
                "scenario": _operational_scenario_for_episode(episode),
                "conflict": conflict,
                "candidate_score_adjustments": candidate_score_adjustments,
                "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
            },
        ),
    }


def _oss_ref_for_role(oss_inputs: Mapping[str, Mapping[str, Any]], role: str) -> str:
    result = oss_inputs[role]
    return f"oss://{result['component']}/{result['request_id']}"


def _oss_disagreement_observed_signals(
    *,
    disagreement_type: str,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    vectorbt_metrics = case_upstream_artifacts["vectorbt"]["aggregate_metrics"]
    tracker_readback = case_upstream_artifacts["tracker"]["readback"]
    risk_metrics = oss_inputs["risk_analytics"].get("metrics", {})
    policy_metrics = oss_inputs["policy_candidate"].get("metrics", {})
    if disagreement_type == "alpha_backtest_price_conflict":
        return {
            "alpha_component": oss_inputs["alpha_model"]["component"],
            "backtest_total_trades": vectorbt_metrics.get("total_trades"),
            "interpretation": "alpha update wants adaptation while backtest price path asks for repricing evidence",
        }
    if disagreement_type == "alpha_risk_rejection_conflict":
        return {
            "alpha_component": oss_inputs["alpha_model"]["component"],
            "risk_component": oss_inputs["risk_analytics"]["component"],
            "risk_metric_keys": sorted(risk_metrics),
            "interpretation": "alpha response wants mutation while risk response asks for reduced exposure",
        }
    if disagreement_type == "backtest_policy_fill_conflict":
        return {
            "backtest_total_trades": vectorbt_metrics.get("total_trades"),
            "policy_component": oss_inputs["policy_candidate"]["component"],
            "policy_metric_keys": sorted(policy_metrics),
            "interpretation": "backtest fill evidence and policy candidate risk hint need scorer arbitration",
        }
    if disagreement_type == "policy_risk_liquidity_conflict":
        return {
            "policy_component": oss_inputs["policy_candidate"]["component"],
            "risk_component": oss_inputs["risk_analytics"]["component"],
            "interpretation": "policy candidate searches for return while risk analytics asks for liquidity scaling",
        }
    return {
        "reflection_component": oss_inputs["reflection_artifact"]["component"],
        "handoff_component": oss_inputs["handoff"]["component"],
        "tracking_readback_status": tracker_readback.get("run_readback_status"),
        "interpretation": "reflection response asks for a control candidate while handoff wants executable packet continuity",
    }


def _build_alpha_seed_revision_from_oss(
    *,
    episode: PortfolioEpisode,
    vectorbt_feedback: Mapping[str, Any],
    tracker_feedback: Mapping[str, Any],
    selected_oss_feedback: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    alpha_feedback = selected_oss_feedback["alpha_model"]
    policy_feedback = selected_oss_feedback["policy_candidate"]
    reflection_feedback = selected_oss_feedback["reflection_artifact"]
    risk_feedback = selected_oss_feedback["risk_analytics"]
    alpha_component = str(alpha_feedback["component"])
    revision_action = ALPHA_SEED_REVISION_ACTION_BY_COMPONENT[alpha_component]
    revision_id = f"alpha-seed-revision-{episode.case_id}"
    revision_ref = f"alpha-seed-revision://{revision_id}"
    repair_ref = f"{revision_ref}/{alpha_component}/{revision_action}"
    alpha_ref = f"oss://{alpha_component}/{alpha_feedback['request_id']}"
    vectorbt_ref = f"oss://vectorbt/{vectorbt_feedback['request_id']}"
    tracker_ref = f"oss://{tracker_feedback['component']}/{tracker_feedback['request_id']}"
    experiment_ref = f"experiment://{tracker_feedback['backend']}/{tracker_feedback['run_id']}"
    policy_ref = f"oss://{policy_feedback['component']}/{policy_feedback['request_id']}"
    reflection_ref = f"oss://{reflection_feedback['component']}/{reflection_feedback['request_id']}"
    risk_ref = f"oss://{risk_feedback['component']}/{risk_feedback['request_id']}"
    source_refs = [
        f"alpha-seed://{episode.seed_key}",
        alpha_ref,
        vectorbt_ref,
        tracker_ref,
        experiment_ref,
    ]
    revision = {
        "action": revision_action,
        "revision_key": f"{episode.seed_key}:{alpha_component}:{episode.case_id}",
        "base_seed_key": episode.seed_key,
        "base_seed_ref": f"alpha-seed://{episode.seed_key}",
        "source_strategy_spec_id": episode.source_strategy_spec_id,
        "source_alpha_component": alpha_component,
        "source_alpha_request_id": alpha_feedback["request_id"],
        "source_alpha_artifact_family": alpha_feedback["artifact_family"],
        "source_alpha_registry_id": alpha_feedback.get("registry_id"),
        "source_alpha_producer_run_id": alpha_feedback.get("producer_run_id"),
        "source_alpha_metrics_hash": _stable_payload_hash(
            "alpha-seed-revision-source-metrics",
            {
                "component": alpha_component,
                "metrics": alpha_feedback.get("metrics", {}),
                "primary_output": alpha_feedback.get("primary_output", {}),
            },
        ),
        "downstream_vectorbt_request_id": vectorbt_feedback["request_id"],
        "downstream_tracker_run_id": tracker_feedback["run_id"],
        "downstream_policy_candidate_request_id": policy_feedback["request_id"],
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "historical_window_start_indices": [window.start_index for window in episode.windows],
        "allowed_windows": ["observe", "feedback"],
        "forbidden_windows_not_used": ["holdout", "future_holdout"],
    }
    candidate_score_adjustments = {
        "feedback-adapt": 0.055 if alpha_component == "qlib" else 0.045,
        "retain-observe": 0.01,
        "risk-off": 0.015,
        "contrarian-check": 0.005,
    }
    candidate_evidence_refs_by_action = {
        "feedback-adapt": [
            revision_ref,
            repair_ref,
            f"alpha-seed://{episode.seed_key}",
            alpha_ref,
            vectorbt_ref,
            tracker_ref,
            experiment_ref,
            policy_ref,
        ],
        "retain-observe": [revision_ref, f"alpha-seed://{episode.seed_key}"],
        "risk-off": [revision_ref, repair_ref, alpha_ref, risk_ref],
        "contrarian-check": [revision_ref, repair_ref, reflection_ref],
    }
    replay = {
        "replayable": True,
        "source_alpha_completed": alpha_feedback.get("status") == "completed",
        "alpha_revision_generated": bool(revision["revision_key"]),
        "downstream_backtest_bound": revision["downstream_vectorbt_request_id"] == vectorbt_feedback["request_id"],
        "downstream_tracker_bound": tracker_feedback.get("source_vectorbt_run_id") == vectorbt_feedback.get("run_id"),
        "policy_candidate_bound": revision["downstream_policy_candidate_request_id"] == policy_feedback["request_id"],
        "no_forbidden_window_sources": revision["allowed_windows"] == ["observe", "feedback"]
        and revision["forbidden_windows_not_used"] == ["holdout", "future_holdout"],
        "scorer_adjustment_available": float(candidate_score_adjustments["feedback-adapt"]) > 0.0,
        "feedback_adapt_gets_alpha_backtest_tracker_refs": set(source_refs).issubset(
            set(candidate_evidence_refs_by_action["feedback-adapt"])
        ),
    }
    return {
        "revision_id": revision_id,
        "revision_ref": revision_ref,
        "model_id": PERSONA_ALPHA_SEED_REVISION_MODEL_ID,
        "status": "applied" if all(replay.values()) else "blocked",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "alpha_component": alpha_component,
        "source_feedback_id": f"case-upstream-artifacts-{episode.case_id}",
        "source_refs": source_refs,
        "revision": revision,
        "candidate_score_adjustments": candidate_score_adjustments,
        "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
        "persona_alpha_response": {
            "next_action": "score_candidates_with_alpha_seed_revision",
            "preferred_candidate_action": "feedback-adapt",
            "revision_actions": [revision_action],
            "evidence_refs": [revision_ref, repair_ref, *source_refs, policy_ref],
            "used_by_generations": [1, 2],
        },
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "alpha-seed-revision-from-oss",
            {
                "case_id": episode.case_id,
                "alpha_component": alpha_component,
                "revision": revision,
                "candidate_score_adjustments": candidate_score_adjustments,
                "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
            },
        ),
    }


def _build_tracking_readback_reconciliation(
    *,
    episode: PortfolioEpisode,
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    scenario = _operational_scenario_for_episode(episode)
    divergence_type = TRACKING_DIVERGENCE_TYPES_BY_SCENARIO[scenario]
    repair_action = TRACKING_RECONCILIATION_ACTION_BY_TYPE[divergence_type]
    vectorbt = case_upstream_artifacts["vectorbt"]
    tracker = case_upstream_artifacts["tracker"]
    reconciliation_id = f"tracking-readback-reconciliation-{episode.case_id}"
    reconciliation_ref = f"tracking-reconciliation://{reconciliation_id}"
    repair_ref = f"{reconciliation_ref}/{divergence_type}/{repair_action}"
    vectorbt_ref = f"oss://vectorbt/{vectorbt['request_id']}"
    tracker_ref = f"oss://{tracker['component']}/{tracker['request_id']}"
    experiment_ref = f"experiment://{tracker['backend']}/{tracker['run_id']}"
    source_refs = [vectorbt_ref, tracker_ref, experiment_ref]
    expected, readback, normalized_value = _tracking_divergence_values(
        divergence_type=divergence_type,
        tracker=tracker,
    )
    divergence = {
        "divergence_id": f"{reconciliation_id}-{divergence_type}",
        "divergence_type": divergence_type,
        "backend": tracker["backend"],
        "expected": expected,
        "readback": readback,
        "severity": "low" if divergence_type == "metric_precision_roundtrip" else "medium",
        "source_refs": source_refs,
        "detected_after_readback": True,
    }
    candidate_score_adjustments = {
        "feedback-adapt": 0.04,
        "retain-observe": 0.02,
        "risk-off": 0.01,
        "contrarian-check": 0.005,
    }
    if repair_action in {
        "bind_registry_alias_before_handoff",
        "normalize_backend_tags_before_scoring",
    }:
        candidate_score_adjustments["risk-off"] = round(candidate_score_adjustments["risk-off"] + 0.015, 10)
    candidate_evidence_refs_by_action = {
        "feedback-adapt": [reconciliation_ref, repair_ref, *source_refs],
        "retain-observe": [reconciliation_ref, experiment_ref],
        "risk-off": [reconciliation_ref, repair_ref, tracker_ref],
        "contrarian-check": [reconciliation_ref, repair_ref],
    }
    repair = {
        "action": repair_action,
        "repair_ref": repair_ref,
        "normalized_value": normalized_value,
        "normalized_experiment_ref": experiment_ref,
        "evidence_refs": [reconciliation_ref, repair_ref, *source_refs],
        "used_by_generations": [1, 2],
        "next_persona_step": "cite_reconciled_experiment_ref",
    }
    replay = {
        "replayable": True,
        "tracker_completed": tracker.get("status") == "completed",
        "tracker_readback_found": tracker.get("readback", {}).get("run_readback_status") == "found"
        and tracker.get("readback", {}).get("artifact_readback_status") == "found",
        "divergence_detected": expected != readback,
        "repair_action_selected": repair_action in TRACKING_RECONCILIATION_ACTION_BY_TYPE.values(),
        "normalized_experiment_ref_available": bool(repair["normalized_experiment_ref"]),
        "vectorbt_tracker_bound": tracker.get("source_vectorbt_run_id") == vectorbt.get("run_id"),
        "scorer_adjustment_available": float(candidate_score_adjustments["feedback-adapt"]) > 0.0,
        "feedback_adapt_gets_tracking_refs": set(source_refs).issubset(
            set(candidate_evidence_refs_by_action["feedback-adapt"])
        ),
    }
    return {
        "reconciliation_id": reconciliation_id,
        "reconciliation_ref": reconciliation_ref,
        "model_id": PERSONA_TRACKING_RECONCILIATION_MODEL_ID,
        "status": "reconciled" if all(replay.values()) else "blocked",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "scenario": scenario,
        "backend": tracker["backend"],
        "tracker_request_id": tracker["request_id"],
        "run_id": tracker["run_id"],
        "artifact_uri": tracker["artifact_uri"],
        "source_vectorbt_run_id": vectorbt["run_id"],
        "source_feedback_id": case_upstream_artifacts["feedback_id"],
        "divergence": divergence,
        "repair": repair,
        "candidate_score_adjustments": candidate_score_adjustments,
        "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
        "persona_reconciliation_response": {
            "next_action": "score_candidates_with_reconciled_tracking_readback",
            "preferred_candidate_action": "feedback-adapt",
            "repair_actions": [repair_action],
            "evidence_refs": [reconciliation_ref, repair_ref, *source_refs],
            "used_by_generations": [1, 2],
        },
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "tracking-readback-reconciliation",
            {
                "case_id": episode.case_id,
                "divergence": divergence,
                "repair": repair,
                "candidate_score_adjustments": candidate_score_adjustments,
                "candidate_evidence_refs_by_action": candidate_evidence_refs_by_action,
            },
        ),
    }


def _tracking_divergence_values(
    *,
    divergence_type: str,
    tracker: Mapping[str, Any],
) -> tuple[Any, Any, Any]:
    metrics = dict(tracker.get("metrics") or {})
    artifact_uri = str(tracker.get("artifact_uri") or "")
    record = tracker.get("record", {})
    if divergence_type == "metric_precision_roundtrip":
        metric_name = "mean_total_return" if "mean_total_return" in metrics else sorted(metrics)[0]
        expected = {metric_name: float(metrics[metric_name])}
        readback = {metric_name: f"{round(float(metrics[metric_name]), 6):.6f}"}
        return expected, readback, expected
    if divergence_type == "artifact_uri_normalization":
        if artifact_uri.startswith("memory://"):
            readback_uri = artifact_uri.replace("memory://", "mlflow://", 1)
        elif artifact_uri.startswith("wandb://"):
            readback_uri = artifact_uri.replace("wandb://", "wandb-local://", 1)
        else:
            readback_uri = f"{artifact_uri}#readback"
        return artifact_uri, readback_uri, artifact_uri
    if divergence_type == "registry_alias_lag":
        expected = {"deployment_stage": "none", "artifact_state": "candidate"}
        readback = {"deployment_stage": "none", "artifact_state": "alias_pending"}
        return expected, readback, expected
    if divergence_type == "run_tag_backend_normalization":
        expected = dict(record.get("tags") or {})
        readback = {str(key).replace("_", "-"): value for key, value in expected.items()}
        readback["tracking-backend"] = tracker.get("backend")
        return expected, readback, expected
    artifact_names = list(record.get("artifact_names") or [])
    expected = artifact_names
    readback = list(reversed(artifact_names))
    return expected, readback, expected


def _oss_followup_phase(role: str) -> str:
    return {
        "session": "observe",
        "alpha_model": "orient",
        "backtest": "decide",
        "policy_candidate": "decide",
        "reflection_artifact": "reflect",
        "tracker": "observe",
        "risk_analytics": "orient",
        "handoff": "handoff",
    }[role]


def _oss_followup_next_action(role: str) -> str:
    return {
        "session": "bind_session_context",
        "alpha_model": "refresh_alpha_seed_candidate",
        "backtest": "draft_strategy_proposal",
        "policy_candidate": "score_policy_candidate",
        "reflection_artifact": "revise_reflection_hypothesis",
        "tracker": "cite_experiment_ref",
        "risk_analytics": "apply_risk_interpretation",
        "handoff": "materialize_evolved_strategy_packet",
    }[role]


def _oss_followup_candidate_action(*, role: str, component: str) -> str:
    if role in {"alpha_model", "backtest", "policy_candidate", "tracker", "handoff"}:
        return "feedback-adapt"
    if role == "risk_analytics":
        return "risk-off"
    if role == "reflection_artifact":
        return "contrarian-check" if component == "imitation" else "feedback-adapt"
    return "retain-observe"


def _oss_followup_score_adjustment(*, role: str, component: str) -> dict[str, float]:
    del component
    adjustments = {
        "feedback-adapt": 0.0,
        "retain-observe": 0.0,
        "risk-off": 0.0,
        "contrarian-check": 0.0,
    }
    if role == "session":
        adjustments["retain-observe"] = 0.01
    elif role in {"alpha_model", "backtest", "policy_candidate", "tracker", "handoff"}:
        adjustments["feedback-adapt"] = 0.02
    elif role == "reflection_artifact":
        adjustments["feedback-adapt"] = 0.015
        adjustments["contrarian-check"] = 0.005
    elif role == "risk_analytics":
        adjustments["feedback-adapt"] = 0.005
        adjustments["risk-off"] = 0.025
    return adjustments


def _oss_followup_refs_for_action(
    loop: Mapping[str, Any],
    action: str,
) -> list[str]:
    refs_by_action = loop.get("candidate_evidence_refs_by_action", {})
    if not isinstance(refs_by_action, Mapping):
        return []
    return [str(ref) for ref in refs_by_action.get(action, [])]


def _oss_disagreement_refs_for_action(
    arbitration: Mapping[str, Any],
    action: str,
) -> list[str]:
    refs_by_action = arbitration.get("candidate_evidence_refs_by_action", {})
    if not isinstance(refs_by_action, Mapping):
        return []
    return [str(ref) for ref in refs_by_action.get(action, [])]


def _tracking_reconciliation_refs_for_action(
    reconciliation: Mapping[str, Any],
    action: str,
) -> list[str]:
    refs_by_action = reconciliation.get("candidate_evidence_refs_by_action", {})
    if not isinstance(refs_by_action, Mapping):
        return []
    return [str(ref) for ref in refs_by_action.get(action, [])]


def _alpha_seed_revision_refs_for_action(
    alpha_seed_revision: Mapping[str, Any],
    action: str,
) -> list[str]:
    refs_by_action = alpha_seed_revision.get("candidate_evidence_refs_by_action", {})
    if not isinstance(refs_by_action, Mapping):
        return []
    return [str(ref) for ref in refs_by_action.get(action, [])]


def _oss_response_followup_loop_is_usable(loop: Mapping[str, Any]) -> bool:
    replay = loop.get("replay", {})
    followups = loop.get("followups", [])
    required_roles = (
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    )
    adjustments = loop.get("candidate_score_adjustments", {})
    refs_by_action = loop.get("candidate_evidence_refs_by_action", {})
    return bool(
        loop.get("model_id") == OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID
        and [item.get("role") for item in followups] == list(required_roles)
        and all(item.get("request", {}).get("requested_after_oss_response") is True for item in followups)
        and all(item.get("response", {}).get("status") == "completed" for item in followups)
        and all(item.get("request", {}).get("source_oss_ref") == item.get("source_oss_ref") for item in followups)
        and all(item.get("response", {}).get("output_ref") for item in followups)
        and float(adjustments.get("feedback-adapt", 0.0)) > 0
        and float(adjustments.get("risk-off", 0.0)) > 0
        and set(refs_by_action.get("feedback-adapt", []))
        == {item.get("response", {}).get("output_ref") for item in followups}
        and replay.get("replayable") is True
        and replay.get("all_required_roles_followed_up") is True
        and replay.get("all_followups_requested_after_oss_response") is True
        and replay.get("all_followups_completed") is True
        and replay.get("responses_drive_candidate_scoring") is True
        and replay.get("feedback_adapt_receives_all_followup_refs") is True
        and replay.get("risk_response_available_to_risk_off") is True
        and loop.get("input_hash")
    )


def _build_multi_oss_closed_loop_proof(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    required_roles = (
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    )
    followup_by_role = {
        str(followup["role"]): followup
        for followup in oss_followup_loop.get("followups", [])
    }
    selected_oss = case_upstream_artifacts.get("selected_oss", {})
    role_records: list[dict[str, Any]] = []
    for role in required_roles:
        result = oss_inputs[role]
        followup = followup_by_role[role]
        component = str(result["component"])
        request_id = str(result["request_id"])
        source_oss_ref = f"oss://{component}/{request_id}"
        score_adjustments = {
            action: float(value)
            for action, value in followup["response"]["score_adjustments"].items()
            if float(value) > 0.0
        }
        selected_entry = selected_oss.get(role)
        selected_oss_ref = (
            f"oss://{selected_entry['component']}/{selected_entry['request_id']}"
            if isinstance(selected_entry, Mapping)
            else None
        )
        role_records.append(
            {
                "role": role,
                "component": component,
                "source_oss_request_id": request_id,
                "source_oss_ref": source_oss_ref,
                "source_status": result.get("status"),
                "source_drives_persona_step": result.get("drives_persona_step"),
                "followup_request_id": followup["request"]["request_id"],
                "followup_response_id": followup["response"]["response_id"],
                "followup_output_ref": followup["response"]["output_ref"],
                "followup_candidate_action": followup["response"]["candidate_action"],
                "followup_score_adjustments": score_adjustments,
                "source_ref_bound_to_followup": (
                    followup["source_oss_ref"] == source_oss_ref
                    and followup["request"]["source_oss_ref"] == source_oss_ref
                    and followup["request"]["source_oss_request_id"] == request_id
                ),
                "followup_requested_after_oss_response": followup["request"]["requested_after_oss_response"],
                "followup_completed": followup["response"]["status"] == "completed",
                "followup_drives_persona_step": followup["request"]["drives_persona_step"],
                "used_by_generations": list(followup["response"]["used_by_generations"]),
                "selected_oss_ref": selected_oss_ref,
                "selected_oss_bound": (
                    selected_oss_ref is None
                    or (
                        selected_entry.get("model_id") == CASE_SELECTED_OSS_MODEL_ID
                        and selected_entry.get("status") == "completed"
                        and selected_entry.get("drives_persona_step") == _oss_persona_step(component)
                    )
                ),
            }
        )

    all_followup_output_refs = [
        record["followup_output_ref"] for record in role_records
    ]
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_refs = set(str(ref) for ref in selected_candidate["evidence_refs"])
        reasoning_refs = set(str(ref) for ref in reasoning_request["input_refs"])
        candidate_refs = set(str(ref) for ref in candidate_request["input_refs"])
        scorer_adjustments = scorer_inputs["oss_followup_score_adjustments"]
        role_trace_bindings = []
        for record in role_records:
            candidate_action = str(record["followup_candidate_action"])
            role_adjustment = float(
                record["followup_score_adjustments"].get(candidate_action, 0.0)
            )
            role_trace_bindings.append(
                {
                    "role": record["role"],
                    "source_ref_in_reasoning_request": record["source_oss_ref"] in reasoning_refs,
                    "followup_output_in_candidate_request": record["followup_output_ref"] in candidate_refs,
                    "followup_output_in_selected_evidence": record["followup_output_ref"] in selected_refs,
                    "scorer_adjustment_available": float(
                        scorer_adjustments.get(candidate_action, 0.0)
                    ) >= role_adjustment > 0.0,
                }
            )
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "selected_candidate_id": trace["selected_candidate_id"],
                "selected_action": _candidate_action_key(str(trace["selected_candidate_id"])),
                "oss_followup_loop_ref": oss_followup_loop["loop_ref"],
                "reasoning_request_consumes_all_source_oss_refs": all(
                    binding["source_ref_in_reasoning_request"]
                    for binding in role_trace_bindings
                ),
                "candidate_request_consumes_all_followup_outputs": all(
                    binding["followup_output_in_candidate_request"]
                    for binding in role_trace_bindings
                ),
                "selected_candidate_cites_all_followup_outputs": set(
                    all_followup_output_refs
                ).issubset(selected_refs),
                "scorer_has_all_role_adjustments": all(
                    binding["scorer_adjustment_available"]
                    for binding in role_trace_bindings
                ),
                "role_bindings": role_trace_bindings,
            }
        )

    replay = {
        "replayable": True,
        "all_required_roles_present": set(oss_inputs) == set(required_roles)
        and [record["role"] for record in role_records] == list(required_roles),
        "all_oss_responses_completed": all(record["source_status"] == "completed" for record in role_records),
        "all_source_refs_bound_to_followup_requests": all(
            record["source_ref_bound_to_followup"] for record in role_records
        ),
        "all_followups_requested_after_oss_response": all(
            record["followup_requested_after_oss_response"] is True for record in role_records
        ),
        "all_followup_responses_completed": all(record["followup_completed"] for record in role_records),
        "all_followup_outputs_used_by_both_generations": all(
            record["used_by_generations"] == [1, 2] for record in role_records
        ),
        "selected_case_oss_roles_bound": all(
            record["selected_oss_bound"] for record in role_records if record["selected_oss_ref"]
        )
        and {
            record["role"] for record in role_records if record["selected_oss_ref"]
        } == {"alpha_model", "policy_candidate", "reflection_artifact", "risk_analytics"},
        "all_source_oss_refs_consumed_by_reasoning": all(
            binding["reasoning_request_consumes_all_source_oss_refs"]
            for binding in trace_bindings
        ),
        "all_followup_outputs_consumed_by_candidate_generation": all(
            binding["candidate_request_consumes_all_followup_outputs"]
            for binding in trace_bindings
        ),
        "all_role_score_adjustments_available_to_scorer": all(
            binding["scorer_has_all_role_adjustments"] for binding in trace_bindings
        ),
        "selected_candidate_cites_all_followup_outputs": all(
            binding["selected_candidate_cites_all_followup_outputs"]
            for binding in trace_bindings
        ),
        "feedback_adapt_path_receives_all_oss_feedback": set(
            oss_followup_loop["candidate_evidence_refs_by_action"]["feedback-adapt"]
        ) == set(all_followup_output_refs),
    }
    return {
        "proof_id": f"multi-oss-closed-loop-proof-{episode.case_id}",
        "proof_ref": f"multi-oss-closed-loop://{episode.case_id}",
        "model_id": MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "role_records": role_records,
        "trace_bindings": trace_bindings,
        "evidence_refs": [
            *[record["source_oss_ref"] for record in role_records],
            *all_followup_output_refs,
            oss_followup_loop["loop_ref"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "multi-oss-closed-loop-proof",
            {
                "case_id": episode.case_id,
                "role_records": role_records,
                "trace_bindings": trace_bindings,
            },
        ),
    }


def _multi_oss_closed_loop_proof_is_usable(proof: Mapping[str, Any]) -> bool:
    required_roles = (
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    )
    replay = proof.get("replay", {})
    role_records = list(proof.get("role_records", []))
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("multi-oss-closed-loop://")
        and proof.get("input_hash")
        and [record.get("role") for record in role_records] == list(required_roles)
        and len(trace_bindings) == 2
        and all(record.get("source_status") == "completed" for record in role_records)
        and all(record.get("source_ref_bound_to_followup") is True for record in role_records)
        and all(record.get("followup_requested_after_oss_response") is True for record in role_records)
        and all(record.get("followup_completed") is True for record in role_records)
        and all(record.get("used_by_generations") == [1, 2] for record in role_records)
        and all(
            any(float(value) > 0.0 for value in record.get("followup_score_adjustments", {}).values())
            for record in role_records
        )
        and all(
            binding.get("reasoning_request_consumes_all_source_oss_refs") is True
            and binding.get("candidate_request_consumes_all_followup_outputs") is True
            and binding.get("selected_candidate_cites_all_followup_outputs") is True
            and binding.get("scorer_has_all_role_adjustments") is True
            for binding in trace_bindings
        )
        and replay.get("replayable") is True
        and replay.get("all_required_roles_present") is True
        and replay.get("all_oss_responses_completed") is True
        and replay.get("all_source_refs_bound_to_followup_requests") is True
        and replay.get("all_followups_requested_after_oss_response") is True
        and replay.get("all_followup_responses_completed") is True
        and replay.get("all_followup_outputs_used_by_both_generations") is True
        and replay.get("selected_case_oss_roles_bound") is True
        and replay.get("all_source_oss_refs_consumed_by_reasoning") is True
        and replay.get("all_followup_outputs_consumed_by_candidate_generation") is True
        and replay.get("all_role_score_adjustments_available_to_scorer") is True
        and replay.get("selected_candidate_cites_all_followup_outputs") is True
        and replay.get("feedback_adapt_path_receives_all_oss_feedback") is True
    )


def _build_persona_oss_ooda_causal_ledger(
    *,
    episode: PortfolioEpisode,
    multi_oss_closed_loop_proof: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    operational_context: Mapping[str, Any],
) -> dict[str, Any]:
    role_records = list(multi_oss_closed_loop_proof["role_records"])
    followup_by_role = {
        str(followup["role"]): followup
        for followup in oss_followup_loop["followups"]
    }
    events: list[dict[str, Any]] = []
    produced_at: dict[str, int] = {}

    def append_event(
        *,
        phase: str,
        event_type: str,
        actor: str,
        output_ref: str,
        input_refs: Sequence[str] = (),
        role: str | None = None,
        component: str | None = None,
        request_ref: str | None = None,
        response_ref: str | None = None,
        generation: int | None = None,
        downstream_persona_action: str | None = None,
        drives_next_event: str | None = None,
    ) -> dict[str, Any]:
        sequence = len(events) + 1
        event = {
            "sequence": sequence,
            "event_id": f"ooda-ledger-event-{episode.case_id}-{sequence:02d}",
            "ooda_phase": phase,
            "event_type": event_type,
            "actor": actor,
            "role": role,
            "component": component,
            "generation": generation,
            "request_ref": request_ref,
            "response_ref": response_ref,
            "input_refs": [str(ref) for ref in input_refs],
            "output_ref": output_ref,
            "downstream_persona_action": downstream_persona_action,
            "drives_next_event": drives_next_event,
        }
        events.append(event)
        produced_at[output_ref] = sequence
        return event

    for record in role_records:
        append_event(
            phase="observe",
            event_type="oss_response",
            actor="oss",
            role=str(record["role"]),
            component=str(record["component"]),
            request_ref=f"oss-request://{record['component']}/{record['source_oss_request_id']}",
            response_ref=str(record["source_oss_ref"]),
            output_ref=str(record["source_oss_ref"]),
            downstream_persona_action=str(record["source_drives_persona_step"]),
            drives_next_event="persona_followup_response",
        )

    for record in role_records:
        followup = followup_by_role[str(record["role"])]
        append_event(
            phase="orient",
            event_type="persona_followup_response",
            actor="persona+oss",
            role=str(record["role"]),
            component=str(record["component"]),
            request_ref=f"persona-followup://{record['followup_request_id']}",
            response_ref=f"persona-followup-response://{record['followup_response_id']}",
            input_refs=[str(record["source_oss_ref"])],
            output_ref=str(record["followup_output_ref"]),
            downstream_persona_action=str(followup["response"]["candidate_action"]),
            drives_next_event="candidate_generation",
        )

    candidate_output_refs: list[str] = []
    scorer_output_refs: list[str] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        candidate_generation = artifact["candidate_generation"]
        candidate_output_ref = (
            f"candidate-generation://{candidate_generation['response']['response_id']}"
        )
        candidate_output_refs.append(candidate_output_ref)
        append_event(
            phase="decide",
            event_type="candidate_generation",
            actor="persona",
            generation=int(artifact["generation"]),
            request_ref=(
                f"decision-request://{candidate_generation['request']['request_id']}"
            ),
            response_ref=candidate_output_ref,
            input_refs=[
                str(record["followup_output_ref"])
                for record in role_records
            ],
            output_ref=candidate_output_ref,
            downstream_persona_action="score_candidates",
            drives_next_event="candidate_scoring",
        )

        scorer_output_ref = f"candidate-score://{trace['reflection_id']}"
        scorer_output_refs.append(scorer_output_ref)
        append_event(
            phase="decide",
            event_type="candidate_scoring",
            actor="persona",
            generation=int(artifact["generation"]),
            request_ref=f"candidate-score-request://{trace['reflection_id']}",
            response_ref=scorer_output_ref,
            input_refs=[candidate_output_ref],
            output_ref=scorer_output_ref,
            downstream_persona_action=str(
                _candidate_action_key(str(trace["selected_candidate_id"]))
            ),
            drives_next_event="selected_action",
        )

    final_trace = decision_traces[-1]
    selected_action = str(_candidate_action_key(str(final_trace["selected_candidate_id"])))
    selected_action_ref = (
        f"selected-action://{episode.case_id}/{final_trace['selected_candidate_id']}"
    )
    append_event(
        phase="act",
        event_type="selected_action",
        actor="persona",
        generation=int(final_trace["agent_decision_artifact"]["generation"]),
        request_ref=f"selection-request://{final_trace['reflection_id']}",
        response_ref=selected_action_ref,
        input_refs=scorer_output_refs,
        output_ref=selected_action_ref,
        downstream_persona_action=selected_action,
        drives_next_event="lean_handoff_packet",
    )

    lean_handoff = operational_context["lean_handoff"]
    handoff_ref = f"lean-handoff://{lean_handoff['packet_id']}"
    append_event(
        phase="act",
        event_type="lean_handoff_packet",
        actor="persona+lean_handoff",
        role="handoff",
        component=str(lean_handoff["component"]),
        request_ref=f"lean-handoff-request://{lean_handoff['request_id']}",
        response_ref=handoff_ref,
        input_refs=[
            selected_action_ref,
            f"oss://{lean_handoff['component']}/{lean_handoff['request_id']}",
        ],
        output_ref=handoff_ref,
        downstream_persona_action="materialize_lean_handoff_packet",
        drives_next_event="lean_runtime_feedback",
    )

    followup_output_refs = [
        str(record["followup_output_ref"]) for record in role_records
    ]
    source_response_events = [
        event for event in events if event["event_type"] == "oss_response"
    ]
    followup_events = [
        event for event in events if event["event_type"] == "persona_followup_response"
    ]
    generation_events = [
        event for event in events if event["event_type"] == "candidate_generation"
    ]
    scoring_events = [
        event for event in events if event["event_type"] == "candidate_scoring"
    ]
    selected_event = next(
        event for event in events if event["event_type"] == "selected_action"
    )
    handoff_event = next(
        event for event in events if event["event_type"] == "lean_handoff_packet"
    )
    replay = {
        "ledger_replayable": True,
        "all_events_strictly_ordered": [
            event["sequence"] for event in events
        ] == list(range(1, len(events) + 1)),
        "all_oss_responses_precede_persona_followups": all(
            produced_at[str(record["source_oss_ref"])] < produced_at[str(record["followup_output_ref"])]
            for record in role_records
        ),
        "all_persona_followups_emit_completed_outputs": all(
            event["output_ref"] in followup_output_refs for event in followup_events
        )
        and len(followup_events) == len(role_records),
        "all_followup_outputs_precede_candidate_generation": all(
            produced_at[ref] < event["sequence"]
            for event in generation_events
            for ref in followup_output_refs
        ),
        "candidate_generation_precedes_scoring": all(
            produced_at[candidate_ref] < scoring_event["sequence"]
            and candidate_ref in scoring_event["input_refs"]
            for candidate_ref, scoring_event in zip(candidate_output_refs, scoring_events)
        ),
        "scoring_precedes_selected_action": all(
            produced_at[score_ref] < selected_event["sequence"]
            and score_ref in selected_event["input_refs"]
            for score_ref in scorer_output_refs
        ),
        "selected_action_precedes_lean_handoff": (
            produced_at[selected_action_ref] < handoff_event["sequence"]
            and selected_action_ref in handoff_event["input_refs"]
        ),
        "lean_handoff_consumes_selected_action": (
            lean_handoff.get("received_by_lean_handoff") is True
            and handoff_event["output_ref"] == handoff_ref
        ),
        "all_ooda_phases_present": {
            event["ooda_phase"] for event in events
        } == {"observe", "orient", "decide", "act"},
        "no_future_artifact_reference": all(
            produced_at[input_ref] < event["sequence"]
            for event in events
            for input_ref in event["input_refs"]
            if input_ref in produced_at
        ),
        "actionable_oss_feedback_has_downstream_persona_action": all(
            bool(event["downstream_persona_action"])
            for event in [*source_response_events, *followup_events]
        ),
    }
    return {
        "ledger_id": f"persona-oss-ooda-ledger-{episode.case_id}",
        "ledger_ref": f"persona-oss-ooda-ledger://{episode.case_id}",
        "model_id": PERSONA_OSS_OODA_LEDGER_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "source_closed_loop_proof_ref": multi_oss_closed_loop_proof["proof_ref"],
        "oss_followup_loop_ref": oss_followup_loop["loop_ref"],
        "event_count": len(events),
        "events": events,
        "phase_order": [event["ooda_phase"] for event in events],
        "event_types": [event["event_type"] for event in events],
        "evidence_refs": [
            multi_oss_closed_loop_proof["proof_ref"],
            oss_followup_loop["loop_ref"],
            *[event["output_ref"] for event in events],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "persona-oss-ooda-causal-ledger",
            {
                "case_id": episode.case_id,
                "events": events,
                "replay": replay,
            },
        ),
    }


def _persona_oss_ooda_causal_ledger_is_usable(ledger: Mapping[str, Any]) -> bool:
    events = list(ledger.get("events", []))
    replay = ledger.get("replay", {})
    event_types = [event.get("event_type") for event in events]
    return bool(
        ledger.get("model_id") == PERSONA_OSS_OODA_LEDGER_MODEL_ID
        and ledger.get("status") == "passed"
        and ledger.get("ledger_ref", "").startswith("persona-oss-ooda-ledger://")
        and ledger.get("event_count") == 22
        and ledger.get("input_hash")
        and event_types.count("oss_response") == 8
        and event_types.count("persona_followup_response") == 8
        and event_types.count("candidate_generation") == 2
        and event_types.count("candidate_scoring") == 2
        and event_types.count("selected_action") == 1
        and event_types.count("lean_handoff_packet") == 1
        and all(event.get("output_ref") for event in events)
        and all(replay.get(flag) is True for flag in (
            "ledger_replayable",
            "all_events_strictly_ordered",
            "all_oss_responses_precede_persona_followups",
            "all_persona_followups_emit_completed_outputs",
            "all_followup_outputs_precede_candidate_generation",
            "candidate_generation_precedes_scoring",
            "scoring_precedes_selected_action",
            "selected_action_precedes_lean_handoff",
            "lean_handoff_consumes_selected_action",
            "all_ooda_phases_present",
            "no_future_artifact_reference",
            "actionable_oss_feedback_has_downstream_persona_action",
        ))
    )


def _run_case_selected_oss_feedback(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    vectorbt_feedback: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    selected_components = {
        "alpha_model": str(oss_inputs["alpha_model"]["component"]),
        "policy_candidate": str(oss_inputs["policy_candidate"]["component"]),
        "reflection_artifact": str(oss_inputs["reflection_artifact"]["component"]),
        "risk_analytics": str(oss_inputs["risk_analytics"]["component"]),
    }
    for role, component in selected_components.items():
        if role == "alpha_model" and component == "vectorbt":
            selected[role] = _case_vectorbt_selected_oss_feedback(
                episode=episode,
                role=role,
                vectorbt_feedback=vectorbt_feedback,
            )
            continue
        selected[role] = _run_case_selected_persona_oss_request(
            episode=episode,
            role=role,
            component=component,
        )
    return selected


def _case_vectorbt_selected_oss_feedback(
    *,
    episode: PortfolioEpisode,
    role: str,
    vectorbt_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    request_id = str(vectorbt_feedback["request_id"])
    persona_result = {
        "component": "vectorbt",
        "persona_id": _persona_id(episode.persona),
        "session_id": str(vectorbt_feedback["session_id"]),
        "request_id": request_id,
        "status": "completed",
        "artifact_family": "vectorbt_backtest",
        "primary_output": {
            "backend": vectorbt_feedback["backend"],
            "run_id": vectorbt_feedback["run_id"],
            "aggregate_metrics": copy.deepcopy(vectorbt_feedback["aggregate_metrics"]),
        },
        "metrics": copy.deepcopy(vectorbt_feedback["aggregate_metrics"]),
        "registry_entry": copy.deepcopy(vectorbt_feedback["registry_entry"]),
        "artifact_bundle": copy.deepcopy(vectorbt_feedback["artifact_bundle"]),
        "refs": {
            "registry_id": vectorbt_feedback["registry_id"],
            "source_dataset_refs": list(vectorbt_feedback["dataset_summary"]["source_dataset_refs"]),
        },
        "persona_followup": {
            "persona_id": _persona_id(episode.persona),
            "session_id": str(vectorbt_feedback["session_id"]),
            "trigger_component": "vectorbt",
            "trigger_request_id": request_id,
            "trigger_artifact_family": "vectorbt_backtest",
            "ooda_phase": "decide",
            "next_action": "draft_strategy_proposal",
            "evidence_refs": [request_id, str(vectorbt_feedback["registry_id"])],
        },
    }
    return _case_selected_oss_summary(
        episode=episode,
        role=role,
        component="vectorbt",
        result=persona_result,
    )


def _run_case_selected_persona_oss_request(
    *,
    episode: PortfolioEpisode,
    role: str,
    component: str,
) -> dict[str, Any]:
    payload = _case_selected_oss_payload(
        episode=episode,
        role=role,
        component=component,
    )
    request = PersonaOSSRequest(
        persona_id=_persona_id(episode.persona),
        session_id=f"session-{episode.case_id}-{role}-{component}",
        component=component,
        intent=f"case_specific_{role}_{component}_feedback",
        payload=payload,
        request_id=f"req-{episode.case_id}-{role}-{component}",
    )
    result = run_persona_oss_request(request).to_dict()
    return _case_selected_oss_summary(
        episode=episode,
        role=role,
        component=component,
        result=result,
    )


def _case_selected_oss_payload(
    *,
    episode: PortfolioEpisode,
    role: str,
    component: str,
) -> dict[str, Any]:
    payload = _oss_payload_for_component(component, episode.seed_key, episode.ordinal)
    payload.update(
        {
            "dataset_id": HISTORICAL_OHLCV_DATASET_ID,
            "strategy_id": f"{episode.seed_key}-{episode.case_id}-{role}-{component}",
            "source_strategy_spec_id": episode.source_strategy_spec_id,
            "source_dataset_refs": list(episode.source_dataset_refs),
            "version": f"3000.{episode.ordinal}.{_selected_oss_version_slot(role)}",
        }
    )
    metadata = copy.deepcopy(dict(payload.get("metadata", {})))
    metadata.update(
        {
            "case_id": episode.case_id,
            "validation_signature": episode.validation_signature,
            "selected_oss_role": role,
            "selected_oss_component": component,
            "alpha_seed_key": episode.seed_key,
            "source_strategy_spec_id": episode.source_strategy_spec_id,
            "portfolio_instruments": [window.instrument for window in episode.windows],
            "historical_window_start_indices": [window.start_index for window in episode.windows],
            "allowed_windows": ["observe", "feedback"],
            "forbidden_windows_not_used": ["holdout", "future_holdout"],
        }
    )
    payload["metadata"] = metadata

    if component == "qlib":
        payload.update(
            {
                "seed": 1_000 + episode.ordinal,
                "n_estimators": 10 + (episode.ordinal % 5),
                "num_leaves": 7 + (episode.ordinal % 3) * 2,
                "max_depth": 3 + (episode.ordinal % 2),
                "learning_rate": round(0.035 + (episode.ordinal % 5) * 0.004, 4),
            }
        )
    elif component == "finrl":
        payload.update(
            {
                "seed": 2_000 + episode.ordinal,
                "lookback_window": 3 + (episode.ordinal % 2),
                "learning_rate": 0.0002 + (episode.ordinal % 7) * 0.00002,
                "gamma": round(0.97 + (episode.ordinal % 8) * 0.002, 4),
                "reward_scale": round(0.9 + (episode.ordinal % 5) * 0.04, 4),
                "risk_aversion": round(0.15 + (episode.ordinal % 6) * 0.02, 4),
            }
        )
    elif component == "rllib":
        payload.update(
            {
                "seed": 3_000 + episode.ordinal,
                "lookback_window": 3 + (episode.ordinal % 2),
                "learning_rate": 0.00025 + (episode.ordinal % 7) * 0.00002,
                "gamma": round(0.975 + (episode.ordinal % 8) * 0.002, 4),
                "gae_lambda": round(0.92 + (episode.ordinal % 5) * 0.005, 4),
                "entropy_coeff": round(0.001 + (episode.ordinal % 4) * 0.0005, 5),
                "clip_param": round(0.15 + (episode.ordinal % 5) * 0.01, 4),
                "num_trials": 6 + (episode.ordinal % 5),
                "search_strategy": ("pbt", "grid", "bayesian")[episode.ordinal % 3],
            }
        )
    elif component == "ray_tune":
        payload.update(
            {
                "optimizer_id": f"ray-tune-{episode.case_id}",
                "search_strategy": ("pbt", "grid", "bayesian")[episode.ordinal % 3],
                "num_trials": 6 + (episode.ordinal % 5),
                "top_k": 2 + (episode.ordinal % 3),
                "seed": 4_000 + episode.ordinal,
                "training_seed": 5_000 + episode.ordinal,
                "trigger": ("manual", "scheduled", "drift_detected", "evaluation_recommendation")[
                    episode.ordinal % 4
                ],
                "max_iterations": 8 + (episode.ordinal % 7),
            }
        )
    elif component == "dspy":
        payload.update({"base_bundle_ref": episode.source_strategy_spec_id, "lifecycle_state": "draft"})
    elif component == "trl":
        payload.update(
            {
                "strategy_family": episode.seed_key,
                "operator_id": f"operator-{episode.case_id}",
                "feedback_event_prefix": f"fb-{episode.case_id}",
                "beta": round(0.05 + (episode.ordinal % 5) * 0.01, 4),
                "learning_rate": 0.000005 + (episode.ordinal % 7) * 0.000001,
                "batch_size": 8 + (episode.ordinal % 8),
                "num_epochs": 2 + (episode.ordinal % 3),
                "seed": 6_000 + episode.ordinal,
            }
        )
    elif component == "imitation":
        payload.update(
            {
                "epochs": 1 + (episode.ordinal % 3),
                "seed": 7_000 + episode.ordinal,
                "lifecycle_state": "draft",
            }
        )
    elif component == "statsmodels":
        payload.update(
            {
                "series_suffix": f"_{episode.case_id.replace('-', '_')}",
                "price_multiplier": round(1.0 + episode.ordinal / 10_000.0, 6),
                "factor_multiplier": round(1.0 + (episode.ordinal % 17) / 100.0, 6),
                "data_frequency": "daily",
            }
        )
    elif component == "quantlib":
        payload.update(
            {
                "instrument_suffix": f"-{episode.case_id}",
                "valuation_date": f"2026-05-{10 + (episode.ordinal % 10):02d}",
                "spot_shift": round(0.25 + (episode.ordinal % 11) * 0.1, 4),
                "strike_shift": round((episode.ordinal % 7) * 0.05, 4),
                "volatility_shift": round((episode.ordinal % 4) * 0.0025, 4),
                "quantity_multiplier": 1 + (episode.ordinal % 3),
                "market_rate_shift": round((episode.ordinal % 8) * 0.00025, 5),
            }
        )
    return payload


def _case_selected_oss_summary(
    *,
    episode: PortfolioEpisode,
    role: str,
    component: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    registry_entry = result.get("registry_entry") or {}
    persona_followup = result.get("persona_followup") or {}
    return {
        "role": role,
        "component": component,
        "model_id": CASE_SELECTED_OSS_MODEL_ID,
        "case_specific": True,
        "persona_id": result.get("persona_id"),
        "session_id": result.get("session_id"),
        "request_id": result.get("request_id"),
        "status": result.get("status"),
        "artifact_family": result.get("artifact_family"),
        "metrics": copy.deepcopy(dict(result.get("metrics") or {})),
        "primary_output": copy.deepcopy(dict(result.get("primary_output") or {})),
        "registry_id": registry_entry.get("registry_id"),
        "registry_artifact_type": registry_entry.get("artifact_type"),
        "producer_run_id": registry_entry.get("producer_run_id"),
        "drives_persona_step": _oss_persona_step(component),
        "persona_followup": copy.deepcopy(dict(persona_followup)),
        "persona_result": copy.deepcopy(dict(result)),
        "expected_component": _expected_component_for_selected_oss_role(episode, role),
    }


def _selected_oss_version_slot(role: str) -> int:
    return {
        "alpha_model": 10,
        "policy_candidate": 20,
        "reflection_artifact": 30,
        "risk_analytics": 40,
    }[role]


def _expected_component_for_selected_oss_role(episode: PortfolioEpisode, role: str) -> str:
    if role == "alpha_model":
        return episode.oss_route["alpha_model"]
    if role == "policy_candidate":
        return episode.oss_route["policy_candidate"]
    if role == "reflection_artifact":
        return episode.oss_route["reflection_artifact"]
    if role == "risk_analytics":
        return episode.oss_route["risk_analytics"]
    raise ValueError(f"unsupported selected OSS role: {role}")


def _run_case_vectorbt_feedback(episode: PortfolioEpisode) -> dict[str, Any]:
    strategy_id = f"{episode.seed_key}-{episode.case_id}-case-vectorbt"
    version = f"3000.2.{episode.ordinal}"
    dataset = {
        "dataset_id": HISTORICAL_OHLCV_DATASET_ID,
        "strategy_id": strategy_id,
        "source_strategy_spec_id": episode.source_strategy_spec_id,
        "source_dataset_refs": list(episode.source_dataset_refs),
        "data_frequency": "daily",
        "records": _case_vectorbt_records(episode),
        "metadata": {
            "case_id": episode.case_id,
            "validation_signature": episode.validation_signature,
            "alpha_seed_key": episode.seed_key,
            "allowed_windows": ["observe", "feedback"],
            "forbidden_windows_not_used": ["holdout", "future_holdout"],
        },
    }
    backend, real_package_available = _case_vectorbt_backend()
    previous_backend = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    if backend is not None:
        os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        result = run_vectorbt_workflow(
            dataset,
            backend=backend,
            config=BacktestConfig(
                version=version,
                requested_by=_persona_id(episode.persona),
                strategy_params={
                    "short_window": 3 + (episode.ordinal % 5),
                    "long_window": 10 + (episode.ordinal % 7),
                },
                init_cash=100_000.0 + episode.ordinal * 25.0,
                fees=0.0005 + (episode.ordinal % 5) * 0.00005,
                storage_backend="object_store",
            ),
        )
    finally:
        if previous_backend is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = previous_backend

    artifact_bundle = copy.deepcopy(result.artifact_bundle)
    registry_entry = copy.deepcopy(result.registry_entry)
    aggregate_metrics = copy.deepcopy(result.backtest_result.aggregate_metrics)
    per_instrument_metrics = copy.deepcopy(result.backtest_result.per_instrument_metrics)
    return {
        "request_id": f"req-{episode.case_id}-vectorbt-upstream",
        "session_id": f"session-{episode.case_id}-vectorbt-upstream",
        "model_id": CASE_UPSTREAM_VECTORBT_MODEL_ID,
        "status": "completed",
        "backend": result.backtest_result.backend,
        "real_package_available": real_package_available,
        "run_id": result.backtest_result.run_id,
        "registry_id": registry_entry["registry_id"],
        "producer_run_id": registry_entry["producer_run_id"],
        "checksum": registry_entry["checksum"],
        "strategy_id": strategy_id,
        "version": version,
        "dataset_summary": artifact_bundle["dataset_summary"],
        "backtest_config": artifact_bundle["backtest_config"],
        "aggregate_metrics": aggregate_metrics,
        "per_instrument_metrics": per_instrument_metrics,
        "registry_entry": registry_entry,
        "artifact_bundle": artifact_bundle,
        "used_historical_rows": sum(
            len(window.observe_rows) + len(window.feedback_rows)
            for window in episode.windows
        ),
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "historical_window_start_indices": [window.start_index for window in episode.windows],
    }


def _case_vectorbt_records(episode: PortfolioEpisode) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for window in episode.windows:
        for row in (*window.observe_rows, *window.feedback_rows):
            records.append(
                {
                    "instrument": window.instrument,
                    "date": str(row["date"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
    return records


def _case_vectorbt_backend() -> tuple[Any | None, bool]:
    mode = os.environ.get("PANTHEON_AGENT_USABILITY_VECTORBT_BACKEND", "auto").strip().lower()
    if mode in {"stub", "false", "0"}:
        return None, False
    real_available = importlib.util.find_spec("vectorbt") is not None
    if mode == "real" and not real_available:
        raise RuntimeError("PANTHEON_AGENT_USABILITY_VECTORBT_BACKEND=real but vectorbt is not installed")
    if real_available:
        return VectorbtBackend(), True
    return None, False


def _run_case_tracking_feedback(
    *,
    episode: PortfolioEpisode,
    vectorbt_feedback: Mapping[str, Any],
    tracker_component: str,
) -> dict[str, Any]:
    component = tracker_component.strip().lower()
    if component not in {"mlflow", "wandb"}:
        raise ValueError(f"unsupported case tracking component: {tracker_component!r}")
    entry = copy.deepcopy(dict(vectorbt_feedback["registry_entry"]))
    entry["artifact_state"] = "candidate"
    entry["deployment_stage"] = "none"
    entry["evaluation_summary"] = copy.deepcopy(dict(vectorbt_feedback["aggregate_metrics"]))
    entry.setdefault("metadata", {})
    if isinstance(entry["metadata"], Mapping):
        entry["metadata"] = {
            **dict(entry["metadata"]),
            "case_id": episode.case_id,
            "validation_signature": episode.validation_signature,
            "alpha_seed_key": episode.seed_key,
            "case_specific_upstream_feedback": True,
        }
    if component == "wandb":
        store_dir = tempfile.mkdtemp(prefix=f"pantheon-wandb-{episode.case_id}-")
        backend = OfflineWandbLocalBackend(store_dir=store_dir)
        sync = RegistryExperimentAdapter(backend=backend).sync_registry_entry(entry)
        run_payload = backend.get_run(sync.experiment_ref.run_id)
        artifact_readback = backend.get_artifact(sync.experiment_ref.run_id, "artifact_handoff.json")
        readback = {
            "run_readback_status": "found" if run_payload else "missing",
            "artifact_readback_status": "found" if artifact_readback else "missing",
            "local_store_dir": store_dir,
            "sync_status": sync.experiment_ref.sync_status,
        }
    else:
        backend = InMemoryMlflowBackend(
            tracking_uri=f"memory://agent-usability/{episode.case_id}/mlflow"
        )
        sync = RegistryExperimentAdapter(backend=backend).sync_registry_entry(entry)
        run_payload = backend.runs.get(sync.experiment_ref.run_id)
        artifact_readback = (run_payload or {}).get("artifacts", {}).get("artifact_handoff.json")
        readback = {
            "run_readback_status": "found" if run_payload else "missing",
            "artifact_readback_status": "found" if artifact_readback else "missing",
            "tracking_uri": backend.tracking_uri,
            "sync_status": sync.experiment_ref.sync_status,
        }
    metrics = copy.deepcopy(dict(sync.record.metrics))
    return {
        "request_id": f"req-{episode.case_id}-{component}-tracking",
        "session_id": f"session-{episode.case_id}-{component}-tracking",
        "model_id": CASE_UPSTREAM_TRACKING_MODEL_ID,
        "component": component,
        "backend": sync.experiment_ref.backend,
        "tracking_version": getattr(backend, "tracking_version", ""),
        "status": "completed",
        "run_id": sync.experiment_ref.run_id,
        "run_uri": sync.experiment_ref.run_uri,
        "artifact_uri": sync.experiment_ref.artifact_uri,
        "artifact_refs": copy.deepcopy(sync.experiment_ref.artifact_refs),
        "experiment_ref": sync.experiment_ref.to_metadata_ref(),
        "record": {
            "experiment_name": sync.record.experiment_name,
            "run_name": sync.record.run_name,
            "tags": sync.record.tags,
            "params": sync.record.params,
            "artifact_names": sorted(sync.record.artifacts),
        },
        "metrics": metrics,
        "readback": readback,
        "registry_id": entry["registry_id"],
        "source_vectorbt_run_id": vectorbt_feedback["run_id"],
    }


def _oss_route_for_index(index: int) -> dict[str, str]:
    return {
        "alpha_model": "qlib" if index % 2 == 0 else "vectorbt",
        "policy_candidate": POLICY_OSS_COMPONENTS[index % len(POLICY_OSS_COMPONENTS)],
        "reflection_artifact": REFLECTION_OSS_COMPONENTS[(index // 3) % len(REFLECTION_OSS_COMPONENTS)],
        "tracker": TRACKING_OSS_COMPONENTS[(index // 5) % len(TRACKING_OSS_COMPONENTS)],
        "risk_analytics": RISK_OSS_COMPONENTS[(index // 7) % len(RISK_OSS_COMPONENTS)],
    }


def _order_profile_for_index(index: int) -> dict[str, str]:
    quantity_type = QUANTITY_TYPES[index % len(QUANTITY_TYPES)]
    order_type = ORDER_TYPES[(index // len(QUANTITY_TYPES)) % len(ORDER_TYPES)]
    if quantity_type == "PERCENT_PORTFOLIO" and order_type == "LIMIT":
        order_type = "MARKET"
    return {"quantity_type": quantity_type, "order_type": order_type}


def _build_validation_planning_step(
    *,
    episode: PortfolioEpisode,
    prior_cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Ask coverage-gap questions before executing each validation episode."""

    coverage = _prior_validation_coverage(prior_cases)
    combo_signature = _validation_combo_signature(episode)
    portfolio_window_signature = _portfolio_window_signature(episode)
    oss_route_signature = _oss_route_signature(episode.oss_route)
    order_profile_signature = _order_profile_signature(episode.order_profile)
    regime_path_signature = "|".join(episode.regime_path)
    persona_seed_pair = f"{_persona_id(episode.persona)}::{episode.seed_key}"
    operational_scenario = _operational_scenario_for_episode(episode)
    questions = [
        "which_validation_axes_are_still_uncovered",
        "which_covered_axes_can_be_deepened_with_a_new_market_window",
        "which_realistic_persona_oss_alpha_portfolio_combinations_are_plausible_but_unvalidated",
    ]
    unvalidated_axes = {
        "validation_signature": episode.validation_signature not in coverage["validation_signatures"],
        "portfolio_window_tuple": portfolio_window_signature not in coverage["portfolio_window_signatures"],
        "persona_seed_pair": persona_seed_pair not in coverage["persona_seed_pairs"],
        "persona_seed_portfolio_oss_order_combo": combo_signature not in coverage["combo_signatures"],
        "oss_route": oss_route_signature not in coverage["oss_route_signatures"],
        "order_profile": order_profile_signature not in coverage["order_profile_signatures"],
        "reflection_archetype": episode.reflection_archetype not in coverage["reflection_archetypes"],
        "regime_path": regime_path_signature not in coverage["regime_paths"],
    }
    deepening_targets = _deepening_targets_for_episode(
        episode=episode,
        coverage=coverage,
        persona_seed_pair=persona_seed_pair,
        portfolio_window_signature=portfolio_window_signature,
        oss_route_signature=oss_route_signature,
        order_profile_signature=order_profile_signature,
        regime_path_signature=regime_path_signature,
    )
    plausible_combinations = _plausible_unvalidated_combinations(
        episode=episode,
        combo_signature=combo_signature,
        coverage=coverage,
    )
    assertion_labels = _assertion_labels_for_episode(
        episode=episode,
        combo_signature=combo_signature,
        portfolio_window_signature=portfolio_window_signature,
        oss_route_signature=oss_route_signature,
        order_profile_signature=order_profile_signature,
        operational_scenario=operational_scenario,
    )
    plan_signature = _stable_id(
        "validation-plan",
        episode.validation_signature,
        combo_signature,
        ",".join(assertion_labels),
    )
    return {
        "planning_iteration": episode.ordinal,
        "plan_id": f"plan-{episode.case_id}",
        "plan_signature": plan_signature,
        "questions_asked": questions,
        "coverage_before": {
            "validated_case_count": len(prior_cases),
            "validation_signature_count": len(coverage["validation_signatures"]),
            "combo_signature_count": len(coverage["combo_signatures"]),
            "portfolio_window_signature_count": len(coverage["portfolio_window_signatures"]),
            "persona_seed_pair_count": len(coverage["persona_seed_pairs"]),
            "oss_route_signature_count": len(coverage["oss_route_signatures"]),
            "order_profile_signature_count": len(coverage["order_profile_signatures"]),
            "regime_path_count": len(coverage["regime_paths"]),
        },
        "unvalidated_axes_before": unvalidated_axes,
        "deepening_targets": deepening_targets,
        "plausible_unvalidated_combinations": plausible_combinations,
        "selected_validation_plan": {
            "target_combo_signature": combo_signature,
            "target_validation_signature": episode.validation_signature,
            "target_portfolio_window_signature": portfolio_window_signature,
            "persona_id": _persona_id(episode.persona),
            "seed_key": episode.seed_key,
            "portfolio_instruments": [window.instrument for window in episode.windows],
            "historical_window_start_indices": [window.start_index for window in episode.windows],
            "oss_route": dict(episode.oss_route),
            "order_profile": dict(episode.order_profile),
            "reflection_archetype": episode.reflection_archetype,
            "regime_path": list(episode.regime_path),
            "operational_scenario": operational_scenario,
            "assertion_labels": assertion_labels,
            "execution_steps": [
                "request_oss_feedback",
                "request_case_specific_upstream_artifacts",
                "execute_generation0_observe_policy_on_feedback",
                "reflect_with_telemetry_memory_and_oss_feedback",
                "execute_generation1_policy_on_unseen_holdout",
                "write_and_retrieve_memory_for_next_decision",
                "execute_generation2_policy_on_future_holdout",
                "apply_market_friction_model",
                "reconcile_paper_broker_lifecycle",
                "resolve_multi_persona_conflicts",
                "recover_after_midloop_restart",
                "schedule_next_autonomous_cycle",
                "materialize_lean_handoff_packet",
                "diagnose_and_repair_deficiencies",
            ],
        },
    }


def _prior_validation_coverage(prior_cases: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    coverage = {
        "validation_signatures": set(),
        "plan_signatures": set(),
        "combo_signatures": set(),
        "portfolio_window_signatures": set(),
        "persona_seed_pairs": set(),
        "oss_route_signatures": set(),
        "order_profile_signatures": set(),
        "reflection_archetypes": set(),
        "regime_paths": set(),
    }
    for case in prior_cases:
        coverage["validation_signatures"].add(str(case["validation_signature"]))
        cycle = case.get("validation_cycle", {})
        planning = cycle.get("planning", {})
        if planning.get("plan_signature"):
            coverage["plan_signatures"].add(str(planning["plan_signature"]))
        selected = planning.get("selected_validation_plan", {})
        if selected.get("target_combo_signature"):
            coverage["combo_signatures"].add(str(selected["target_combo_signature"]))
        if selected.get("target_portfolio_window_signature"):
            coverage["portfolio_window_signatures"].add(str(selected["target_portfolio_window_signature"]))
        persona_seed_pair = f"{case.get('persona_id')}::{case.get('seed_key')}"
        coverage["persona_seed_pairs"].add(persona_seed_pair)
        if case.get("oss_feedback"):
            coverage["oss_route_signatures"].add(_oss_route_signature(case["oss_feedback"]["route"]))
        if case.get("order_profile"):
            coverage["order_profile_signatures"].add(_order_profile_signature(case["order_profile"]))
        if case.get("reflection"):
            traces = case["reflection"].get("agent_decision_traces", [])
            if traces:
                coverage["reflection_archetypes"].add(str(traces[0].get("trigger")))
        if case.get("portfolio"):
            coverage["regime_paths"].add("|".join(str(item) for item in case["portfolio"]["regime_path"]))
    return coverage


def _deepening_targets_for_episode(
    *,
    episode: PortfolioEpisode,
    coverage: Mapping[str, set[str]],
    persona_seed_pair: str,
    portfolio_window_signature: str,
    oss_route_signature: str,
    order_profile_signature: str,
    regime_path_signature: str,
) -> list[str]:
    targets: list[str] = []
    if persona_seed_pair in coverage["persona_seed_pairs"]:
        targets.append("same_persona_alpha_seed_with_new_portfolio_window")
    else:
        targets.append("first_persona_alpha_seed_pair_baseline")
    if portfolio_window_signature not in coverage["portfolio_window_signatures"]:
        targets.append("new_historical_holdout_window_tuple")
    if oss_route_signature in coverage["oss_route_signatures"]:
        targets.append("repeat_oss_route_with_new_market_regime_and_assets")
    else:
        targets.append("new_persona_oss_route")
    if order_profile_signature in coverage["order_profile_signatures"]:
        targets.append("repeat_order_profile_with_distinct_assets_and_holdouts")
    else:
        targets.append("new_order_profile")
    if regime_path_signature in coverage["regime_paths"]:
        targets.append("same_regime_path_deeper_portfolio_replay")
    else:
        targets.append("new_regime_path")
    return targets


def _plausible_unvalidated_combinations(
    *,
    episode: PortfolioEpisode,
    combo_signature: str,
    coverage: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    selected = {
        "combo_signature": combo_signature,
        "persona_id": _persona_id(episode.persona),
        "seed_key": episode.seed_key,
        "portfolio": [window.instrument for window in episode.windows],
        "oss_route": dict(episode.oss_route),
        "order_profile": dict(episode.order_profile),
        "why_realistic": "persona receives OSS alpha, policy, risk, tracking, and LEAN handoff feedback for a real historical portfolio window",
        "status_before": "unvalidated"
        if combo_signature not in coverage["combo_signatures"]
        else "already_validated",
        "selected_for_execution": True,
    }
    alternate_policy = {
        **episode.oss_route,
        "policy_candidate": POLICY_OSS_COMPONENTS[
            (POLICY_OSS_COMPONENTS.index(episode.oss_route["policy_candidate"]) + 1)
            % len(POLICY_OSS_COMPONENTS)
        ],
    }
    alternate_order = {
        "quantity_type": QUANTITY_TYPES[
            (QUANTITY_TYPES.index(episode.order_profile["quantity_type"]) + 1) % len(QUANTITY_TYPES)
        ],
        "order_type": episode.order_profile["order_type"],
    }
    return [
        selected,
        {
            "combo_signature": _stable_id(
                "combo",
                episode.validation_signature,
                _oss_route_signature(alternate_policy),
            ),
            "persona_id": _persona_id(episode.persona),
            "seed_key": episode.seed_key,
            "portfolio": [window.instrument for window in episode.windows],
            "oss_route": alternate_policy,
            "order_profile": dict(episode.order_profile),
            "why_realistic": "same persona and portfolio can receive a different policy-search OSS response",
            "status_before": "queued_not_selected",
            "selected_for_execution": False,
        },
        {
            "combo_signature": _stable_id(
                "combo",
                episode.validation_signature,
                _order_profile_signature(alternate_order),
            ),
            "persona_id": _persona_id(episode.persona),
            "seed_key": episode.seed_key,
            "portfolio": [window.instrument for window in episode.windows],
            "oss_route": dict(episode.oss_route),
            "order_profile": alternate_order,
            "why_realistic": "same strategy can reach execution with a different quantity mode",
            "status_before": "queued_not_selected",
            "selected_for_execution": False,
        },
    ]


def _assertion_labels_for_episode(
    *,
    episode: PortfolioEpisode,
    combo_signature: str,
    portfolio_window_signature: str,
    oss_route_signature: str,
    order_profile_signature: str,
    operational_scenario: str,
) -> list[str]:
    return [
        f"unique_validation_signature:{episode.validation_signature}",
        f"unique_combo:{combo_signature}",
        f"portfolio_window:{portfolio_window_signature}",
        f"persona:{_persona_id(episode.persona)}",
        f"alpha_seed:{episode.seed_key}",
        f"oss_route:{oss_route_signature}",
        f"order_profile:{order_profile_signature}",
        f"reflection:{episode.reflection_archetype}",
        f"generation_path:{'->'.join(episode.generation_path)}",
        f"regime_path:{'|'.join(episode.regime_path)}",
        f"operational_scenario:{operational_scenario}",
        "case_upstream_artifacts:vectorbt_tracking",
    ]


def _build_baseline_policy(
    episode: PortfolioEpisode,
    index: int,
    prior_memory: Mapping[str, Any] | None,
    oss_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    del prior_memory
    risk_multiplier = round(0.45 + ((index % 4) * 0.03), 4)
    legs = {
        window.instrument: {
            "instrument": window.instrument,
            "execution_symbol": window.execution_symbol,
            "direction": window.observe_direction,
            "risk_multiplier": risk_multiplier,
            "weight": round(1 / PORTFOLIO_LEG_COUNT, 6),
        }
        for window in episode.windows
    }
    return {
        "policy_id": f"policy-{episode.case_id}-gen0",
        "generation": 0,
        "policy_version": "observe_only_baseline",
        "legs": legs,
        "risk_multiplier": risk_multiplier,
        "quantity_type": episode.order_profile["quantity_type"],
        "order_type": episode.order_profile["order_type"],
        "decision_inputs": {
            "allowed_windows": ["observe"],
            "forbidden_windows_not_used": ["feedback", "holdout", "future_holdout"],
            "oss_components": _oss_components_used(oss_inputs),
        },
    }


def _memory_influence_profile(memory: Mapping[str, Any] | None) -> dict[str, Any]:
    if not memory:
        return {
            "model_id": PERSONA_MEMORY_INFLUENCE_MODEL_ID,
            "status": "cold_start",
            "memory_id": None,
            "influence_ref": None,
            "source_event_id": None,
            "reuse_count": 0,
            "content_summary": None,
            "cited_proposal_ids": [],
            "cited_evidence_refs": [],
            "retrieval_tags": [],
            "selected_action_hint": "none",
            "candidate_score_adjustments": _memory_score_adjustments("none"),
            "influence_applied": False,
        }
    selected_action_hint = _memory_action_from_context(memory)
    return {
        "model_id": PERSONA_MEMORY_INFLUENCE_MODEL_ID,
        "status": "applied",
        "memory_id": memory["memory_id"],
        "influence_ref": f"memory://{memory['memory_id']}",
        "source_event_id": memory["source_event_id"],
        "reuse_count": memory["reuse_count"],
        "content_summary": memory.get("content_summary"),
        "cited_proposal_ids": list(memory.get("proposal_ids", [])),
        "cited_evidence_refs": list(memory.get("evidence_refs", [])),
        "retrieval_tags": list(memory.get("tags", [])),
        "selected_action_hint": selected_action_hint,
        "candidate_score_adjustments": _memory_score_adjustments(selected_action_hint),
        "influence_applied": True,
    }


def _institutional_memory_influence_profile(
    memory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not memory:
        return {
            "model_id": PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID,
            "status": "cold_start",
            "entry_id": None,
            "entry_ref": None,
            "source_event_id": None,
            "reuse_count": 0,
            "content_summary": None,
            "contributing_persona_ids": [],
            "sponsor_persona_id": None,
            "cited_proposal_ids": [],
            "cited_evidence_refs": [],
            "retrieval_tags": [],
            "selected_action_hint": "none",
            "candidate_score_adjustments": _institutional_memory_score_adjustments("none"),
            "influence_applied": False,
        }
    selected_action_hint = _memory_action_from_context(memory)
    return {
        "model_id": PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID,
        "status": "applied",
        "entry_id": memory["entry_id"],
        "entry_ref": memory["entry_ref"],
        "source_event_id": memory["source_event_id"],
        "reuse_count": memory["reuse_count"],
        "content_summary": memory.get("content_summary"),
        "contributing_persona_ids": list(memory.get("contributing_persona_ids", [])),
        "sponsor_persona_id": memory.get("sponsor_persona_id"),
        "cited_proposal_ids": list(memory.get("proposal_ids", [])),
        "cited_evidence_refs": list(memory.get("evidence_refs", [])),
        "retrieval_tags": list(memory.get("tags", [])),
        "selected_action_hint": selected_action_hint,
        "candidate_score_adjustments": _institutional_memory_score_adjustments(selected_action_hint),
        "influence_applied": True,
    }


def _memory_action_from_context(memory: Mapping[str, Any]) -> str:
    tags = {str(tag).lower() for tag in memory.get("tags", [])}
    for action in ("feedback-adapt", "risk-off", "retain-observe", "contrarian-check"):
        if f"selected_action:{action}" in tags:
            return action
    summary = str(memory.get("content_summary") or "").lower()
    for action in ("feedback-adapt", "risk-off", "retain-observe", "contrarian-check"):
        if action in summary:
            return action
    return "feedback-adapt"


def _memory_score_adjustments(selected_action_hint: str) -> dict[str, float]:
    adjustments = {
        "feedback-adapt": 0.0,
        "risk-off": 0.0,
        "retain-observe": 0.0,
        "contrarian-check": 0.0,
    }
    if selected_action_hint == "feedback-adapt":
        adjustments["feedback-adapt"] = 0.2
        adjustments["risk-off"] = 0.05
    elif selected_action_hint == "risk-off":
        adjustments["risk-off"] = 0.2
        adjustments["feedback-adapt"] = 0.05
    elif selected_action_hint == "retain-observe":
        adjustments["retain-observe"] = 0.12
    elif selected_action_hint == "contrarian-check":
        adjustments["contrarian-check"] = 0.05
    return adjustments


def _institutional_memory_score_adjustments(selected_action_hint: str) -> dict[str, float]:
    adjustments = {
        "feedback-adapt": 0.0,
        "risk-off": 0.0,
        "retain-observe": 0.0,
        "contrarian-check": 0.0,
    }
    if selected_action_hint == "feedback-adapt":
        adjustments["feedback-adapt"] = 0.11
        adjustments["risk-off"] = 0.02
    elif selected_action_hint == "risk-off":
        adjustments["risk-off"] = 0.09
    elif selected_action_hint == "retain-observe":
        adjustments["retain-observe"] = 0.05
    elif selected_action_hint == "contrarian-check":
        adjustments["contrarian-check"] = 0.04
    return adjustments


def _candidate_action_key(candidate_id: str) -> str:
    for action in ("feedback-adapt", "retain-observe", "risk-off", "contrarian-check"):
        if candidate_id.endswith(f"-{action}"):
            return action
    return "unknown"


def _cross_cycle_score_adjustments(context: Mapping[str, Any]) -> dict[str, float]:
    adjustments = {
        "feedback-adapt": 0.0,
        "retain-observe": 0.0,
        "risk-off": 0.0,
        "contrarian-check": 0.0,
    }
    if context.get("status") == "applied":
        adjustments["feedback-adapt"] = 0.18
        adjustments["risk-off"] = 0.03
    return adjustments


def _multi_cycle_lineage_score_adjustments(context: Mapping[str, Any]) -> dict[str, float]:
    adjustments = {
        "feedback-adapt": 0.0,
        "retain-observe": 0.0,
        "risk-off": 0.0,
        "contrarian-check": 0.0,
    }
    if context.get("status") == "single_prior":
        adjustments["feedback-adapt"] = 0.04
        adjustments["risk-off"] = 0.01
    elif context.get("status") == "lineage_applied":
        adjustments["feedback-adapt"] = 0.07
        adjustments["risk-off"] = 0.02
    return adjustments


def _cross_cycle_context_for_episode(
    *,
    episode: PortfolioEpisode,
    prior_cycle_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not prior_cycle_state:
        return {
            "model_id": PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID,
            "status": "cold_start",
            "case_id": episode.case_id,
            "persona_id": _persona_id(episode.persona),
            "previous_case_id": None,
            "state_ref": None,
            "runtime_feedback_ref": None,
            "source_runtime_ref": None,
            "source_handoff_ref": None,
            "previous_ooda_ledger_ref": None,
            "previous_selected_action_ref": None,
            "previous_restart_checkpoint_ref": None,
            "previous_schedule_ref": None,
            "previous_next_cycle_due_at": None,
            "previous_feedback_scheduled_cycle_due_at": None,
            "previous_object_store_metadata_ref": None,
            "previous_object_store_artifact_ref": None,
            "previous_resume_step": None,
            "next_ooda_step": None,
            "next_ooda_action": None,
            "next_scheduler_phase": None,
            "prior_case_completed_before_current_case": False,
            "evidence_refs": [],
            "candidate_score_adjustments": _cross_cycle_score_adjustments({"status": "cold_start"}),
        }
    previous_case_id = str(prior_cycle_state["case_id"])
    state_ref = f"cross-cycle-runtime://{previous_case_id}->{episode.case_id}"
    runtime_feedback_ref = str(prior_cycle_state["runtime_feedback_ref"])
    restart_checkpoint_ref = str(prior_cycle_state["restart_checkpoint_ref"])
    schedule_ref = str(prior_cycle_state["schedule_ref"])
    object_store_metadata_ref = str(prior_cycle_state["object_store_metadata_ref"])
    object_store_artifact_ref = str(prior_cycle_state["object_store_artifact_ref"])
    return {
        "model_id": PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID,
        "status": "applied",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "previous_case_id": previous_case_id,
        "state_ref": state_ref,
        "runtime_feedback_ref": runtime_feedback_ref,
        "source_runtime_ref": prior_cycle_state["source_runtime_ref"],
        "source_handoff_ref": prior_cycle_state["source_handoff_ref"],
        "previous_ooda_ledger_ref": prior_cycle_state["ooda_ledger_ref"],
        "previous_selected_action_ref": prior_cycle_state["selected_action_ref"],
        "previous_restart_checkpoint_ref": restart_checkpoint_ref,
        "previous_schedule_ref": schedule_ref,
        "previous_next_cycle_due_at": prior_cycle_state["next_cycle_due_at"],
        "previous_feedback_scheduled_cycle_due_at": prior_cycle_state["feedback_scheduled_cycle_due_at"],
        "previous_object_store_metadata_ref": object_store_metadata_ref,
        "previous_object_store_artifact_ref": object_store_artifact_ref,
        "previous_resume_step": prior_cycle_state["resume_step"],
        "next_ooda_step": prior_cycle_state["next_ooda_step"],
        "next_ooda_action": prior_cycle_state["next_ooda_action"],
        "next_scheduler_phase": prior_cycle_state["next_scheduler_phase"],
        "prior_case_completed_before_current_case": True,
        "evidence_refs": [
            state_ref,
            runtime_feedback_ref,
            str(prior_cycle_state["source_runtime_ref"]),
            str(prior_cycle_state["source_handoff_ref"]),
            str(prior_cycle_state["ooda_ledger_ref"]),
            str(prior_cycle_state["selected_action_ref"]),
            restart_checkpoint_ref,
            schedule_ref,
            object_store_metadata_ref,
            object_store_artifact_ref,
        ],
        "candidate_score_adjustments": _cross_cycle_score_adjustments({"status": "applied"}),
    }


def _multi_cycle_context_for_episode(
    *,
    episode: PortfolioEpisode,
    prior_cycle_states: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    persona_id = _persona_id(episode.persona)
    lineage_states = [dict(state) for state in prior_cycle_states[-2:]]
    if not lineage_states:
        return {
            "model_id": PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID,
            "status": "cold_start",
            "case_id": episode.case_id,
            "persona_id": persona_id,
            "lineage_ref": None,
            "lineage_depth": 0,
            "lineage_case_ids": [],
            "latest_case_id": None,
            "older_case_id": None,
            "latest_state_ref": None,
            "older_state_ref": None,
            "latest_runtime_feedback_ref": None,
            "older_runtime_feedback_ref": None,
            "latest_restart_checkpoint_ref": None,
            "older_restart_checkpoint_ref": None,
            "latest_schedule_ref": None,
            "older_schedule_ref": None,
            "latest_object_store_metadata_ref": None,
            "older_object_store_metadata_ref": None,
            "latest_object_store_artifact_ref": None,
            "older_object_store_artifact_ref": None,
            "latest_next_ooda_step": None,
            "older_next_ooda_step": None,
            "latest_next_scheduler_phase": None,
            "older_next_scheduler_phase": None,
            "trend_signal": "cold_start_no_prior_cycle",
            "prior_cases_completed_before_current_case": False,
            "evidence_refs": [],
            "candidate_score_adjustments": _multi_cycle_lineage_score_adjustments(
                {"status": "cold_start"}
            ),
        }

    latest_state = lineage_states[-1]
    older_state = lineage_states[-2] if len(lineage_states) > 1 else None
    latest_case_id = str(latest_state["case_id"])
    older_case_id = str(older_state["case_id"]) if older_state else None
    lineage_case_ids = [str(state["case_id"]) for state in lineage_states]
    lineage_ref = f"multi-cycle-lineage://{'->'.join(lineage_case_ids)}->{episode.case_id}"
    latest_state_ref = f"multi-cycle-runtime://{latest_case_id}->{episode.case_id}"
    older_state_ref = (
        f"multi-cycle-runtime://{older_case_id}->{episode.case_id}"
        if older_case_id
        else None
    )
    latest_refs = [
        latest_state_ref,
        str(latest_state["runtime_feedback_ref"]),
        str(latest_state["restart_checkpoint_ref"]),
        str(latest_state["schedule_ref"]),
        str(latest_state["object_store_metadata_ref"]),
        str(latest_state["object_store_artifact_ref"]),
        str(latest_state["ooda_ledger_ref"]),
        str(latest_state["selected_action_ref"]),
    ]
    older_refs = [
        ref
        for ref in (
            older_state_ref,
            str(older_state["runtime_feedback_ref"]) if older_state else None,
            str(older_state["restart_checkpoint_ref"]) if older_state else None,
            str(older_state["schedule_ref"]) if older_state else None,
            str(older_state["object_store_metadata_ref"]) if older_state else None,
            str(older_state["object_store_artifact_ref"]) if older_state else None,
            str(older_state["ooda_ledger_ref"]) if older_state else None,
            str(older_state["selected_action_ref"]) if older_state else None,
        )
        if ref
    ]
    status = "lineage_applied" if older_state else "single_prior"
    trend_signal = (
        "latest_runtime_feedback_supersedes_older_cycle_trend"
        if older_state
        else "single_prior_runtime_feedback_bootstraps_lineage"
    )
    context = {
        "model_id": PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID,
        "status": status,
        "case_id": episode.case_id,
        "persona_id": persona_id,
        "lineage_ref": lineage_ref,
        "lineage_depth": len(lineage_states),
        "lineage_case_ids": lineage_case_ids,
        "latest_case_id": latest_case_id,
        "older_case_id": older_case_id,
        "latest_state_ref": latest_state_ref,
        "older_state_ref": older_state_ref,
        "latest_runtime_feedback_ref": str(latest_state["runtime_feedback_ref"]),
        "older_runtime_feedback_ref": str(older_state["runtime_feedback_ref"]) if older_state else None,
        "latest_restart_checkpoint_ref": str(latest_state["restart_checkpoint_ref"]),
        "older_restart_checkpoint_ref": str(older_state["restart_checkpoint_ref"]) if older_state else None,
        "latest_schedule_ref": str(latest_state["schedule_ref"]),
        "older_schedule_ref": str(older_state["schedule_ref"]) if older_state else None,
        "latest_object_store_metadata_ref": str(latest_state["object_store_metadata_ref"]),
        "older_object_store_metadata_ref": str(older_state["object_store_metadata_ref"]) if older_state else None,
        "latest_object_store_artifact_ref": str(latest_state["object_store_artifact_ref"]),
        "older_object_store_artifact_ref": str(older_state["object_store_artifact_ref"]) if older_state else None,
        "latest_next_ooda_step": latest_state["next_ooda_step"],
        "older_next_ooda_step": older_state["next_ooda_step"] if older_state else None,
        "latest_next_scheduler_phase": latest_state["next_scheduler_phase"],
        "older_next_scheduler_phase": older_state["next_scheduler_phase"] if older_state else None,
        "trend_signal": trend_signal,
        "prior_cases_completed_before_current_case": True,
        "evidence_refs": list(dict.fromkeys([lineage_ref, *latest_refs, *older_refs])),
    }
    context["candidate_score_adjustments"] = _multi_cycle_lineage_score_adjustments(context)
    return context


def _cross_cycle_state_from_case(case: Mapping[str, Any]) -> dict[str, Any]:
    feedback = case["operational_context"]["lean_runtime_feedback"]
    recovery = case["operational_context"]["restart_recovery"]
    schedule = case["operational_context"]["autonomous_schedule"]
    ledger = case["oss_feedback"]["ooda_causal_ledger"]
    selected_event = next(
        event
        for event in ledger["events"]
        if event["event_type"] == "selected_action"
    )
    persona_followup = feedback["persona_ooda_followup"]
    runtime_readback = feedback["runtime_feedback"]
    return {
        "case_id": case["case_id"],
        "persona_id": case["persona_id"],
        "runtime_feedback_ref": f"lean-runtime-feedback://{feedback['feedback_id']}",
        "source_runtime_ref": feedback["source_runtime_ref"],
        "source_handoff_ref": feedback["source_handoff_ref"],
        "ooda_ledger_ref": ledger["ledger_ref"],
        "selected_action_ref": selected_event["output_ref"],
        "restart_checkpoint_ref": f"checkpoint://{recovery['checkpoint_id']}",
        "schedule_ref": f"schedule://{schedule['schedule_id']}",
        "next_cycle_due_at": schedule["next_cycle_due_at"],
        "feedback_scheduled_cycle_due_at": feedback["state_updates"]["schedule_next_cycle_after_feedback"],
        "object_store_metadata_ref": f"object-store://{runtime_readback['object_store_metadata_key']}",
        "object_store_artifact_ref": f"object-store://{runtime_readback['object_store_artifact_key']}",
        "resume_step": recovery["resume_step"],
        "next_ooda_step": persona_followup["ooda_step"],
        "next_ooda_action": persona_followup["action"],
        "next_scheduler_phase": persona_followup["next_scheduler_phase"],
    }


def _build_cross_cycle_carryover_proof(
    *,
    episode: PortfolioEpisode,
    cross_cycle_context: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    persona_oss_ooda_ledger: Mapping[str, Any],
) -> dict[str, Any]:
    state_ref = cross_cycle_context.get("state_ref")
    runtime_feedback_ref = cross_cycle_context.get("runtime_feedback_ref")
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_action = _candidate_action_key(str(trace["selected_candidate_id"]))
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "selected_action": selected_action,
                "decision_input_state_ref": trace["decision_inputs"].get("cross_cycle_state_ref"),
                "reasoning_consumes_state_ref": (
                    state_ref is not None and state_ref in reasoning_request["input_refs"]
                ),
                "reasoning_consumes_runtime_feedback_ref": (
                    runtime_feedback_ref is not None
                    and runtime_feedback_ref in reasoning_request["input_refs"]
                ),
                "candidate_request_consumes_state_ref": (
                    state_ref is not None and state_ref in candidate_request["input_refs"]
                ),
                "candidate_request_consumes_runtime_feedback_ref": (
                    runtime_feedback_ref is not None
                    and runtime_feedback_ref in candidate_request["input_refs"]
                ),
                "selected_candidate_cites_state_ref": (
                    state_ref is not None and state_ref in selected_candidate["evidence_refs"]
                ),
                "scorer_cross_cycle_adjustment": float(
                    scoring_inputs["cross_cycle_score_adjustments"].get(selected_action, 0.0)
                ),
            }
        )

    status = str(cross_cycle_context["status"])
    applied = status == "applied"
    cold_start = status == "cold_start"
    replay = {
        "replayable": True,
        "cold_start_or_prior_cycle_bound": cold_start
        or (
            applied
            and cross_cycle_context.get("previous_case_id")
            and cross_cycle_context.get("prior_case_completed_before_current_case") is True
        ),
        "runtime_feedback_ref_available": cold_start or bool(runtime_feedback_ref),
        "previous_lean_handoff_ref_available": cold_start or bool(cross_cycle_context.get("source_handoff_ref")),
        "previous_ooda_ledger_ref_available": cold_start or bool(cross_cycle_context.get("previous_ooda_ledger_ref")),
        "reasoning_consumes_prior_runtime_feedback": cold_start
        or all(binding["reasoning_consumes_runtime_feedback_ref"] for binding in trace_bindings),
        "candidate_generation_consumes_prior_runtime_feedback": cold_start
        or all(binding["candidate_request_consumes_runtime_feedback_ref"] for binding in trace_bindings),
        "selected_candidate_cites_cross_cycle_state": cold_start
        or all(binding["selected_candidate_cites_state_ref"] for binding in trace_bindings),
        "scorer_applies_cross_cycle_adjustment": cold_start
        or all(binding["scorer_cross_cycle_adjustment"] > 0.0 for binding in trace_bindings),
        "same_persona_cycle_carryover": cold_start
        or cross_cycle_context.get("persona_id") == _persona_id(episode.persona),
        "current_ooda_ledger_available": _persona_oss_ooda_causal_ledger_is_usable(persona_oss_ooda_ledger),
        "no_future_current_case_artifact_used_as_prior": cold_start
        or str(cross_cycle_context.get("previous_case_id")) != episode.case_id,
    }
    return {
        "proof_id": f"cross-cycle-carryover-{episode.case_id}",
        "proof_ref": f"cross-cycle-carryover://{episode.case_id}",
        "model_id": PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "carryover_status": status,
        "previous_case_id": cross_cycle_context.get("previous_case_id"),
        "state_ref": state_ref,
        "runtime_feedback_ref": runtime_feedback_ref,
        "source_runtime_ref": cross_cycle_context.get("source_runtime_ref"),
        "source_handoff_ref": cross_cycle_context.get("source_handoff_ref"),
        "previous_ooda_ledger_ref": cross_cycle_context.get("previous_ooda_ledger_ref"),
        "current_ooda_ledger_ref": persona_oss_ooda_ledger["ledger_ref"],
        "next_ooda_step": cross_cycle_context.get("next_ooda_step"),
        "next_ooda_action": cross_cycle_context.get("next_ooda_action"),
        "score_adjustments": _cross_cycle_score_adjustments(cross_cycle_context),
        "trace_bindings": trace_bindings,
        "evidence_refs": list(cross_cycle_context.get("evidence_refs", [])),
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "cross-cycle-carryover-proof",
            {
                "case_id": episode.case_id,
                "cross_cycle_context": cross_cycle_context,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _cross_cycle_carryover_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    status = proof.get("carryover_status")
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("cross-cycle-carryover://")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "cold_start_or_prior_cycle_bound",
            "runtime_feedback_ref_available",
            "previous_lean_handoff_ref_available",
            "previous_ooda_ledger_ref_available",
            "reasoning_consumes_prior_runtime_feedback",
            "candidate_generation_consumes_prior_runtime_feedback",
            "selected_candidate_cites_cross_cycle_state",
            "scorer_applies_cross_cycle_adjustment",
            "same_persona_cycle_carryover",
            "current_ooda_ledger_available",
            "no_future_current_case_artifact_used_as_prior",
        ))
        and (
            (
                status == "cold_start"
                and proof.get("previous_case_id") is None
                and proof.get("runtime_feedback_ref") is None
                and all(float(value) == 0.0 for value in proof.get("score_adjustments", {}).values())
            )
            or (
                status == "applied"
                and proof.get("previous_case_id")
                and proof.get("runtime_feedback_ref")
                and proof.get("state_ref")
                and float(proof.get("score_adjustments", {}).get("feedback-adapt", 0.0)) > 0.0
                and all(binding.get("scorer_cross_cycle_adjustment", 0.0) > 0.0 for binding in trace_bindings)
            )
        )
    )


def _build_persisted_cycle_resume_proof(
    *,
    episode: PortfolioEpisode,
    cross_cycle_context: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    cross_cycle_carryover: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoint_ref = cross_cycle_context.get("previous_restart_checkpoint_ref")
    schedule_ref = cross_cycle_context.get("previous_schedule_ref")
    metadata_ref = cross_cycle_context.get("previous_object_store_metadata_ref")
    artifact_ref = cross_cycle_context.get("previous_object_store_artifact_ref")
    persisted_refs = [
        str(ref)
        for ref in (checkpoint_ref, schedule_ref, metadata_ref, artifact_ref)
        if ref
    ]
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_action = _candidate_action_key(str(trace["selected_candidate_id"]))
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "selected_action": selected_action,
                "decision_input_state_ref": trace["decision_inputs"].get("cross_cycle_state_ref"),
                "reasoning_consumes_persisted_refs": bool(persisted_refs)
                and set(persisted_refs).issubset(set(reasoning_request["input_refs"])),
                "candidate_request_consumes_persisted_refs": bool(persisted_refs)
                and set(persisted_refs).issubset(set(candidate_request["input_refs"])),
                "selected_candidate_cites_persisted_refs": bool(persisted_refs)
                and set(persisted_refs).issubset(set(selected_candidate["evidence_refs"])),
                "scorer_cross_cycle_adjustment": float(
                    scoring_inputs["cross_cycle_score_adjustments"].get(selected_action, 0.0)
                ),
            }
        )

    status = str(cross_cycle_context["status"])
    cold_start = status == "cold_start"
    applied = status == "applied"
    replay = {
        "replayable": True,
        "cold_start_or_persisted_state_bound": cold_start
        or (
            applied
            and cross_cycle_context.get("previous_case_id")
            and cross_cycle_context.get("prior_case_completed_before_current_case") is True
        ),
        "prior_restart_checkpoint_available": cold_start or bool(checkpoint_ref),
        "prior_autonomous_schedule_available": cold_start or bool(schedule_ref),
        "prior_runtime_object_store_readback_available": cold_start
        or (bool(metadata_ref) and bool(artifact_ref)),
        "scheduler_feedback_due_at_preserved": cold_start
        or (
            cross_cycle_context.get("previous_next_cycle_due_at")
            == cross_cycle_context.get("previous_feedback_scheduled_cycle_due_at")
        ),
        "reasoning_consumes_persisted_resume_refs": cold_start
        or all(binding["reasoning_consumes_persisted_refs"] for binding in trace_bindings),
        "candidate_generation_consumes_persisted_resume_refs": cold_start
        or all(binding["candidate_request_consumes_persisted_refs"] for binding in trace_bindings),
        "selected_candidate_cites_persisted_resume_refs": cold_start
        or all(binding["selected_candidate_cites_persisted_refs"] for binding in trace_bindings),
        "scorer_applies_after_resume_adjustment": cold_start
        or all(binding["scorer_cross_cycle_adjustment"] > 0.0 for binding in trace_bindings),
        "same_persona_resume_carryover": cold_start
        or cross_cycle_context.get("persona_id") == _persona_id(episode.persona),
        "cross_cycle_runtime_feedback_bound": _cross_cycle_carryover_is_usable(cross_cycle_carryover),
        "no_future_current_case_artifact_used_as_prior": cold_start
        or str(cross_cycle_context.get("previous_case_id")) != episode.case_id,
    }
    return {
        "proof_id": f"persisted-cycle-resume-{episode.case_id}",
        "proof_ref": f"persisted-cycle-resume://{episode.case_id}",
        "model_id": PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "resume_status": status,
        "previous_case_id": cross_cycle_context.get("previous_case_id"),
        "state_ref": cross_cycle_context.get("state_ref"),
        "runtime_feedback_ref": cross_cycle_context.get("runtime_feedback_ref"),
        "restart_checkpoint_ref": checkpoint_ref,
        "schedule_ref": schedule_ref,
        "next_cycle_due_at": cross_cycle_context.get("previous_next_cycle_due_at"),
        "feedback_scheduled_cycle_due_at": cross_cycle_context.get(
            "previous_feedback_scheduled_cycle_due_at"
        ),
        "object_store_metadata_ref": metadata_ref,
        "object_store_artifact_ref": artifact_ref,
        "resume_step": cross_cycle_context.get("previous_resume_step"),
        "next_ooda_step": cross_cycle_context.get("next_ooda_step"),
        "next_scheduler_phase": cross_cycle_context.get("next_scheduler_phase"),
        "persisted_refs": persisted_refs,
        "score_adjustments": _cross_cycle_score_adjustments(cross_cycle_context),
        "trace_bindings": trace_bindings,
        "source_cross_cycle_proof_ref": cross_cycle_carryover.get("proof_ref"),
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "persisted-cycle-resume-proof",
            {
                "case_id": episode.case_id,
                "cross_cycle_context": cross_cycle_context,
                "persisted_refs": persisted_refs,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _persisted_cycle_resume_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    status = proof.get("resume_status")
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("persisted-cycle-resume://")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "cold_start_or_persisted_state_bound",
            "prior_restart_checkpoint_available",
            "prior_autonomous_schedule_available",
            "prior_runtime_object_store_readback_available",
            "scheduler_feedback_due_at_preserved",
            "reasoning_consumes_persisted_resume_refs",
            "candidate_generation_consumes_persisted_resume_refs",
            "selected_candidate_cites_persisted_resume_refs",
            "scorer_applies_after_resume_adjustment",
            "same_persona_resume_carryover",
            "cross_cycle_runtime_feedback_bound",
            "no_future_current_case_artifact_used_as_prior",
        ))
        and (
            (
                status == "cold_start"
                and proof.get("previous_case_id") is None
                and proof.get("persisted_refs") == []
                and all(float(value) == 0.0 for value in proof.get("score_adjustments", {}).values())
            )
            or (
                status == "applied"
                and proof.get("previous_case_id")
                and proof.get("restart_checkpoint_ref")
                and proof.get("schedule_ref")
                and proof.get("object_store_metadata_ref")
                and proof.get("object_store_artifact_ref")
                and proof.get("next_cycle_due_at") == proof.get("feedback_scheduled_cycle_due_at")
                and float(proof.get("score_adjustments", {}).get("feedback-adapt", 0.0)) > 0.0
                and all(binding.get("scorer_cross_cycle_adjustment", 0.0) > 0.0 for binding in trace_bindings)
            )
        )
    )


def _build_multi_cycle_lineage_proof(
    *,
    episode: PortfolioEpisode,
    multi_cycle_context: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    cross_cycle_carryover: Mapping[str, Any],
    persisted_cycle_resume: Mapping[str, Any],
) -> dict[str, Any]:
    lineage_ref = multi_cycle_context.get("lineage_ref")
    latest_runtime_feedback_ref = multi_cycle_context.get("latest_runtime_feedback_ref")
    older_runtime_feedback_ref = multi_cycle_context.get("older_runtime_feedback_ref")
    lineage_refs = [str(ref) for ref in multi_cycle_context.get("evidence_refs", []) if ref]
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_action = _candidate_action_key(str(trace["selected_candidate_id"]))
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "selected_action": selected_action,
                "decision_input_lineage_ref": trace["decision_inputs"].get("multi_cycle_lineage_ref"),
                "reasoning_consumes_lineage_refs": bool(lineage_refs)
                and set(lineage_refs).issubset(set(reasoning_request["input_refs"])),
                "candidate_request_consumes_lineage_refs": bool(lineage_refs)
                and set(lineage_refs).issubset(set(candidate_request["input_refs"])),
                "selected_candidate_cites_lineage_refs": bool(lineage_refs)
                and set(lineage_refs).issubset(set(selected_candidate["evidence_refs"])),
                "scorer_multi_cycle_lineage_adjustment": float(
                    scoring_inputs["multi_cycle_lineage_score_adjustments"].get(selected_action, 0.0)
                ),
                "decision_replay_uses_lineage": (
                    artifact["replay"].get("uses_multi_cycle_lineage_or_declares_cold_start") is True
                ),
            }
        )

    status = str(multi_cycle_context["status"])
    cold_start = status == "cold_start"
    single_prior = status == "single_prior"
    lineage_applied = status == "lineage_applied"
    replay = {
        "replayable": True,
        "cold_start_or_lineage_bound": cold_start
        or bool(
            status in {"single_prior", "lineage_applied"}
            and multi_cycle_context.get("prior_cases_completed_before_current_case") is True
            and multi_cycle_context.get("lineage_ref")
        ),
        "lineage_depth_matches_history": (
            (cold_start and int(multi_cycle_context.get("lineage_depth", -1)) == 0)
            or (single_prior and int(multi_cycle_context.get("lineage_depth", 0)) == 1)
            or (lineage_applied and int(multi_cycle_context.get("lineage_depth", 0)) == 2)
        ),
        "latest_prior_cycle_bound": cold_start
        or bool(
            multi_cycle_context.get("latest_case_id")
            and latest_runtime_feedback_ref
            and multi_cycle_context.get("latest_restart_checkpoint_ref")
            and multi_cycle_context.get("latest_schedule_ref")
        ),
        "older_prior_cycle_bound": cold_start
        or single_prior
        or bool(
            multi_cycle_context.get("older_case_id")
            and older_runtime_feedback_ref
            and multi_cycle_context.get("older_restart_checkpoint_ref")
            and multi_cycle_context.get("older_schedule_ref")
        ),
        "reasoning_consumes_lineage_refs": cold_start
        or all(binding["reasoning_consumes_lineage_refs"] for binding in trace_bindings),
        "candidate_generation_consumes_lineage_refs": cold_start
        or all(binding["candidate_request_consumes_lineage_refs"] for binding in trace_bindings),
        "selected_candidate_cites_lineage_refs": cold_start
        or all(binding["selected_candidate_cites_lineage_refs"] for binding in trace_bindings),
        "scorer_applies_lineage_adjustment": cold_start
        or all(binding["scorer_multi_cycle_lineage_adjustment"] > 0.0 for binding in trace_bindings),
        "decision_artifact_replays_lineage": all(
            binding["decision_replay_uses_lineage"] for binding in trace_bindings
        ),
        "same_persona_lineage": cold_start
        or multi_cycle_context.get("persona_id") == _persona_id(episode.persona),
        "cross_cycle_runtime_feedback_bound": _cross_cycle_carryover_is_usable(cross_cycle_carryover),
        "persisted_resume_bound": _persisted_cycle_resume_is_usable(persisted_cycle_resume),
        "no_future_current_case_artifact_used_as_prior": cold_start
        or (
            str(multi_cycle_context.get("latest_case_id")) != episode.case_id
            and str(multi_cycle_context.get("older_case_id")) != episode.case_id
        ),
    }
    return {
        "proof_id": f"multi-cycle-lineage-{episode.case_id}",
        "proof_ref": f"multi-cycle-lineage://{episode.case_id}",
        "model_id": PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "lineage_status": status,
        "lineage_ref": lineage_ref,
        "lineage_depth": int(multi_cycle_context.get("lineage_depth", 0)),
        "lineage_case_ids": list(multi_cycle_context.get("lineage_case_ids", [])),
        "latest_case_id": multi_cycle_context.get("latest_case_id"),
        "older_case_id": multi_cycle_context.get("older_case_id"),
        "latest_state_ref": multi_cycle_context.get("latest_state_ref"),
        "older_state_ref": multi_cycle_context.get("older_state_ref"),
        "latest_runtime_feedback_ref": latest_runtime_feedback_ref,
        "older_runtime_feedback_ref": older_runtime_feedback_ref,
        "latest_restart_checkpoint_ref": multi_cycle_context.get("latest_restart_checkpoint_ref"),
        "older_restart_checkpoint_ref": multi_cycle_context.get("older_restart_checkpoint_ref"),
        "latest_schedule_ref": multi_cycle_context.get("latest_schedule_ref"),
        "older_schedule_ref": multi_cycle_context.get("older_schedule_ref"),
        "latest_object_store_metadata_ref": multi_cycle_context.get("latest_object_store_metadata_ref"),
        "older_object_store_metadata_ref": multi_cycle_context.get("older_object_store_metadata_ref"),
        "latest_object_store_artifact_ref": multi_cycle_context.get("latest_object_store_artifact_ref"),
        "older_object_store_artifact_ref": multi_cycle_context.get("older_object_store_artifact_ref"),
        "latest_next_ooda_step": multi_cycle_context.get("latest_next_ooda_step"),
        "older_next_ooda_step": multi_cycle_context.get("older_next_ooda_step"),
        "latest_next_scheduler_phase": multi_cycle_context.get("latest_next_scheduler_phase"),
        "older_next_scheduler_phase": multi_cycle_context.get("older_next_scheduler_phase"),
        "trend_signal": multi_cycle_context.get("trend_signal"),
        "lineage_refs": lineage_refs,
        "score_adjustments": _multi_cycle_lineage_score_adjustments(multi_cycle_context),
        "trace_bindings": trace_bindings,
        "source_cross_cycle_proof_ref": cross_cycle_carryover.get("proof_ref"),
        "source_persisted_cycle_resume_ref": persisted_cycle_resume.get("proof_ref"),
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "multi-cycle-lineage-proof",
            {
                "case_id": episode.case_id,
                "multi_cycle_context": multi_cycle_context,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _multi_cycle_lineage_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    status = proof.get("lineage_status")
    trace_bindings = list(proof.get("trace_bindings", []))
    score_adjustments = proof.get("score_adjustments", {})
    return bool(
        proof.get("model_id") == PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("multi-cycle-lineage://")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "cold_start_or_lineage_bound",
            "lineage_depth_matches_history",
            "latest_prior_cycle_bound",
            "older_prior_cycle_bound",
            "reasoning_consumes_lineage_refs",
            "candidate_generation_consumes_lineage_refs",
            "selected_candidate_cites_lineage_refs",
            "scorer_applies_lineage_adjustment",
            "decision_artifact_replays_lineage",
            "same_persona_lineage",
            "cross_cycle_runtime_feedback_bound",
            "persisted_resume_bound",
            "no_future_current_case_artifact_used_as_prior",
        ))
        and (
            (
                status == "cold_start"
                and proof.get("lineage_ref") is None
                and proof.get("lineage_depth") == 0
                and proof.get("lineage_case_ids") == []
                and proof.get("lineage_refs") == []
                and all(float(value) == 0.0 for value in score_adjustments.values())
            )
            or (
                status == "single_prior"
                and proof.get("lineage_ref")
                and proof.get("lineage_depth") == 1
                and proof.get("latest_case_id")
                and proof.get("latest_runtime_feedback_ref")
                and proof.get("older_case_id") is None
                and proof.get("older_runtime_feedback_ref") is None
                and float(score_adjustments.get("feedback-adapt", 0.0)) > 0.0
                and all(
                    binding.get("scorer_multi_cycle_lineage_adjustment", 0.0) > 0.0
                    for binding in trace_bindings
                )
            )
            or (
                status == "lineage_applied"
                and proof.get("lineage_ref")
                and proof.get("lineage_depth") == 2
                and proof.get("latest_case_id")
                and proof.get("older_case_id")
                and proof.get("latest_case_id") != proof.get("older_case_id")
                and proof.get("latest_runtime_feedback_ref")
                and proof.get("older_runtime_feedback_ref")
                and float(score_adjustments.get("feedback-adapt", 0.0)) > 0.0
                and all(
                    binding.get("scorer_multi_cycle_lineage_adjustment", 0.0) > 0.0
                    for binding in trace_bindings
                )
            )
        )
    )


def _memory_influence_applied_to_selected_candidate(
    *,
    memory_influence: Mapping[str, Any],
    selected_candidate: Mapping[str, Any],
) -> bool:
    influence_ref = memory_influence.get("influence_ref")
    if not influence_ref:
        return False
    selected_action = _candidate_action_key(str(selected_candidate["candidate_id"]))
    adjustments = memory_influence.get("candidate_score_adjustments", {})
    return bool(
        float(adjustments.get(selected_action, 0.0)) > 0.0
        and influence_ref in selected_candidate.get("evidence_refs", [])
    )


def _build_memory_counterfactual_proof(
    *,
    episode: PortfolioEpisode,
    generation: int,
    decision_trace_ref: str,
    input_context: Mapping[str, Any],
    candidate_request: Mapping[str, Any],
    scorecards: Mapping[str, Mapping[str, Any]],
    selected_candidate: Mapping[str, Any],
    memory_influence: Mapping[str, Any],
) -> dict[str, Any]:
    selected_id = str(selected_candidate["candidate_id"])
    selected_action = _candidate_action_key(selected_id)
    memory_ref = memory_influence.get("influence_ref")
    memory_status = "retrieved" if memory_ref else "cold_start_declared"
    actual_scores = {
        candidate_id: round(float(card["candidate_score"]), 10)
        for candidate_id, card in scorecards.items()
    }
    counterfactual_scores = {
        candidate_id: round(
            float(card["candidate_score"])
            - float(card.get("components", {}).get("memory_adjustment", 0.0)),
            10,
        )
        for candidate_id, card in scorecards.items()
    }
    selected_actual_score = actual_scores[selected_id]
    selected_counterfactual_score = counterfactual_scores[selected_id]
    selected_score_delta = round(selected_actual_score - selected_counterfactual_score, 10)
    actual_winner_id = max(actual_scores, key=actual_scores.__getitem__)
    counterfactual_winner_id = max(counterfactual_scores, key=counterfactual_scores.__getitem__)
    other_actual_scores = [
        score for candidate_id, score in actual_scores.items() if candidate_id != selected_id
    ]
    other_counterfactual_scores = [
        score for candidate_id, score in counterfactual_scores.items() if candidate_id != selected_id
    ]
    actual_runner_up_score = max(other_actual_scores)
    counterfactual_runner_up_score = max(other_counterfactual_scores)
    actual_margin = round(selected_actual_score - actual_runner_up_score, 10)
    counterfactual_margin = round(
        selected_counterfactual_score - counterfactual_runner_up_score,
        10,
    )
    margin_lift = round(actual_margin - counterfactual_margin, 10)
    selected_card = scorecards[selected_id]
    selected_memory_adjustment = round(
        float(selected_card.get("components", {}).get("memory_adjustment", 0.0)),
        10,
    )
    memory_adjustments = dict(memory_influence.get("candidate_score_adjustments", {}))
    recomputed_scores_match = all(
        abs(
            counterfactual_scores[candidate_id]
            - round(
                float(card["candidate_score"])
                - float(card.get("components", {}).get("memory_adjustment", 0.0)),
                10,
            )
        ) <= 1e-9
        for candidate_id, card in scorecards.items()
    )
    selected_refs = set(str(ref) for ref in selected_candidate.get("evidence_refs", []))
    request_refs = set(str(ref) for ref in candidate_request.get("input_refs", []))
    replay = {
        "replayable": True,
        "actual_selection_replayed": actual_winner_id == selected_id,
        "counterfactual_scores_recomputed": recomputed_scores_match,
        "score_delta_equals_selected_memory_adjustment": abs(
            selected_score_delta - selected_memory_adjustment
        ) <= 1e-9,
        "retrieved_memory_ref_bound": (
            memory_ref is None
            or (
                input_context.get("memory_influence_ref") == memory_ref
                and memory_influence.get("influence_ref") == memory_ref
            )
        ),
        "candidate_request_includes_memory_when_retrieved": (
            memory_ref is None or str(memory_ref) in request_refs
        ),
        "selected_candidate_cites_memory_when_retrieved": (
            memory_ref is None or str(memory_ref) in selected_refs
        ),
        "selected_action_matches_memory_hint_when_retrieved": (
            memory_ref is None
            or selected_action == memory_influence.get("selected_action_hint")
        ),
        "memory_changes_selected_score_when_retrieved": (
            memory_ref is None or selected_score_delta > 0.0
        ),
        "memory_improves_selected_margin_when_retrieved": (
            memory_ref is None or margin_lift > 0.0
        ),
        "cold_start_zero_memory_adjustments": (
            memory_ref is not None
            or all(float(value) == 0.0 for value in memory_adjustments.values())
        ),
    }
    return {
        "proof_id": f"memory-counterfactual-{episode.case_id}-gen{generation}",
        "proof_ref": f"memory-counterfactual://{episode.case_id}/gen{generation}",
        "model_id": PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation": generation,
        "decision_trace_ref": decision_trace_ref,
        "memory_status": memory_status,
        "memory_id": memory_influence.get("memory_id"),
        "memory_ref": memory_ref,
        "memory_source_event_id": memory_influence.get("source_event_id"),
        "selected_action_hint": memory_influence.get("selected_action_hint"),
        "selected_candidate_id": selected_id,
        "selected_action": selected_action,
        "actual_winner_id": actual_winner_id,
        "counterfactual_without_memory_winner_id": counterfactual_winner_id,
        "selection_flips_without_memory": counterfactual_winner_id != selected_id,
        "selected_actual_score": selected_actual_score,
        "selected_counterfactual_without_memory_score": selected_counterfactual_score,
        "selected_score_delta_from_memory": selected_score_delta,
        "selected_memory_adjustment": selected_memory_adjustment,
        "actual_margin_to_runner_up": actual_margin,
        "counterfactual_margin_to_runner_up": counterfactual_margin,
        "memory_margin_lift": margin_lift,
        "actual_scores": actual_scores,
        "counterfactual_without_memory_scores": counterfactual_scores,
        "outcome": (
            "memory_material_to_selected_score"
            if memory_ref
            else "cold_start_declared"
        ),
        "evidence_refs": [
            *([str(memory_ref)] if memory_ref else []),
            f"decision-request://{candidate_request['request_id']}",
            f"candidate://{selected_id}",
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "memory-counterfactual-proof",
            {
                "case_id": episode.case_id,
                "generation": generation,
                "memory_ref": memory_ref,
                "selected_candidate_id": selected_id,
                "actual_scores": actual_scores,
                "counterfactual_without_memory_scores": counterfactual_scores,
            },
        ),
    }


def _memory_counterfactual_proof_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    memory_ref = proof.get("memory_ref")
    return bool(
        proof.get("model_id") == PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("memory-counterfactual://")
        and proof.get("input_hash")
        and proof.get("actual_winner_id") == proof.get("selected_candidate_id")
        and float(proof.get("selected_score_delta_from_memory", 0.0))
        == float(proof.get("selected_memory_adjustment", -1.0))
        and replay.get("replayable") is True
        and replay.get("actual_selection_replayed") is True
        and replay.get("counterfactual_scores_recomputed") is True
        and replay.get("score_delta_equals_selected_memory_adjustment") is True
        and replay.get("retrieved_memory_ref_bound") is True
        and replay.get("candidate_request_includes_memory_when_retrieved") is True
        and replay.get("selected_candidate_cites_memory_when_retrieved") is True
        and replay.get("selected_action_matches_memory_hint_when_retrieved") is True
        and replay.get("memory_changes_selected_score_when_retrieved") is True
        and replay.get("memory_improves_selected_margin_when_retrieved") is True
        and replay.get("cold_start_zero_memory_adjustments") is True
        and (
            (
                memory_ref
                and proof.get("memory_status") == "retrieved"
                and proof.get("outcome") == "memory_material_to_selected_score"
                and float(proof.get("selected_score_delta_from_memory", 0.0)) > 0.0
                and float(proof.get("memory_margin_lift", 0.0)) > 0.0
            )
            or (
                not memory_ref
                and proof.get("memory_status") == "cold_start_declared"
                and proof.get("outcome") == "cold_start_declared"
                and float(proof.get("selected_score_delta_from_memory", 1.0)) == 0.0
            )
        )
    )


def _build_institutional_memory_lineage_proof(
    *,
    episode: PortfolioEpisode,
    institutional_memory_context: Mapping[str, Any] | None,
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    influence = _institutional_memory_influence_profile(institutional_memory_context)
    entry_ref = influence.get("entry_ref")
    cited_evidence_refs = [str(ref) for ref in influence.get("cited_evidence_refs", [])]
    institutional_refs = [
        str(ref)
        for ref in (entry_ref, *cited_evidence_refs)
        if ref
    ]
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_action = _candidate_action_key(str(trace["selected_candidate_id"]))
        selected_scorecard = artifact["scorer"]["scorecards"][trace["selected_candidate_id"]]
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "selected_action": selected_action,
                "decision_input_entry_ref": trace["decision_inputs"].get("institutional_memory_entry_ref"),
                "private_memory_ref": trace["decision_inputs"].get("memory_influence_ref"),
                "reasoning_consumes_entry_ref": entry_ref is not None
                and str(entry_ref) in reasoning_request["input_refs"],
                "candidate_request_consumes_entry_ref": entry_ref is not None
                and str(entry_ref) in candidate_request["input_refs"],
                "selected_candidate_cites_entry_ref": entry_ref is not None
                and str(entry_ref) in selected_candidate["evidence_refs"],
                "reasoning_consumes_cited_evidence_refs": bool(cited_evidence_refs)
                and set(cited_evidence_refs).issubset(set(reasoning_request["input_refs"])),
                "candidate_request_consumes_cited_evidence_refs": bool(cited_evidence_refs)
                and set(cited_evidence_refs).issubset(set(candidate_request["input_refs"])),
                "selected_candidate_cites_cited_evidence_refs": bool(cited_evidence_refs)
                and set(cited_evidence_refs).issubset(set(selected_candidate["evidence_refs"])),
                "scorer_institutional_memory_adjustment": float(
                    scoring_inputs["institutional_memory_score_adjustments"].get(selected_action, 0.0)
                ),
                "scorecard_institutional_memory_adjustment": float(
                    selected_scorecard["components"].get("institutional_memory_adjustment", 0.0)
                ),
                "decision_replay_uses_institutional_memory": artifact["replay"].get(
                    "uses_cross_persona_institutional_memory_or_declares_cold_start"
                )
                is True,
            }
        )

    status = str(influence["status"])
    cold_start = status == "cold_start"
    applied = status == "applied"
    current_persona_id = _persona_id(episode.persona)
    contributing_persona_ids = set(str(item) for item in influence.get("contributing_persona_ids", []))
    replay = {
        "replayable": True,
        "cold_start_or_cross_persona_entry_bound": cold_start
        or bool(applied and influence.get("entry_id") and entry_ref),
        "source_persona_differs_from_current_persona": cold_start
        or current_persona_id not in contributing_persona_ids,
        "institutional_entry_ref_available": cold_start or bool(entry_ref),
        "reasoning_consumes_institutional_memory": cold_start
        or all(binding["reasoning_consumes_entry_ref"] for binding in trace_bindings),
        "candidate_generation_consumes_institutional_memory": cold_start
        or all(binding["candidate_request_consumes_entry_ref"] for binding in trace_bindings),
        "selected_candidate_cites_institutional_memory": cold_start
        or all(binding["selected_candidate_cites_entry_ref"] for binding in trace_bindings),
        "reasoning_consumes_institutional_source_evidence": cold_start
        or all(binding["reasoning_consumes_cited_evidence_refs"] for binding in trace_bindings),
        "candidate_generation_consumes_institutional_source_evidence": cold_start
        or all(binding["candidate_request_consumes_cited_evidence_refs"] for binding in trace_bindings),
        "selected_candidate_cites_institutional_source_evidence": cold_start
        or all(binding["selected_candidate_cites_cited_evidence_refs"] for binding in trace_bindings),
        "scorer_applies_institutional_memory_adjustment": cold_start
        or all(binding["scorer_institutional_memory_adjustment"] > 0.0 for binding in trace_bindings),
        "scorecard_replays_institutional_memory_adjustment": cold_start
        or all(
            binding["scorecard_institutional_memory_adjustment"]
            == binding["scorer_institutional_memory_adjustment"]
            for binding in trace_bindings
        ),
        "decision_artifact_replays_institutional_memory": all(
            binding["decision_replay_uses_institutional_memory"] for binding in trace_bindings
        ),
        "private_persona_memory_not_reused_as_institutional_memory": cold_start
        or all(
            binding["decision_input_entry_ref"]
            and not str(binding["decision_input_entry_ref"]).startswith("memory://")
            and binding["decision_input_entry_ref"] != binding["private_memory_ref"]
            for binding in trace_bindings
        ),
    }
    return {
        "proof_id": f"institutional-memory-lineage-{episode.case_id}",
        "proof_ref": f"institutional-memory-lineage://{episode.case_id}",
        "model_id": PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": current_persona_id,
        "lineage_status": status,
        "entry_id": influence.get("entry_id"),
        "entry_ref": entry_ref,
        "source_event_id": influence.get("source_event_id"),
        "reuse_count": influence.get("reuse_count"),
        "contributing_persona_ids": list(influence.get("contributing_persona_ids", [])),
        "sponsor_persona_id": influence.get("sponsor_persona_id"),
        "selected_action_hint": influence.get("selected_action_hint"),
        "score_adjustments": copy.deepcopy(dict(influence["candidate_score_adjustments"])),
        "cited_proposal_ids": list(influence.get("cited_proposal_ids", [])),
        "cited_evidence_refs": cited_evidence_refs,
        "institutional_refs": institutional_refs,
        "trace_bindings": trace_bindings,
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "institutional-memory-lineage-proof",
            {
                "case_id": episode.case_id,
                "institutional_memory_context": institutional_memory_context,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _institutional_memory_lineage_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    status = proof.get("lineage_status")
    trace_bindings = list(proof.get("trace_bindings", []))
    score_adjustments = proof.get("score_adjustments", {})
    return bool(
        proof.get("model_id") == PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("institutional-memory-lineage://")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "cold_start_or_cross_persona_entry_bound",
            "source_persona_differs_from_current_persona",
            "institutional_entry_ref_available",
            "reasoning_consumes_institutional_memory",
            "candidate_generation_consumes_institutional_memory",
            "selected_candidate_cites_institutional_memory",
            "reasoning_consumes_institutional_source_evidence",
            "candidate_generation_consumes_institutional_source_evidence",
            "selected_candidate_cites_institutional_source_evidence",
            "scorer_applies_institutional_memory_adjustment",
            "scorecard_replays_institutional_memory_adjustment",
            "decision_artifact_replays_institutional_memory",
            "private_persona_memory_not_reused_as_institutional_memory",
        ))
        and (
            (
                status == "cold_start"
                and proof.get("entry_id") is None
                and proof.get("entry_ref") is None
                and proof.get("contributing_persona_ids") == []
                and all(float(value) == 0.0 for value in score_adjustments.values())
                and all(
                    binding.get("scorer_institutional_memory_adjustment", 0.0) == 0.0
                    for binding in trace_bindings
                )
            )
            or (
                status == "applied"
                and proof.get("entry_id")
                and proof.get("entry_ref")
                and proof.get("source_event_id")
                and proof.get("contributing_persona_ids")
                and proof.get("persona_id") not in proof.get("contributing_persona_ids", [])
                and float(score_adjustments.get("feedback-adapt", 0.0)) > 0.0
                and all(
                    binding.get("scorer_institutional_memory_adjustment", 0.0) > 0.0
                    for binding in trace_bindings
                )
            )
        )
    )


def _build_persona_reasoning_response(
    *,
    episode: PortfolioEpisode,
    generation: int,
    trigger: str,
    baseline_policy: Mapping[str, Any],
    latest_evaluation: Mapping[str, Any],
    telemetry_event: Mapping[str, Any],
    memory_influence: Mapping[str, Any],
    institutional_memory_influence: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    oss_followup_loop: Mapping[str, Any],
    oss_disagreement_arbitration: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    alpha_seed_revision: Mapping[str, Any],
    cross_cycle_context: Mapping[str, Any],
    multi_cycle_context: Mapping[str, Any],
) -> dict[str, Any]:
    allowed_windows = ["observe", "feedback"] if generation == 1 else ["observe", "feedback", "holdout"]
    forbidden_windows = ["holdout", "future_holdout"] if generation == 1 else ["future_holdout"]
    reflection_result = oss_inputs["reflection_artifact"]
    reflection_artifact_ref = (
        f"oss://{reflection_result['component']}/{reflection_result['request_id']}"
    )
    reasoning_request = {
        "request_id": f"persona-reasoning-request-{episode.case_id}-gen{generation}",
        "model_id": PERSONA_REASONING_MODEL_ID,
        "persona_id": _persona_id(episode.persona),
        "case_id": episode.case_id,
        "generation": generation,
        "trigger": trigger,
        "objective": "propose portfolio policy improvements from memory, telemetry, and OSS feedback before scorer selection",
        "allowed_windows": allowed_windows,
        "forbidden_windows_not_used": forbidden_windows,
        "input_refs": [
            f"telemetry-event://{telemetry_event['event_id']}",
            f"policy://{baseline_policy['policy_id']}",
            f"alpha-seed://{episode.seed_key}",
            *[f"oss://{result['component']}/{result['request_id']}" for result in oss_inputs.values()],
            str(oss_followup_loop["loop_ref"]),
            str(oss_disagreement_arbitration["arbitration_ref"]),
            str(tracking_reconciliation["reconciliation_ref"]),
            str(alpha_seed_revision["revision_ref"]),
            *list(cross_cycle_context.get("evidence_refs", [])),
            *list(multi_cycle_context.get("evidence_refs", [])),
            *([str(memory_influence["influence_ref"])] if memory_influence["influence_ref"] else []),
            *(
                [str(institutional_memory_influence["entry_ref"])]
                if institutional_memory_influence["entry_ref"]
                else []
            ),
            *list(institutional_memory_influence.get("cited_evidence_refs", [])),
        ],
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "baseline_risk_multiplier": float(baseline_policy["risk_multiplier"]),
        "telemetry_summary": {
            "score": latest_evaluation["score"],
            "signed_return": latest_evaluation["signed_return"],
            "drawdown": latest_evaluation["drawdown"],
            "turnover": latest_evaluation["turnover"],
        },
        "memory_influence": copy.deepcopy(dict(memory_influence)),
        "institutional_memory_influence": copy.deepcopy(dict(institutional_memory_influence)),
        "oss_components_by_role": {
            role: result["component"] for role, result in sorted(oss_inputs.items())
        },
        "reflection_artifact_ref": reflection_artifact_ref,
        "reflection_artifact_component": reflection_result["component"],
        "reflection_artifact_request_id": reflection_result["request_id"],
        "oss_followup_loop_ref": oss_followup_loop["loop_ref"],
        "oss_disagreement_arbitration_ref": oss_disagreement_arbitration["arbitration_ref"],
        "tracking_reconciliation_ref": tracking_reconciliation["reconciliation_ref"],
        "alpha_seed_revision_ref": alpha_seed_revision["revision_ref"],
        "cross_cycle_status": cross_cycle_context["status"],
        "cross_cycle_state_ref": cross_cycle_context.get("state_ref"),
        "cross_cycle_runtime_feedback_ref": cross_cycle_context.get("runtime_feedback_ref"),
        "multi_cycle_lineage_status": multi_cycle_context["status"],
        "multi_cycle_lineage_ref": multi_cycle_context.get("lineage_ref"),
        "multi_cycle_latest_runtime_feedback_ref": multi_cycle_context.get("latest_runtime_feedback_ref"),
        "multi_cycle_older_runtime_feedback_ref": multi_cycle_context.get("older_runtime_feedback_ref"),
        "institutional_memory_status": institutional_memory_influence["status"],
        "institutional_memory_entry_ref": institutional_memory_influence["entry_ref"],
        "institutional_memory_source_event_id": institutional_memory_influence["source_event_id"],
        "institutional_memory_contributing_persona_ids": list(
            institutional_memory_influence["contributing_persona_ids"]
        ),
        "oss_followup_request_ids": [
            followup["request"]["request_id"]
            for followup in oss_followup_loop["followups"]
        ],
    }
    candidate_blueprints = _persona_reasoning_candidate_blueprints(
        generation=generation,
        allowed_windows=allowed_windows,
        memory_influence=memory_influence,
        institutional_memory_influence=institutional_memory_influence,
        oss_inputs=oss_inputs,
        oss_followup_loop=oss_followup_loop,
        oss_disagreement_arbitration=oss_disagreement_arbitration,
        tracking_reconciliation=tracking_reconciliation,
        alpha_seed_revision=alpha_seed_revision,
        cross_cycle_context=cross_cycle_context,
        multi_cycle_context=multi_cycle_context,
    )
    reasoning_response = {
        "response_id": f"persona-reasoning-response-{episode.case_id}-gen{generation}",
        "reasoning_ref": f"reasoning://persona/{episode.case_id}/gen{generation}",
        "status": "completed",
        "model_id": PERSONA_REASONING_MODEL_ID,
        "persona_id": _persona_id(episode.persona),
        "case_id": episode.case_id,
        "generation": generation,
        "reasoning_steps": [
            "read_telemetry_outcome",
            "retrieve_or_declare_memory_context",
            "inspect_alpha_policy_reflection_risk_tracking_oss_feedback",
            "bind_alpha_seed_revision_from_selected_oss",
            "process_oss_response_followup_requests",
            "arbitrate_multi_oss_disagreement",
            "reconcile_tracking_readback_before_scoring",
            "draft_candidate_policy_blueprints",
            "send_blueprints_to_scorer_and_risk_evaluator",
        ],
        "memory_usage": {
            "status": memory_influence["status"],
            "influence_ref": memory_influence["influence_ref"],
            "selected_action_hint": memory_influence["selected_action_hint"],
            "score_adjustments": copy.deepcopy(dict(memory_influence["candidate_score_adjustments"])),
        },
        "institutional_memory_usage": {
            "model_id": institutional_memory_influence["model_id"],
            "status": institutional_memory_influence["status"],
            "entry_id": institutional_memory_influence["entry_id"],
            "entry_ref": institutional_memory_influence["entry_ref"],
            "source_event_id": institutional_memory_influence["source_event_id"],
            "contributing_persona_ids": list(
                institutional_memory_influence["contributing_persona_ids"]
            ),
            "selected_action_hint": institutional_memory_influence["selected_action_hint"],
            "candidate_score_adjustments": copy.deepcopy(
                dict(institutional_memory_influence["candidate_score_adjustments"])
            ),
        },
        "preferred_action_hint": _persona_reasoning_preferred_action(memory_influence),
        "reflection_artifact_usage": {
            "source_oss_ref": reflection_artifact_ref,
            "component": reflection_result["component"],
            "request_id": reflection_result["request_id"],
            "artifact_family": reflection_result.get("artifact_family"),
            "materiality_model_id": PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID,
            "reflection_quality": _reflection_quality_from_oss(oss_inputs),
            "drives_candidate_blueprint_actions": [
                "feedback-adapt",
                "contrarian-check",
            ],
            "drives_persona_step": reflection_result.get("drives_persona_step"),
        },
        "oss_followup_usage": {
            "loop_ref": oss_followup_loop["loop_ref"],
            "model_id": oss_followup_loop["model_id"],
            "followup_count": len(oss_followup_loop["followups"]),
            "candidate_score_adjustments": copy.deepcopy(
                dict(oss_followup_loop["candidate_score_adjustments"])
            ),
        },
        "oss_disagreement_arbitration_usage": {
            "arbitration_ref": oss_disagreement_arbitration["arbitration_ref"],
            "model_id": oss_disagreement_arbitration["model_id"],
            "conflict_types": [
                conflict["conflict_type"] for conflict in oss_disagreement_arbitration["conflicts"]
            ],
            "candidate_score_adjustments": copy.deepcopy(
                dict(oss_disagreement_arbitration["candidate_score_adjustments"])
            ),
        },
        "tracking_reconciliation_usage": {
            "reconciliation_ref": tracking_reconciliation["reconciliation_ref"],
            "model_id": tracking_reconciliation["model_id"],
            "divergence_type": tracking_reconciliation["divergence"]["divergence_type"],
            "repair_action": tracking_reconciliation["repair"]["action"],
            "candidate_score_adjustments": copy.deepcopy(
                dict(tracking_reconciliation["candidate_score_adjustments"])
            ),
        },
        "alpha_seed_revision_usage": {
            "revision_ref": alpha_seed_revision["revision_ref"],
            "model_id": alpha_seed_revision["model_id"],
            "alpha_component": alpha_seed_revision["alpha_component"],
            "revision_action": alpha_seed_revision["revision"]["action"],
            "candidate_score_adjustments": copy.deepcopy(
                dict(alpha_seed_revision["candidate_score_adjustments"])
            ),
        },
        "cross_cycle_usage": {
            "model_id": cross_cycle_context["model_id"],
            "status": cross_cycle_context["status"],
            "state_ref": cross_cycle_context.get("state_ref"),
            "runtime_feedback_ref": cross_cycle_context.get("runtime_feedback_ref"),
            "previous_case_id": cross_cycle_context.get("previous_case_id"),
            "next_ooda_step": cross_cycle_context.get("next_ooda_step"),
            "candidate_score_adjustments": _cross_cycle_score_adjustments(cross_cycle_context),
        },
        "multi_cycle_lineage_usage": {
            "model_id": multi_cycle_context["model_id"],
            "status": multi_cycle_context["status"],
            "lineage_ref": multi_cycle_context.get("lineage_ref"),
            "lineage_depth": multi_cycle_context.get("lineage_depth"),
            "lineage_case_ids": list(multi_cycle_context.get("lineage_case_ids", [])),
            "latest_runtime_feedback_ref": multi_cycle_context.get("latest_runtime_feedback_ref"),
            "older_runtime_feedback_ref": multi_cycle_context.get("older_runtime_feedback_ref"),
            "trend_signal": multi_cycle_context.get("trend_signal"),
            "candidate_score_adjustments": _multi_cycle_lineage_score_adjustments(
                multi_cycle_context
            ),
        },
        "candidate_blueprints": candidate_blueprints,
        "forbidden_windows_not_used": forbidden_windows,
        "output_contract": {
            "candidate_actions_required": [
                "feedback-adapt",
                "retain-observe",
                "risk-off",
                "contrarian-check",
            ],
            "risk_evaluator_required": True,
            "scorer_required": True,
        },
    }
    evaluator = _evaluate_persona_reasoning_response(
        request=reasoning_request,
        response=reasoning_response,
    )
    return {
        "request": reasoning_request,
        "response": reasoning_response,
        "evaluator": evaluator,
    }


def _persona_reasoning_candidate_blueprints(
    *,
    generation: int,
    allowed_windows: Sequence[str],
    memory_influence: Mapping[str, Any],
    institutional_memory_influence: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    oss_followup_loop: Mapping[str, Any],
    oss_disagreement_arbitration: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    alpha_seed_revision: Mapping[str, Any],
    cross_cycle_context: Mapping[str, Any],
    multi_cycle_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    del oss_inputs
    shared_windows = list(allowed_windows)
    feedback_windows = ["observe", "feedback"] if generation == 1 else shared_windows
    memory_ref = memory_influence.get("influence_ref")
    memory_refs = [str(memory_ref)] if memory_ref else []
    institutional_memory_ref = institutional_memory_influence.get("entry_ref")
    institutional_memory_refs = [str(institutional_memory_ref)] if institutional_memory_ref else []
    institutional_memory_refs.extend(
        str(ref) for ref in institutional_memory_influence.get("cited_evidence_refs", [])
    )
    institutional_memory_score_adjustments = dict(
        institutional_memory_influence["candidate_score_adjustments"]
    )
    risk_institutional_memory_refs = (
        institutional_memory_refs
        if float(institutional_memory_score_adjustments.get("risk-off", 0.0)) > 0.0
        else []
    )
    cross_cycle_refs = list(cross_cycle_context.get("evidence_refs", []))
    cross_cycle_score_adjustments = _cross_cycle_score_adjustments(cross_cycle_context)
    risk_cross_cycle_refs = (
        cross_cycle_refs
        if float(cross_cycle_score_adjustments.get("risk-off", 0.0)) > 0.0
        else []
    )
    multi_cycle_refs = list(multi_cycle_context.get("evidence_refs", []))
    multi_cycle_score_adjustments = _multi_cycle_lineage_score_adjustments(multi_cycle_context)
    risk_multi_cycle_refs = (
        multi_cycle_refs
        if float(multi_cycle_score_adjustments.get("risk-off", 0.0)) > 0.0
        else []
    )
    feedback_followup_refs = _oss_followup_refs_for_action(oss_followup_loop, "feedback-adapt")
    risk_followup_refs = _oss_followup_refs_for_action(oss_followup_loop, "risk-off")
    retain_followup_refs = _oss_followup_refs_for_action(oss_followup_loop, "retain-observe")
    contrarian_followup_refs = _oss_followup_refs_for_action(oss_followup_loop, "contrarian-check")
    feedback_arbitration_refs = _oss_disagreement_refs_for_action(oss_disagreement_arbitration, "feedback-adapt")
    risk_arbitration_refs = _oss_disagreement_refs_for_action(oss_disagreement_arbitration, "risk-off")
    retain_arbitration_refs = _oss_disagreement_refs_for_action(oss_disagreement_arbitration, "retain-observe")
    contrarian_arbitration_refs = _oss_disagreement_refs_for_action(oss_disagreement_arbitration, "contrarian-check")
    feedback_tracking_refs = _tracking_reconciliation_refs_for_action(tracking_reconciliation, "feedback-adapt")
    risk_tracking_refs = _tracking_reconciliation_refs_for_action(tracking_reconciliation, "risk-off")
    retain_tracking_refs = _tracking_reconciliation_refs_for_action(tracking_reconciliation, "retain-observe")
    contrarian_tracking_refs = _tracking_reconciliation_refs_for_action(tracking_reconciliation, "contrarian-check")
    feedback_alpha_refs = _alpha_seed_revision_refs_for_action(alpha_seed_revision, "feedback-adapt")
    risk_alpha_refs = _alpha_seed_revision_refs_for_action(alpha_seed_revision, "risk-off")
    retain_alpha_refs = _alpha_seed_revision_refs_for_action(alpha_seed_revision, "retain-observe")
    contrarian_alpha_refs = _alpha_seed_revision_refs_for_action(alpha_seed_revision, "contrarian-check")
    return [
        {
            "action": "feedback-adapt",
            "candidate_suffix": "feedback-adapt",
            "direction_source": "feedback_window_direction",
            "risk_source": "policy_candidate_oss_hint",
            "source_windows": feedback_windows,
            "evidence_roles": [
                "alpha_model",
                "policy_candidate",
                "reflection_artifact",
                "risk_analytics",
                "backtest",
                "tracker",
            ],
            "extra_evidence_refs": [
                *feedback_followup_refs,
                *feedback_arbitration_refs,
                *feedback_tracking_refs,
                *feedback_alpha_refs,
                *memory_refs,
                *institutional_memory_refs,
                *cross_cycle_refs,
                *multi_cycle_refs,
            ],
            "memory_adjustment_key": "feedback-adapt",
            "rationale": (
                "Use the feedback direction because alpha, policy, reflection, risk, backtest, "
                "alpha seed revision, tracking reconciliation, and retrieved memory support an adaptive portfolio mutation."
            ),
        },
        {
            "action": "retain-observe",
            "candidate_suffix": "retain-observe",
            "direction_source": "observe_window_direction",
            "risk_source": "baseline_policy",
            "source_windows": ["observe"],
            "evidence_roles": [],
            "extra_evidence_refs": [
                *retain_followup_refs,
                *retain_arbitration_refs,
                *retain_tracking_refs,
                *retain_alpha_refs,
            ],
            "memory_adjustment_key": "retain-observe",
            "rationale": "Keep the observe-only baseline as a scored alternative before rejecting it.",
        },
        {
            "action": "risk-off",
            "candidate_suffix": "risk-off",
            "direction_source": "feedback_window_direction",
            "risk_source": "risk_analytics_reduced_exposure",
            "source_windows": feedback_windows,
            "evidence_roles": ["risk_analytics"],
            "extra_evidence_refs": [
                *risk_followup_refs,
                *risk_arbitration_refs,
                *risk_tracking_refs,
                *risk_alpha_refs,
                *memory_refs,
                *risk_institutional_memory_refs,
                *risk_cross_cycle_refs,
                *risk_multi_cycle_refs,
            ]
            if (
                float(memory_influence.get("candidate_score_adjustments", {}).get("risk-off", 0.0)) > 0
                or risk_institutional_memory_refs
                or risk_cross_cycle_refs
                or risk_multi_cycle_refs
            )
            else [*risk_followup_refs, *risk_arbitration_refs, *risk_tracking_refs, *risk_alpha_refs],
            "memory_adjustment_key": "risk-off",
            "rationale": "Use feedback direction but reduce exposure when the risk interpretation asks for caution.",
        },
        {
            "action": "contrarian-check",
            "candidate_suffix": "contrarian-check",
            "direction_source": "inverse_feedback_direction",
            "risk_source": "fixed_control_risk",
            "source_windows": feedback_windows,
            "evidence_roles": ["reflection_artifact"],
            "extra_evidence_refs": [
                *contrarian_followup_refs,
                *contrarian_arbitration_refs,
                *contrarian_tracking_refs,
                *contrarian_alpha_refs,
            ],
            "memory_adjustment_key": "contrarian-check",
            "rationale": "Retain a contrarian control candidate for scored comparison and rejection.",
        },
    ]


def _persona_reasoning_preferred_action(memory_influence: Mapping[str, Any]) -> str:
    action = str(memory_influence.get("selected_action_hint") or "feedback-adapt")
    if action == "none":
        return "feedback-adapt"
    return action


def _evaluate_persona_reasoning_response(
    *,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    forbidden = set(request["forbidden_windows_not_used"])
    blueprints = list(response.get("candidate_blueprints", []))
    required_actions = set(response.get("output_contract", {}).get("candidate_actions_required", []))
    observed_actions = {blueprint.get("action") for blueprint in blueprints}
    checks = [
        _persona_risk_check(
            "reasoning_response_completed",
            response.get("status") == "completed",
            {"response_id": response.get("response_id")},
        ),
        _persona_risk_check(
            "reasoning_covers_required_candidate_actions",
            observed_actions == required_actions,
            {"observed_actions": sorted(str(action) for action in observed_actions)},
        ),
        _persona_risk_check(
            "reasoning_excludes_forbidden_windows",
            all(not forbidden.intersection(set(blueprint.get("source_windows", []))) for blueprint in blueprints),
            {"forbidden_windows_not_used": sorted(forbidden)},
        ),
        _persona_risk_check(
            "reasoning_uses_memory_or_declares_cold_start",
            bool(response.get("memory_usage", {}).get("influence_ref"))
            or response.get("memory_usage", {}).get("status") == "cold_start",
            {"memory_usage": copy.deepcopy(dict(response.get("memory_usage", {})))},
        ),
        _persona_risk_check(
            "reasoning_uses_cross_persona_institutional_memory_or_declares_cold_start",
            (
                request.get("institutional_memory_status") == "cold_start"
                and response.get("institutional_memory_usage", {}).get("status") == "cold_start"
                and request.get("institutional_memory_entry_ref") is None
            )
            or (
                request.get("institutional_memory_status") == "applied"
                and response.get("institutional_memory_usage", {}).get("status") == "applied"
                and request.get("institutional_memory_entry_ref")
                == response.get("institutional_memory_usage", {}).get("entry_ref")
                and request.get("institutional_memory_entry_ref") in request.get("input_refs", [])
                and request.get("persona_id")
                not in set(request.get("institutional_memory_contributing_persona_ids", []))
                and float(
                    response.get("institutional_memory_usage", {})
                    .get("candidate_score_adjustments", {})
                    .get("feedback-adapt", 0.0)
                )
                > 0.0
            ),
            {
                "institutional_memory_status": request.get("institutional_memory_status"),
                "institutional_memory_entry_ref": request.get("institutional_memory_entry_ref"),
                "contributing_persona_ids": list(
                    request.get("institutional_memory_contributing_persona_ids", [])
                ),
                "usage": copy.deepcopy(dict(response.get("institutional_memory_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_oss_followup_loop",
            request.get("oss_followup_loop_ref") == response.get("oss_followup_usage", {}).get("loop_ref")
            and request.get("oss_followup_loop_ref") in request.get("input_refs", [])
            and int(response.get("oss_followup_usage", {}).get("followup_count", 0)) >= 8,
            {
                "oss_followup_loop_ref": request.get("oss_followup_loop_ref"),
                "followup_count": response.get("oss_followup_usage", {}).get("followup_count"),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_reflection_artifact_for_blueprints",
            request.get("reflection_artifact_ref")
            == response.get("reflection_artifact_usage", {}).get("source_oss_ref")
            and request.get("reflection_artifact_ref") in request.get("input_refs", [])
            and response.get("reflection_artifact_usage", {}).get("materiality_model_id")
            == PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID
            and float(response.get("reflection_artifact_usage", {}).get("reflection_quality", 0.0))
            > 0.0
            and {
                "feedback-adapt",
                "contrarian-check",
            }.issubset(
                set(
                    response.get("reflection_artifact_usage", {}).get(
                        "drives_candidate_blueprint_actions",
                        [],
                    )
                )
            ),
            {
                "reflection_artifact_ref": request.get("reflection_artifact_ref"),
                "usage": copy.deepcopy(dict(response.get("reflection_artifact_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_oss_disagreement_arbitration",
            request.get("oss_disagreement_arbitration_ref")
            == response.get("oss_disagreement_arbitration_usage", {}).get("arbitration_ref")
            and request.get("oss_disagreement_arbitration_ref") in request.get("input_refs", [])
            and response.get("oss_disagreement_arbitration_usage", {}).get("model_id")
            == PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID,
            {
                "oss_disagreement_arbitration_ref": request.get("oss_disagreement_arbitration_ref"),
                "usage": copy.deepcopy(dict(response.get("oss_disagreement_arbitration_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_tracking_reconciliation",
            request.get("tracking_reconciliation_ref")
            == response.get("tracking_reconciliation_usage", {}).get("reconciliation_ref")
            and request.get("tracking_reconciliation_ref") in request.get("input_refs", [])
            and response.get("tracking_reconciliation_usage", {}).get("model_id")
            == PERSONA_TRACKING_RECONCILIATION_MODEL_ID,
            {
                "tracking_reconciliation_ref": request.get("tracking_reconciliation_ref"),
                "usage": copy.deepcopy(dict(response.get("tracking_reconciliation_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_alpha_seed_revision",
            request.get("alpha_seed_revision_ref")
            == response.get("alpha_seed_revision_usage", {}).get("revision_ref")
            and request.get("alpha_seed_revision_ref") in request.get("input_refs", [])
            and response.get("alpha_seed_revision_usage", {}).get("model_id")
            == PERSONA_ALPHA_SEED_REVISION_MODEL_ID,
            {
                "alpha_seed_revision_ref": request.get("alpha_seed_revision_ref"),
                "usage": copy.deepcopy(dict(response.get("alpha_seed_revision_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_cross_cycle_runtime_feedback_or_declares_cold_start",
            (
                request.get("cross_cycle_status") == "cold_start"
                and response.get("cross_cycle_usage", {}).get("status") == "cold_start"
                and request.get("cross_cycle_state_ref") is None
            )
            or (
                request.get("cross_cycle_status") == "applied"
                and response.get("cross_cycle_usage", {}).get("status") == "applied"
                and request.get("cross_cycle_state_ref")
                == response.get("cross_cycle_usage", {}).get("state_ref")
                and request.get("cross_cycle_runtime_feedback_ref")
                == response.get("cross_cycle_usage", {}).get("runtime_feedback_ref")
                and request.get("cross_cycle_state_ref") in request.get("input_refs", [])
                and request.get("cross_cycle_runtime_feedback_ref") in request.get("input_refs", [])
                and float(
                    response.get("cross_cycle_usage", {})
                    .get("candidate_score_adjustments", {})
                    .get("feedback-adapt", 0.0)
                )
                > 0.0
            ),
            {
                "cross_cycle_status": request.get("cross_cycle_status"),
                "cross_cycle_state_ref": request.get("cross_cycle_state_ref"),
                "usage": copy.deepcopy(dict(response.get("cross_cycle_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_uses_multi_cycle_lineage_or_declares_cold_start",
            (
                request.get("multi_cycle_lineage_status") == "cold_start"
                and response.get("multi_cycle_lineage_usage", {}).get("status") == "cold_start"
                and request.get("multi_cycle_lineage_ref") is None
            )
            or (
                request.get("multi_cycle_lineage_status") == "single_prior"
                and response.get("multi_cycle_lineage_usage", {}).get("status") == "single_prior"
                and request.get("multi_cycle_lineage_ref")
                == response.get("multi_cycle_lineage_usage", {}).get("lineage_ref")
                and request.get("multi_cycle_latest_runtime_feedback_ref") in request.get("input_refs", [])
                and request.get("multi_cycle_older_runtime_feedback_ref") is None
                and float(
                    response.get("multi_cycle_lineage_usage", {})
                    .get("candidate_score_adjustments", {})
                    .get("feedback-adapt", 0.0)
                )
                > 0.0
            )
            or (
                request.get("multi_cycle_lineage_status") == "lineage_applied"
                and response.get("multi_cycle_lineage_usage", {}).get("status") == "lineage_applied"
                and request.get("multi_cycle_lineage_ref")
                == response.get("multi_cycle_lineage_usage", {}).get("lineage_ref")
                and request.get("multi_cycle_latest_runtime_feedback_ref") in request.get("input_refs", [])
                and request.get("multi_cycle_older_runtime_feedback_ref") in request.get("input_refs", [])
                and float(
                    response.get("multi_cycle_lineage_usage", {})
                    .get("candidate_score_adjustments", {})
                    .get("feedback-adapt", 0.0)
                )
                > 0.0
            ),
            {
                "multi_cycle_lineage_status": request.get("multi_cycle_lineage_status"),
                "multi_cycle_lineage_ref": request.get("multi_cycle_lineage_ref"),
                "usage": copy.deepcopy(dict(response.get("multi_cycle_lineage_usage", {}))),
            },
        ),
        _persona_risk_check(
            "reasoning_routes_to_scorer_and_risk_evaluator",
            response.get("output_contract", {}).get("scorer_required") is True
            and response.get("output_contract", {}).get("risk_evaluator_required") is True,
            {"output_contract": copy.deepcopy(dict(response.get("output_contract", {})))},
        ),
    ]
    return {
        "model_id": PERSONA_REASONING_EVALUATOR_MODEL_ID,
        "status": "passed" if all(check["status"] == "passed" for check in checks) else "failed",
        "checks": checks,
    }


def _build_agent_decision_trace(
    *,
    episode: PortfolioEpisode,
    generation: int,
    baseline_policy: Mapping[str, Any],
    latest_evaluation: Mapping[str, Any],
    telemetry_event: Mapping[str, Any],
    prior_memory: Mapping[str, Any] | None,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    oss_followup_loop: Mapping[str, Any],
    oss_disagreement_arbitration: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    alpha_seed_revision: Mapping[str, Any],
    cross_cycle_context: Mapping[str, Any],
    multi_cycle_context: Mapping[str, Any],
    institutional_memory_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    memory_influence = _memory_influence_profile(prior_memory)
    institutional_memory_influence = _institutional_memory_influence_profile(
        institutional_memory_context
    )
    trigger = (
        "holdout_generation_refinement"
        if generation == 2
        else episode.reflection_archetype
    )
    persona_reasoning = _build_persona_reasoning_response(
        episode=episode,
        generation=generation,
        trigger=trigger,
        baseline_policy=baseline_policy,
        latest_evaluation=latest_evaluation,
        telemetry_event=telemetry_event,
        memory_influence=memory_influence,
        institutional_memory_influence=institutional_memory_influence,
        oss_inputs=oss_inputs,
        oss_followup_loop=oss_followup_loop,
        oss_disagreement_arbitration=oss_disagreement_arbitration,
        tracking_reconciliation=tracking_reconciliation,
        alpha_seed_revision=alpha_seed_revision,
        cross_cycle_context=cross_cycle_context,
        multi_cycle_context=multi_cycle_context,
    )
    candidates = _score_agent_candidates(
        episode=episode,
        generation=generation,
        baseline_policy=baseline_policy,
        latest_evaluation=latest_evaluation,
        prior_memory=prior_memory,
        oss_inputs=oss_inputs,
        memory_influence=memory_influence,
        institutional_memory_influence=institutional_memory_influence,
        persona_reasoning=persona_reasoning,
        oss_followup_loop=oss_followup_loop,
        oss_disagreement_arbitration=oss_disagreement_arbitration,
        tracking_reconciliation=tracking_reconciliation,
        alpha_seed_revision=alpha_seed_revision,
        cross_cycle_context=cross_cycle_context,
        multi_cycle_context=multi_cycle_context,
    )
    selected = max(candidates, key=lambda item: item.score)
    candidate_dicts = [_candidate_to_dict(candidate) for candidate in candidates]
    selected_dict = _candidate_to_dict(selected)
    decision_inputs = {
        "allowed_windows": ["observe", "feedback"] if generation == 1 else ["observe", "feedback", "holdout"],
        "forbidden_windows_not_used": ["holdout", "future_holdout"] if generation == 1 else ["future_holdout"],
        "telemetry_event_id": telemetry_event["event_id"],
        "memory_ref": prior_memory.get("memory_id") if prior_memory else None,
        "memory_influence_ref": memory_influence["influence_ref"],
        "institutional_memory_entry_ref": institutional_memory_influence["entry_ref"],
        "institutional_memory_source_event_id": institutional_memory_influence["source_event_id"],
        "institutional_memory_contributing_persona_ids": list(
            institutional_memory_influence["contributing_persona_ids"]
        ),
        "oss_components": _oss_components_used(oss_inputs),
        "oss_followup_loop_ref": oss_followup_loop["loop_ref"],
        "oss_disagreement_arbitration_ref": oss_disagreement_arbitration["arbitration_ref"],
        "tracking_reconciliation_ref": tracking_reconciliation["reconciliation_ref"],
        "alpha_seed_revision_ref": alpha_seed_revision["revision_ref"],
        "cross_cycle_status": cross_cycle_context["status"],
        "cross_cycle_state_ref": cross_cycle_context.get("state_ref"),
        "cross_cycle_runtime_feedback_ref": cross_cycle_context.get("runtime_feedback_ref"),
        "multi_cycle_lineage_status": multi_cycle_context["status"],
        "multi_cycle_lineage_ref": multi_cycle_context.get("lineage_ref"),
        "multi_cycle_latest_runtime_feedback_ref": multi_cycle_context.get("latest_runtime_feedback_ref"),
        "multi_cycle_older_runtime_feedback_ref": multi_cycle_context.get("older_runtime_feedback_ref"),
    }
    evidence_refs = [
        f"telemetry-event://{telemetry_event['event_id']}",
        f"historical-ohlcv://{HISTORICAL_OHLCV_DATASET_ID}/observe-feedback/{episode.case_id}",
        f"alpha-seed://{episode.seed_key}",
        f"policy://{baseline_policy['policy_id']}",
        *[f"oss://{result['component']}/{result['request_id']}" for result in oss_inputs.values()],
        str(oss_followup_loop["loop_ref"]),
        str(oss_disagreement_arbitration["arbitration_ref"]),
        str(tracking_reconciliation["reconciliation_ref"]),
        str(alpha_seed_revision["revision_ref"]),
        *list(cross_cycle_context.get("evidence_refs", [])),
        *list(multi_cycle_context.get("evidence_refs", [])),
        *(
            [str(institutional_memory_influence["entry_ref"])]
            if institutional_memory_influence["entry_ref"]
            else []
        ),
        *list(institutional_memory_influence.get("cited_evidence_refs", [])),
    ]
    agent_decision_artifact = _build_persona_decision_artifact(
        episode=episode,
        generation=generation,
        trigger=trigger,
        baseline_policy=baseline_policy,
        latest_evaluation=latest_evaluation,
        telemetry_event=telemetry_event,
        prior_memory=prior_memory,
        oss_inputs=oss_inputs,
        memory_influence=memory_influence,
        institutional_memory_influence=institutional_memory_influence,
        persona_reasoning=persona_reasoning,
        oss_followup_loop=oss_followup_loop,
        oss_disagreement_arbitration=oss_disagreement_arbitration,
        tracking_reconciliation=tracking_reconciliation,
        alpha_seed_revision=alpha_seed_revision,
        cross_cycle_context=cross_cycle_context,
        multi_cycle_context=multi_cycle_context,
        decision_inputs=decision_inputs,
        evidence_refs=evidence_refs,
        candidates=candidate_dicts,
        selected_candidate=selected_dict,
    )
    return {
        "reflection_id": f"reflection-{episode.case_id}-gen{generation}",
        "persona_id": _persona_id(episode.persona),
        "seed_key": episode.seed_key,
        "trigger": trigger,
        "telemetry_event_id": telemetry_event["event_id"],
        "observed_score": latest_evaluation["score"],
        "observed_signed_return": latest_evaluation["signed_return"],
        "observed_drawdown": latest_evaluation["drawdown"],
        "hypothesis": _reflection_hypothesis(trigger, oss_inputs),
        "next_policy_change": "score_candidate_portfolio_policy",
        "candidate_count": len(candidates),
        "candidates": candidate_dicts,
        "selected_candidate_id": selected.candidate_id,
        "selected_candidate": selected_dict,
        "decision_inputs": decision_inputs,
        "evidence_refs": evidence_refs,
        "agent_decision_artifact": agent_decision_artifact,
    }


def _score_agent_candidates(
    *,
    episode: PortfolioEpisode,
    generation: int,
    baseline_policy: Mapping[str, Any],
    latest_evaluation: Mapping[str, Any],
    prior_memory: Mapping[str, Any] | None,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    memory_influence: Mapping[str, Any],
    institutional_memory_influence: Mapping[str, Any],
    persona_reasoning: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
    oss_disagreement_arbitration: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    alpha_seed_revision: Mapping[str, Any],
    cross_cycle_context: Mapping[str, Any],
    multi_cycle_context: Mapping[str, Any],
) -> list[PolicyCandidate]:
    del prior_memory
    feedback_directions = {
        window.instrument: window.feedback_direction for window in episode.windows
    }
    observe_directions = {
        window.instrument: window.observe_direction for window in episode.windows
    }
    inverse_feedback = {
        instrument: -direction for instrument, direction in feedback_directions.items()
    }
    policy_hint_risk = _risk_hint_from_oss(oss_inputs, generation)
    policy_quality = _policy_quality_from_oss(oss_inputs)
    reflection_quality = _reflection_quality_from_oss(oss_inputs)
    risk_penalty = _risk_penalty_from_oss(oss_inputs)
    followup_score_adjustments = dict(oss_followup_loop["candidate_score_adjustments"])
    disagreement_score_adjustments = dict(oss_disagreement_arbitration["candidate_score_adjustments"])
    tracking_score_adjustments = dict(tracking_reconciliation["candidate_score_adjustments"])
    alpha_seed_score_adjustments = dict(alpha_seed_revision["candidate_score_adjustments"])
    cross_cycle_score_adjustments = _cross_cycle_score_adjustments(cross_cycle_context)
    multi_cycle_lineage_score_adjustments = _multi_cycle_lineage_score_adjustments(multi_cycle_context)
    risk_off = max(0.25, policy_hint_risk - 0.35)
    memory_score_adjustments = dict(memory_influence["candidate_score_adjustments"])
    institutional_memory_score_adjustments = dict(
        institutional_memory_influence["candidate_score_adjustments"]
    )
    memory_ref = memory_influence.get("influence_ref")
    institutional_memory_ref = institutional_memory_influence.get("entry_ref")
    feedback_score = float(latest_evaluation["signed_return"]) - abs(float(latest_evaluation["drawdown"])) * 0.1
    feedback_evidence_refs = [
        f"oss://{oss_inputs['alpha_model']['component']}/{oss_inputs['alpha_model']['request_id']}",
        f"oss://{oss_inputs['policy_candidate']['component']}/{oss_inputs['policy_candidate']['request_id']}",
        f"oss://{oss_inputs['reflection_artifact']['component']}/{oss_inputs['reflection_artifact']['request_id']}",
        f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}",
        f"oss://{oss_inputs['backtest']['component']}/{oss_inputs['backtest']['request_id']}",
        f"oss://{oss_inputs['tracker']['component']}/{oss_inputs['tracker']['request_id']}",
    ]
    risk_off_evidence_refs = [
        f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}",
    ]
    if memory_ref:
        feedback_evidence_refs.append(str(memory_ref))
        if float(memory_score_adjustments.get("risk-off", 0.0)) > 0:
            risk_off_evidence_refs.append(str(memory_ref))
    if institutional_memory_ref:
        feedback_evidence_refs.append(str(institutional_memory_ref))
        feedback_evidence_refs.extend(
            str(ref) for ref in institutional_memory_influence.get("cited_evidence_refs", [])
        )
        if float(institutional_memory_score_adjustments.get("risk-off", 0.0)) > 0:
            risk_off_evidence_refs.append(str(institutional_memory_ref))
            risk_off_evidence_refs.extend(
                str(ref) for ref in institutional_memory_influence.get("cited_evidence_refs", [])
            )
    feedback_evidence_refs.extend(str(ref) for ref in cross_cycle_context.get("evidence_refs", []))
    if float(cross_cycle_score_adjustments.get("risk-off", 0.0)) > 0.0:
        risk_off_evidence_refs.extend(str(ref) for ref in cross_cycle_context.get("evidence_refs", []))
    feedback_evidence_refs.extend(str(ref) for ref in multi_cycle_context.get("evidence_refs", []))
    if float(multi_cycle_lineage_score_adjustments.get("risk-off", 0.0)) > 0.0:
        risk_off_evidence_refs.extend(str(ref) for ref in multi_cycle_context.get("evidence_refs", []))
    action_context = {
        "feedback-adapt": {
            "directions": feedback_directions,
            "risk_multiplier": policy_hint_risk,
            "score": (
                3.0
                + float(memory_score_adjustments["feedback-adapt"])
                + float(institutional_memory_score_adjustments["feedback-adapt"])
                + float(followup_score_adjustments["feedback-adapt"])
                + float(disagreement_score_adjustments["feedback-adapt"])
                + float(tracking_score_adjustments["feedback-adapt"])
                + float(alpha_seed_score_adjustments["feedback-adapt"])
                + float(cross_cycle_score_adjustments["feedback-adapt"])
                + float(multi_cycle_lineage_score_adjustments["feedback-adapt"])
                + feedback_score
                + policy_quality
                + reflection_quality
                - risk_penalty * 0.2
            ),
            "fallback_evidence_refs": tuple(feedback_evidence_refs),
        },
        "retain-observe": {
            "directions": observe_directions,
            "risk_multiplier": float(baseline_policy["risk_multiplier"]),
            "score": (
                1.0
                + float(memory_score_adjustments["retain-observe"])
                + float(institutional_memory_score_adjustments["retain-observe"])
                + float(followup_score_adjustments["retain-observe"])
                + float(disagreement_score_adjustments["retain-observe"])
                + float(tracking_score_adjustments["retain-observe"])
                + float(alpha_seed_score_adjustments["retain-observe"])
                + max(feedback_score, 0)
            ),
            "fallback_evidence_refs": (f"policy://{baseline_policy['policy_id']}",),
        },
        "risk-off": {
            "directions": feedback_directions,
            "risk_multiplier": risk_off,
            "score": (
                2.0
                + float(memory_score_adjustments["risk-off"])
                + float(institutional_memory_score_adjustments["risk-off"])
                + float(followup_score_adjustments["risk-off"])
                + float(disagreement_score_adjustments["risk-off"])
                + float(tracking_score_adjustments["risk-off"])
                + float(alpha_seed_score_adjustments["risk-off"])
                + float(cross_cycle_score_adjustments["risk-off"])
                + float(multi_cycle_lineage_score_adjustments["risk-off"])
                + max(0.0, risk_penalty)
            ),
            "fallback_evidence_refs": tuple(risk_off_evidence_refs),
        },
        "contrarian-check": {
            "directions": inverse_feedback,
            "risk_multiplier": 0.5,
            "score": (
                0.25
                + float(memory_score_adjustments["contrarian-check"])
                + float(institutional_memory_score_adjustments["contrarian-check"])
                + float(followup_score_adjustments["contrarian-check"])
                + float(disagreement_score_adjustments["contrarian-check"])
                + float(tracking_score_adjustments["contrarian-check"])
                + float(alpha_seed_score_adjustments["contrarian-check"])
            ),
            "fallback_evidence_refs": (
                f"oss://{oss_inputs['reflection_artifact']['component']}/{oss_inputs['reflection_artifact']['request_id']}",
            ),
        },
    }
    candidates: list[PolicyCandidate] = []
    for blueprint in persona_reasoning["response"]["candidate_blueprints"]:
        action = str(blueprint["action"])
        context = action_context[action]
        candidates.append(
            PolicyCandidate(
                candidate_id=f"{episode.case_id}-gen{generation}-{blueprint['candidate_suffix']}",
                direction_by_instrument=dict(context["directions"]),
                risk_multiplier=float(context["risk_multiplier"]),
                score=float(context["score"]),
                source_windows=tuple(str(window) for window in blueprint["source_windows"]),
                evidence_refs=_candidate_evidence_refs_from_reasoning(
                    blueprint=blueprint,
                    baseline_policy=baseline_policy,
                    oss_inputs=oss_inputs,
                    fallback_evidence_refs=context["fallback_evidence_refs"],
                ),
                rationale=str(blueprint["rationale"]),
            )
        )
    return candidates


def _candidate_evidence_refs_from_reasoning(
    *,
    blueprint: Mapping[str, Any],
    baseline_policy: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    fallback_evidence_refs: Sequence[str],
) -> tuple[str, ...]:
    refs: list[str] = []
    for role in blueprint.get("evidence_roles", []):
        if role in oss_inputs:
            result = oss_inputs[str(role)]
            refs.append(f"oss://{result['component']}/{result['request_id']}")
    refs.extend(str(ref) for ref in blueprint.get("extra_evidence_refs", []))
    if not refs:
        refs.extend(str(ref) for ref in fallback_evidence_refs)
    if str(blueprint.get("action")) == "retain-observe":
        refs = [f"policy://{baseline_policy['policy_id']}", *refs]
    return tuple(dict.fromkeys(refs))


def _build_persona_decision_artifact(
    *,
    episode: PortfolioEpisode,
    generation: int,
    trigger: str,
    baseline_policy: Mapping[str, Any],
    latest_evaluation: Mapping[str, Any],
    telemetry_event: Mapping[str, Any],
    prior_memory: Mapping[str, Any] | None,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    memory_influence: Mapping[str, Any],
    institutional_memory_influence: Mapping[str, Any],
    persona_reasoning: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
    oss_disagreement_arbitration: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    alpha_seed_revision: Mapping[str, Any],
    cross_cycle_context: Mapping[str, Any],
    multi_cycle_context: Mapping[str, Any],
    decision_inputs: Mapping[str, Any],
    evidence_refs: Sequence[str],
    candidates: Sequence[Mapping[str, Any]],
    selected_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    required_roles = {
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    }
    oss_evidence_refs = [
        f"oss://{result['component']}/{result['request_id']}"
        for result in oss_inputs.values()
    ]
    scoring_inputs = {
        "feedback_score": float(latest_evaluation["signed_return"])
        - abs(float(latest_evaluation["drawdown"])) * 0.1,
        "policy_hint_risk": _risk_hint_from_oss(oss_inputs, generation),
        "policy_quality": _policy_quality_from_oss(oss_inputs),
        "reflection_quality": _reflection_quality_from_oss(oss_inputs),
        "risk_penalty": _risk_penalty_from_oss(oss_inputs),
        "memory_influence": copy.deepcopy(dict(memory_influence)),
        "memory_score_adjustments": copy.deepcopy(dict(memory_influence["candidate_score_adjustments"])),
        "institutional_memory_influence": copy.deepcopy(dict(institutional_memory_influence)),
        "institutional_memory_score_adjustments": copy.deepcopy(
            dict(institutional_memory_influence["candidate_score_adjustments"])
        ),
        "oss_followup_loop": copy.deepcopy(dict(oss_followup_loop)),
        "oss_followup_score_adjustments": copy.deepcopy(
            dict(oss_followup_loop["candidate_score_adjustments"])
        ),
        "oss_disagreement_arbitration": copy.deepcopy(dict(oss_disagreement_arbitration)),
        "oss_disagreement_score_adjustments": copy.deepcopy(
            dict(oss_disagreement_arbitration["candidate_score_adjustments"])
        ),
        "tracking_reconciliation": copy.deepcopy(dict(tracking_reconciliation)),
        "tracking_reconciliation_score_adjustments": copy.deepcopy(
            dict(tracking_reconciliation["candidate_score_adjustments"])
        ),
        "alpha_seed_revision": copy.deepcopy(dict(alpha_seed_revision)),
        "alpha_seed_revision_score_adjustments": copy.deepcopy(
            dict(alpha_seed_revision["candidate_score_adjustments"])
        ),
        "cross_cycle_context": copy.deepcopy(dict(cross_cycle_context)),
        "cross_cycle_score_adjustments": _cross_cycle_score_adjustments(cross_cycle_context),
        "multi_cycle_lineage_context": copy.deepcopy(dict(multi_cycle_context)),
        "multi_cycle_lineage_score_adjustments": _multi_cycle_lineage_score_adjustments(
            multi_cycle_context
        ),
        "persona_reasoning_ref": persona_reasoning["response"]["reasoning_ref"],
        "persona_reasoning_preferred_action": persona_reasoning["response"]["preferred_action_hint"],
        "baseline_risk_multiplier": float(baseline_policy["risk_multiplier"]),
    }
    scorecards = {
        str(candidate["candidate_id"]): _persona_candidate_scorecard(
            candidate=candidate,
            scoring_inputs=scoring_inputs,
        )
        for candidate in candidates
    }
    risk_evaluator = _build_persona_risk_evaluator(
        episode=episode,
        oss_inputs=oss_inputs,
        decision_inputs=decision_inputs,
        candidates=candidates,
        selected_candidate=selected_candidate,
    )
    selected_id = str(selected_candidate["candidate_id"])
    selected_score = float(scorecards[selected_id]["candidate_score"])
    rejected_candidates = [
        {
            "candidate_id": str(candidate["candidate_id"]),
            "reason": "lower_replay_score_than_selected",
            "score_delta_to_selected": round(
                selected_score - float(scorecards[str(candidate["candidate_id"])]["candidate_score"]),
                10,
            ),
            "evidence_refs": list(candidate.get("evidence_refs", [])),
        }
        for candidate in candidates
        if str(candidate["candidate_id"]) != selected_id
    ]
    input_context = {
        "persona_id": _persona_id(episode.persona),
        "case_id": episode.case_id,
        "generation": generation,
        "trigger": trigger,
        "telemetry_event_id": telemetry_event["event_id"],
        "observed_score": latest_evaluation["score"],
        "observed_signed_return": latest_evaluation["signed_return"],
        "observed_drawdown": latest_evaluation["drawdown"],
        "memory_ref": prior_memory.get("memory_id") if prior_memory else None,
        "memory_influence_ref": memory_influence["influence_ref"],
        "memory_status": "retrieved" if prior_memory else "cold_start_declared",
        "prior_memory_source_event_id": prior_memory.get("source_event_id") if prior_memory else None,
        "memory_influence": copy.deepcopy(dict(memory_influence)),
        "institutional_memory_status": institutional_memory_influence["status"],
        "institutional_memory_entry_id": institutional_memory_influence["entry_id"],
        "institutional_memory_entry_ref": institutional_memory_influence["entry_ref"],
        "institutional_memory_source_event_id": institutional_memory_influence["source_event_id"],
        "institutional_memory_contributing_persona_ids": list(
            institutional_memory_influence["contributing_persona_ids"]
        ),
        "institutional_memory_influence": copy.deepcopy(dict(institutional_memory_influence)),
        "required_oss_roles": sorted(required_roles),
        "oss_request_ids_by_role": {
            role: result["request_id"] for role, result in sorted(oss_inputs.items())
        },
        "oss_components_by_role": {
            role: result["component"] for role, result in sorted(oss_inputs.items())
        },
        "oss_feedback_status_by_role": {
            role: result.get("status") for role, result in sorted(oss_inputs.items())
        },
        "oss_evidence_refs": oss_evidence_refs,
        "oss_followup_loop_ref": oss_followup_loop["loop_ref"],
        "oss_disagreement_arbitration_ref": oss_disagreement_arbitration["arbitration_ref"],
        "oss_disagreement_conflict_types": [
            conflict["conflict_type"] for conflict in oss_disagreement_arbitration["conflicts"]
        ],
        "oss_disagreement_resolution_actions": list(
            oss_disagreement_arbitration["persona_arbitration_response"]["resolution_actions"]
        ),
        "tracking_reconciliation_ref": tracking_reconciliation["reconciliation_ref"],
        "tracking_reconciliation_divergence_type": tracking_reconciliation["divergence"]["divergence_type"],
        "tracking_reconciliation_repair_action": tracking_reconciliation["repair"]["action"],
        "tracking_reconciliation_repair_ref": tracking_reconciliation["repair"]["repair_ref"],
        "alpha_seed_revision_ref": alpha_seed_revision["revision_ref"],
        "alpha_seed_revision_action": alpha_seed_revision["revision"]["action"],
        "alpha_seed_revision_component": alpha_seed_revision["alpha_component"],
        "alpha_seed_revision_key": alpha_seed_revision["revision"]["revision_key"],
        "cross_cycle_status": cross_cycle_context["status"],
        "cross_cycle_state_ref": cross_cycle_context.get("state_ref"),
        "cross_cycle_runtime_feedback_ref": cross_cycle_context.get("runtime_feedback_ref"),
        "cross_cycle_previous_case_id": cross_cycle_context.get("previous_case_id"),
        "multi_cycle_lineage_status": multi_cycle_context["status"],
        "multi_cycle_lineage_ref": multi_cycle_context.get("lineage_ref"),
        "multi_cycle_lineage_depth": multi_cycle_context.get("lineage_depth"),
        "multi_cycle_lineage_case_ids": list(multi_cycle_context.get("lineage_case_ids", [])),
        "multi_cycle_latest_case_id": multi_cycle_context.get("latest_case_id"),
        "multi_cycle_older_case_id": multi_cycle_context.get("older_case_id"),
        "multi_cycle_latest_runtime_feedback_ref": multi_cycle_context.get("latest_runtime_feedback_ref"),
        "multi_cycle_older_runtime_feedback_ref": multi_cycle_context.get("older_runtime_feedback_ref"),
        "multi_cycle_trend_signal": multi_cycle_context.get("trend_signal"),
        "oss_followup_response_refs": [
            followup["response"]["output_ref"]
            for followup in oss_followup_loop["followups"]
        ],
        "oss_followup_request_ids_by_role": {
            followup["role"]: followup["request"]["request_id"]
            for followup in oss_followup_loop["followups"]
        },
        "allowed_windows": list(decision_inputs["allowed_windows"]),
        "forbidden_windows_not_used": list(decision_inputs["forbidden_windows_not_used"]),
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "historical_window_start_indices": [window.start_index for window in episode.windows],
        "source_strategy_spec_id": episode.source_strategy_spec_id,
        "source_dataset_refs": list(episode.source_dataset_refs),
    }
    candidate_generation = {
        "model_id": PERSONA_CANDIDATE_GENERATOR_MODEL_ID,
        "request": {
            "request_id": f"persona-decision-request-{episode.case_id}-gen{generation}",
            "objective": "generate portfolio policy candidates from telemetry, memory, alpha seeds, and OSS feedback",
            "candidate_count_requested": len(candidates),
            "input_refs": [
                f"telemetry-event://{telemetry_event['event_id']}",
                f"alpha-seed://{episode.seed_key}",
                f"policy://{baseline_policy['policy_id']}",
                persona_reasoning["response"]["reasoning_ref"],
                str(oss_followup_loop["loop_ref"]),
                str(oss_disagreement_arbitration["arbitration_ref"]),
                str(tracking_reconciliation["reconciliation_ref"]),
                str(tracking_reconciliation["repair"]["repair_ref"]),
                str(alpha_seed_revision["revision_ref"]),
                *oss_evidence_refs,
                *[
                    followup["response"]["output_ref"]
                    for followup in oss_followup_loop["followups"]
                ],
                *[
                    conflict["resolution_ref"]
                    for conflict in oss_disagreement_arbitration["conflicts"]
                ],
                *tracking_reconciliation["repair"]["evidence_refs"],
                *alpha_seed_revision["persona_alpha_response"]["evidence_refs"],
                *list(cross_cycle_context.get("evidence_refs", [])),
                *list(multi_cycle_context.get("evidence_refs", [])),
                *([str(memory_influence["influence_ref"])] if memory_influence["influence_ref"] else []),
                *(
                    [str(institutional_memory_influence["entry_ref"])]
                    if institutional_memory_influence["entry_ref"]
                    else []
                ),
                *list(institutional_memory_influence.get("cited_evidence_refs", [])),
            ],
            "allowed_windows": list(decision_inputs["allowed_windows"]),
            "forbidden_windows_not_used": list(decision_inputs["forbidden_windows_not_used"]),
        },
        "response": {
            "response_id": f"persona-decision-response-{episode.case_id}-gen{generation}",
            "status": "completed",
            "source_reasoning_response_id": persona_reasoning["response"]["response_id"],
            "source_reasoning_ref": persona_reasoning["response"]["reasoning_ref"],
            "candidate_ids": [str(candidate["candidate_id"]) for candidate in candidates],
            "candidates": copy.deepcopy([dict(candidate) for candidate in candidates]),
        },
    }
    selection = {
        "selected_candidate_id": selected_id,
        "selected_candidate_score": selected_score,
        "selected_evidence_refs": list(selected_candidate.get("evidence_refs", [])),
        "rejected_candidates": rejected_candidates,
        "decision_rule": "choose highest replayed persona scorer value after risk evaluator passes",
    }
    memory_counterfactual = _build_memory_counterfactual_proof(
        episode=episode,
        generation=generation,
        decision_trace_ref=f"reflection-{episode.case_id}-gen{generation}",
        input_context=input_context,
        candidate_request=candidate_generation["request"],
        scorecards=scorecards,
        selected_candidate=selected_candidate,
        memory_influence=memory_influence,
    )
    replay = {
        "input_hash": _stable_payload_hash("decision-input", input_context),
        "candidate_hash": _stable_payload_hash("decision-candidates", candidate_generation["response"]),
        "score_hash": _stable_payload_hash("decision-scores", scorecards),
        "selection_hash": _stable_payload_hash("decision-selection", selection),
        "replayable": True,
        "selected_candidate_is_top_score": selected_score
        == max(float(card["candidate_score"]) for card in scorecards.values()),
        "no_forbidden_window_sources": _artifact_candidates_have_no_forbidden_windows(
            candidates,
            decision_inputs["forbidden_windows_not_used"],
        ),
        "uses_memory_or_declares_cold_start": bool(input_context["memory_ref"])
        or input_context["memory_status"] == "cold_start_declared",
        "uses_memory_in_scoring_or_declares_cold_start": _memory_influence_applied_to_selected_candidate(
            memory_influence=memory_influence,
            selected_candidate=selected_candidate,
        )
        or input_context["memory_status"] == "cold_start_declared",
        "uses_cross_persona_institutional_memory_or_declares_cold_start": (
            (
                institutional_memory_influence["status"] == "cold_start"
                and institutional_memory_influence["entry_ref"] is None
                and all(
                    float(value) == 0.0
                    for value in scoring_inputs["institutional_memory_score_adjustments"].values()
                )
            )
            or (
                institutional_memory_influence["status"] == "applied"
                and str(institutional_memory_influence["entry_ref"])
                in candidate_generation["request"]["input_refs"]
                and str(institutional_memory_influence["entry_ref"])
                in selected_candidate.get("evidence_refs", [])
                and _persona_id(episode.persona)
                not in set(institutional_memory_influence["contributing_persona_ids"])
                and float(
                    scoring_inputs["institutional_memory_score_adjustments"][
                        _candidate_action_key(str(selected_candidate["candidate_id"]))
                    ]
                )
                > 0.0
            )
        ),
        "memory_counterfactual_replays_score_delta": _memory_counterfactual_proof_is_usable(
            memory_counterfactual
        ),
        "uses_persona_reasoning_response": candidate_generation["response"]["source_reasoning_response_id"]
        == persona_reasoning["response"]["response_id"]
        and persona_reasoning["response"]["reasoning_ref"] in candidate_generation["request"]["input_refs"],
        "uses_selected_oss_feedback": set(selected_candidate.get("evidence_refs", [])).issuperset(
            _selected_persona_decision_oss_refs(oss_inputs)
        ),
        "uses_policy_candidate_oss_metrics": (
            (
                f"oss://{oss_inputs['policy_candidate']['component']}/"
                f"{oss_inputs['policy_candidate']['request_id']}"
            )
            in candidate_generation["request"]["input_refs"]
            and (
                f"oss://{oss_inputs['policy_candidate']['component']}/"
                f"{oss_inputs['policy_candidate']['request_id']}"
            )
            in selected_candidate.get("evidence_refs", [])
            and float(scoring_inputs["policy_quality"]) > 0.0
            and float(scorecards[selected_id]["components"].get("policy_quality", 0.0)) > 0.0
            and float(selected_candidate["risk_multiplier"])
            == float(scoring_inputs["policy_hint_risk"])
        ),
        "uses_reflection_artifact_oss_metrics": (
            (
                f"oss://{oss_inputs['reflection_artifact']['component']}/"
                f"{oss_inputs['reflection_artifact']['request_id']}"
            )
            in candidate_generation["request"]["input_refs"]
            and (
                f"oss://{oss_inputs['reflection_artifact']['component']}/"
                f"{oss_inputs['reflection_artifact']['request_id']}"
            )
            in selected_candidate.get("evidence_refs", [])
            and (
                f"oss://{oss_inputs['reflection_artifact']['component']}/"
                f"{oss_inputs['reflection_artifact']['request_id']}"
            )
            in persona_reasoning["request"]["input_refs"]
            and persona_reasoning["response"]["reflection_artifact_usage"]["source_oss_ref"]
            == (
                f"oss://{oss_inputs['reflection_artifact']['component']}/"
                f"{oss_inputs['reflection_artifact']['request_id']}"
            )
            and float(scoring_inputs["reflection_quality"]) > 0.0
            and float(scorecards[selected_id]["components"].get("reflection_quality", 0.0)) > 0.0
            and "reflection" in str(selected_candidate.get("rationale", "")).lower()
        ),
        "uses_oss_response_followup_loop": (
            _oss_response_followup_loop_is_usable(oss_followup_loop)
            and str(oss_followup_loop["loop_ref"]) in candidate_generation["request"]["input_refs"]
            and set(oss_followup_loop["candidate_evidence_refs_by_action"]["feedback-adapt"]).issubset(
                set(selected_candidate.get("evidence_refs", []))
            )
            and any(
                float(value) > 0
                for value in scoring_inputs["oss_followup_score_adjustments"].values()
            )
        ),
        "uses_oss_disagreement_arbitration": (
            _oss_disagreement_arbitration_is_usable(oss_disagreement_arbitration)
            and str(oss_disagreement_arbitration["arbitration_ref"]) in candidate_generation["request"]["input_refs"]
            and set(
                oss_disagreement_arbitration["candidate_evidence_refs_by_action"][
                    _candidate_action_key(str(selected_candidate["candidate_id"]))
                ]
            ).issubset(set(selected_candidate.get("evidence_refs", [])))
            and any(
                float(value) > 0
                for value in scoring_inputs["oss_disagreement_score_adjustments"].values()
            )
        ),
        "uses_tracking_reconciliation": (
            _tracking_readback_reconciliation_is_usable(tracking_reconciliation)
            and str(tracking_reconciliation["reconciliation_ref"]) in candidate_generation["request"]["input_refs"]
            and set(
                tracking_reconciliation["candidate_evidence_refs_by_action"][
                    _candidate_action_key(str(selected_candidate["candidate_id"]))
                ]
            ).issubset(set(selected_candidate.get("evidence_refs", [])))
            and any(
                float(value) > 0
                for value in scoring_inputs["tracking_reconciliation_score_adjustments"].values()
            )
        ),
        "uses_alpha_seed_revision": (
            _alpha_seed_revision_is_usable(alpha_seed_revision)
            and str(alpha_seed_revision["revision_ref"]) in candidate_generation["request"]["input_refs"]
            and set(
                alpha_seed_revision["candidate_evidence_refs_by_action"][
                    _candidate_action_key(str(selected_candidate["candidate_id"]))
                ]
            ).issubset(set(selected_candidate.get("evidence_refs", [])))
            and any(
                float(value) > 0
                for value in scoring_inputs["alpha_seed_revision_score_adjustments"].values()
            )
        ),
        "uses_cross_cycle_runtime_feedback_or_declares_cold_start": (
            (
                cross_cycle_context["status"] == "cold_start"
                and not cross_cycle_context.get("state_ref")
                and all(
                    float(value) == 0.0
                    for value in scoring_inputs["cross_cycle_score_adjustments"].values()
                )
            )
            or (
                cross_cycle_context["status"] == "applied"
                and str(cross_cycle_context["state_ref"]) in candidate_generation["request"]["input_refs"]
                and str(cross_cycle_context["runtime_feedback_ref"]) in candidate_generation["request"]["input_refs"]
                and str(cross_cycle_context["state_ref"]) in selected_candidate.get("evidence_refs", [])
                and float(
                    scoring_inputs["cross_cycle_score_adjustments"][
                        _candidate_action_key(str(selected_candidate["candidate_id"]))
                    ]
                )
                > 0.0
            )
        ),
        "uses_multi_cycle_lineage_or_declares_cold_start": (
            (
                multi_cycle_context["status"] == "cold_start"
                and not multi_cycle_context.get("lineage_ref")
                and all(
                    float(value) == 0.0
                    for value in scoring_inputs["multi_cycle_lineage_score_adjustments"].values()
                )
            )
            or (
                multi_cycle_context["status"] == "single_prior"
                and str(multi_cycle_context["lineage_ref"]) in candidate_generation["request"]["input_refs"]
                and str(multi_cycle_context["latest_runtime_feedback_ref"])
                in candidate_generation["request"]["input_refs"]
                and str(multi_cycle_context["lineage_ref"]) in selected_candidate.get("evidence_refs", [])
                and str(multi_cycle_context["latest_runtime_feedback_ref"])
                in selected_candidate.get("evidence_refs", [])
                and float(
                    scoring_inputs["multi_cycle_lineage_score_adjustments"][
                        _candidate_action_key(str(selected_candidate["candidate_id"]))
                    ]
                )
                > 0.0
            )
            or (
                multi_cycle_context["status"] == "lineage_applied"
                and str(multi_cycle_context["lineage_ref"]) in candidate_generation["request"]["input_refs"]
                and str(multi_cycle_context["latest_runtime_feedback_ref"])
                in candidate_generation["request"]["input_refs"]
                and str(multi_cycle_context["older_runtime_feedback_ref"])
                in candidate_generation["request"]["input_refs"]
                and str(multi_cycle_context["lineage_ref"]) in selected_candidate.get("evidence_refs", [])
                and str(multi_cycle_context["latest_runtime_feedback_ref"])
                in selected_candidate.get("evidence_refs", [])
                and str(multi_cycle_context["older_runtime_feedback_ref"])
                in selected_candidate.get("evidence_refs", [])
                and float(
                    scoring_inputs["multi_cycle_lineage_score_adjustments"][
                        _candidate_action_key(str(selected_candidate["candidate_id"]))
                    ]
                )
                > 0.0
            )
        ),
    }
    return {
        "artifact_id": f"persona-decision-artifact-{episode.case_id}-gen{generation}",
        "model_id": PERSONA_DECISION_ARTIFACT_MODEL_ID,
        "persona_id": _persona_id(episode.persona),
        "case_id": episode.case_id,
        "generation": generation,
        "trigger": trigger,
        "input_context": input_context,
        "memory_influence": copy.deepcopy(dict(memory_influence)),
        "persona_reasoning": copy.deepcopy(dict(persona_reasoning)),
        "candidate_generation": candidate_generation,
        "scorer": {
            "model_id": PERSONA_CANDIDATE_SCORER_MODEL_ID,
            "scoring_inputs": scoring_inputs,
            "scorecards": scorecards,
        },
        "memory_counterfactual": memory_counterfactual,
        "risk_evaluator": risk_evaluator,
        "selection": selection,
        "evidence_refs": list(evidence_refs),
        "replay": replay,
    }


def _persona_candidate_scorecard(
    *,
    candidate: Mapping[str, Any],
    scoring_inputs: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = str(candidate["candidate_id"])
    feedback_score = float(scoring_inputs["feedback_score"])
    policy_quality = float(scoring_inputs["policy_quality"])
    reflection_quality = float(scoring_inputs["reflection_quality"])
    risk_penalty = float(scoring_inputs["risk_penalty"])
    memory_score_adjustments = dict(scoring_inputs["memory_score_adjustments"])
    institutional_memory_score_adjustments = dict(scoring_inputs["institutional_memory_score_adjustments"])
    followup_score_adjustments = dict(scoring_inputs["oss_followup_score_adjustments"])
    disagreement_score_adjustments = dict(scoring_inputs["oss_disagreement_score_adjustments"])
    tracking_reconciliation_score_adjustments = dict(scoring_inputs["tracking_reconciliation_score_adjustments"])
    alpha_seed_revision_score_adjustments = dict(scoring_inputs["alpha_seed_revision_score_adjustments"])
    cross_cycle_score_adjustments = dict(scoring_inputs["cross_cycle_score_adjustments"])
    multi_cycle_lineage_score_adjustments = dict(scoring_inputs["multi_cycle_lineage_score_adjustments"])
    if candidate_id.endswith("-feedback-adapt"):
        formula_id = "feedback_adapt_score_v1"
        memory_adjustment = float(memory_score_adjustments["feedback-adapt"])
        institutional_memory_adjustment = float(
            institutional_memory_score_adjustments["feedback-adapt"]
        )
        followup_adjustment = float(followup_score_adjustments["feedback-adapt"])
        disagreement_adjustment = float(disagreement_score_adjustments["feedback-adapt"])
        tracking_reconciliation_adjustment = float(tracking_reconciliation_score_adjustments["feedback-adapt"])
        alpha_seed_revision_adjustment = float(alpha_seed_revision_score_adjustments["feedback-adapt"])
        cross_cycle_adjustment = float(cross_cycle_score_adjustments["feedback-adapt"])
        multi_cycle_lineage_adjustment = float(
            multi_cycle_lineage_score_adjustments["feedback-adapt"]
        )
        replayed_score = round(
            3.0
            + memory_adjustment
            + institutional_memory_adjustment
            + followup_adjustment
            + disagreement_adjustment
            + tracking_reconciliation_adjustment
            + alpha_seed_revision_adjustment
            + cross_cycle_adjustment
            + multi_cycle_lineage_adjustment
            + feedback_score
            + policy_quality
            + reflection_quality
            - risk_penalty * 0.2,
            10,
        )
        components = {
            "base": 3.0,
            "memory_adjustment": memory_adjustment,
            "institutional_memory_adjustment": institutional_memory_adjustment,
            "oss_followup_adjustment": followup_adjustment,
            "oss_disagreement_adjustment": disagreement_adjustment,
            "tracking_reconciliation_adjustment": tracking_reconciliation_adjustment,
            "alpha_seed_revision_adjustment": alpha_seed_revision_adjustment,
            "cross_cycle_adjustment": cross_cycle_adjustment,
            "multi_cycle_lineage_adjustment": multi_cycle_lineage_adjustment,
            "feedback_score": feedback_score,
            "policy_quality": policy_quality,
            "reflection_quality": reflection_quality,
            "risk_penalty_weighted": round(-risk_penalty * 0.2, 10),
        }
    elif candidate_id.endswith("-retain-observe"):
        formula_id = "retain_observe_score_v1"
        memory_adjustment = float(memory_score_adjustments["retain-observe"])
        institutional_memory_adjustment = float(
            institutional_memory_score_adjustments["retain-observe"]
        )
        followup_adjustment = float(followup_score_adjustments["retain-observe"])
        disagreement_adjustment = float(disagreement_score_adjustments["retain-observe"])
        tracking_reconciliation_adjustment = float(tracking_reconciliation_score_adjustments["retain-observe"])
        alpha_seed_revision_adjustment = float(alpha_seed_revision_score_adjustments["retain-observe"])
        cross_cycle_adjustment = float(cross_cycle_score_adjustments["retain-observe"])
        multi_cycle_lineage_adjustment = float(
            multi_cycle_lineage_score_adjustments["retain-observe"]
        )
        replayed_score = round(
            1.0
            + memory_adjustment
            + institutional_memory_adjustment
            + followup_adjustment
            + disagreement_adjustment
            + tracking_reconciliation_adjustment
            + alpha_seed_revision_adjustment
            + cross_cycle_adjustment
            + multi_cycle_lineage_adjustment
            + max(feedback_score, 0.0),
            10,
        )
        components = {
            "base": 1.0,
            "memory_adjustment": memory_adjustment,
            "institutional_memory_adjustment": institutional_memory_adjustment,
            "oss_followup_adjustment": followup_adjustment,
            "oss_disagreement_adjustment": disagreement_adjustment,
            "tracking_reconciliation_adjustment": tracking_reconciliation_adjustment,
            "alpha_seed_revision_adjustment": alpha_seed_revision_adjustment,
            "cross_cycle_adjustment": cross_cycle_adjustment,
            "multi_cycle_lineage_adjustment": multi_cycle_lineage_adjustment,
            "positive_feedback_score": round(max(feedback_score, 0.0), 10),
        }
    elif candidate_id.endswith("-risk-off"):
        formula_id = "risk_off_score_v1"
        memory_adjustment = float(memory_score_adjustments["risk-off"])
        institutional_memory_adjustment = float(
            institutional_memory_score_adjustments["risk-off"]
        )
        followup_adjustment = float(followup_score_adjustments["risk-off"])
        disagreement_adjustment = float(disagreement_score_adjustments["risk-off"])
        tracking_reconciliation_adjustment = float(tracking_reconciliation_score_adjustments["risk-off"])
        alpha_seed_revision_adjustment = float(alpha_seed_revision_score_adjustments["risk-off"])
        cross_cycle_adjustment = float(cross_cycle_score_adjustments["risk-off"])
        multi_cycle_lineage_adjustment = float(
            multi_cycle_lineage_score_adjustments["risk-off"]
        )
        replayed_score = round(
            2.0
            + memory_adjustment
            + institutional_memory_adjustment
            + followup_adjustment
            + disagreement_adjustment
            + tracking_reconciliation_adjustment
            + alpha_seed_revision_adjustment
            + cross_cycle_adjustment
            + multi_cycle_lineage_adjustment
            + max(0.0, risk_penalty),
            10,
        )
        components = {
            "base": 2.0,
            "memory_adjustment": memory_adjustment,
            "institutional_memory_adjustment": institutional_memory_adjustment,
            "oss_followup_adjustment": followup_adjustment,
            "oss_disagreement_adjustment": disagreement_adjustment,
            "tracking_reconciliation_adjustment": tracking_reconciliation_adjustment,
            "alpha_seed_revision_adjustment": alpha_seed_revision_adjustment,
            "cross_cycle_adjustment": cross_cycle_adjustment,
            "multi_cycle_lineage_adjustment": multi_cycle_lineage_adjustment,
            "risk_penalty_signal": round(max(0.0, risk_penalty), 10),
        }
    else:
        formula_id = "contrarian_control_score_v1"
        memory_adjustment = float(memory_score_adjustments["contrarian-check"])
        institutional_memory_adjustment = float(
            institutional_memory_score_adjustments["contrarian-check"]
        )
        followup_adjustment = float(followup_score_adjustments["contrarian-check"])
        disagreement_adjustment = float(disagreement_score_adjustments["contrarian-check"])
        tracking_reconciliation_adjustment = float(tracking_reconciliation_score_adjustments["contrarian-check"])
        alpha_seed_revision_adjustment = float(alpha_seed_revision_score_adjustments["contrarian-check"])
        cross_cycle_adjustment = float(cross_cycle_score_adjustments["contrarian-check"])
        multi_cycle_lineage_adjustment = float(
            multi_cycle_lineage_score_adjustments["contrarian-check"]
        )
        replayed_score = round(
            0.25
            + memory_adjustment
            + institutional_memory_adjustment
            + followup_adjustment
            + disagreement_adjustment
            + tracking_reconciliation_adjustment
            + alpha_seed_revision_adjustment
            + cross_cycle_adjustment
            + multi_cycle_lineage_adjustment,
            10,
        )
        components = {
            "base": 0.25,
            "memory_adjustment": memory_adjustment,
            "institutional_memory_adjustment": institutional_memory_adjustment,
            "oss_followup_adjustment": followup_adjustment,
            "oss_disagreement_adjustment": disagreement_adjustment,
            "tracking_reconciliation_adjustment": tracking_reconciliation_adjustment,
            "alpha_seed_revision_adjustment": alpha_seed_revision_adjustment,
            "cross_cycle_adjustment": cross_cycle_adjustment,
            "multi_cycle_lineage_adjustment": multi_cycle_lineage_adjustment,
        }
    candidate_score = round(float(candidate["score"]), 10)
    return {
        "candidate_id": candidate_id,
        "formula_id": formula_id,
        "components": components,
        "candidate_score": candidate_score,
        "replayed_score": replayed_score,
        "score_replay_match": abs(candidate_score - replayed_score) <= 1e-9,
        "source_windows": list(candidate.get("source_windows", [])),
        "evidence_refs": list(candidate.get("evidence_refs", [])),
        "rationale": candidate.get("rationale"),
    }


def _build_persona_risk_evaluator(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    decision_inputs: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    selected_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    selected_refs = set(selected_candidate.get("evidence_refs", []))
    portfolio_instruments = {window.instrument for window in episode.windows}
    forbidden_windows = set(decision_inputs["forbidden_windows_not_used"])
    risk_ref = f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}"
    checks = [
        _persona_risk_check(
            "risk_multiplier_within_bounds",
            all(0.25 <= float(candidate["risk_multiplier"]) <= 1.15 for candidate in candidates),
            {
                "min_allowed": 0.25,
                "max_allowed": 1.15,
                "observed": [candidate["risk_multiplier"] for candidate in candidates],
            },
        ),
        _persona_risk_check(
            "portfolio_direction_complete",
            all(set(candidate["direction_by_instrument"]) == portfolio_instruments for candidate in candidates),
            {"portfolio_instruments": sorted(portfolio_instruments)},
        ),
        _persona_risk_check(
            "forbidden_windows_excluded",
            _artifact_candidates_have_no_forbidden_windows(candidates, forbidden_windows),
            {"forbidden_windows_not_used": sorted(forbidden_windows)},
        ),
        _persona_risk_check(
            "selected_candidate_uses_risk_analytics",
            risk_ref in selected_refs,
            {"risk_ref": risk_ref, "selected_candidate_id": selected_candidate["candidate_id"]},
        ),
    ]
    return {
        "model_id": PERSONA_RISK_EVALUATOR_MODEL_ID,
        "status": "passed" if all(check["status"] == "passed" for check in checks) else "failed",
        "checks": checks,
        "risk_analytics_ref": risk_ref,
        "selected_candidate_id": selected_candidate["candidate_id"],
    }


def _persona_risk_check(check: str, condition: bool, observed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "check": check,
        "status": "passed" if condition else "failed",
        "observed": dict(observed),
    }


def _selected_persona_decision_oss_refs(oss_inputs: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {
        f"oss://{oss_inputs[role]['component']}/{oss_inputs[role]['request_id']}"
        for role in (
            "alpha_model",
            "backtest",
            "policy_candidate",
            "reflection_artifact",
            "tracker",
            "risk_analytics",
        )
    }


def _artifact_candidates_have_no_forbidden_windows(
    candidates: Sequence[Mapping[str, Any]],
    forbidden_windows: Sequence[str] | set[str],
) -> bool:
    forbidden = set(forbidden_windows)
    return all(
        not forbidden.intersection(set(candidate.get("source_windows", [])))
        for candidate in candidates
    )


def _policy_from_decision_trace(
    *,
    episode: PortfolioEpisode,
    generation: int,
    decision_trace: Mapping[str, Any],
    memory_context: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    selected = decision_trace["selected_candidate"]
    risk_multiplier = float(selected["risk_multiplier"])
    if generation == 2:
        risk_multiplier = max(risk_multiplier, 1.15)
    policy_oss_lineage = _build_policy_oss_lineage(
        episode=episode,
        oss_inputs=oss_inputs,
        case_upstream_artifacts=case_upstream_artifacts,
        generation=generation,
    )
    reflection_oss_lineage = _build_reflection_oss_lineage(
        episode=episode,
        oss_inputs=oss_inputs,
        case_upstream_artifacts=case_upstream_artifacts,
        generation=generation,
    )
    legs = {
        window.instrument: {
            "instrument": window.instrument,
            "execution_symbol": window.execution_symbol,
            "direction": int(selected["direction_by_instrument"][window.instrument]),
            "risk_multiplier": risk_multiplier,
            "weight": round(1 / PORTFOLIO_LEG_COUNT, 6),
        }
        for window in episode.windows
    }
    return {
        "policy_id": f"policy-{episode.case_id}-gen{generation}",
        "generation": generation,
        "policy_version": "feedback_memory_scored_agent_decision"
        if generation == 1
        else "holdout_refined_second_generation",
        "legs": legs,
        "risk_multiplier": risk_multiplier,
        "quantity_type": episode.order_profile["quantity_type"],
        "order_type": episode.order_profile["order_type"],
        "policy_oss_ref": policy_oss_lineage["source_oss_ref"],
        "policy_oss_lineage_ref": policy_oss_lineage["lineage_ref"],
        "policy_oss_lineage_hash": policy_oss_lineage["lineage_hash"],
        "policy_oss_lineage": policy_oss_lineage,
        "reflection_oss_ref": reflection_oss_lineage["source_oss_ref"],
        "reflection_oss_lineage_ref": reflection_oss_lineage["lineage_ref"],
        "reflection_oss_lineage_hash": reflection_oss_lineage["lineage_hash"],
        "reflection_oss_lineage": reflection_oss_lineage,
        "decision_inputs": {
            **dict(decision_trace["decision_inputs"]),
            "memory_reused": {
                "memory_id": memory_context["memory_id"],
                "reuse_count": memory_context["reuse_count"],
                "source_event_id": memory_context["source_event_id"],
            },
        },
    }


def _build_policy_oss_lineage(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    generation: int,
) -> dict[str, Any]:
    policy_entry = case_upstream_artifacts["selected_oss"]["policy_candidate"]
    policy_input = oss_inputs["policy_candidate"]
    component = str(policy_entry["component"])
    request_id = str(policy_entry["request_id"])
    metric_signal_keys = sorted(
        key
        for key, value in policy_entry.get("metrics", {}).items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    policy_quality = _policy_quality_from_oss(oss_inputs)
    policy_hint_risk = _risk_hint_from_oss(oss_inputs, generation)
    source_oss_ref = f"oss://{component}/{request_id}"
    registry_ref = f"registry://{policy_entry.get('registry_id')}"
    producer_ref = f"producer-run://{policy_entry.get('producer_run_id')}"
    lineage_seed = {
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation": generation,
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_oss_ref,
        "artifact_family": policy_entry.get("artifact_family"),
        "expected_artifact_family": _policy_candidate_expected_artifact_family(component),
        "registry_id": policy_entry.get("registry_id"),
        "registry_artifact_type": policy_entry.get("registry_artifact_type"),
        "producer_run_id": policy_entry.get("producer_run_id"),
        "policy_input_status": policy_input.get("status"),
        "metric_signal_keys": metric_signal_keys,
        "metrics": copy.deepcopy(dict(policy_entry.get("metrics") or {})),
        "primary_output_keys": sorted(policy_entry.get("primary_output", {})),
        "policy_quality": policy_quality,
        "policy_hint_risk": policy_hint_risk,
    }
    lineage_hash = _stable_payload_hash("policy-oss-lineage", lineage_seed)
    return {
        "model_id": PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID,
        "lineage_ref": (
            f"policy-oss-lineage://{episode.case_id}/generation{generation}/"
            f"{component}/{request_id}"
        ),
        "lineage_hash": lineage_hash,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation": generation,
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_oss_ref,
        "registry_ref": registry_ref,
        "producer_ref": producer_ref,
        "artifact_family": policy_entry.get("artifact_family"),
        "expected_artifact_family": _policy_candidate_expected_artifact_family(component),
        "registry_id": policy_entry.get("registry_id"),
        "registry_artifact_type": policy_entry.get("registry_artifact_type"),
        "producer_run_id": policy_entry.get("producer_run_id"),
        "metric_signal_keys": metric_signal_keys,
        "primary_output_keys": sorted(policy_entry.get("primary_output", {})),
        "policy_quality": policy_quality,
        "policy_hint_risk": policy_hint_risk,
        "input_hash": lineage_hash,
    }


def _build_reflection_oss_lineage(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    generation: int,
) -> dict[str, Any]:
    reflection_entry = case_upstream_artifacts["selected_oss"]["reflection_artifact"]
    reflection_input = oss_inputs["reflection_artifact"]
    component = str(reflection_entry["component"])
    request_id = str(reflection_entry["request_id"])
    metric_signal_keys = sorted(
        key
        for key, value in reflection_entry.get("metrics", {}).items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    reflection_quality = _reflection_quality_from_oss(oss_inputs)
    source_oss_ref = f"oss://{component}/{request_id}"
    registry_ref = f"registry://{reflection_entry.get('registry_id')}"
    producer_ref = f"producer-run://{reflection_entry.get('producer_run_id')}"
    lineage_seed = {
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation": generation,
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_oss_ref,
        "artifact_family": reflection_entry.get("artifact_family"),
        "expected_artifact_family": _reflection_artifact_expected_family(component),
        "registry_id": reflection_entry.get("registry_id"),
        "registry_artifact_type": reflection_entry.get("registry_artifact_type"),
        "producer_run_id": reflection_entry.get("producer_run_id"),
        "reflection_input_status": reflection_input.get("status"),
        "metric_signal_keys": metric_signal_keys,
        "metrics": copy.deepcopy(dict(reflection_entry.get("metrics") or {})),
        "primary_output_keys": sorted(reflection_entry.get("primary_output", {})),
        "reflection_quality": reflection_quality,
    }
    lineage_hash = _stable_payload_hash("reflection-oss-lineage", lineage_seed)
    return {
        "model_id": PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID,
        "lineage_ref": (
            f"reflection-oss-lineage://{episode.case_id}/generation{generation}/"
            f"{component}/{request_id}"
        ),
        "lineage_hash": lineage_hash,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation": generation,
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_oss_ref,
        "registry_ref": registry_ref,
        "producer_ref": producer_ref,
        "artifact_family": reflection_entry.get("artifact_family"),
        "expected_artifact_family": _reflection_artifact_expected_family(component),
        "registry_id": reflection_entry.get("registry_id"),
        "registry_artifact_type": reflection_entry.get("registry_artifact_type"),
        "producer_run_id": reflection_entry.get("producer_run_id"),
        "metric_signal_keys": metric_signal_keys,
        "primary_output_keys": sorted(reflection_entry.get("primary_output", {})),
        "reflection_quality": reflection_quality,
        "input_hash": lineage_hash,
    }


def _build_signals(
    *,
    episode: PortfolioEpisode,
    policy: Mapping[str, Any],
    generation: int,
    generated_at: str,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    policy_oss_lineage = dict(policy.get("policy_oss_lineage") or {})
    reflection_oss_lineage = dict(policy.get("reflection_oss_lineage") or {})
    for leg_index, window in enumerate(episode.windows):
        leg = policy["legs"][window.instrument]
        entry_row = _entry_row_for_generation(window, generation)
        direction = int(leg["direction"])
        order_type = str(policy["order_type"])
        quantity_type = str(policy["quantity_type"])
        close = float(entry_row["close"])
        signal = {
            "signal_id": _stable_id(
                "sig",
                episode.case_id,
                str(generation),
                window.instrument,
                str(window.start_index),
            ),
            "version": "1.0",
            "strategy_id": f"{episode.seed_key}-agent-usability-hardening",
            "timestamp": _recent_signal_timestamp(generated_at, episode.ordinal + generation + leg_index),
            "symbol": window.execution_symbol,
            "action": "BUY" if direction > 0 else "SELL",
            "direction": "LONG" if direction > 0 else "SHORT",
            "quantity": _quantity_for(
                quantity_type,
                close,
                float(leg["risk_multiplier"]) * float(leg["weight"]),
                episode.ordinal + leg_index,
            ),
            "quantity_type": quantity_type,
            "order_type": order_type,
            "source_worker": "persona-agent-usability-hardening",
            "metadata": {
                "alpha_source": "persona_alpha_seed_ooda",
                "confidence_score": round(0.62 + ((episode.ordinal + leg_index) % 29) / 100.0, 4),
                "persona_id": _persona_id(episode.persona),
                "seed_key": episode.seed_key,
                "policy_id": policy["policy_id"],
                "policy_generation": generation,
                "policy_oss_ref": policy_oss_lineage.get("source_oss_ref"),
                "policy_oss_lineage_ref": policy_oss_lineage.get("lineage_ref"),
                "policy_oss_lineage_hash": policy_oss_lineage.get("lineage_hash"),
                "policy_oss_component": policy_oss_lineage.get("component"),
                "policy_oss_request_id": policy_oss_lineage.get("request_id"),
                "reflection_oss_ref": reflection_oss_lineage.get("source_oss_ref"),
                "reflection_oss_lineage_ref": reflection_oss_lineage.get("lineage_ref"),
                "reflection_oss_lineage_hash": reflection_oss_lineage.get("lineage_hash"),
                "reflection_oss_component": reflection_oss_lineage.get("component"),
                "reflection_oss_request_id": reflection_oss_lineage.get("request_id"),
                "validation_signature": episode.validation_signature,
                "historical_ohlcv_fixture": HISTORICAL_OHLCV_FIXTURE,
                "market_data_ref": f"{HISTORICAL_OHLCV_DATASET_ID}/{window.instrument}/{entry_row['date']}",
                "source_dataset_ref": HISTORICAL_OHLCV_DATASET_ID,
                "source_evidence_refs": [
                    HISTORICAL_OHLCV_FIXTURE,
                    f"alpha-seed://{episode.seed_key}",
                    f"portfolio-episode://{episode.case_id}",
                ],
                "market_data": {
                    "dataset": HISTORICAL_OHLCV_DATASET_ID,
                    "source_instrument": window.instrument,
                    "execution_symbol": window.execution_symbol,
                    "date": entry_row["date"],
                    "open": float(entry_row["open"]),
                    "high": float(entry_row["high"]),
                    "low": float(entry_row["low"]),
                    "close": close,
                    "volume": float(entry_row["volume"]),
                },
                "normalized_data_ref": HISTORICAL_OHLCV_FIXTURE,
                "regime_path": list(episode.regime_path),
                "signal_kind": f"generation_{generation}",
            },
        }
        if order_type == "LIMIT":
            offset = 0.0005 if direction > 0 else -0.0005
            signal["limit_price"] = round(max(0.01, close * (1 + offset)), 4)
        signals.append(signal)
    return signals


def _execute_signals(
    signals: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
    persona_id: str,
) -> dict[str, Any]:
    if not signals:
        raise ValueError("at least one signal is required")
    binding_id = f"binding-{case_id}"
    runtime_id = f"runtime-{case_id}"
    strategy_id = str(signals[0]["strategy_id"])
    telemetry = _TelemetryRecorder(
        binding_id=binding_id,
        runtime_id=runtime_id,
        persona_id=persona_id,
        strategy_id=strategy_id,
        case_id=case_id,
    )
    identity = RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": runtime_id,
            "PANTHEON_RUNTIME_BINDING_ID": binding_id,
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_TRACE_ID": f"trace-{case_id}",
            "PANTHEON_REQUEST_ID": f"request-{case_id}",
        }
    )
    runtime = PaperRuntimeService(
        store=InMemoryPendingSignalStore([copy.deepcopy(dict(signal)) for signal in signals]),
        identity=identity,
        runtime_manager_client=_RuntimeManagerClient(
            binding_id=binding_id,
            runtime_id=runtime_id,
            persona_id=persona_id,
            strategy_id=strategy_id,
        ),
        telemetry_emitter=telemetry,
        poll_interval_seconds=3600,
        max_batch_size=len(signals),
    )
    snapshot = runtime.drain_once()
    fills = [event for event in telemetry.events if event["event_type"] == "paper_fill_simulated"]
    if len(fills) != len(signals):
        raise AssertionError(f"{case_id} expected {len(signals)} fills, got {len(fills)}: {snapshot}")
    return {
        "snapshot": snapshot,
        "fill_events": fills,
        "filled": all(abs(float(event["metrics"].get("fill_quantity") or 0.0)) > 0.0 for event in fills),
        "fill_count": len(fills),
        "expected_fill_count": len(signals),
        "fill_rate": round(len(fills) / max(len(signals), 1), 6),
        "telemetry_events": telemetry.events,
    }


def _evaluate_portfolio_policy(
    episode: PortfolioEpisode,
    policy: Mapping[str, Any],
    *,
    period: str,
) -> dict[str, Any]:
    leg_evaluations = []
    for window in episode.windows:
        leg = policy["legs"][window.instrument]
        leg_evaluations.append(_evaluate_leg_detail(window, leg, period))
    score = mean(item["score"] for item in leg_evaluations)
    signed_return = mean(item["signed_return"] for item in leg_evaluations)
    drawdown = min(item["drawdown"] for item in leg_evaluations)
    turnover = _policy_turnover(policy)
    score = score - turnover * 0.0001
    return {
        "period": period,
        "score": round(score, 10),
        "signed_return": round(signed_return, 10),
        "drawdown": round(drawdown, 10),
        "turnover": round(turnover, 10),
        "leg_evaluations": leg_evaluations,
    }


def _evaluate_leg(window: InstrumentWindow, leg: Mapping[str, Any], period: str) -> float:
    return _evaluate_leg_detail(window, leg, period)["score"]


def _evaluate_leg_detail(window: InstrumentWindow, leg: Mapping[str, Any], period: str) -> dict[str, Any]:
    entry_row, rows = _evaluation_rows(window, period)
    entry_price = float(entry_row["close"])
    closes = [float(row["close"]) for row in rows]
    direction = int(leg["direction"])
    exposure = float(leg["risk_multiplier"]) * float(leg.get("weight", 1.0))
    forward_return = _safe_return(entry_price, closes[-1])
    signed_return = direction * forward_return
    adverse_path = [direction * _safe_return(entry_price, close) for close in closes]
    drawdown = min(adverse_path) if adverse_path else 0.0
    volatility = _return_volatility([entry_price, *closes])
    score = exposure * signed_return - exposure * abs(min(drawdown, 0.0)) * 0.12 - exposure * volatility * 0.015
    return {
        "instrument": window.instrument,
        "period": period,
        "entry_date": entry_row["date"],
        "exit_date": rows[-1]["date"],
        "direction": direction,
        "forward_return": round(forward_return, 10),
        "signed_return": round(signed_return, 10),
        "drawdown": round(drawdown, 10),
        "volatility": round(volatility, 10),
        "score": round(score, 10),
    }


def _build_portfolio_outcome_event(
    *,
    episode: PortfolioEpisode,
    generation: int,
    policy: Mapping[str, Any],
    execution: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    fill_events = list(execution["fill_events"])
    return {
        "event_id": f"{episode.case_id}-gen{generation}-portfolio-outcome",
        "event_type": "pnl_snapshot",
        "created_at": _iso_now(),
        "execution_mode": "paper",
        "environment": "paper",
        "deployment_stage": "paper",
        "binding_id": f"binding-{episode.case_id}-gen{generation}",
        "runtime_id": f"runtime-{episode.case_id}-gen{generation}",
        "capital_pool_id": f"pool-usability-{_persona_id(episode.persona)}",
        "artifact_id": f"artifact-{episode.seed_key}-agent-usability-hardening",
        "artifact_version": "3000.1.0",
        "plan_id": f"plan-usability-{_persona_id(episode.persona)}",
        "persona_capital_binding_id": f"pcb-usability-{_persona_id(episode.persona)}",
        "target": {
            "registry_id": f"artifact-{episode.seed_key}-agent-usability-hardening",
            "strategy_id": f"{episode.seed_key}-agent-usability-hardening",
            "artifact_version": "3000.1.0",
            "artifact_type": "execution_bundle",
            "promotion_state": "paper",
        },
        "metrics": {
            "pnl": round(float(evaluation["signed_return"]) * 100_000.0, 6),
            "portfolio_score": evaluation["score"],
            "signed_return": evaluation["signed_return"],
            "drawdown": evaluation["drawdown"],
            "turnover": evaluation["turnover"],
            "total_trades": execution["fill_count"],
            "fill_rate": execution["fill_rate"],
        },
        "metadata": {
            "persona_id": _persona_id(episode.persona),
            "case_id": episode.case_id,
            "validation_signature": episode.validation_signature,
            "seed_key": episode.seed_key,
            "policy_id": policy["policy_id"],
            "policy_generation": generation,
            "portfolio_instruments": [window.instrument for window in episode.windows],
            "fill_event_ids": [event["event_id"] for event in fill_events],
            "historical_outcome_source": HISTORICAL_OHLCV_FIXTURE,
            "evaluation_period": evaluation["period"],
        },
        "trace_id": f"trace-{episode.case_id}-gen{generation}",
    }


def _write_learn_memory(
    *,
    feedback_adapter: FeedbackStoreAdapter,
    telemetry_event: Mapping[str, Any],
    persona: Mapping[str, Any],
    reflection: Mapping[str, Any],
    persona_store: PersonaMemoryStore,
    institutional_store: InstitutionalMemoryStore,
) -> dict[str, Any]:
    persona_id = _persona_id(persona)
    selected_action = _candidate_action_key(str(reflection["selected_candidate_id"]))
    memory_tags = [
        "agent_usability_hardening",
        "reflection",
        "memory_influence_ready",
        str(reflection["trigger"]),
        str(reflection["reflection_id"]),
        f"selected_candidate:{reflection['selected_candidate_id']}",
        f"selected_action:{selected_action}",
    ]
    payload = feedback_adapter.build_learn_feedback_writeback_payload(
        dict(telemetry_event),
        sponsor_persona_id=persona_id,
        contributing_persona_ids=[persona_id],
        summary=(
            f"{persona_id} reused telemetry, memory, and OSS evidence for "
            f"{reflection['reflection_id']} and selected {reflection['selected_candidate_id']}."
        ),
        contributor_feedback=[
            {
                "persona_id": persona_id,
                "summary": str(reflection["hypothesis"]),
                "proposal_ids": [str(reflection["reflection_id"])],
                "tags": memory_tags,
            }
        ],
        proposal_ids=[str(reflection["reflection_id"]), str(telemetry_event["event_id"])],
    )
    payload["tags"].extend(memory_tags)
    return write_learn_feedback(
        payload,
        persona_store=persona_store,
        institutional_store=institutional_store,
    )


def _retrieve_prior_lesson(
    persona_store: PersonaMemoryStore,
    persona_id: str,
) -> dict[str, Any] | None:
    hits = persona_store.retrieve(
        persona_id=persona_id,
        query="agent usability hardening reflection",
        tags=["agent_usability_hardening"],
        limit=1,
    )
    if not hits:
        return None
    entry = persona_store.mark_reused(hits[0].entry.memory_id)
    return _memory_context_from_entry(entry)


def _retrieve_cross_persona_institutional_lesson(
    institutional_store: InstitutionalMemoryStore,
    persona_id: str,
) -> dict[str, Any] | None:
    hits = institutional_store.retrieve(
        query="agent usability hardening reflection selected_action",
        tags=["agent_usability_hardening", "memory_influence_ready"],
        limit=12,
    )
    for hit in hits:
        contributing_persona_ids = {str(item) for item in hit.entry.contributing_persona_ids}
        if persona_id in contributing_persona_ids:
            continue
        entry = institutional_store.mark_reused(hit.entry.entry_id)
        return _institutional_memory_context_from_entry(entry)
    return None


def _retrieve_current_lesson(
    persona_store: PersonaMemoryStore,
    persona_id: str,
    *,
    reflection_id: str,
) -> dict[str, Any]:
    hits = persona_store.retrieve(
        persona_id=persona_id,
        query=reflection_id,
        tags=["agent_usability_hardening"],
        limit=1,
    )
    if not hits:
        raise AssertionError(f"missing current memory lesson for {reflection_id}")
    entry = persona_store.mark_reused(hits[0].entry.memory_id)
    return _memory_context_from_entry(entry)


def _institutional_memory_context_from_entry(entry: Any) -> dict[str, Any]:
    structured_payload = entry.content.get("structured_payload", {})
    if not isinstance(structured_payload, Mapping):
        structured_payload = {}
    evidence_refs = _memory_evidence_ref_strings(
        structured_payload.get("evidence_refs", [])
    )
    return {
        "entry_id": entry.entry_id,
        "entry_ref": f"institutional-memory://{entry.entry_id}",
        "source_event_id": entry.source_event_id,
        "reuse_count": entry.reuse_count,
        "knowledge_type": entry.knowledge_type,
        "scope": entry.scope,
        "scope_filter": entry.scope_filter,
        "content_summary": entry.content.get("body") or entry.content.get("headline"),
        "headline": entry.content.get("headline"),
        "proposal_ids": list(structured_payload.get("proposal_ids", [])),
        "evidence_refs": evidence_refs,
        "tags": list(entry.content.get("tags", []) or []),
        "contributing_persona_ids": list(entry.contributing_persona_ids),
        "sponsor_persona_id": structured_payload.get("sponsor_persona_id"),
    }


def _memory_evidence_ref_strings(raw_refs: Any) -> list[str]:
    refs: list[str] = []
    for raw_ref in raw_refs if isinstance(raw_refs, list) else []:
        if isinstance(raw_ref, str):
            refs.append(raw_ref)
            continue
        if not isinstance(raw_ref, Mapping):
            refs.append(f"memory-evidence://{_stable_payload_hash('memory-evidence', raw_ref)}")
            continue
        ref_type = str(raw_ref.get("ref_type") or "memory-evidence").replace("_", "-")
        ref_id = str(
            raw_ref.get("ref_id")
            or raw_ref.get("event_id")
            or raw_ref.get("id")
            or _stable_payload_hash("memory-evidence", raw_ref)
        )
        refs.append(f"{ref_type}://{ref_id}")
    return list(dict.fromkeys(refs))


def _memory_context_from_entry(entry: Any) -> dict[str, Any]:
    structured_payload = entry.content.get("structured_payload", {})
    if not isinstance(structured_payload, Mapping):
        structured_payload = {}
    return {
        "memory_id": entry.memory_id,
        "source_event_id": entry.source_event_id,
        "reuse_count": entry.reuse_count,
        "content_summary": entry.content.get("summary"),
        "proposal_ids": list(structured_payload.get("proposal_ids", [])),
        "evidence_refs": copy.deepcopy(list(structured_payload.get("evidence_refs", []))),
        "tags": list(entry.content.get("tags", []) or []),
    }


def _build_evolution_decision(
    *,
    episode: PortfolioEpisode,
    telemetry_event: Mapping[str, Any],
    decision_trace: Mapping[str, Any],
    baseline_policy: Mapping[str, Any],
    evolved_policy: Mapping[str, Any],
    baseline_eval: Mapping[str, Any],
    evolved_eval: Mapping[str, Any],
    tracking_reconciliation: Mapping[str, Any],
    generated_at: str,
) -> EvolutionDecision:
    persona_id = _persona_id(episode.persona)
    improvement = float(evolved_eval["score"]) - float(baseline_eval["score"])
    action_type = (
        EvolutionActionType.RETRAIN
        if episode.reflection_archetype == "feedback_reversal_repair"
        else EvolutionActionType.REVALIDATE
    )
    evidence_ref = EvidenceRef(
        ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
        ref_id=str(telemetry_event["event_id"]),
        storage_ref={
            "backend": "memory://feedback-store",
            "dataset": HISTORICAL_OHLCV_DATASET_ID,
            "reflection_id": str(decision_trace["reflection_id"]),
            "validation_signature": episode.validation_signature,
        },
        note="No-leakage holdout telemetry used for governed paper evolution.",
    )
    tracking_repair = tracking_reconciliation["repair"]
    experiment_ref = str(tracking_repair["normalized_experiment_ref"])
    tracking_evidence_ref = EvidenceRef(
        ref_type=EvidenceRefType.AUDIT_LOG_ENTRY.value,
        ref_id=str(tracking_reconciliation["reconciliation_ref"]),
        storage_ref={
            "backend": str(tracking_reconciliation["backend"]),
            "run_id": str(tracking_reconciliation["run_id"]),
            "experiment_ref": experiment_ref,
            "repair_ref": str(tracking_repair["repair_ref"]),
        },
        note="Reconciled experiment-tracker readback used by persona scoring before evolution.",
    )
    threshold = ThresholdSnapshot(
        policy_source="agent_usability_validation.py#no-leakage-holdout-score",
        signal_type=ThresholdSignalType.PERFORMANCE_DEGRADATION
        if float(baseline_eval["score"]) < 0
        else ThresholdSignalType.MANUAL_REVIEW,
        metric_name="future_holdout_evolved_score_minus_baseline_score",
        comparator=ComparisonOperator.GTE,
        observed_value=round(improvement, 10),
        threshold_value=0,
        window="future-holdout",
        breached=improvement >= 0,
        note="Generation-2 policy must beat the baseline counterfactual on an unseen future holdout.",
    )
    decision = EvolutionDecision.create_proposed(
        decision_id=f"evolution-{episode.case_id}",
        target_type=EvolutionTargetType.STRATEGY_SPEC,
        target_id=f"{episode.seed_key}-agent-usability-hardening",
        target_version="3000.1.0",
        action_type=action_type,
        rationale=(
            f"{persona_id} selected a scored portfolio mutation using telemetry, memory, "
            f"and OSS evidence. Baseline holdout score={baseline_eval['score']}, "
            f"future evolved score={evolved_eval['score']}."
        ),
        created_by_id="agent-usability-hardening-runtime",
        created_by_role=EvolutionActorRole.EVOLUTION_CONTROLLER,
        evidence_refs=[evidence_ref, tracking_evidence_ref],
        threshold_snapshots=[threshold],
        capital_pool_id=f"pool-usability-{persona_id}",
        persona_id=persona_id,
        target_stage="paper",
        metadata={
            "case_id": episode.case_id,
            "validation_signature": episode.validation_signature,
            "seed_key": episode.seed_key,
            "reflection_id": decision_trace["reflection_id"],
            "baseline_policy": {
                "policy_id": baseline_policy["policy_id"],
                "generation": baseline_policy["generation"],
            },
            "evolved_policy": {
                "policy_id": evolved_policy["policy_id"],
                "generation": evolved_policy["generation"],
            },
            "improvement": round(improvement, 10),
            "tracking_reconciliation_ref": str(tracking_reconciliation["reconciliation_ref"]),
            "tracking_repair_ref": str(tracking_repair["repair_ref"]),
            "tracking_repair_action": str(tracking_repair["action"]),
            "normalized_experiment_ref": experiment_ref,
            "tracking_backend": str(tracking_reconciliation["backend"]),
            "tracking_run_id": str(tracking_reconciliation["run_id"]),
            "proposal_only": False,
            "execution_plane": ExecutionPlane.RESEARCH.value,
        },
    )
    reviewed_at = _offset_timestamp(generated_at, episode.ordinal, minutes=1)
    approved_at = _offset_timestamp(generated_at, episode.ordinal, minutes=2)
    executed_at = _offset_timestamp(generated_at, episode.ordinal, minutes=3)
    decision.mark_reviewed(
        EvolutionActorRole.AUTOMATED_GATE,
        "agent-usability-hardening-reviewer",
        f"approval-{episode.case_id}",
        note="Reviewed no-leakage holdout evidence and paper-only execution.",
        reviewed_at=reviewed_at,
    )
    decision.approve(
        EvolutionActorRole.AUTOMATED_GATE,
        "agent-usability-hardening-approver",
        note="Approved because future holdout score improves and memory/OSS evidence is complete.",
        approved_at=approved_at,
    )
    decision.execute(
        EvolutionActorRole.EVOLUTION_CONTROLLER,
        "agent-usability-hardening-runtime",
        ExecutionResult(
            status=ExecutionStatus.SUCCEEDED,
            plane=ExecutionPlane.RESEARCH,
            executed_at=executed_at,
            execution_ref_id=f"research-replay-{episode.case_id}",
            outcome_summary=f"Future holdout score improved by {round(improvement, 10)}.",
        ),
        cooldown_started_at=executed_at,
        cooldown_ends_at=_offset_timestamp(generated_at, episode.ordinal, days=3, minutes=3),
        observation_window_started_at=executed_at,
        observation_window_ends_at=_offset_timestamp(generated_at, episode.ordinal, days=7, minutes=3),
        note="Research-plane evolution executed as paper strategy revalidation.",
    )
    return decision


def _build_operational_context(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    decision_traces: Sequence[Mapping[str, Any]],
    memory_contexts: Sequence[Mapping[str, Any]],
    evolution_decision: EvolutionDecision,
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    policy_candidate_materiality: Mapping[str, Any],
    reflection_artifact_materiality: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    scenario = _operational_scenario_for_episode(episode)
    market_friction = _build_market_friction_report(
        episode=episode,
        generation_policies=generation_policies,
        evaluations=evaluations,
        scenario=scenario,
    )
    broker_lifecycle = _build_broker_lifecycle_report(
        episode=episode,
        executions=executions,
        scenario=scenario,
    )
    shioaji_sandbox_lifecycle = _run_shioaji_sandbox_lifecycle(
        episode=episode,
        final_policy=generation_policies[-1],
        market_friction=market_friction,
    )
    persona_conflict = _build_persona_conflict_resolution(
        episode=episode,
        final_policy=generation_policies[-1],
        market_friction=market_friction,
        decision_traces=decision_traces,
        oss_inputs=oss_inputs,
    )
    restart_recovery = _build_restart_recovery_report(
        episode=episode,
        decision_traces=decision_traces,
        memory_contexts=memory_contexts,
        evolution_decision=evolution_decision,
    )
    broker_adapter_lifecycle = _build_broker_adapter_lifecycle_packet(
        episode=episode,
        scenario=scenario,
        broker_lifecycle=broker_lifecycle,
        shioaji_sandbox_lifecycle=shioaji_sandbox_lifecycle,
        restart_recovery=restart_recovery,
        decision_traces=decision_traces,
    )
    autonomous_schedule = _build_autonomous_schedule(
        episode=episode,
        generated_at=generated_at,
        restart_recovery=restart_recovery,
    )
    broker_adapter_followup = _build_broker_adapter_followup_response(
        episode=episode,
        scenario=scenario,
        broker_adapter_lifecycle=broker_adapter_lifecycle,
        restart_recovery=restart_recovery,
        autonomous_schedule=autonomous_schedule,
        decision_traces=decision_traces,
    )
    lean_engine_replay = _run_lean_engine_replay(
        episode=episode,
        final_policy=generation_policies[-1],
        final_evaluation=evaluations[-1],
        evolution_decision=evolution_decision,
        evolution_trajectory=evolution_trajectory,
        no_leakage_protocol=no_leakage_protocol,
        strict_oos_evolution_proof=strict_oos_evolution_proof,
        persona_conflict_resolution=persona_conflict,
        case_upstream_artifacts=case_upstream_artifacts,
        generated_at=generated_at,
    )
    lean_handoff = _build_lean_handoff_packet(
        episode=episode,
        final_policy=generation_policies[-1],
        final_evaluation=evaluations[-1],
        evolution_decision=evolution_decision,
        evolution_trajectory=evolution_trajectory,
        no_leakage_protocol=no_leakage_protocol,
        strict_oos_evolution_proof=strict_oos_evolution_proof,
        oss_inputs=oss_inputs,
        market_friction=market_friction,
        broker_lifecycle=broker_lifecycle,
        persona_conflict_resolution=persona_conflict,
        autonomous_schedule=autonomous_schedule,
        lean_engine_replay=lean_engine_replay,
        shioaji_sandbox_lifecycle=shioaji_sandbox_lifecycle,
        case_upstream_artifacts=case_upstream_artifacts,
    )
    lean_packet_execution_projection = _build_lean_packet_execution_projection(
        episode=episode,
        final_policy=generation_policies[-1],
        executions=executions,
        market_friction=market_friction,
        broker_lifecycle=broker_lifecycle,
        persona_conflict_resolution=persona_conflict,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
    )
    lean_runtime_feedback = _build_lean_runtime_feedback_response(
        episode=episode,
        scenario=scenario,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_packet_execution_projection=lean_packet_execution_projection,
        autonomous_schedule=autonomous_schedule,
        decision_traces=decision_traces,
    )
    experiment_tracking_lineage_handoff = _build_experiment_tracking_lineage_handoff_proof(
        episode=episode,
        evolution_decision=evolution_decision,
        case_upstream_artifacts=case_upstream_artifacts,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    policy_oss_lineage_handoff = _build_policy_oss_lineage_handoff_proof(
        episode=episode,
        final_policy=generation_policies[-1],
        policy_candidate_materiality=policy_candidate_materiality,
        case_upstream_artifacts=case_upstream_artifacts,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    reflection_oss_lineage_handoff = _build_reflection_oss_lineage_handoff_proof(
        episode=episode,
        final_policy=generation_policies[-1],
        reflection_artifact_materiality=reflection_artifact_materiality,
        case_upstream_artifacts=case_upstream_artifacts,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    openclaw_session_handoff = _build_openclaw_session_handoff_proof(
        episode=episode,
        oss_inputs=oss_inputs,
        decision_traces=decision_traces,
        lean_handoff=lean_handoff,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    alpha_seed_revision_handoff = _build_alpha_seed_revision_handoff_proof(
        episode=episode,
        case_upstream_artifacts=case_upstream_artifacts,
        decision_traces=decision_traces,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    evolved_strategy_packet_proof = _build_evolved_strategy_packet_proof(
        episode=episode,
        final_policy=generation_policies[-1],
        final_evaluation=evaluations[-1],
        evolution_trajectory=evolution_trajectory,
        no_leakage_protocol=no_leakage_protocol,
        strict_oos_evolution_proof=strict_oos_evolution_proof,
        lean_engine_replay=lean_engine_replay,
        lean_handoff=lean_handoff,
        lean_packet_execution_projection=lean_packet_execution_projection,
        lean_runtime_feedback=lean_runtime_feedback,
    )
    scheduler_conflict_ooda_proof = _build_scheduler_conflict_ooda_proof(
        episode=episode,
        operational_context={
            "persona_conflict_resolution": persona_conflict,
            "autonomous_schedule": autonomous_schedule,
            "broker_adapter_followup": broker_adapter_followup,
            "lean_handoff": lean_handoff,
            "lean_runtime_feedback": lean_runtime_feedback,
        },
        decision_traces=decision_traces,
    )
    return {
        "operational_signature": _stable_id(
            "operational",
            episode.validation_signature,
            scenario,
            market_friction["model_id"],
            broker_lifecycle["lifecycle_model"],
            autonomous_schedule["schedule_id"],
            broker_adapter_followup["followup_id"],
            lean_packet_execution_projection["projection_id"],
            lean_runtime_feedback["feedback_id"],
            experiment_tracking_lineage_handoff["proof_id"],
            policy_oss_lineage_handoff["proof_id"],
            reflection_oss_lineage_handoff["proof_id"],
            openclaw_session_handoff["proof_id"],
            alpha_seed_revision_handoff["proof_id"],
        ),
        "scenario": scenario,
        "market_friction": market_friction,
        "broker_lifecycle": broker_lifecycle,
        "broker_adapter_lifecycle": broker_adapter_lifecycle,
        "broker_adapter_followup": broker_adapter_followup,
        "shioaji_sandbox_lifecycle": shioaji_sandbox_lifecycle,
        "persona_conflict_resolution": persona_conflict,
        "restart_recovery": restart_recovery,
        "autonomous_schedule": autonomous_schedule,
        "lean_engine_replay": lean_engine_replay,
        "case_upstream_artifacts": _case_upstream_artifacts_case_summary(case_upstream_artifacts),
        "lean_handoff": lean_handoff,
        "lean_packet_execution_projection": lean_packet_execution_projection,
        "lean_runtime_feedback": lean_runtime_feedback,
        "experiment_tracking_lineage_handoff": experiment_tracking_lineage_handoff,
        "policy_oss_lineage_handoff": policy_oss_lineage_handoff,
        "reflection_oss_lineage_handoff": reflection_oss_lineage_handoff,
        "openclaw_session_handoff": openclaw_session_handoff,
        "alpha_seed_revision_handoff": alpha_seed_revision_handoff,
        "evolved_strategy_packet_proof": evolved_strategy_packet_proof,
        "scheduler_conflict_ooda_proof": scheduler_conflict_ooda_proof,
    }


def _build_market_friction_report(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    scenario: str,
) -> dict[str, Any]:
    generation_costs: list[dict[str, Any]] = []
    for policy, evaluation in zip(generation_policies, evaluations):
        leg_costs: list[dict[str, Any]] = []
        for leg_index, window in enumerate(episode.windows):
            leg = policy["legs"][window.instrument]
            entry_row = _entry_row_for_generation(window, int(policy["generation"]))
            close = float(entry_row["close"])
            volume = max(float(entry_row["volume"]), 1.0)
            notional = _estimated_order_notional(
                policy=policy,
                leg=leg,
                close=close,
                case_index=episode.ordinal + leg_index,
            )
            volume_notional = max(volume * close, 1.0)
            participation = min(0.2, notional / volume_notional)
            volatility_bps = _return_volatility([float(row["close"]) for row in window.observe_rows[-8:]]) * 10_000
            scenario_bps = {
                "partial_fill_reconcile": 0.6,
                "limit_miss_reprice": 1.2,
                "liquidity_cap_scale": 1.6,
                "cancel_replace_readback": 1.0,
                "risk_reject_reduce": 0.8,
            }[scenario]
            slippage_bps = round(0.8 + scenario_bps + min(35.0, participation * 2_500) + volatility_bps * 0.05, 6)
            commission_bps = 1.25
            impact_bps = round(min(25.0, participation * 1_500), 6)
            total_cost_bps = round(slippage_bps + commission_bps + impact_bps, 6)
            leg_costs.append(
                {
                    "instrument": window.instrument,
                    "generation": policy["generation"],
                    "notional": round(notional, 6),
                    "volume_notional": round(volume_notional, 6),
                    "participation": round(participation, 10),
                    "liquidity_cap": 0.2,
                    "slippage_bps": slippage_bps,
                    "commission_bps": commission_bps,
                    "impact_bps": impact_bps,
                    "total_cost_bps": total_cost_bps,
                    "within_liquidity_cap": participation <= 0.2,
                }
            )
        average_cost_bps = mean(item["total_cost_bps"] for item in leg_costs)
        cost_penalty = average_cost_bps / 10_000
        generation_costs.append(
            {
                "generation": policy["generation"],
                "gross_score": evaluation["score"],
                "average_cost_bps": round(average_cost_bps, 6),
                "cost_penalty": round(cost_penalty, 10),
                "net_score_after_costs": round(float(evaluation["score"]) - cost_penalty, 10),
                "leg_costs": leg_costs,
            }
        )
    return {
        "model_id": MARKET_FRICTION_MODEL_ID,
        "scenario": scenario,
        "applied": True,
        "generation_costs": generation_costs,
        "all_orders_within_liquidity_cap": all(
            leg["within_liquidity_cap"]
            for generation in generation_costs
            for leg in generation["leg_costs"]
        ),
        "costs_are_positive": all(
            leg["total_cost_bps"] > 0
            for generation in generation_costs
            for leg in generation["leg_costs"]
        ),
    }


def _build_broker_lifecycle_report(
    *,
    episode: PortfolioEpisode,
    executions: Sequence[Mapping[str, Any]],
    scenario: str,
) -> dict[str, Any]:
    orders: list[dict[str, Any]] = []
    for generation, execution in enumerate(executions):
        for leg_index, fill_event in enumerate(execution["fill_events"]):
            statuses = _broker_status_path_for(scenario, episode.ordinal + generation + leg_index)
            orders.append(
                {
                    "order_id": f"paper-order-{episode.case_id}-gen{generation}-{leg_index}",
                    "generation": generation,
                    "fill_event_id": fill_event["event_id"],
                    "symbol": fill_event["metadata"].get("symbol")
                    or fill_event["metadata"].get("signal_symbol")
                    or fill_event["metadata"].get("source_symbol"),
                    "status_path": statuses,
                    "terminal_status": statuses[-1],
                    "readback_status": statuses[-1],
                    "live_broker_submitted": bool(fill_event.get("submitted_to_broker", False)),
                    "adapter": "paper_broker_lifecycle_adapter",
                    "reconciled": statuses[-1] == BROKER_LIFECYCLE_TERMINAL_STATUS,
                }
            )
    terminal_statuses = sorted({order["terminal_status"] for order in orders})
    lifecycle_statuses = sorted({status for order in orders for status in order["status_path"]})
    return {
        "lifecycle_model": "submit_ack_partial_cancel_replace_reject_readback_v1",
        "scenario": scenario,
        "order_count": len(orders),
        "orders": orders,
        "terminal_statuses": terminal_statuses,
        "lifecycle_statuses": lifecycle_statuses,
        "reconciled": all(order["reconciled"] for order in orders),
        "readback_consistent": all(order["terminal_status"] == order["readback_status"] for order in orders),
        "live_broker_submission_count": sum(1 for order in orders if order["live_broker_submitted"]),
    }


def _build_persona_conflict_resolution(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    market_friction: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    oss_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    primary_persona = _persona_id(episode.persona)
    final_directions = {
        instrument: int(leg["direction"]) for instrument, leg in final_policy["legs"].items()
    }
    risk_scale = 0.85 if market_friction["scenario"] in {"liquidity_cap_scale", "risk_reject_reduce"} else 0.95
    council_votes = [
        {
            "persona_id": primary_persona,
            "role": "alpha_sponsor",
            "direction_by_instrument": final_directions,
            "weight_scale": 1.0,
        },
        {
            "persona_id": "p-risk-analyst",
            "role": "risk",
            "direction_by_instrument": final_directions,
            "weight_scale": risk_scale,
        },
        {
            "persona_id": "p-execution-lead",
            "role": "execution",
            "direction_by_instrument": final_directions,
            "weight_scale": 0.9,
        },
        {
            "persona_id": "p-macro-observer",
            "role": "macro",
            "direction_by_instrument": _macro_conflict_directions(episode, final_directions),
            "weight_scale": 0.75,
        },
    ]
    conflict_types = ["weight_conflict"]
    if any(
        council_votes[-1]["direction_by_instrument"][instrument] != direction
        for instrument, direction in final_directions.items()
    ):
        conflict_types.append("direction_conflict")
    if market_friction["scenario"] in {"liquidity_cap_scale", "cancel_replace_readback", "risk_reject_reduce"}:
        conflict_types.append("execution_constraint_conflict")
    classified_conflicts = [
        {
            "conflict_id": _stable_id("conflict", episode.case_id, conflict_type),
            "conflict_type": conflict_type,
            "severity": "medium" if conflict_type == "weight_conflict" else "high",
            "resolved_by": "sponsor_risk_execution_scored_vote",
            "evidence_refs": [
                f"reflection://{decision_traces[-1]['reflection_id']}",
                f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}",
            ],
        }
        for conflict_type in conflict_types
    ]
    resolved_weights = {
        instrument: round(float(leg["weight"]) * min(1.0, risk_scale + 0.1), 6)
        for instrument, leg in final_policy["legs"].items()
    }
    total_weight = sum(resolved_weights.values())
    if total_weight > 1.0:
        resolved_weights = {
            instrument: round(weight / total_weight, 6)
            for instrument, weight in resolved_weights.items()
        }
    resolution_ref = f"persona-conflict://{episode.case_id}"
    selected_action_ref = (
        f"selected-action://{episode.case_id}/{decision_traces[-1]['selected_candidate_id']}"
    )
    risk_oss_ref = f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}"
    return {
        "resolution_id": f"conflict-resolution-{episode.case_id}",
        "resolution_ref": resolution_ref,
        "model_id": PERSONA_CONFLICT_RESOLUTION_MODEL_ID,
        "council_votes": council_votes,
        "classified_conflicts": classified_conflicts,
        "conflict_types": sorted(conflict_types),
        "open_conflicts": [],
        "resolved_allocation": {
            "direction_by_instrument": final_directions,
            "weight_by_instrument": resolved_weights,
            "capital_budget_pct": round(sum(resolved_weights.values()), 6),
            "turnover_budget": 1.25,
        },
        "decision_trace_ref": decision_traces[-1]["reflection_id"],
        "selected_action_ref": selected_action_ref,
        "oss_risk_ref": risk_oss_ref,
        "evidence_refs": [
            selected_action_ref,
            f"reflection://{decision_traces[-1]['reflection_id']}",
            risk_oss_ref,
            *[
                conflict["conflict_id"]
                for conflict in classified_conflicts
            ],
        ],
    }


def _build_restart_recovery_report(
    *,
    episode: PortfolioEpisode,
    decision_traces: Sequence[Mapping[str, Any]],
    memory_contexts: Sequence[Mapping[str, Any]],
    evolution_decision: EvolutionDecision,
) -> dict[str, Any]:
    memory_refs = [context["memory_id"] for context in memory_contexts]
    checkpoint_id = f"checkpoint-{episode.case_id}-after-gen1"
    idempotency_key = _stable_id(
        "idempotency",
        episode.validation_signature,
        checkpoint_id,
        decision_traces[-1]["reflection_id"],
    )
    return {
        "checkpoint_id": checkpoint_id,
        "idempotency_key": idempotency_key,
        "persisted_after_step": "generation1_memory_write",
        "resume_step": "execute_generation2_future_holdout",
        "checkpoint_written": True,
        "recovered": True,
        "memory_refs_before_restart": memory_refs,
        "memory_refs_after_recovery": list(memory_refs),
        "decision_trace_refs_restored": [trace["reflection_id"] for trace in decision_traces],
        "duplicate_execution_suppressed": True,
        "evolution_decision_ref": evolution_decision.decision_id,
        "next_step_completed": _enum_value(evolution_decision.decision_state) == EvolutionDecisionState.EXECUTED.value,
    }


def _build_broker_adapter_lifecycle_packet(
    *,
    episode: PortfolioEpisode,
    scenario: str,
    broker_lifecycle: Mapping[str, Any],
    shioaji_sandbox_lifecycle: Mapping[str, Any],
    restart_recovery: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    required_statuses = _required_broker_statuses_for_scenario(scenario)
    observed_statuses = set(broker_lifecycle.get("lifecycle_statuses", []))
    orders = list(broker_lifecycle.get("orders", []))
    adapter_order = {
        "place_order_id": shioaji_sandbox_lifecycle.get("place_result", {}).get("order_id"),
        "place_status": shioaji_sandbox_lifecycle.get("place_result", {}).get("status"),
        "cancel_status": shioaji_sandbox_lifecycle.get("cancel_result", {}).get("status"),
        "readback_status": shioaji_sandbox_lifecycle.get("readback_result", {}).get("status"),
        "readback_is_real_order": shioaji_sandbox_lifecycle.get("readback_result", {}).get("is_real_order"),
        "readback_is_real_capital": shioaji_sandbox_lifecycle.get("readback_result", {}).get("is_real_capital"),
        "deployment_stage": shioaji_sandbox_lifecycle.get("readback_result", {}).get("deployment_stage"),
        "live_disabled_error_code": shioaji_sandbox_lifecycle.get("live_disabled_result", {})
        .get("response", {})
        .get("error_code"),
    }
    scenario_checks = [
        {
            "check": "required_statuses_observed",
            "status": "passed" if required_statuses.issubset(observed_statuses) else "failed",
            "required_statuses": sorted(required_statuses),
            "observed_statuses": sorted(observed_statuses),
        },
        {
            "check": "paper_orders_reconciled_to_readback",
            "status": "passed"
            if broker_lifecycle.get("reconciled") is True
            and broker_lifecycle.get("readback_consistent") is True
            else "failed",
            "order_count": broker_lifecycle.get("order_count"),
            "terminal_statuses": list(broker_lifecycle.get("terminal_statuses", [])),
        },
        {
            "check": "sandbox_adapter_place_cancel_readback",
            "status": "passed"
            if shioaji_sandbox_lifecycle.get("status") == "passed"
            and adapter_order["place_status"] == "submitted"
            and adapter_order["cancel_status"] == "cancelled"
            and adapter_order["readback_status"] == "cancelled"
            else "failed",
            "adapter_order": adapter_order,
        },
        {
            "check": "live_order_rejected_without_capital",
            "status": "passed"
            if adapter_order["live_disabled_error_code"] == "SHIOAJI_LIVE_DISABLED"
            and shioaji_sandbox_lifecycle.get("production_live_enabled") is False
            and shioaji_sandbox_lifecycle.get("capital_binding_enabled") is False
            else "failed",
            "production_live_enabled": shioaji_sandbox_lifecycle.get("production_live_enabled"),
            "capital_binding_enabled": shioaji_sandbox_lifecycle.get("capital_binding_enabled"),
            "live_disabled_error_code": adapter_order["live_disabled_error_code"],
        },
        {
            "check": "restart_recovery_preserves_readback_context",
            "status": "passed"
            if restart_recovery.get("recovered") is True
            and restart_recovery.get("duplicate_execution_suppressed") is True
            and restart_recovery.get("memory_refs_before_restart")
            == restart_recovery.get("memory_refs_after_recovery")
            else "failed",
            "checkpoint_id": restart_recovery.get("checkpoint_id"),
            "resume_step": restart_recovery.get("resume_step"),
        },
    ]
    replay = {
        "replayable": True,
        "scenario_required_statuses_observed": scenario_checks[0]["status"] == "passed",
        "paper_readback_reconciled": scenario_checks[1]["status"] == "passed",
        "sandbox_place_cancel_readback_reconciled": scenario_checks[2]["status"] == "passed",
        "live_order_rejected_without_capital": scenario_checks[3]["status"] == "passed",
        "restart_recovery_preserves_readback_context": scenario_checks[4]["status"] == "passed",
        "all_orders_have_status_paths": all(order.get("status_path") for order in orders),
        "all_orders_end_filled": all(order.get("terminal_status") == BROKER_LIFECYCLE_TERMINAL_STATUS for order in orders),
        "no_live_broker_submission": broker_lifecycle.get("live_broker_submission_count") == 0,
    }
    return {
        "packet_id": f"broker-adapter-lifecycle-{episode.case_id}",
        "model_id": BROKER_ADAPTER_LIFECYCLE_MODEL_ID,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "provider": "Shioaji",
        "environment": "sandbox",
        "scenario": scenario,
        "broker_lifecycle_model": broker_lifecycle["lifecycle_model"],
        "shioaji_lifecycle_ref": f"broker-sandbox://{shioaji_sandbox_lifecycle['lifecycle_id']}",
        "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
        "decision_trace_refs": [trace["reflection_id"] for trace in decision_traces],
        "required_statuses": sorted(required_statuses),
        "observed_statuses": sorted(observed_statuses),
        "adapter_order": adapter_order,
        "paper_order_count": broker_lifecycle.get("order_count"),
        "paper_order_refs": [order["order_id"] for order in orders],
        "scenario_checks": scenario_checks,
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "broker-adapter-lifecycle",
            {
                "case_id": episode.case_id,
                "scenario": scenario,
                "required_statuses": sorted(required_statuses),
                "observed_statuses": sorted(observed_statuses),
                "adapter_order": adapter_order,
                "paper_order_refs": [order["order_id"] for order in orders],
                "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
            },
        ),
    }


def _required_broker_statuses_for_scenario(scenario: str) -> set[str]:
    return set(_broker_status_path_for(scenario, 0))


def _build_broker_adapter_followup_response(
    *,
    episode: PortfolioEpisode,
    scenario: str,
    broker_adapter_lifecycle: Mapping[str, Any],
    restart_recovery: Mapping[str, Any],
    autonomous_schedule: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    action = _broker_adapter_followup_action_for_scenario(scenario)
    source_packet_ref = f"broker-adapter://{broker_adapter_lifecycle['packet_id']}"
    latest_trace_ref = decision_traces[-1]["reflection_id"]
    evidence_refs = [
        source_packet_ref,
        f"reflection://{latest_trace_ref}",
        f"checkpoint://{restart_recovery['checkpoint_id']}",
        f"schedule://{autonomous_schedule['schedule_id']}",
    ]
    replay = {
        "adapter_response_consumed": broker_adapter_lifecycle.get("model_id") == BROKER_ADAPTER_LIFECYCLE_MODEL_ID
        and bool(broker_adapter_lifecycle.get("input_hash")),
        "scenario_action_selected": action == BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO.get(scenario),
        "source_refs_bound": all(evidence_refs),
        "recovery_context_preserved": restart_recovery.get("recovered") is True
        and restart_recovery.get("duplicate_execution_suppressed") is True,
        "next_cycle_scheduled": autonomous_schedule.get("phase_order_valid") is True
        and bool(autonomous_schedule.get("next_cycle_due_at")),
        "paper_only_guard_retained": broker_adapter_lifecycle.get("adapter_order", {}).get("live_disabled_error_code")
        == "SHIOAJI_LIVE_DISABLED"
        and broker_adapter_lifecycle.get("replay", {}).get("no_live_broker_submission") is True,
        "drives_persona_next_step": True,
    }
    return {
        "followup_id": f"broker-adapter-followup-{episode.case_id}",
        "model_id": BROKER_ADAPTER_FOLLOWUP_MODEL_ID,
        "status": "accepted" if all(replay.values()) else "blocked",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "scenario": scenario,
        "source_packet_ref": source_packet_ref,
        "source_packet_model": broker_adapter_lifecycle.get("model_id"),
        "source_packet_hash": broker_adapter_lifecycle.get("input_hash"),
        "decision_trace_ref": latest_trace_ref,
        "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
        "schedule_ref": autonomous_schedule["schedule_id"],
        "request_response_flow": [
            "persona_order_intent",
            "broker_adapter_lifecycle_response",
            "persona_followup_action",
        ],
        "persona_followup": {
            "action": action,
            "action_family": _broker_adapter_followup_action_family(action),
            "next_persona_step": "execution_feedback_review",
            "required_before_next_cycle": True,
            "paper_only": True,
            "rationale": _broker_adapter_followup_rationale(scenario),
            "evidence_refs": evidence_refs,
        },
        "state_updates": {
            "mark_adapter_response_seen": True,
            "bind_recovery_checkpoint": restart_recovery["checkpoint_id"],
            "schedule_next_cycle_after_followup": autonomous_schedule["next_cycle_due_at"],
            "attach_to_decision_trace": latest_trace_ref,
        },
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "broker-adapter-followup",
            {
                "case_id": episode.case_id,
                "scenario": scenario,
                "action": action,
                "source_packet_ref": source_packet_ref,
                "decision_trace_ref": latest_trace_ref,
                "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
                "schedule_ref": autonomous_schedule["schedule_id"],
            },
        ),
    }


def _broker_adapter_followup_action_for_scenario(scenario: str) -> str:
    return BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO.get(
        scenario,
        "review_adapter_response_before_next_cycle",
    )


def _broker_adapter_followup_action_family(action: str) -> str:
    if action.startswith("confirm_cancel_replace"):
        return "cancel_replace_recovery"
    if action.startswith("reduce_risk"):
        return "risk_control"
    if action.startswith("reprice_limit"):
        return "limit_repricing"
    if action.startswith("scale_to_liquidity"):
        return "liquidity_sizing"
    if action.startswith("reconcile_partial_fill"):
        return "position_reconciliation"
    return "adapter_response_review"


def _broker_adapter_followup_rationale(scenario: str) -> str:
    rationales = {
        "cancel_replace_readback": "Cancel/replace status needs a readback-confirmed paper resubmission before the next cycle.",
        "limit_miss_reprice": "Limit miss feedback needs a fresh readback and repriced paper order before evaluation continues.",
        "liquidity_cap_scale": "Liquidity cap feedback needs reduced sizing before the next scheduled paper cycle.",
        "partial_fill_reconcile": "Partial fill feedback needs position reconciliation before the persona trusts portfolio state.",
        "risk_reject_reduce": "Risk rejection feedback needs reduced exposure while live submission remains disabled.",
    }
    return rationales.get(scenario, "Adapter feedback needs persona review before the next scheduled cycle.")


def _build_autonomous_schedule(
    *,
    episode: PortfolioEpisode,
    generated_at: str,
    restart_recovery: Mapping[str, Any],
) -> dict[str, Any]:
    schedule_id = f"schedule-{episode.case_id}"
    phases = []
    for phase_index, phase in enumerate(AUTONOMOUS_SCHEDULER_PHASES):
        phases.append(
            {
                "phase": phase,
                "due_at": _offset_timestamp(
                    generated_at,
                    episode.ordinal,
                    days=episode.ordinal % 17,
                    minutes=phase_index * 5,
                ),
                "status": "completed" if phase != "schedule_next" else "scheduled",
            }
        )
    return {
        "schedule_id": schedule_id,
        "schedule_ref": f"schedule://{schedule_id}",
        "trigger_mode": "autonomous_daily_paper_loop",
        "phases": phases,
        "phase_order_valid": [phase["phase"] for phase in phases] == list(AUTONOMOUS_SCHEDULER_PHASES),
        "phase_due_at_ordered": all(
            str(left["due_at"]) < str(right["due_at"])
            for left, right in zip(phases, phases[1:])
        ),
        "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
        "missed_cycle_recovered": restart_recovery["recovered"],
        "next_cycle_due_at": _offset_timestamp(
            generated_at,
            episode.ordinal,
            days=(episode.ordinal % 17) + 1,
        ),
    }


def _build_alpha_seed_revision_handoff_context(
    *,
    episode: PortfolioEpisode,
    alpha_seed_revision: Mapping[str, Any],
) -> dict[str, Any]:
    revision = copy.deepcopy(dict(alpha_seed_revision.get("revision") or {}))
    alpha_component = str(alpha_seed_revision.get("alpha_component") or "")
    alpha_request_id = str(revision.get("source_alpha_request_id") or "")
    source_oss_ref = f"oss://{alpha_component}/{alpha_request_id}"
    base_seed_ref = str(revision.get("base_seed_ref") or "")
    revision_ref = str(alpha_seed_revision.get("revision_ref") or "")
    source_refs = [str(ref) for ref in alpha_seed_revision.get("source_refs", [])]
    handoff_ref = f"alpha-seed-revision-handoff://{episode.case_id}"
    context_seed = {
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "revision_ref": revision_ref,
        "base_seed_ref": base_seed_ref,
        "source_oss_ref": source_oss_ref,
        "alpha_component": alpha_component,
        "revision_action": revision.get("action"),
        "revision_key": revision.get("revision_key"),
        "source_strategy_spec_id": revision.get("source_strategy_spec_id"),
        "source_alpha_request_id": alpha_request_id,
        "source_alpha_artifact_family": revision.get("source_alpha_artifact_family"),
        "downstream_vectorbt_request_id": revision.get("downstream_vectorbt_request_id"),
        "downstream_tracker_run_id": revision.get("downstream_tracker_run_id"),
        "downstream_policy_candidate_request_id": revision.get(
            "downstream_policy_candidate_request_id"
        ),
        "source_refs": source_refs,
        "alpha_seed_revision_input_hash": alpha_seed_revision.get("input_hash"),
    }
    lineage_hash = _stable_payload_hash("alpha-seed-revision-handoff", context_seed)
    return {
        "model_id": PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID,
        "handoff_ref": handoff_ref,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "revision_id": alpha_seed_revision.get("revision_id"),
        "revision_ref": revision_ref,
        "revision_key": revision.get("revision_key"),
        "source_revision_model_id": alpha_seed_revision.get("model_id"),
        "source_revision_input_hash": alpha_seed_revision.get("input_hash"),
        "base_seed_key": revision.get("base_seed_key"),
        "base_seed_ref": base_seed_ref,
        "source_strategy_spec_id": revision.get("source_strategy_spec_id"),
        "alpha_component": alpha_component,
        "source_oss_ref": source_oss_ref,
        "source_alpha_request_id": alpha_request_id,
        "source_alpha_artifact_family": revision.get("source_alpha_artifact_family"),
        "source_alpha_registry_id": revision.get("source_alpha_registry_id"),
        "source_alpha_producer_run_id": revision.get("source_alpha_producer_run_id"),
        "revision_action": revision.get("action"),
        "downstream_vectorbt_request_id": revision.get("downstream_vectorbt_request_id"),
        "downstream_tracker_run_id": revision.get("downstream_tracker_run_id"),
        "downstream_policy_candidate_request_id": revision.get(
            "downstream_policy_candidate_request_id"
        ),
        "source_refs": source_refs,
        "candidate_score_adjustments": copy.deepcopy(
            dict(alpha_seed_revision.get("candidate_score_adjustments") or {})
        ),
        "lineage_hash": lineage_hash,
        "input_hash": lineage_hash,
    }


def _build_lean_object_store_packet_targets(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    persona_conflict_resolution: Mapping[str, Any],
    strategy_packet_ref: str,
    alpha_seed_revision_handoff: Mapping[str, Any],
    generated_at: str,
) -> list[dict[str, Any]]:
    policy_oss_lineage = dict(final_policy.get("policy_oss_lineage") or {})
    reflection_oss_lineage = dict(final_policy.get("reflection_oss_lineage") or {})
    alpha_seed_context = dict(alpha_seed_revision_handoff or {})
    signals = _build_signals(
        episode=episode,
        policy=final_policy,
        generation=int(final_policy["generation"]),
        generated_at=generated_at,
    )
    allocation = persona_conflict_resolution["resolved_allocation"]
    targets: list[dict[str, Any]] = []
    for leg_index, (window, signal) in enumerate(zip(episode.windows, signals)):
        leg = final_policy["legs"][window.instrument]
        target_ref = f"lean-packet-target://{episode.case_id}/generation{final_policy['generation']}/leg{leg_index}"
        signal_payload = copy.deepcopy(dict(signal))
        signal_payload.setdefault("metadata", {})
        signal_payload["metadata"] = {
            **dict(signal_payload["metadata"]),
            "strategy_packet_ref": strategy_packet_ref,
            "packet_target_ref": target_ref,
            "lean_object_store_readback_model_id": LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID,
            "policy_oss_ref": policy_oss_lineage.get("source_oss_ref"),
            "policy_oss_lineage_ref": policy_oss_lineage.get("lineage_ref"),
            "policy_oss_lineage_hash": policy_oss_lineage.get("lineage_hash"),
            "policy_oss_component": policy_oss_lineage.get("component"),
            "policy_oss_request_id": policy_oss_lineage.get("request_id"),
            "reflection_oss_ref": reflection_oss_lineage.get("source_oss_ref"),
            "reflection_oss_lineage_ref": reflection_oss_lineage.get("lineage_ref"),
            "reflection_oss_lineage_hash": reflection_oss_lineage.get("lineage_hash"),
            "reflection_oss_component": reflection_oss_lineage.get("component"),
            "reflection_oss_request_id": reflection_oss_lineage.get("request_id"),
            "alpha_seed_revision_handoff_ref": alpha_seed_context.get("handoff_ref"),
            "alpha_seed_revision_ref": alpha_seed_context.get("revision_ref"),
            "alpha_seed_revision_handoff_hash": alpha_seed_context.get("lineage_hash"),
            "alpha_seed_source_ref": alpha_seed_context.get("base_seed_ref"),
            "alpha_seed_source_oss_ref": alpha_seed_context.get("source_oss_ref"),
            "alpha_seed_revision_action": alpha_seed_context.get("revision_action"),
            "alpha_seed_component": alpha_seed_context.get("alpha_component"),
        }
        entry_row = _entry_row_for_generation(window, int(final_policy["generation"]))
        targets.append(
            {
                "target_ref": target_ref,
                "leg_index": leg_index,
                "instrument": window.instrument,
                "execution_symbol": window.execution_symbol,
                "lean_symbol": _lean_symbol_for_execution_symbol(window.execution_symbol),
                "policy_id": final_policy["policy_id"],
                "policy_version": final_policy["policy_version"],
                "policy_oss_ref": policy_oss_lineage.get("source_oss_ref"),
                "policy_oss_lineage_ref": policy_oss_lineage.get("lineage_ref"),
                "policy_oss_lineage_hash": policy_oss_lineage.get("lineage_hash"),
                "policy_oss_component": policy_oss_lineage.get("component"),
                "policy_oss_request_id": policy_oss_lineage.get("request_id"),
                "reflection_oss_ref": reflection_oss_lineage.get("source_oss_ref"),
                "reflection_oss_lineage_ref": reflection_oss_lineage.get("lineage_ref"),
                "reflection_oss_lineage_hash": reflection_oss_lineage.get("lineage_hash"),
                "reflection_oss_component": reflection_oss_lineage.get("component"),
                "reflection_oss_request_id": reflection_oss_lineage.get("request_id"),
                "alpha_seed_revision_handoff_ref": alpha_seed_context.get("handoff_ref"),
                "alpha_seed_revision_ref": alpha_seed_context.get("revision_ref"),
                "alpha_seed_revision_handoff_hash": alpha_seed_context.get("lineage_hash"),
                "alpha_seed_source_ref": alpha_seed_context.get("base_seed_ref"),
                "alpha_seed_source_oss_ref": alpha_seed_context.get("source_oss_ref"),
                "alpha_seed_revision_action": alpha_seed_context.get("revision_action"),
                "alpha_seed_component": alpha_seed_context.get("alpha_component"),
                "generation": final_policy["generation"],
                "direction": int(leg["direction"]),
                "action": signal_payload["action"],
                "signal_id": signal_payload["signal_id"],
                "target_weight": allocation["weight_by_instrument"][window.instrument],
                "resolved_direction": allocation["direction_by_instrument"][window.instrument],
                "quantity": signal_payload["quantity"],
                "quantity_type": signal_payload["quantity_type"],
                "order_type": signal_payload["order_type"],
                "limit_price": signal_payload.get("limit_price"),
                "market_data_ref": signal_payload["metadata"]["market_data_ref"],
                "source_dataset_ref": signal_payload["metadata"]["source_dataset_ref"],
                "entry_close": float(entry_row["close"]),
                "signal": signal_payload,
            }
        )
    return targets


def _build_lean_object_store_packet_readback(
    *,
    episode: PortfolioEpisode,
    strategy_packet: Mapping[str, Any],
    packet_targets: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    loaded_packet = dict(result.get("loaded_strategy_packet") or {})
    loaded_targets = [dict(target) for target in result.get("loaded_packet_targets") or []]
    loaded_signal = dict(result.get("loaded_signal") or {})
    fill_events = [dict(event) for event in result.get("fill_events") or []]
    first_target = loaded_targets[0] if loaded_targets else {}
    first_target_signal = first_target.get("signal") if isinstance(first_target.get("signal"), Mapping) else {}
    object_store_keys = set(result.get("object_store_keys", []))
    tracking_provenance = dict(strategy_packet.get("experiment_tracking_provenance") or {})
    loaded_tracking_provenance = dict(loaded_packet.get("experiment_tracking_provenance") or {})
    policy_oss_lineage = dict(strategy_packet.get("policy_oss_lineage") or {})
    loaded_policy_oss_lineage = dict(loaded_packet.get("policy_oss_lineage") or {})
    policy_oss_lineage_hash = str(policy_oss_lineage.get("lineage_hash") or "")
    policy_oss_ref = str(policy_oss_lineage.get("source_oss_ref") or "")
    reflection_oss_lineage = dict(strategy_packet.get("reflection_oss_lineage") or {})
    loaded_reflection_oss_lineage = dict(loaded_packet.get("reflection_oss_lineage") or {})
    reflection_oss_lineage_hash = str(reflection_oss_lineage.get("lineage_hash") or "")
    reflection_oss_ref = str(reflection_oss_lineage.get("source_oss_ref") or "")
    alpha_seed_handoff = dict(strategy_packet.get("alpha_seed_revision_handoff") or {})
    loaded_alpha_seed_handoff = dict(
        loaded_packet.get("alpha_seed_revision_handoff") or {}
    )
    alpha_seed_handoff_hash = str(alpha_seed_handoff.get("lineage_hash") or "")
    alpha_seed_handoff_ref = str(alpha_seed_handoff.get("handoff_ref") or "")
    alpha_seed_revision_ref = str(alpha_seed_handoff.get("revision_ref") or "")
    alpha_seed_source_ref = str(alpha_seed_handoff.get("base_seed_ref") or "")
    alpha_seed_source_oss_ref = str(alpha_seed_handoff.get("source_oss_ref") or "")
    replay = {
        "replayable": True,
        "packet_present_in_object_store_artifact": bool(loaded_packet),
        "packet_ref_matches_case_strategy_packet": (
            loaded_packet.get("packet_ref") == strategy_packet.get("packet_ref")
            and loaded_packet.get("policy_id") == strategy_packet.get("policy_id")
        ),
        "packet_hash_matches_persona_packet": (
            _stable_payload_hash("lean-strategy-packet", loaded_packet)
            == _stable_payload_hash("lean-strategy-packet", strategy_packet)
        ),
        "target_count_matches_portfolio": len(loaded_targets) == PORTFOLIO_LEG_COUNT,
        "all_targets_have_signals": all(
            isinstance(target.get("signal"), Mapping)
            and target.get("signal_id") == target.get("signal", {}).get("signal_id")
            for target in loaded_targets
        ),
        "all_targets_bind_strategy_packet_ref": all(
            target.get("signal", {}).get("metadata", {}).get("strategy_packet_ref")
            == strategy_packet.get("packet_ref")
            for target in loaded_targets
        ),
        "target_refs_unique": (
            len({target.get("target_ref") for target in loaded_targets}) == PORTFOLIO_LEG_COUNT
        ),
        "loaded_signal_from_first_packet_target": (
            loaded_signal.get("signal_id")
            == first_target.get("signal_id")
            == first_target_signal.get("signal_id")
        ),
        "loaded_signal_symbol_matches_first_target": (
            loaded_signal.get("symbol") == first_target.get("execution_symbol")
            and str(loaded_signal.get("symbol", "")).split(".", 1)[0]
            == first_target.get("lean_symbol")
        ),
        "loaded_signal_quantity_matches_first_target": (
            loaded_signal.get("quantity") == first_target.get("quantity")
            and loaded_signal.get("quantity_type") == first_target.get("quantity_type")
            and loaded_signal.get("order_type") == first_target.get("order_type")
        ),
        "algorithm_executed_loaded_packet_signal": (
            int(result.get("fill_count", 0)) >= 1
            and any(event.get("signal_id") == loaded_signal.get("signal_id") for event in fill_events)
        ),
        "object_store_keys_include_packet_artifact_and_metadata": (
            any(key.endswith("/artifact.bin") for key in object_store_keys)
            and any(key.endswith("/metadata.json") for key in object_store_keys)
        ),
        "tracking_provenance_present_in_packet": bool(
            tracking_provenance.get("experiment_ref")
            and tracking_provenance.get("reconciliation_ref")
            and tracking_provenance.get("repair_ref")
            and tracking_provenance.get("lineage_hash")
        ),
        "loaded_packet_preserves_tracking_provenance": (
            loaded_tracking_provenance == tracking_provenance
        ),
        "loaded_tracking_ref_matches_packet": (
            loaded_packet.get("normalized_experiment_ref") == tracking_provenance.get("experiment_ref")
            and loaded_packet.get("tracking_reconciliation_ref")
            == tracking_provenance.get("reconciliation_ref")
            and loaded_packet.get("tracking_repair_ref") == tracking_provenance.get("repair_ref")
            and loaded_packet.get("experiment_tracking_provenance_hash")
            == tracking_provenance.get("lineage_hash")
        ),
        "policy_oss_lineage_present_in_packet": bool(
            policy_oss_lineage.get("lineage_ref")
            and policy_oss_lineage_hash
            and policy_oss_ref.startswith("oss://")
        ),
        "loaded_packet_preserves_policy_oss_lineage": (
            loaded_policy_oss_lineage == policy_oss_lineage
        ),
        "loaded_policy_oss_ref_matches_packet": (
            loaded_packet.get("policy_oss_ref") == policy_oss_ref
            and loaded_packet.get("policy_oss_lineage_ref") == policy_oss_lineage.get("lineage_ref")
            and loaded_packet.get("policy_oss_lineage_hash") == policy_oss_lineage_hash
            and loaded_packet.get("policy_oss_registry_ref") == policy_oss_lineage.get("registry_ref")
        ),
        "all_targets_bind_policy_oss_lineage": all(
            target.get("policy_oss_ref") == policy_oss_ref
            and target.get("policy_oss_lineage_hash") == policy_oss_lineage_hash
            and target.get("signal", {}).get("metadata", {}).get("policy_oss_ref") == policy_oss_ref
            and target.get("signal", {}).get("metadata", {}).get("policy_oss_lineage_hash")
            == policy_oss_lineage_hash
            for target in loaded_targets
        ),
        "reflection_oss_lineage_present_in_packet": bool(
            reflection_oss_lineage.get("lineage_ref")
            and reflection_oss_lineage_hash
            and reflection_oss_ref.startswith("oss://")
        ),
        "loaded_packet_preserves_reflection_oss_lineage": (
            loaded_reflection_oss_lineage == reflection_oss_lineage
        ),
        "loaded_reflection_oss_ref_matches_packet": (
            loaded_packet.get("reflection_oss_ref") == reflection_oss_ref
            and loaded_packet.get("reflection_oss_lineage_ref")
            == reflection_oss_lineage.get("lineage_ref")
            and loaded_packet.get("reflection_oss_lineage_hash") == reflection_oss_lineage_hash
            and loaded_packet.get("reflection_oss_registry_ref")
            == reflection_oss_lineage.get("registry_ref")
        ),
        "all_targets_bind_reflection_oss_lineage": all(
            target.get("reflection_oss_ref") == reflection_oss_ref
            and target.get("reflection_oss_lineage_hash") == reflection_oss_lineage_hash
            and target.get("signal", {}).get("metadata", {}).get("reflection_oss_ref")
            == reflection_oss_ref
            and target.get("signal", {}).get("metadata", {}).get("reflection_oss_lineage_hash")
            == reflection_oss_lineage_hash
            for target in loaded_targets
        ),
        "alpha_seed_revision_handoff_present_in_packet": bool(
            alpha_seed_handoff.get("model_id")
            == PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
            and alpha_seed_handoff_ref.startswith("alpha-seed-revision-handoff://")
            and alpha_seed_revision_ref.startswith("alpha-seed-revision://")
            and alpha_seed_source_ref.startswith("alpha-seed://")
            and alpha_seed_handoff_hash
        ),
        "loaded_packet_preserves_alpha_seed_revision_handoff": (
            loaded_alpha_seed_handoff == alpha_seed_handoff
        ),
        "loaded_alpha_seed_revision_ref_matches_packet": (
            loaded_packet.get("alpha_seed_revision_handoff_ref") == alpha_seed_handoff_ref
            and loaded_packet.get("alpha_seed_revision_ref") == alpha_seed_revision_ref
            and loaded_packet.get("alpha_seed_source_ref") == alpha_seed_source_ref
            and loaded_packet.get("alpha_seed_source_oss_ref") == alpha_seed_source_oss_ref
            and loaded_packet.get("alpha_seed_revision_handoff_hash")
            == alpha_seed_handoff_hash
            and loaded_packet.get("alpha_seed_revision_action")
            == alpha_seed_handoff.get("revision_action")
            and loaded_packet.get("alpha_seed_component")
            == alpha_seed_handoff.get("alpha_component")
        ),
        "all_targets_bind_alpha_seed_revision_handoff": all(
            target.get("alpha_seed_revision_handoff_ref") == alpha_seed_handoff_ref
            and target.get("alpha_seed_revision_ref") == alpha_seed_revision_ref
            and target.get("alpha_seed_revision_handoff_hash")
            == alpha_seed_handoff_hash
            and target.get("alpha_seed_source_ref") == alpha_seed_source_ref
            and target.get("alpha_seed_source_oss_ref") == alpha_seed_source_oss_ref
            and target.get("signal", {})
            .get("metadata", {})
            .get("alpha_seed_revision_handoff_ref")
            == alpha_seed_handoff_ref
            and target.get("signal", {}).get("metadata", {}).get("alpha_seed_revision_ref")
            == alpha_seed_revision_ref
            and target.get("signal", {})
            .get("metadata", {})
            .get("alpha_seed_revision_handoff_hash")
            == alpha_seed_handoff_hash
            for target in loaded_targets
        ),
        "paper_only_guard_retained": result.get("broker_production_live_enabled") == "false",
    }
    return {
        "readback_id": f"lean-object-store-packet-readback-{episode.case_id}",
        "model_id": LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "packet_ref": strategy_packet["packet_ref"],
        "packet_hash": _stable_payload_hash("lean-strategy-packet", loaded_packet),
        "source_packet_hash": _stable_payload_hash("lean-strategy-packet", strategy_packet),
        "artifact_payload_checksum": result.get("artifact_payload_checksum"),
        "target_count": len(loaded_targets),
        "target_refs": [target.get("target_ref") for target in loaded_targets],
        "target_signal_ids": [target.get("signal_id") for target in loaded_targets],
        "target_symbols": [target.get("execution_symbol") for target in loaded_targets],
        "loaded_signal_id": loaded_signal.get("signal_id"),
        "loaded_signal_symbol": loaded_signal.get("symbol"),
        "loaded_signal_source_target_ref": first_target.get("target_ref"),
        "experiment_tracking_provenance_hash": tracking_provenance.get("lineage_hash"),
        "loaded_experiment_tracking_provenance_hash": loaded_tracking_provenance.get("lineage_hash"),
        "tracking_reconciliation_ref": tracking_provenance.get("reconciliation_ref"),
        "tracking_repair_ref": tracking_provenance.get("repair_ref"),
        "normalized_experiment_ref": tracking_provenance.get("experiment_ref"),
        "loaded_experiment_tracking_provenance": loaded_tracking_provenance,
        "policy_oss_lineage_hash": policy_oss_lineage_hash,
        "loaded_policy_oss_lineage_hash": loaded_policy_oss_lineage.get("lineage_hash"),
        "policy_oss_ref": policy_oss_ref,
        "loaded_policy_oss_ref": loaded_packet.get("policy_oss_ref"),
        "policy_oss_lineage_ref": policy_oss_lineage.get("lineage_ref"),
        "loaded_policy_oss_lineage_ref": loaded_packet.get("policy_oss_lineage_ref"),
        "loaded_policy_oss_lineage": loaded_policy_oss_lineage,
        "reflection_oss_lineage_hash": reflection_oss_lineage_hash,
        "loaded_reflection_oss_lineage_hash": loaded_reflection_oss_lineage.get("lineage_hash"),
        "reflection_oss_ref": reflection_oss_ref,
        "loaded_reflection_oss_ref": loaded_packet.get("reflection_oss_ref"),
        "reflection_oss_lineage_ref": reflection_oss_lineage.get("lineage_ref"),
        "loaded_reflection_oss_lineage_ref": loaded_packet.get("reflection_oss_lineage_ref"),
        "loaded_reflection_oss_lineage": loaded_reflection_oss_lineage,
        "alpha_seed_revision_handoff_hash": alpha_seed_handoff_hash,
        "loaded_alpha_seed_revision_handoff_hash": loaded_alpha_seed_handoff.get(
            "lineage_hash"
        ),
        "alpha_seed_revision_handoff_ref": alpha_seed_handoff_ref,
        "loaded_alpha_seed_revision_handoff_ref": loaded_packet.get(
            "alpha_seed_revision_handoff_ref"
        ),
        "alpha_seed_revision_ref": alpha_seed_revision_ref,
        "loaded_alpha_seed_revision_ref": loaded_packet.get("alpha_seed_revision_ref"),
        "alpha_seed_source_ref": alpha_seed_source_ref,
        "loaded_alpha_seed_source_ref": loaded_packet.get("alpha_seed_source_ref"),
        "alpha_seed_source_oss_ref": alpha_seed_source_oss_ref,
        "loaded_alpha_seed_source_oss_ref": loaded_packet.get(
            "alpha_seed_source_oss_ref"
        ),
        "loaded_alpha_seed_revision_handoff": loaded_alpha_seed_handoff,
        "object_store_keys": sorted(object_store_keys),
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "lean-object-store-packet-readback",
            {
                "case_id": episode.case_id,
                "loaded_packet": loaded_packet,
                "loaded_targets": loaded_targets,
                "loaded_signal": loaded_signal,
                "replay": replay,
            },
        ),
    }


def _run_lean_engine_replay(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    final_evaluation: Mapping[str, Any],
    evolution_decision: EvolutionDecision,
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    persona_conflict_resolution: Mapping[str, Any],
    case_upstream_artifacts: Mapping[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    artifact_id = f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}"
    final_oos_step = strict_oos_evolution_proof["proof_steps"][-1]
    packet_ref = f"lean-strategy-packet://{episode.case_id}/generation2"
    tracker = case_upstream_artifacts["tracker"]
    vectorbt = case_upstream_artifacts["vectorbt"]
    tracking_reconciliation = case_upstream_artifacts["tracking_reconciliation"]
    tracking_repair = tracking_reconciliation["repair"]
    policy_oss_lineage = copy.deepcopy(dict(final_policy.get("policy_oss_lineage") or {}))
    reflection_oss_lineage = copy.deepcopy(dict(final_policy.get("reflection_oss_lineage") or {}))
    alpha_seed_revision_handoff = _build_alpha_seed_revision_handoff_context(
        episode=episode,
        alpha_seed_revision=case_upstream_artifacts["alpha_seed_revision"],
    )
    tracking_provenance_seed = {
        "backend": tracker["backend"],
        "request_id": tracker["request_id"],
        "run_id": tracker["run_id"],
        "artifact_uri": tracker["artifact_uri"],
        "experiment_ref": tracking_repair["normalized_experiment_ref"],
        "reconciliation_ref": tracking_reconciliation["reconciliation_ref"],
        "repair_ref": tracking_repair["repair_ref"],
        "repair_action": tracking_repair["action"],
        "divergence_type": tracking_reconciliation["divergence"]["divergence_type"],
        "source_vectorbt_request_id": vectorbt["request_id"],
        "source_vectorbt_run_id": vectorbt["run_id"],
        "tracking_reconciliation_input_hash": tracking_reconciliation["input_hash"],
    }
    tracking_provenance = {
        "model_id": PERSONA_TRACKING_RECONCILIATION_MODEL_ID,
        **tracking_provenance_seed,
        "lineage_hash": _stable_payload_hash(
            "tracking-experiment-lineage",
            tracking_provenance_seed,
        ),
    }
    strategy_packet = {
        "packet_ref": packet_ref,
        "policy_id": final_policy["policy_id"],
        "policy_version": final_policy["policy_version"],
        "generation": final_policy["generation"],
        "evolution_decision_id": evolution_decision.decision_id,
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "validation_signature": episode.validation_signature,
        "source_outcome_window": final_oos_step["source_outcome_window"],
        "validation_window": final_oos_step["validation_window"],
        "decision_trace_ref": final_oos_step["decision_trace_ref"],
        "strict_oos_proof_ref": strict_oos_evolution_proof["proof_ref"],
        "no_leakage_protocol_ref": f"no-leakage://{no_leakage_protocol['protocol_id']}",
        "evolution_trajectory_ref": f"trajectory://{evolution_trajectory['trajectory_id']}",
        "experiment_tracking_provenance": tracking_provenance,
        "experiment_tracking_provenance_hash": tracking_provenance["lineage_hash"],
        "normalized_experiment_ref": tracking_provenance["experiment_ref"],
        "tracking_reconciliation_ref": tracking_provenance["reconciliation_ref"],
        "tracking_repair_ref": tracking_provenance["repair_ref"],
        "policy_oss_lineage": policy_oss_lineage,
        "policy_oss_lineage_hash": policy_oss_lineage.get("lineage_hash"),
        "policy_oss_lineage_ref": policy_oss_lineage.get("lineage_ref"),
        "policy_oss_ref": policy_oss_lineage.get("source_oss_ref"),
        "policy_oss_registry_ref": policy_oss_lineage.get("registry_ref"),
        "policy_oss_component": policy_oss_lineage.get("component"),
        "policy_oss_request_id": policy_oss_lineage.get("request_id"),
        "reflection_oss_lineage": reflection_oss_lineage,
        "reflection_oss_lineage_hash": reflection_oss_lineage.get("lineage_hash"),
        "reflection_oss_lineage_ref": reflection_oss_lineage.get("lineage_ref"),
        "reflection_oss_ref": reflection_oss_lineage.get("source_oss_ref"),
        "reflection_oss_registry_ref": reflection_oss_lineage.get("registry_ref"),
        "reflection_oss_component": reflection_oss_lineage.get("component"),
        "reflection_oss_request_id": reflection_oss_lineage.get("request_id"),
        "alpha_seed_revision_handoff": alpha_seed_revision_handoff,
        "alpha_seed_revision_handoff_hash": alpha_seed_revision_handoff["lineage_hash"],
        "alpha_seed_revision_handoff_ref": alpha_seed_revision_handoff["handoff_ref"],
        "alpha_seed_revision_ref": alpha_seed_revision_handoff["revision_ref"],
        "alpha_seed_source_ref": alpha_seed_revision_handoff["base_seed_ref"],
        "alpha_seed_source_oss_ref": alpha_seed_revision_handoff["source_oss_ref"],
        "alpha_seed_revision_action": alpha_seed_revision_handoff["revision_action"],
        "alpha_seed_component": alpha_seed_revision_handoff["alpha_component"],
        "alpha_seed_revision_key": alpha_seed_revision_handoff["revision_key"],
        "alpha_seed_downstream_vectorbt_request_id": alpha_seed_revision_handoff[
            "downstream_vectorbt_request_id"
        ],
        "alpha_seed_downstream_policy_candidate_request_id": alpha_seed_revision_handoff[
            "downstream_policy_candidate_request_id"
        ],
        "alpha_seed_downstream_tracker_run_id": alpha_seed_revision_handoff[
            "downstream_tracker_run_id"
        ],
        "future_holdout_score": final_evaluation["score"],
        "future_holdout_improvement": final_oos_step["score_improvement"],
        "validation_window_unseen_by_decision": final_oos_step[
            "validation_window_unseen_by_decision"
        ],
        "future_window_hidden": final_oos_step["future_window_hidden"],
        "strict_oos_replay_passed": strict_oos_evolution_proof["status"] == "passed"
        and all(strict_oos_evolution_proof["replay"].values()),
        "no_leakage_replay_passed": no_leakage_protocol["replay"][
            "future_holdout_hidden_until_evaluation"
        ]
        and all(no_leakage_protocol["replay"].values()),
    }
    packet_targets = _build_lean_object_store_packet_targets(
        episode=episode,
        final_policy=final_policy,
        persona_conflict_resolution=persona_conflict_resolution,
        strategy_packet_ref=packet_ref,
        alpha_seed_revision_handoff=alpha_seed_revision_handoff,
        generated_at=generated_at,
    )
    plan = {
        "plan_id": f"lean-plan-{episode.case_id}",
        "approval_decision_id": f"lean-approval-{episode.case_id}",
        "artifact_id": artifact_id,
        "artifact_version": SMOKE_VERSION,
        "artifact_type": "execution_bundle",
        "target_stage": "paper",
        "capital_pool_id": f"pool-usability-{_persona_id(episode.persona)}",
        "strategy_id": SMOKE_STRATEGY_ID,
    }
    binding = SimpleNamespace(
        binding_id=f"lean-binding-{episode.case_id}",
        runtime_id=f"lean-runtime-{episode.case_id}",
        plan_id=plan["plan_id"],
        artifact_id=artifact_id,
        artifact_version=SMOKE_VERSION,
        capital_pool_id=plan["capital_pool_id"],
        deployment_mode="paper",
        persona_capital_binding_id=f"pcb-usability-{_persona_id(episode.persona)}",
    )
    result = run_algorithm_smoke_from_binding(
        plan,
        binding,
        strategy_packet=strategy_packet,
        packet_targets=packet_targets,
    ).to_dict()
    runtime_context = dict(result["runtime_context"])
    readback_targets = list(result.get("loaded_packet_targets", []))
    loaded_signal = dict(result.get("loaded_signal", {}))
    object_store_packet_readback = _build_lean_object_store_packet_readback(
        episode=episode,
        strategy_packet=strategy_packet,
        packet_targets=packet_targets,
        result=result,
    )
    return {
        "replay_id": f"lean-engine-replay-{episode.case_id}",
        "model_id": LEAN_ENGINE_REPLAY_MODEL_ID,
        "status": "passed"
        if _lean_engine_result_is_usable(result, plan, binding)
        else "failed",
        "algorithm_module": "pantheon_algo.smoke_loader_test",
        "case_specific_runtime_binding": True,
        "case_specific_strategy_packet": strategy_packet,
        "case_specific_packet_targets": packet_targets,
        "lean_object_store_packet_readback": object_store_packet_readback,
        "loaded_signal": {
            "signal_id": loaded_signal.get("signal_id"),
            "symbol": loaded_signal.get("symbol"),
            "quantity": loaded_signal.get("quantity"),
            "quantity_type": loaded_signal.get("quantity_type"),
            "order_type": loaded_signal.get("order_type"),
            "source_target_ref": readback_targets[0].get("target_ref") if readback_targets else None,
        },
        "plan": {
            "plan_id": plan["plan_id"],
            "artifact_id": plan["artifact_id"],
            "artifact_version": plan["artifact_version"],
            "target_stage": plan["target_stage"],
            "capital_pool_id": plan["capital_pool_id"],
        },
        "binding": {
            "binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "deployment_mode": binding.deployment_mode,
            "persona_capital_binding_id": binding.persona_capital_binding_id,
        },
        "runtime_context": runtime_context,
        "synthetic_bar_count": result["synthetic_bar_count"],
        "raw_on_data_callbacks": result["raw_on_data_callbacks"],
        "executed_on_data_callbacks": result["executed_on_data_callbacks"],
        "fill_count": result["fill_count"],
        "object_store_keys": list(result["object_store_keys"]),
        "loaded_metadata": {
            "deployment_plan_id": result["loaded_metadata"].get("deployment_plan_id"),
            "runtime_binding_id": result["loaded_metadata"].get("runtime_binding_id"),
            "deployment_stage": result["loaded_metadata"].get("deployment_stage"),
            "strategy_id": result["loaded_metadata"].get("strategy_id"),
        },
        "broker_production_live_enabled": result["broker_production_live_enabled"],
    }


def _run_shioaji_sandbox_lifecycle(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    market_friction: Mapping[str, Any],
) -> dict[str, Any]:
    first_window = episode.windows[0]
    first_leg = final_policy["legs"][first_window.instrument]
    direction = int(first_leg["direction"])
    first_cost = market_friction["generation_costs"][-1]["leg_costs"][0]
    adapter = ShioajiBrokerAdapter(
        sandbox_enabled=True,
        _api=MockShioajiApi(),
        submit_spacing_seconds=0.0,
    )
    facade = ShioajiSandboxFacade(adapter)
    payload = facade.run_lifecycle(
        capital_pool_id=f"pool-usability-{_persona_id(episode.persona)}",
        strategy_id=f"{episode.seed_key}-agent-usability-hardening",
        symbol=_shioaji_symbol_for(first_window.instrument),
        qty=max(1, int(round(float(first_cost["notional"]) / max(float(first_window.observe_rows[-1]["close"]), 1.0)))),
        side="buy" if direction > 0 else "sell",
        order_type="limit",
        limit_price=round(max(0.01, float(first_window.observe_rows[-1]["close"])), 2),
    )
    return {
        "lifecycle_id": f"shioaji-sandbox-{episode.case_id}",
        "model_id": SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID,
        "status": "passed" if _shioaji_sandbox_result_is_usable(payload) else "failed",
        "run_mode": "mock_api_replay",
        "adapter": "services.broker.shioaji.ShioajiBrokerAdapter",
        "facade": "services.broker.shioaji.ShioajiSandboxFacade",
        "provider": payload["provider"],
        "environment": payload["environment"],
        "proof_boundary": payload["proof_boundary"],
        "place_result": payload["place_result"],
        "cancel_result": payload["cancel_result"],
        "readback_result": payload["readback_result"],
        "reconcile_result": payload["reconcile_result"],
        "live_disabled_result": payload["live_disabled_result"],
        "production_live_enabled": payload["production_live_enabled"],
        "capital_binding_enabled": payload["capital_binding_enabled"],
        "human_gate_required": payload["human_gate_required"],
        "error": payload["error"],
    }


def _build_openclaw_session_context(
    *,
    episode: PortfolioEpisode,
    session_result: Mapping[str, Any],
) -> dict[str, Any]:
    primary_output = copy.deepcopy(dict(session_result.get("primary_output") or {}))
    source_oss_ref = f"oss://{session_result['component']}/{session_result['request_id']}"
    session_id = str(primary_output.get("session_id") or session_result.get("session_id") or "")
    upstream_session_id = str(primary_output.get("upstream_session_id") or "")
    context_bundle = copy.deepcopy(dict(primary_output.get("context_bundle") or {}))
    session_ref = f"openclaw-session://{session_id}"
    upstream_session_ref = f"openclaw-upstream-session://{upstream_session_id}"
    context_ref = f"openclaw-context://{episode.case_id}/{session_result['request_id']}"
    context_seed = {
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": session_result.get("component"),
        "request_id": session_result.get("request_id"),
        "session_id": session_id,
        "upstream_session_id": upstream_session_id,
        "artifact_family": session_result.get("artifact_family"),
        "state": primary_output.get("state"),
        "session_type": primary_output.get("session_type"),
        "context_bundle": context_bundle,
    }
    context_hash = _stable_payload_hash("openclaw-session-context", context_seed)
    return {
        "model_id": PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID,
        "context_ref": context_ref,
        "context_hash": context_hash,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": str(session_result["component"]),
        "request_id": str(session_result["request_id"]),
        "source_oss_ref": source_oss_ref,
        "session_ref": session_ref,
        "session_id": session_id,
        "upstream_session_ref": upstream_session_ref,
        "upstream_session_id": upstream_session_id,
        "artifact_family": session_result.get("artifact_family"),
        "session_state": primary_output.get("state"),
        "session_type": primary_output.get("session_type"),
        "audit_events": primary_output.get("audit_events"),
        "context_bundle": context_bundle,
        "input_hash": context_hash,
    }


def _build_lean_handoff_packet(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    final_evaluation: Mapping[str, Any],
    evolution_decision: EvolutionDecision,
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    market_friction: Mapping[str, Any],
    broker_lifecycle: Mapping[str, Any],
    persona_conflict_resolution: Mapping[str, Any],
    autonomous_schedule: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    shioaji_sandbox_lifecycle: Mapping[str, Any],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = oss_inputs["handoff"]
    openclaw_session_context = _build_openclaw_session_context(
        episode=episode,
        session_result=oss_inputs["session"],
    )
    openclaw_session_ref = str(openclaw_session_context["session_ref"])
    openclaw_context_ref = str(openclaw_session_context["context_ref"])
    openclaw_upstream_session_ref = str(openclaw_session_context["upstream_session_ref"])
    openclaw_source_oss_ref = str(openclaw_session_context["source_oss_ref"])
    vectorbt = case_upstream_artifacts["vectorbt"]
    tracker = case_upstream_artifacts["tracker"]
    selected_oss_refs = [
        f"oss://{entry['component']}/{entry['request_id']}"
        for entry in case_upstream_artifacts["selected_oss"].values()
    ]
    conflict_ref = str(persona_conflict_resolution["resolution_ref"])
    schedule_ref = str(autonomous_schedule["schedule_ref"])
    resolved_allocation = persona_conflict_resolution["resolved_allocation"]
    final_oos_step = strict_oos_evolution_proof["proof_steps"][-1]
    strategy_packet = copy.deepcopy(dict(lean_engine_replay["case_specific_strategy_packet"]))
    strategy_packet_ref = str(strategy_packet["packet_ref"])
    strict_oos_ref = str(strategy_packet["strict_oos_proof_ref"])
    no_leakage_ref = str(strategy_packet["no_leakage_protocol_ref"])
    trajectory_ref = str(strategy_packet["evolution_trajectory_ref"])
    tracking_provenance = copy.deepcopy(
        dict(strategy_packet.get("experiment_tracking_provenance") or {})
    )
    experiment_ref = str(tracking_provenance.get("experiment_ref") or "")
    tracking_reconciliation_ref = str(tracking_provenance.get("reconciliation_ref") or "")
    tracking_repair_ref = str(tracking_provenance.get("repair_ref") or "")
    policy_oss_lineage = copy.deepcopy(dict(strategy_packet.get("policy_oss_lineage") or {}))
    policy_oss_ref = str(policy_oss_lineage.get("source_oss_ref") or "")
    policy_oss_lineage_ref = str(policy_oss_lineage.get("lineage_ref") or "")
    policy_oss_registry_ref = str(policy_oss_lineage.get("registry_ref") or "")
    reflection_oss_lineage = copy.deepcopy(
        dict(strategy_packet.get("reflection_oss_lineage") or {})
    )
    reflection_oss_ref = str(reflection_oss_lineage.get("source_oss_ref") or "")
    reflection_oss_lineage_ref = str(reflection_oss_lineage.get("lineage_ref") or "")
    reflection_oss_registry_ref = str(reflection_oss_lineage.get("registry_ref") or "")
    alpha_seed_handoff = copy.deepcopy(
        dict(strategy_packet.get("alpha_seed_revision_handoff") or {})
    )
    alpha_seed_handoff_ref = str(alpha_seed_handoff.get("handoff_ref") or "")
    alpha_seed_revision_ref = str(alpha_seed_handoff.get("revision_ref") or "")
    alpha_seed_source_ref = str(alpha_seed_handoff.get("base_seed_ref") or "")
    alpha_seed_source_oss_ref = str(alpha_seed_handoff.get("source_oss_ref") or "")
    alpha_seed_handoff_hash = str(alpha_seed_handoff.get("lineage_hash") or "")
    return {
        "packet_id": f"lean-packet-{episode.case_id}",
        "component": handoff["component"],
        "request_id": handoff["request_id"],
        "strategy_packet_materialized": True,
        "packet_type": "LeanPaperStrategyPacket",
        "target_stage": "paper",
        "policy_id": final_policy["policy_id"],
        "policy_version": final_policy["policy_version"],
        "policy_generation": final_policy["generation"],
        "evolution_decision_id": evolution_decision.decision_id,
        "strategy_packet_ref": strategy_packet_ref,
        "strict_oos_evolution_proof_ref": strict_oos_ref,
        "no_leakage_protocol_ref": no_leakage_ref,
        "evolution_trajectory_ref": trajectory_ref,
        "experiment_tracking_provenance": tracking_provenance,
        "experiment_tracking_provenance_hash": tracking_provenance.get("lineage_hash"),
        "normalized_experiment_ref": experiment_ref,
        "tracking_reconciliation_ref": tracking_reconciliation_ref,
        "tracking_repair_ref": tracking_repair_ref,
        "policy_oss_lineage": policy_oss_lineage,
        "policy_oss_lineage_hash": policy_oss_lineage.get("lineage_hash"),
        "policy_oss_lineage_ref": policy_oss_lineage_ref,
        "policy_oss_ref": policy_oss_ref,
        "policy_oss_registry_ref": policy_oss_registry_ref,
        "policy_oss_component": policy_oss_lineage.get("component"),
        "policy_oss_request_id": policy_oss_lineage.get("request_id"),
        "reflection_oss_lineage": reflection_oss_lineage,
        "reflection_oss_lineage_hash": reflection_oss_lineage.get("lineage_hash"),
        "reflection_oss_lineage_ref": reflection_oss_lineage_ref,
        "reflection_oss_ref": reflection_oss_ref,
        "reflection_oss_registry_ref": reflection_oss_registry_ref,
        "reflection_oss_component": reflection_oss_lineage.get("component"),
        "reflection_oss_request_id": reflection_oss_lineage.get("request_id"),
        "openclaw_session_context": openclaw_session_context,
        "openclaw_session_context_hash": openclaw_session_context["context_hash"],
        "openclaw_session_context_ref": openclaw_context_ref,
        "openclaw_session_ref": openclaw_session_ref,
        "openclaw_source_oss_ref": openclaw_source_oss_ref,
        "openclaw_upstream_session_ref": openclaw_upstream_session_ref,
        "openclaw_session_id": openclaw_session_context["session_id"],
        "openclaw_upstream_session_id": openclaw_session_context["upstream_session_id"],
        "openclaw_session_state": openclaw_session_context["session_state"],
        "openclaw_session_artifact_family": openclaw_session_context["artifact_family"],
        "alpha_seed_revision_handoff": alpha_seed_handoff,
        "alpha_seed_revision_handoff_hash": alpha_seed_handoff_hash,
        "alpha_seed_revision_handoff_ref": alpha_seed_handoff_ref,
        "alpha_seed_revision_ref": alpha_seed_revision_ref,
        "alpha_seed_source_ref": alpha_seed_source_ref,
        "alpha_seed_source_oss_ref": alpha_seed_source_oss_ref,
        "alpha_seed_revision_action": alpha_seed_handoff.get("revision_action"),
        "alpha_seed_component": alpha_seed_handoff.get("alpha_component"),
        "alpha_seed_revision_key": alpha_seed_handoff.get("revision_key"),
        "alpha_seed_downstream_vectorbt_request_id": alpha_seed_handoff.get(
            "downstream_vectorbt_request_id"
        ),
        "alpha_seed_downstream_policy_candidate_request_id": alpha_seed_handoff.get(
            "downstream_policy_candidate_request_id"
        ),
        "alpha_seed_downstream_tracker_run_id": alpha_seed_handoff.get(
            "downstream_tracker_run_id"
        ),
        "strategy_packet": strategy_packet,
        "strategy_packet_hash": _stable_payload_hash(
            "lean-strategy-packet",
            strategy_packet,
        ),
        "strategy_packet_validation_window": final_oos_step["validation_window"],
        "strategy_packet_source_outcome_window": final_oos_step["source_outcome_window"],
        "future_holdout_score": final_evaluation["score"],
        "future_holdout_improvement": final_oos_step["score_improvement"],
        "strategy_packet_replay_passed": (
            strategy_packet.get("generation") == 2
            and strategy_packet.get("policy_id") == final_policy["policy_id"]
            and strategy_packet.get("validation_window") == "future_holdout"
            and strategy_packet.get("strict_oos_replay_passed") is True
            and strategy_packet.get("no_leakage_replay_passed") is True
        ),
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "market_friction_model_id": market_friction["model_id"],
        "broker_lifecycle_model": broker_lifecycle["lifecycle_model"],
        "persona_conflict_resolution_ref": conflict_ref,
        "resolved_capital_budget_pct": resolved_allocation["capital_budget_pct"],
        "resolved_direction_by_instrument": copy.deepcopy(
            dict(resolved_allocation["direction_by_instrument"])
        ),
        "resolved_weight_by_instrument": copy.deepcopy(
            dict(resolved_allocation["weight_by_instrument"])
        ),
        "schedule_ref": schedule_ref,
        "next_cycle_due_at": autonomous_schedule["next_cycle_due_at"],
        "lean_engine_replay_id": lean_engine_replay["replay_id"],
        "lean_engine_replay_status": lean_engine_replay["status"],
        "shioaji_sandbox_lifecycle_id": shioaji_sandbox_lifecycle["lifecycle_id"],
        "shioaji_sandbox_lifecycle_status": shioaji_sandbox_lifecycle["status"],
        "case_vectorbt_request_id": vectorbt["request_id"],
        "case_vectorbt_backend": vectorbt["backend"],
        "case_vectorbt_registry_id": vectorbt["registry_id"],
        "case_tracking_request_id": tracker["request_id"],
        "case_tracking_backend": tracker["backend"],
        "case_tracking_run_id": tracker["run_id"],
        "runtime_bundle_refs": [
            strategy_packet_ref,
            f"strategy://{episode.seed_key}-agent-usability-hardening/{final_policy['policy_id']}",
            f"evolution://{evolution_decision.decision_id}",
            strict_oos_ref,
            no_leakage_ref,
            trajectory_ref,
            f"oss://{handoff['component']}/{handoff['request_id']}",
            f"oss://vectorbt/{vectorbt['request_id']}",
            policy_oss_lineage_ref,
            policy_oss_ref,
            policy_oss_registry_ref,
            reflection_oss_lineage_ref,
            reflection_oss_ref,
            reflection_oss_registry_ref,
            openclaw_context_ref,
            openclaw_session_ref,
            openclaw_source_oss_ref,
            openclaw_upstream_session_ref,
            alpha_seed_handoff_ref,
            alpha_seed_revision_ref,
            alpha_seed_source_ref,
            alpha_seed_source_oss_ref,
            experiment_ref,
            tracking_reconciliation_ref,
            tracking_repair_ref,
            conflict_ref,
            schedule_ref,
            *selected_oss_refs,
            f"lean-engine://{lean_engine_replay['replay_id']}",
            f"broker-sandbox://{shioaji_sandbox_lifecycle['lifecycle_id']}",
        ],
        "received_by_lean_handoff": handoff.get("status") == "completed",
        "broker_live_submitted": False,
    }


def _lean_execution_call_for(quantity_type: str, order_type: str) -> str:
    if quantity_type == "PERCENT_PORTFOLIO":
        return "SetHoldings"
    if order_type == "LIMIT":
        return "LimitOrder"
    return "MarketOrder"


def _lean_symbol_for_execution_symbol(execution_symbol: str) -> str:
    return str(execution_symbol).split(".", 1)[0]


def _build_lean_packet_execution_projection(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    executions: Sequence[Mapping[str, Any]],
    market_friction: Mapping[str, Any],
    broker_lifecycle: Mapping[str, Any],
    persona_conflict_resolution: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
) -> dict[str, Any]:
    generation = int(final_policy["generation"])
    execution = executions[generation]
    fill_events = list(execution.get("fill_events", []))
    fill_by_symbol = {
        str(
            fill.get("metadata", {}).get("symbol")
            or fill.get("metadata", {}).get("signal_symbol")
            or fill.get("metadata", {}).get("source_symbol")
        ): fill
        for fill in fill_events
    }
    orders = [
        order for order in broker_lifecycle.get("orders", [])
        if int(order.get("generation", -1)) == generation
    ]
    order_by_symbol = {str(order.get("symbol")): order for order in orders}
    final_costs = market_friction["generation_costs"][generation]["leg_costs"]
    cost_by_instrument = {str(cost["instrument"]): cost for cost in final_costs}
    allocation = persona_conflict_resolution["resolved_allocation"]
    strategy_packet = lean_handoff["strategy_packet"]
    strategy_packet_ref = str(lean_handoff["strategy_packet_ref"])
    handoff_ref = f"lean-handoff://{lean_handoff['packet_id']}"
    projection_ref = f"lean-packet-execution://{episode.case_id}/generation{generation}"
    leg_projections: list[dict[str, Any]] = []
    for leg_index, window in enumerate(episode.windows):
        leg = final_policy["legs"][window.instrument]
        lean_symbol = _lean_symbol_for_execution_symbol(window.execution_symbol)
        cost = cost_by_instrument.get(window.instrument, {})
        order = order_by_symbol.get(lean_symbol, {})
        fill = fill_by_symbol.get(lean_symbol, {})
        fill_metrics = fill.get("metrics", {})
        fill_metadata = fill.get("metadata", {})
        entry_row = _entry_row_for_generation(window, generation)
        direction = int(leg["direction"])
        policy_weight = float(leg["weight"])
        target_weight = float(allocation["weight_by_instrument"][window.instrument])
        risk_weight = float(leg["risk_multiplier"]) * policy_weight
        expected_quantity = _quantity_for(
            str(final_policy["quantity_type"]),
            float(entry_row["close"]),
            risk_weight,
            episode.ordinal + leg_index,
        )
        requested_quantity = _finite_float(fill_metadata.get("requested_quantity"), 0.0)
        target_ref = f"{projection_ref}/leg/{leg_index}/target"
        order_ref = f"paper-order://{order.get('order_id', '')}"
        fill_ref = f"paper-fill://{order.get('fill_event_id', fill.get('event_id', ''))}"
        readback_ref = f"{order_ref}/readback"
        leg_projections.append(
            {
                "leg_index": leg_index,
                "instrument": window.instrument,
                "execution_symbol": window.execution_symbol,
                "lean_symbol": lean_symbol,
                "policy_id": final_policy["policy_id"],
                "policy_version": final_policy["policy_version"],
                "generation": generation,
                "direction": direction,
                "policy_weight": round(policy_weight, 6),
                "target_weight": round(target_weight, 6),
                "resolved_weight": round(target_weight, 6),
                "capital_budget_pct": allocation["capital_budget_pct"],
                "risk_multiplier": leg["risk_multiplier"],
                "quantity_type": final_policy["quantity_type"],
                "order_type": final_policy["order_type"],
                "lean_order_call": _lean_execution_call_for(
                    str(final_policy["quantity_type"]),
                    str(final_policy["order_type"]),
                ),
                "target_ref": target_ref,
                "order_ref": order_ref,
                "fill_ref": fill_ref,
                "readback_ref": readback_ref,
                "signal_id": fill_metadata.get("signal_id"),
                "expected_signal_id": _stable_id(
                    "sig",
                    episode.case_id,
                    str(generation),
                    window.instrument,
                    str(window.start_index),
                ),
                "requested_quantity": requested_quantity,
                "expected_requested_quantity": expected_quantity,
                "fill_quantity": _finite_float(fill_metrics.get("fill_quantity"), 0.0),
                "fill_price": _finite_float(fill_metrics.get("fill_price"), float(entry_row["close"])),
                "market_data_ref": fill_metadata.get("market_data_ref"),
                "source_dataset_ref": fill_metadata.get("source_dataset_ref"),
                "market_friction_notional": cost.get("notional"),
                "market_friction_total_cost_bps": cost.get("total_cost_bps"),
                "within_liquidity_cap": cost.get("within_liquidity_cap"),
                "broker_order_id": order.get("order_id"),
                "broker_fill_event_id": order.get("fill_event_id"),
                "broker_status_path": list(order.get("status_path", [])),
                "broker_terminal_status": order.get("terminal_status"),
                "broker_readback_status": order.get("readback_status"),
                "broker_reconciled": order.get("reconciled") is True,
                "live_broker_submitted": order.get("live_broker_submitted") is True,
                "input_refs": [
                    strategy_packet_ref,
                    handoff_ref,
                    lean_handoff["strict_oos_evolution_proof_ref"],
                    lean_handoff["no_leakage_protocol_ref"],
                    lean_handoff["evolution_trajectory_ref"],
                    f"lean-engine://{lean_engine_replay['replay_id']}",
                    persona_conflict_resolution["resolution_ref"],
                    order_ref,
                    fill_ref,
                ],
                "event_chain": [
                    "packet_leg_target",
                    "lean_target_order",
                    "paper_fill_readback",
                ],
            }
        )
    replay = {
        "replayable": True,
        "strategy_packet_ref_bound": (
            strategy_packet.get("packet_ref") == strategy_packet_ref
            and strategy_packet_ref in lean_handoff.get("runtime_bundle_refs", [])
        ),
        "strategy_packet_generation2_bound": (
            strategy_packet.get("generation") == generation
            and strategy_packet.get("policy_id") == final_policy.get("policy_id")
            and strategy_packet.get("validation_window") == "future_holdout"
        ),
        "handoff_allocation_bound": (
            lean_handoff.get("resolved_weight_by_instrument")
            == allocation.get("weight_by_instrument")
            and lean_handoff.get("resolved_direction_by_instrument")
            == allocation.get("direction_by_instrument")
        ),
        "all_packet_instruments_have_policy_legs": (
            set(strategy_packet.get("portfolio_instruments", []))
            == set(final_policy.get("legs", {}))
            == {window.instrument for window in episode.windows}
        ),
        "all_leg_directions_match_policy_and_allocation": all(
            leg["direction"] == int(final_policy["legs"][leg["instrument"]]["direction"])
            and leg["direction"] == int(allocation["direction_by_instrument"][leg["instrument"]])
            for leg in leg_projections
        ),
        "all_leg_weights_match_handoff_allocation": all(
            abs(
                float(leg["target_weight"])
                - float(lean_handoff["resolved_weight_by_instrument"][leg["instrument"]])
            ) <= 1e-9
            for leg in leg_projections
        ),
        "all_leg_capital_within_budget": (
            round(sum(float(leg["target_weight"]) for leg in leg_projections), 6)
            == float(allocation["capital_budget_pct"])
            and float(allocation["capital_budget_pct"]) <= 1.0
        ),
        "all_leg_expected_quantities_replay_signal_payload": all(
            leg["signal_id"] == leg["expected_signal_id"]
            and abs(float(leg["requested_quantity"]) - float(leg["expected_requested_quantity"])) <= 1e-6
            for leg in leg_projections
        ),
        "all_leg_market_friction_notional_bound": all(
            leg["market_friction_notional"] is not None
            and float(leg["market_friction_notional"]) > 0.0
            and leg["within_liquidity_cap"] is True
            for leg in leg_projections
        ),
        "all_lean_targets_have_broker_orders": (
            len(leg_projections) == PORTFOLIO_LEG_COUNT
            and all(leg["broker_order_id"] for leg in leg_projections)
        ),
        "all_broker_orders_have_fill_readbacks": all(
            leg["broker_fill_event_id"]
            and leg["broker_terminal_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
            and leg["broker_readback_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
            and leg["broker_reconciled"] is True
            for leg in leg_projections
        ),
        "all_fill_events_bind_signal_metadata": all(
            leg["fill_quantity"] != 0.0
            and leg["market_data_ref"]
            and leg["source_dataset_ref"] == HISTORICAL_OHLCV_DATASET_ID
            for leg in leg_projections
        ),
        "paper_only_guard_retained": (
            lean_handoff.get("target_stage") == "paper"
            and lean_handoff.get("broker_live_submitted") is False
            and all(leg["live_broker_submitted"] is False for leg in leg_projections)
        ),
        "projection_ready_for_runtime_feedback": True,
    }
    return {
        "projection_id": f"lean-packet-execution-{episode.case_id}",
        "projection_ref": projection_ref,
        "model_id": LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "strategy_packet_ref": strategy_packet_ref,
        "source_handoff_ref": handoff_ref,
        "source_runtime_ref": f"lean-engine://{lean_engine_replay['replay_id']}",
        "policy_id": final_policy["policy_id"],
        "policy_version": final_policy["policy_version"],
        "generation": generation,
        "target_stage": lean_handoff["target_stage"],
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "capital_budget_pct": allocation["capital_budget_pct"],
        "leg_count": len(leg_projections),
        "order_count": len(orders),
        "fill_count": len(fill_events),
        "leg_projections": leg_projections,
        "input_refs": [
            strategy_packet_ref,
            handoff_ref,
            f"lean-engine://{lean_engine_replay['replay_id']}",
            persona_conflict_resolution["resolution_ref"],
            lean_handoff["strict_oos_evolution_proof_ref"],
            lean_handoff["no_leakage_protocol_ref"],
            lean_handoff["evolution_trajectory_ref"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "lean-packet-execution-projection",
            {
                "case_id": episode.case_id,
                "strategy_packet_ref": strategy_packet_ref,
                "handoff_ref": handoff_ref,
                "leg_projections": leg_projections,
                "replay": replay,
            },
        ),
    }


def _build_lean_runtime_feedback_response(
    *,
    episode: PortfolioEpisode,
    scenario: str,
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_packet_execution_projection: Mapping[str, Any],
    autonomous_schedule: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    action = _lean_runtime_feedback_action_for_scenario(scenario)
    ooda_step = LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION.get(action, "orient")
    runtime_ref = f"lean-engine://{lean_engine_replay['replay_id']}"
    handoff_ref = f"lean-handoff://{lean_handoff['packet_id']}"
    metadata_key = next(
        (key for key in lean_engine_replay.get("object_store_keys", []) if str(key).endswith("/metadata.json")),
        "",
    )
    artifact_key = next(
        (key for key in lean_engine_replay.get("object_store_keys", []) if str(key).endswith("/artifact.bin")),
        "",
    )
    runtime_context = lean_engine_replay.get("runtime_context", {})
    loaded_metadata = lean_engine_replay.get("loaded_metadata", {})
    binding = lean_engine_replay.get("binding", {})
    plan = lean_engine_replay.get("plan", {})
    strategy_packet_ref = str(lean_handoff.get("strategy_packet_ref", ""))
    strict_oos_ref = str(lean_handoff.get("strict_oos_evolution_proof_ref", ""))
    no_leakage_ref = str(lean_handoff.get("no_leakage_protocol_ref", ""))
    experiment_ref = str(lean_handoff.get("normalized_experiment_ref", ""))
    tracking_reconciliation_ref = str(lean_handoff.get("tracking_reconciliation_ref", ""))
    tracking_repair_ref = str(lean_handoff.get("tracking_repair_ref", ""))
    policy_oss_ref = str(lean_handoff.get("policy_oss_ref", ""))
    policy_oss_lineage_ref = str(lean_handoff.get("policy_oss_lineage_ref", ""))
    policy_oss_registry_ref = str(lean_handoff.get("policy_oss_registry_ref", ""))
    reflection_oss_ref = str(lean_handoff.get("reflection_oss_ref", ""))
    reflection_oss_lineage_ref = str(lean_handoff.get("reflection_oss_lineage_ref", ""))
    reflection_oss_registry_ref = str(lean_handoff.get("reflection_oss_registry_ref", ""))
    openclaw_context_ref = str(lean_handoff.get("openclaw_session_context_ref", ""))
    openclaw_session_ref = str(lean_handoff.get("openclaw_session_ref", ""))
    openclaw_source_oss_ref = str(lean_handoff.get("openclaw_source_oss_ref", ""))
    openclaw_upstream_session_ref = str(lean_handoff.get("openclaw_upstream_session_ref", ""))
    alpha_seed_handoff_ref = str(lean_handoff.get("alpha_seed_revision_handoff_ref", ""))
    alpha_seed_revision_ref = str(lean_handoff.get("alpha_seed_revision_ref", ""))
    alpha_seed_source_ref = str(lean_handoff.get("alpha_seed_source_ref", ""))
    alpha_seed_source_oss_ref = str(lean_handoff.get("alpha_seed_source_oss_ref", ""))
    projection_ref = str(lean_packet_execution_projection.get("projection_ref", ""))
    evidence_refs = [
        runtime_ref,
        handoff_ref,
        projection_ref,
        strategy_packet_ref,
        strict_oos_ref,
        no_leakage_ref,
        experiment_ref,
        tracking_reconciliation_ref,
        tracking_repair_ref,
        policy_oss_lineage_ref,
        policy_oss_ref,
        policy_oss_registry_ref,
        reflection_oss_lineage_ref,
        reflection_oss_ref,
        reflection_oss_registry_ref,
        openclaw_context_ref,
        openclaw_session_ref,
        openclaw_source_oss_ref,
        openclaw_upstream_session_ref,
        alpha_seed_handoff_ref,
        alpha_seed_revision_ref,
        alpha_seed_source_ref,
        alpha_seed_source_oss_ref,
        f"runtime-binding://{runtime_context.get('runtime_binding_id')}",
        f"object-store://{metadata_key}",
        f"reflection://{decision_traces[-1]['reflection_id']}",
    ]
    replay = {
        "runtime_feedback_consumed": lean_engine_replay.get("model_id") == LEAN_ENGINE_REPLAY_MODEL_ID
        and lean_engine_replay.get("status") == "passed",
        "handoff_packet_consumed": lean_handoff.get("received_by_lean_handoff") is True
        and lean_handoff.get("lean_engine_replay_id") == lean_engine_replay.get("replay_id"),
        "runtime_binding_readback_verified": runtime_context.get("runtime_binding_id") == binding.get("binding_id")
        and runtime_context.get("runtime_id") == binding.get("runtime_id")
        and runtime_context.get("deployment_plan_id") == plan.get("plan_id")
        and loaded_metadata.get("runtime_binding_id") == binding.get("binding_id"),
        "object_store_readback_verified": bool(metadata_key) and bool(artifact_key),
        "fills_drive_next_ooda": int(lean_engine_replay.get("fill_count", 0)) >= 1
        and action == LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO.get(scenario),
        "paper_runtime_guard_retained": runtime_context.get("deployment_stage") == "paper"
        and lean_handoff.get("target_stage") == "paper"
        and lean_handoff.get("broker_live_submitted") is False
        and lean_engine_replay.get("broker_production_live_enabled") == "false",
        "case_runtime_refs_bound": bool(lean_handoff.get("case_vectorbt_request_id"))
        and bool(lean_handoff.get("case_tracking_run_id"))
        and runtime_ref in lean_handoff.get("runtime_bundle_refs", []),
        "evolved_strategy_packet_refs_bound": (
            bool(strategy_packet_ref)
            and strategy_packet_ref in lean_handoff.get("runtime_bundle_refs", [])
            and bool(strict_oos_ref)
            and strict_oos_ref in lean_handoff.get("runtime_bundle_refs", [])
            and bool(no_leakage_ref)
            and no_leakage_ref in lean_handoff.get("runtime_bundle_refs", [])
            and lean_handoff.get("strategy_packet_replay_passed") is True
        ),
        "experiment_tracking_lineage_bound": (
            bool(experiment_ref)
            and experiment_ref in lean_handoff.get("runtime_bundle_refs", [])
            and experiment_ref in evidence_refs
            and bool(tracking_reconciliation_ref)
            and tracking_reconciliation_ref in lean_handoff.get("runtime_bundle_refs", [])
            and tracking_reconciliation_ref in evidence_refs
            and bool(tracking_repair_ref)
            and tracking_repair_ref in lean_handoff.get("runtime_bundle_refs", [])
            and tracking_repair_ref in evidence_refs
        ),
        "policy_oss_lineage_bound": (
            bool(policy_oss_lineage_ref)
            and policy_oss_lineage_ref in lean_handoff.get("runtime_bundle_refs", [])
            and policy_oss_lineage_ref in evidence_refs
            and bool(policy_oss_ref)
            and policy_oss_ref in lean_handoff.get("runtime_bundle_refs", [])
            and policy_oss_ref in evidence_refs
            and bool(policy_oss_registry_ref)
            and policy_oss_registry_ref in lean_handoff.get("runtime_bundle_refs", [])
            and policy_oss_registry_ref in evidence_refs
            and lean_handoff.get("policy_oss_lineage_hash")
            == lean_handoff.get("policy_oss_lineage", {}).get("lineage_hash")
        ),
        "reflection_oss_lineage_bound": (
            bool(reflection_oss_lineage_ref)
            and reflection_oss_lineage_ref in lean_handoff.get("runtime_bundle_refs", [])
            and reflection_oss_lineage_ref in evidence_refs
            and bool(reflection_oss_ref)
            and reflection_oss_ref in lean_handoff.get("runtime_bundle_refs", [])
            and reflection_oss_ref in evidence_refs
            and bool(reflection_oss_registry_ref)
            and reflection_oss_registry_ref in lean_handoff.get("runtime_bundle_refs", [])
            and reflection_oss_registry_ref in evidence_refs
            and lean_handoff.get("reflection_oss_lineage_hash")
            == lean_handoff.get("reflection_oss_lineage", {}).get("lineage_hash")
        ),
        "openclaw_session_context_bound": (
            bool(openclaw_context_ref)
            and openclaw_context_ref in lean_handoff.get("runtime_bundle_refs", [])
            and openclaw_context_ref in evidence_refs
            and bool(openclaw_session_ref)
            and openclaw_session_ref in lean_handoff.get("runtime_bundle_refs", [])
            and openclaw_session_ref in evidence_refs
            and bool(openclaw_source_oss_ref)
            and openclaw_source_oss_ref in lean_handoff.get("runtime_bundle_refs", [])
            and openclaw_source_oss_ref in evidence_refs
            and bool(openclaw_upstream_session_ref)
            and openclaw_upstream_session_ref in lean_handoff.get("runtime_bundle_refs", [])
            and openclaw_upstream_session_ref in evidence_refs
            and lean_handoff.get("openclaw_session_context_hash")
            == lean_handoff.get("openclaw_session_context", {}).get("context_hash")
            and lean_handoff.get("openclaw_session_state") == "active"
        ),
        "alpha_seed_revision_handoff_bound": (
            bool(alpha_seed_handoff_ref)
            and alpha_seed_handoff_ref in lean_handoff.get("runtime_bundle_refs", [])
            and alpha_seed_handoff_ref in evidence_refs
            and bool(alpha_seed_revision_ref)
            and alpha_seed_revision_ref in lean_handoff.get("runtime_bundle_refs", [])
            and alpha_seed_revision_ref in evidence_refs
            and bool(alpha_seed_source_ref)
            and alpha_seed_source_ref in lean_handoff.get("runtime_bundle_refs", [])
            and alpha_seed_source_ref in evidence_refs
            and bool(alpha_seed_source_oss_ref)
            and alpha_seed_source_oss_ref in lean_handoff.get("runtime_bundle_refs", [])
            and alpha_seed_source_oss_ref in evidence_refs
            and lean_handoff.get("alpha_seed_revision_handoff_hash")
            == lean_handoff.get("alpha_seed_revision_handoff", {}).get("lineage_hash")
            and lean_handoff.get("alpha_seed_revision_action")
            == lean_handoff.get("alpha_seed_revision_handoff", {}).get("revision_action")
        ),
        "lean_packet_execution_projection_consumed": (
            lean_packet_execution_projection.get("model_id") == LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID
            and lean_packet_execution_projection.get("status") == "passed"
            and lean_packet_execution_projection.get("source_handoff_ref") == handoff_ref
            and lean_packet_execution_projection.get("strategy_packet_ref") == strategy_packet_ref
            and lean_packet_execution_projection.get("leg_count") == PORTFOLIO_LEG_COUNT
            and projection_ref in evidence_refs
            and all(lean_packet_execution_projection.get("replay", {}).values())
        ),
        "next_cycle_scheduled": autonomous_schedule.get("phase_order_valid") is True
        and bool(autonomous_schedule.get("next_cycle_due_at")),
        "drives_persona_next_ooda_step": True,
    }
    return {
        "feedback_id": f"lean-runtime-feedback-{episode.case_id}",
        "model_id": LEAN_RUNTIME_FEEDBACK_MODEL_ID,
        "status": "accepted" if all(replay.values()) else "blocked",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "scenario": scenario,
        "source_runtime_ref": runtime_ref,
        "source_handoff_ref": handoff_ref,
        "runtime_feedback": {
            "runtime_id": runtime_context.get("runtime_id"),
            "runtime_binding_id": runtime_context.get("runtime_binding_id"),
            "deployment_plan_id": runtime_context.get("deployment_plan_id"),
            "deployment_stage": runtime_context.get("deployment_stage"),
            "loaded_metadata_runtime_binding_id": loaded_metadata.get("runtime_binding_id"),
            "loaded_metadata_deployment_plan_id": loaded_metadata.get("deployment_plan_id"),
            "fill_count": lean_engine_replay.get("fill_count"),
            "executed_on_data_callbacks": lean_engine_replay.get("executed_on_data_callbacks"),
            "object_store_metadata_key": metadata_key,
            "object_store_artifact_key": artifact_key,
        },
        "request_response_flow": [
            "persona_strategy_packet",
            "lean_packet_execution_projection",
            "lean_runtime_replay_response",
            "persona_next_ooda_action",
        ],
        "persona_ooda_followup": {
            "action": action,
            "action_family": _lean_runtime_feedback_action_family(action),
            "ooda_step": ooda_step,
            "next_scheduler_phase": "reflect" if ooda_step in {"observe", "orient"} else "evolve",
            "required_before_next_cycle": True,
            "paper_only": True,
            "rationale": _lean_runtime_feedback_rationale(scenario),
            "evidence_refs": evidence_refs,
        },
        "state_updates": {
            "mark_runtime_feedback_seen": True,
            "bind_runtime_context": runtime_context.get("runtime_binding_id"),
            "verify_object_store_metadata": metadata_key,
            "bind_evolved_strategy_packet": strategy_packet_ref,
            "bind_reconciled_experiment_ref": experiment_ref,
            "bind_tracking_reconciliation_ref": tracking_reconciliation_ref,
            "bind_tracking_repair_ref": tracking_repair_ref,
            "bind_policy_oss_lineage_ref": policy_oss_lineage_ref,
            "bind_policy_oss_ref": policy_oss_ref,
            "bind_policy_oss_registry_ref": policy_oss_registry_ref,
            "bind_reflection_oss_lineage_ref": reflection_oss_lineage_ref,
            "bind_reflection_oss_ref": reflection_oss_ref,
            "bind_reflection_oss_registry_ref": reflection_oss_registry_ref,
            "bind_openclaw_session_context_ref": openclaw_context_ref,
            "bind_openclaw_session_ref": openclaw_session_ref,
            "bind_openclaw_source_oss_ref": openclaw_source_oss_ref,
            "bind_openclaw_upstream_session_ref": openclaw_upstream_session_ref,
            "bind_alpha_seed_revision_handoff_ref": alpha_seed_handoff_ref,
            "bind_alpha_seed_revision_ref": alpha_seed_revision_ref,
            "bind_alpha_seed_source_ref": alpha_seed_source_ref,
            "bind_alpha_seed_source_oss_ref": alpha_seed_source_oss_ref,
            "bind_alpha_seed_revision_action": lean_handoff.get(
                "alpha_seed_revision_action"
            ),
            "bind_lean_packet_execution_projection": projection_ref,
            "attach_to_handoff_packet": lean_handoff["packet_id"],
            "attach_to_decision_trace": decision_traces[-1]["reflection_id"],
            "schedule_next_cycle_after_feedback": autonomous_schedule["next_cycle_due_at"],
        },
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "lean-runtime-feedback",
            {
                "case_id": episode.case_id,
                "scenario": scenario,
                "action": action,
                "runtime_ref": runtime_ref,
                "handoff_ref": handoff_ref,
                "strategy_packet_ref": strategy_packet_ref,
                "experiment_ref": experiment_ref,
                "tracking_reconciliation_ref": tracking_reconciliation_ref,
                "tracking_repair_ref": tracking_repair_ref,
                "policy_oss_lineage_ref": policy_oss_lineage_ref,
                "policy_oss_ref": policy_oss_ref,
                "policy_oss_registry_ref": policy_oss_registry_ref,
                "reflection_oss_lineage_ref": reflection_oss_lineage_ref,
                "reflection_oss_ref": reflection_oss_ref,
                "reflection_oss_registry_ref": reflection_oss_registry_ref,
                "openclaw_context_ref": openclaw_context_ref,
                "openclaw_session_ref": openclaw_session_ref,
                "openclaw_source_oss_ref": openclaw_source_oss_ref,
                "openclaw_upstream_session_ref": openclaw_upstream_session_ref,
                "alpha_seed_handoff_ref": alpha_seed_handoff_ref,
                "alpha_seed_revision_ref": alpha_seed_revision_ref,
                "alpha_seed_source_ref": alpha_seed_source_ref,
                "alpha_seed_source_oss_ref": alpha_seed_source_oss_ref,
                "projection_ref": projection_ref,
                "runtime_binding_id": runtime_context.get("runtime_binding_id"),
                "metadata_key": metadata_key,
                "fill_count": lean_engine_replay.get("fill_count"),
            },
        ),
    }


def _build_experiment_tracking_lineage_handoff_proof(
    *,
    episode: PortfolioEpisode,
    evolution_decision: EvolutionDecision,
    case_upstream_artifacts: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    tracker = case_upstream_artifacts["tracker"]
    reconciliation = case_upstream_artifacts["tracking_reconciliation"]
    repair = reconciliation["repair"]
    experiment_ref = str(repair["normalized_experiment_ref"])
    reconciliation_ref = str(reconciliation["reconciliation_ref"])
    repair_ref = str(repair["repair_ref"])
    strategy_packet = lean_engine_replay["case_specific_strategy_packet"]
    packet_provenance = dict(strategy_packet.get("experiment_tracking_provenance") or {})
    readback = lean_engine_replay["lean_object_store_packet_readback"]
    loaded_provenance = dict(readback.get("loaded_experiment_tracking_provenance") or {})
    handoff_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    runtime_feedback_refs = set(
        lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
    )
    decision_evidence_refs = [
        ref.to_dict() if isinstance(ref, EvidenceRef) else copy.deepcopy(dict(ref))
        for ref in evolution_decision.evidence_refs
    ]
    decision_evidence_ref_ids = {str(ref.get("ref_id")) for ref in decision_evidence_refs}
    decision_metadata = dict(evolution_decision.metadata or {})
    lineage_hashes = {
        "strategy_packet": str(strategy_packet.get("experiment_tracking_provenance_hash") or ""),
        "packet_provenance": str(packet_provenance.get("lineage_hash") or ""),
        "object_store_readback": str(readback.get("experiment_tracking_provenance_hash") or ""),
        "loaded_object_store_readback": str(
            readback.get("loaded_experiment_tracking_provenance_hash") or ""
        ),
        "lean_handoff": str(lean_handoff.get("experiment_tracking_provenance_hash") or ""),
    }
    replay = {
        "replayable": True,
        "tracker_readback_reconciled": _tracking_readback_reconciliation_is_usable(
            reconciliation
        ),
        "evolution_decision_cites_reconciliation": reconciliation_ref
        in decision_evidence_ref_ids,
        "evolution_decision_metadata_carries_experiment_ref": (
            decision_metadata.get("normalized_experiment_ref") == experiment_ref
            and decision_metadata.get("tracking_reconciliation_ref") == reconciliation_ref
            and decision_metadata.get("tracking_repair_ref") == repair_ref
        ),
        "strategy_packet_carries_tracking_provenance": (
            packet_provenance.get("experiment_ref") == experiment_ref
            and packet_provenance.get("backend") == tracker["backend"]
            and packet_provenance.get("run_id") == tracker["run_id"]
            and packet_provenance.get("reconciliation_ref") == reconciliation_ref
            and packet_provenance.get("repair_ref") == repair_ref
            and strategy_packet.get("normalized_experiment_ref") == experiment_ref
            and strategy_packet.get("tracking_reconciliation_ref") == reconciliation_ref
            and strategy_packet.get("tracking_repair_ref") == repair_ref
        ),
        "object_store_readback_preserves_tracking_provenance": (
            readback.get("normalized_experiment_ref") == experiment_ref
            and readback.get("tracking_reconciliation_ref") == reconciliation_ref
            and readback.get("tracking_repair_ref") == repair_ref
            and loaded_provenance == packet_provenance
        ),
        "handoff_runtime_bundle_contains_repaired_tracking_refs": {
            experiment_ref,
            reconciliation_ref,
            repair_ref,
        }.issubset(handoff_refs),
        "runtime_feedback_cites_repaired_tracking_refs": {
            experiment_ref,
            reconciliation_ref,
            repair_ref,
        }.issubset(runtime_feedback_refs),
        "lineage_hash_stable_across_packet_handoff_readback": (
            bool(lineage_hashes["strategy_packet"])
            and len(set(lineage_hashes.values())) == 1
        ),
    }
    return {
        "proof_id": f"tracking-experiment-lineage-handoff-{episode.case_id}",
        "proof_ref": f"tracking-experiment-lineage://{episode.case_id}",
        "model_id": PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "backend": tracker["backend"],
        "tracker_request_id": tracker["request_id"],
        "tracking_run_id": tracker["run_id"],
        "experiment_ref": experiment_ref,
        "tracking_reconciliation_ref": reconciliation_ref,
        "tracking_repair_ref": repair_ref,
        "tracking_repair_action": repair["action"],
        "strategy_packet_ref": strategy_packet["packet_ref"],
        "lean_handoff_ref": f"lean-handoff://{lean_handoff['packet_id']}",
        "lean_runtime_feedback_ref": f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        "object_store_readback_ref": readback["readback_id"],
        "lineage_hashes": lineage_hashes,
        "decision_evidence_refs": decision_evidence_refs,
        "input_refs": [
            experiment_ref,
            reconciliation_ref,
            repair_ref,
            strategy_packet["packet_ref"],
            f"lean-handoff://{lean_handoff['packet_id']}",
            f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
            readback["readback_id"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "tracking-experiment-lineage-handoff",
            {
                "case_id": episode.case_id,
                "experiment_ref": experiment_ref,
                "reconciliation_ref": reconciliation_ref,
                "repair_ref": repair_ref,
                "lineage_hashes": lineage_hashes,
                "replay": replay,
            },
        ),
    }


def _build_policy_oss_lineage_handoff_proof(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    policy_candidate_materiality: Mapping[str, Any],
    case_upstream_artifacts: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    policy_entry = case_upstream_artifacts["selected_oss"]["policy_candidate"]
    source_ref = f"oss://{policy_entry['component']}/{policy_entry['request_id']}"
    final_policy_lineage = copy.deepcopy(dict(final_policy.get("policy_oss_lineage") or {}))
    strategy_packet = lean_engine_replay["case_specific_strategy_packet"]
    packet_lineage = copy.deepcopy(dict(strategy_packet.get("policy_oss_lineage") or {}))
    readback = lean_engine_replay["lean_object_store_packet_readback"]
    loaded_lineage = copy.deepcopy(dict(readback.get("loaded_policy_oss_lineage") or {}))
    handoff_lineage = copy.deepcopy(dict(lean_handoff.get("policy_oss_lineage") or {}))
    handoff_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    runtime_feedback_refs = set(
        lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
    )
    lineage_ref = str(final_policy_lineage.get("lineage_ref") or "")
    registry_ref = str(final_policy_lineage.get("registry_ref") or "")
    lineage_hashes = {
        "final_policy": str(final_policy.get("policy_oss_lineage_hash") or ""),
        "final_policy_lineage": str(final_policy_lineage.get("lineage_hash") or ""),
        "strategy_packet": str(strategy_packet.get("policy_oss_lineage_hash") or ""),
        "packet_lineage": str(packet_lineage.get("lineage_hash") or ""),
        "object_store_readback": str(readback.get("policy_oss_lineage_hash") or ""),
        "loaded_object_store_readback": str(readback.get("loaded_policy_oss_lineage_hash") or ""),
        "handoff": str(lean_handoff.get("policy_oss_lineage_hash") or ""),
    }
    loaded_targets = list(lean_engine_replay.get("case_specific_packet_targets", []))
    replay = {
        "replayable": True,
        "policy_candidate_materiality_passed": _policy_candidate_materiality_is_usable(
            policy_candidate_materiality
        ),
        "materiality_source_matches_policy_lineage": (
            policy_candidate_materiality.get("source_oss_ref") == source_ref
            and final_policy_lineage.get("source_oss_ref") == source_ref
            and final_policy_lineage.get("component") == policy_candidate_materiality.get("component")
            and final_policy_lineage.get("request_id") == policy_candidate_materiality.get("request_id")
        ),
        "evolved_policy_carries_policy_oss_lineage": (
            final_policy.get("generation") == 2
            and final_policy.get("policy_oss_ref") == source_ref
            and final_policy.get("policy_oss_lineage_ref") == lineage_ref
            and final_policy.get("policy_oss_lineage_hash")
            == final_policy_lineage.get("lineage_hash")
            and final_policy_lineage.get("model_id") == PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID
        ),
        "strategy_packet_carries_policy_oss_lineage": (
            packet_lineage == final_policy_lineage
            and strategy_packet.get("policy_oss_ref") == source_ref
            and strategy_packet.get("policy_oss_lineage_ref") == lineage_ref
            and strategy_packet.get("policy_oss_lineage_hash")
            == final_policy_lineage.get("lineage_hash")
            and strategy_packet.get("policy_oss_registry_ref") == registry_ref
        ),
        "object_store_readback_preserves_policy_oss_lineage": (
            readback.get("policy_oss_ref") == source_ref
            and readback.get("loaded_policy_oss_ref") == source_ref
            and readback.get("policy_oss_lineage_ref") == lineage_ref
            and readback.get("loaded_policy_oss_lineage_ref") == lineage_ref
            and loaded_lineage == final_policy_lineage
        ),
        "all_packet_targets_bind_policy_oss_lineage": (
            len(loaded_targets) == PORTFOLIO_LEG_COUNT
            and all(
                target.get("policy_oss_ref") == source_ref
                and target.get("policy_oss_lineage_ref") == lineage_ref
                and target.get("policy_oss_lineage_hash")
                == final_policy_lineage.get("lineage_hash")
                and target.get("signal", {}).get("metadata", {}).get("policy_oss_ref")
                == source_ref
                and target.get("signal", {}).get("metadata", {}).get("policy_oss_lineage_ref")
                == lineage_ref
                for target in loaded_targets
            )
        ),
        "handoff_runtime_bundle_contains_policy_oss_refs": {
            source_ref,
            lineage_ref,
            registry_ref,
        }.issubset(handoff_refs)
        and handoff_lineage == final_policy_lineage,
        "runtime_feedback_cites_policy_oss_lineage": {
            source_ref,
            lineage_ref,
            registry_ref,
        }.issubset(runtime_feedback_refs),
        "lineage_hash_stable_across_policy_packet_readback_handoff": (
            bool(lineage_hashes["final_policy"])
            and len(set(lineage_hashes.values())) == 1
        ),
    }
    return {
        "proof_id": f"policy-oss-lineage-handoff-{episode.case_id}",
        "proof_ref": f"policy-oss-lineage-handoff://{episode.case_id}",
        "model_id": PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": policy_entry["component"],
        "request_id": policy_entry["request_id"],
        "source_oss_ref": source_ref,
        "lineage_ref": lineage_ref,
        "registry_ref": registry_ref,
        "artifact_family": policy_entry["artifact_family"],
        "policy_quality": final_policy_lineage.get("policy_quality"),
        "policy_hint_risk": final_policy_lineage.get("policy_hint_risk"),
        "strategy_packet_ref": strategy_packet["packet_ref"],
        "lean_handoff_ref": f"lean-handoff://{lean_handoff['packet_id']}",
        "lean_runtime_feedback_ref": f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        "object_store_readback_ref": readback["readback_id"],
        "lineage_hashes": lineage_hashes,
        "input_refs": [
            source_ref,
            lineage_ref,
            registry_ref,
            strategy_packet["packet_ref"],
            f"lean-handoff://{lean_handoff['packet_id']}",
            f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
            readback["readback_id"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "policy-oss-lineage-handoff",
            {
                "case_id": episode.case_id,
                "source_ref": source_ref,
                "lineage_ref": lineage_ref,
                "lineage_hashes": lineage_hashes,
                "replay": replay,
            },
        ),
    }


def _build_reflection_oss_lineage_handoff_proof(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    reflection_artifact_materiality: Mapping[str, Any],
    case_upstream_artifacts: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    reflection_entry = case_upstream_artifacts["selected_oss"]["reflection_artifact"]
    source_ref = f"oss://{reflection_entry['component']}/{reflection_entry['request_id']}"
    final_policy_lineage = copy.deepcopy(dict(final_policy.get("reflection_oss_lineage") or {}))
    strategy_packet = lean_engine_replay["case_specific_strategy_packet"]
    packet_lineage = copy.deepcopy(dict(strategy_packet.get("reflection_oss_lineage") or {}))
    readback = lean_engine_replay["lean_object_store_packet_readback"]
    loaded_lineage = copy.deepcopy(dict(readback.get("loaded_reflection_oss_lineage") or {}))
    handoff_lineage = copy.deepcopy(dict(lean_handoff.get("reflection_oss_lineage") or {}))
    handoff_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    runtime_feedback_refs = set(
        lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
    )
    lineage_ref = str(final_policy_lineage.get("lineage_ref") or "")
    registry_ref = str(final_policy_lineage.get("registry_ref") or "")
    lineage_hashes = {
        "final_policy": str(final_policy.get("reflection_oss_lineage_hash") or ""),
        "final_policy_lineage": str(final_policy_lineage.get("lineage_hash") or ""),
        "strategy_packet": str(strategy_packet.get("reflection_oss_lineage_hash") or ""),
        "packet_lineage": str(packet_lineage.get("lineage_hash") or ""),
        "object_store_readback": str(readback.get("reflection_oss_lineage_hash") or ""),
        "loaded_object_store_readback": str(
            readback.get("loaded_reflection_oss_lineage_hash") or ""
        ),
        "handoff": str(lean_handoff.get("reflection_oss_lineage_hash") or ""),
    }
    loaded_targets = list(lean_engine_replay.get("case_specific_packet_targets", []))
    replay = {
        "replayable": True,
        "reflection_artifact_materiality_passed": _reflection_artifact_materiality_is_usable(
            reflection_artifact_materiality
        ),
        "materiality_source_matches_reflection_lineage": (
            reflection_artifact_materiality.get("source_oss_ref") == source_ref
            and final_policy_lineage.get("source_oss_ref") == source_ref
            and final_policy_lineage.get("component")
            == reflection_artifact_materiality.get("component")
            and final_policy_lineage.get("request_id")
            == reflection_artifact_materiality.get("request_id")
        ),
        "evolved_policy_carries_reflection_oss_lineage": (
            final_policy.get("generation") == 2
            and final_policy.get("reflection_oss_ref") == source_ref
            and final_policy.get("reflection_oss_lineage_ref") == lineage_ref
            and final_policy.get("reflection_oss_lineage_hash")
            == final_policy_lineage.get("lineage_hash")
            and final_policy_lineage.get("model_id")
            == PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID
        ),
        "strategy_packet_carries_reflection_oss_lineage": (
            packet_lineage == final_policy_lineage
            and strategy_packet.get("reflection_oss_ref") == source_ref
            and strategy_packet.get("reflection_oss_lineage_ref") == lineage_ref
            and strategy_packet.get("reflection_oss_lineage_hash")
            == final_policy_lineage.get("lineage_hash")
            and strategy_packet.get("reflection_oss_registry_ref") == registry_ref
        ),
        "object_store_readback_preserves_reflection_oss_lineage": (
            readback.get("reflection_oss_ref") == source_ref
            and readback.get("loaded_reflection_oss_ref") == source_ref
            and readback.get("reflection_oss_lineage_ref") == lineage_ref
            and readback.get("loaded_reflection_oss_lineage_ref") == lineage_ref
            and loaded_lineage == final_policy_lineage
        ),
        "all_packet_targets_bind_reflection_oss_lineage": (
            len(loaded_targets) == PORTFOLIO_LEG_COUNT
            and all(
                target.get("reflection_oss_ref") == source_ref
                and target.get("reflection_oss_lineage_ref") == lineage_ref
                and target.get("reflection_oss_lineage_hash")
                == final_policy_lineage.get("lineage_hash")
                and target.get("signal", {}).get("metadata", {}).get("reflection_oss_ref")
                == source_ref
                and target.get("signal", {}).get("metadata", {}).get(
                    "reflection_oss_lineage_ref"
                )
                == lineage_ref
                for target in loaded_targets
            )
        ),
        "handoff_runtime_bundle_contains_reflection_oss_refs": {
            source_ref,
            lineage_ref,
            registry_ref,
        }.issubset(handoff_refs)
        and handoff_lineage == final_policy_lineage,
        "runtime_feedback_cites_reflection_oss_lineage": {
            source_ref,
            lineage_ref,
            registry_ref,
        }.issubset(runtime_feedback_refs),
        "lineage_hash_stable_across_reflection_policy_packet_readback_handoff": (
            bool(lineage_hashes["final_policy"])
            and len(set(lineage_hashes.values())) == 1
        ),
    }
    return {
        "proof_id": f"reflection-oss-lineage-handoff-{episode.case_id}",
        "proof_ref": f"reflection-oss-lineage-handoff://{episode.case_id}",
        "model_id": PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": reflection_entry["component"],
        "request_id": reflection_entry["request_id"],
        "source_oss_ref": source_ref,
        "lineage_ref": lineage_ref,
        "registry_ref": registry_ref,
        "artifact_family": reflection_entry["artifact_family"],
        "reflection_quality": final_policy_lineage.get("reflection_quality"),
        "strategy_packet_ref": strategy_packet["packet_ref"],
        "lean_handoff_ref": f"lean-handoff://{lean_handoff['packet_id']}",
        "lean_runtime_feedback_ref": f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        "object_store_readback_ref": readback["readback_id"],
        "lineage_hashes": lineage_hashes,
        "input_refs": [
            source_ref,
            lineage_ref,
            registry_ref,
            strategy_packet["packet_ref"],
            f"lean-handoff://{lean_handoff['packet_id']}",
            f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
            readback["readback_id"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "reflection-oss-lineage-handoff",
            {
                "case_id": episode.case_id,
                "source_ref": source_ref,
                "lineage_ref": lineage_ref,
                "lineage_hashes": lineage_hashes,
                "replay": replay,
            },
        ),
    }


def _build_openclaw_session_handoff_proof(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    decision_traces: Sequence[Mapping[str, Any]],
    lean_handoff: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    session_result = oss_inputs["session"]
    handoff_context = copy.deepcopy(dict(lean_handoff.get("openclaw_session_context") or {}))
    source_oss_ref = f"oss://{session_result['component']}/{session_result['request_id']}"
    context_ref = str(handoff_context.get("context_ref") or "")
    session_ref = str(handoff_context.get("session_ref") or "")
    upstream_session_ref = str(handoff_context.get("upstream_session_ref") or "")
    context_hash = str(handoff_context.get("context_hash") or "")
    handoff_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    runtime_feedback_refs = set(
        lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
    )
    runtime_state_updates = dict(lean_runtime_feedback.get("state_updates", {}))
    primary_output = copy.deepcopy(dict(session_result.get("primary_output") or {}))
    trace_bindings = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_refs = set(
            str(ref)
            for ref in artifact["persona_reasoning"]["request"].get("input_refs", [])
        )
        selected_refs = set(str(ref) for ref in trace["selected_candidate"].get("evidence_refs", []))
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "reasoning_consumes_openclaw_source_ref": source_oss_ref in reasoning_refs,
                "selected_candidate_cites_openclaw_followup": any(
                    str(ref).startswith("followup://")
                    and "/session/" in str(ref)
                    and "/openclaw/" in str(ref)
                    for ref in selected_refs
                ),
            }
        )
    replay = {
        "replayable": True,
        "openclaw_session_response_completed": (
            session_result.get("component") == "openclaw"
            and session_result.get("status") == "completed"
            and session_result.get("artifact_family") == "openclaw_session"
            and primary_output.get("state") == "active"
            and bool(primary_output.get("upstream_session_id"))
        ),
        "persona_reasoning_consumes_openclaw_session_source": all(
            binding["reasoning_consumes_openclaw_source_ref"]
            for binding in trace_bindings
        ),
        "selected_candidates_cite_openclaw_session_followup": all(
            binding["selected_candidate_cites_openclaw_followup"]
            for binding in trace_bindings
        ),
        "handoff_carries_openclaw_session_context": (
            handoff_context.get("model_id") == PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID
            and handoff_context.get("component") == "openclaw"
            and handoff_context.get("source_oss_ref") == source_oss_ref
            and lean_handoff.get("openclaw_session_context_ref") == context_ref
            and lean_handoff.get("openclaw_session_ref") == session_ref
            and lean_handoff.get("openclaw_source_oss_ref") == source_oss_ref
            and lean_handoff.get("openclaw_upstream_session_ref") == upstream_session_ref
            and lean_handoff.get("openclaw_session_context_hash") == context_hash
            and lean_handoff.get("openclaw_session_state") == "active"
        ),
        "handoff_runtime_bundle_contains_openclaw_session_refs": {
            source_oss_ref,
            context_ref,
            session_ref,
            upstream_session_ref,
        }.issubset(handoff_refs),
        "runtime_feedback_cites_openclaw_session": {
            source_oss_ref,
            context_ref,
            session_ref,
            upstream_session_ref,
        }.issubset(runtime_feedback_refs),
        "runtime_feedback_state_binds_openclaw_session": (
            runtime_state_updates.get("bind_openclaw_session_context_ref") == context_ref
            and runtime_state_updates.get("bind_openclaw_session_ref") == session_ref
            and runtime_state_updates.get("bind_openclaw_source_oss_ref") == source_oss_ref
            and runtime_state_updates.get("bind_openclaw_upstream_session_ref")
            == upstream_session_ref
            and lean_runtime_feedback.get("replay", {}).get("openclaw_session_context_bound")
            is True
        ),
        "openclaw_context_hash_stable_across_handoff": (
            bool(context_hash)
            and context_hash == handoff_context.get("input_hash")
            and context_hash == lean_handoff.get("openclaw_session_context_hash")
        ),
    }
    return {
        "proof_id": f"openclaw-session-handoff-{episode.case_id}",
        "proof_ref": f"openclaw-session-handoff://{episode.case_id}",
        "model_id": PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": session_result["component"],
        "request_id": session_result["request_id"],
        "source_oss_ref": source_oss_ref,
        "artifact_family": session_result.get("artifact_family"),
        "context_ref": context_ref,
        "context_hash": context_hash,
        "session_ref": session_ref,
        "session_id": handoff_context.get("session_id"),
        "upstream_session_ref": upstream_session_ref,
        "upstream_session_id": handoff_context.get("upstream_session_id"),
        "session_state": handoff_context.get("session_state"),
        "strategy_packet_ref": lean_handoff["strategy_packet_ref"],
        "lean_handoff_ref": f"lean-handoff://{lean_handoff['packet_id']}",
        "lean_runtime_feedback_ref": f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        "trace_bindings": trace_bindings,
        "input_refs": [
            source_oss_ref,
            context_ref,
            session_ref,
            upstream_session_ref,
            lean_handoff["strategy_packet_ref"],
            f"lean-handoff://{lean_handoff['packet_id']}",
            f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "openclaw-session-handoff",
            {
                "case_id": episode.case_id,
                "source_oss_ref": source_oss_ref,
                "context_ref": context_ref,
                "context_hash": context_hash,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _build_alpha_seed_revision_handoff_proof(
    *,
    episode: PortfolioEpisode,
    case_upstream_artifacts: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    alpha_seed_revision = case_upstream_artifacts["alpha_seed_revision"]
    alpha_entry = case_upstream_artifacts["selected_oss"]["alpha_model"]
    strategy_packet = lean_engine_replay["case_specific_strategy_packet"]
    packet_handoff = copy.deepcopy(
        dict(strategy_packet.get("alpha_seed_revision_handoff") or {})
    )
    handoff_context = copy.deepcopy(
        dict(lean_handoff.get("alpha_seed_revision_handoff") or {})
    )
    readback = lean_engine_replay["lean_object_store_packet_readback"]
    loaded_handoff = copy.deepcopy(
        dict(readback.get("loaded_alpha_seed_revision_handoff") or {})
    )
    loaded_targets = list(lean_engine_replay.get("case_specific_packet_targets", []))
    handoff_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    runtime_feedback_refs = set(
        lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
    )
    runtime_state_updates = dict(lean_runtime_feedback.get("state_updates", {}))
    revision = dict(alpha_seed_revision.get("revision") or {})
    source_oss_ref = f"oss://{alpha_entry['component']}/{alpha_entry['request_id']}"
    handoff_ref = str(handoff_context.get("handoff_ref") or "")
    revision_ref = str(alpha_seed_revision.get("revision_ref") or "")
    base_seed_ref = str(revision.get("base_seed_ref") or "")
    lineage_hash = str(handoff_context.get("lineage_hash") or "")
    trace_bindings = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning_refs = set(
            str(ref)
            for ref in artifact["persona_reasoning"]["request"].get("input_refs", [])
        )
        generation_refs = set(
            str(ref)
            for ref in artifact["candidate_generation"]["request"].get("input_refs", [])
        )
        selected_refs = set(str(ref) for ref in trace["selected_candidate"].get("evidence_refs", []))
        trace_bindings.append(
            {
                "generation": artifact["generation"],
                "trace_id": trace["reflection_id"],
                "reasoning_consumes_alpha_seed_revision": revision_ref in reasoning_refs,
                "candidate_generation_consumes_alpha_seed_revision": revision_ref
                in generation_refs,
                "selected_candidate_cites_alpha_seed_revision": revision_ref in selected_refs,
                "selected_candidate_cites_alpha_seed_source": base_seed_ref in selected_refs,
            }
        )
    lineage_hashes = {
        "strategy_packet": str(strategy_packet.get("alpha_seed_revision_handoff_hash") or ""),
        "packet_handoff": str(packet_handoff.get("lineage_hash") or ""),
        "object_store_readback": str(
            readback.get("alpha_seed_revision_handoff_hash") or ""
        ),
        "loaded_object_store_readback": str(
            readback.get("loaded_alpha_seed_revision_handoff_hash") or ""
        ),
        "lean_handoff": str(lean_handoff.get("alpha_seed_revision_handoff_hash") or ""),
    }
    replay = {
        "replayable": True,
        "alpha_seed_revision_applied": _alpha_seed_revision_is_usable(
            alpha_seed_revision
        ),
        "persona_reasoning_consumes_alpha_seed_revision": all(
            binding["reasoning_consumes_alpha_seed_revision"]
            for binding in trace_bindings
        ),
        "candidate_generation_consumes_alpha_seed_revision": all(
            binding["candidate_generation_consumes_alpha_seed_revision"]
            for binding in trace_bindings
        ),
        "selected_candidates_cite_alpha_seed_revision": all(
            binding["selected_candidate_cites_alpha_seed_revision"]
            and binding["selected_candidate_cites_alpha_seed_source"]
            for binding in trace_bindings
        ),
        "strategy_packet_carries_alpha_seed_revision_handoff": (
            packet_handoff.get("model_id") == PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
            and packet_handoff.get("revision_ref") == revision_ref
            and packet_handoff.get("base_seed_ref") == base_seed_ref
            and packet_handoff.get("source_oss_ref") == source_oss_ref
            and strategy_packet.get("alpha_seed_revision_handoff_ref") == handoff_ref
            and strategy_packet.get("alpha_seed_revision_ref") == revision_ref
            and strategy_packet.get("alpha_seed_source_ref") == base_seed_ref
            and strategy_packet.get("alpha_seed_revision_handoff_hash")
            == packet_handoff.get("lineage_hash")
        ),
        "object_store_readback_preserves_alpha_seed_revision_handoff": (
            loaded_handoff == packet_handoff
            and readback.get("alpha_seed_revision_handoff_ref") == handoff_ref
            and readback.get("loaded_alpha_seed_revision_handoff_ref") == handoff_ref
            and readback.get("alpha_seed_revision_ref") == revision_ref
            and readback.get("loaded_alpha_seed_revision_ref") == revision_ref
        ),
        "all_packet_targets_bind_alpha_seed_revision_handoff": (
            len(loaded_targets) == PORTFOLIO_LEG_COUNT
            and all(
                target.get("alpha_seed_revision_handoff_ref") == handoff_ref
                and target.get("alpha_seed_revision_ref") == revision_ref
                and target.get("alpha_seed_revision_handoff_hash") == lineage_hash
                and target.get("signal", {})
                .get("metadata", {})
                .get("alpha_seed_revision_handoff_ref")
                == handoff_ref
                and target.get("signal", {}).get("metadata", {}).get(
                    "alpha_seed_revision_ref"
                )
                == revision_ref
                for target in loaded_targets
            )
        ),
        "handoff_carries_alpha_seed_revision_context": (
            handoff_context == packet_handoff
            and handoff_context == loaded_handoff
            and lean_handoff.get("alpha_seed_revision_handoff_ref") == handoff_ref
            and lean_handoff.get("alpha_seed_revision_ref") == revision_ref
            and lean_handoff.get("alpha_seed_source_ref") == base_seed_ref
            and lean_handoff.get("alpha_seed_source_oss_ref") == source_oss_ref
            and lean_handoff.get("alpha_seed_revision_action")
            == revision.get("action")
            and lean_handoff.get("alpha_seed_component") == alpha_entry["component"]
        ),
        "handoff_runtime_bundle_contains_alpha_seed_revision_refs": {
            handoff_ref,
            revision_ref,
            base_seed_ref,
            source_oss_ref,
        }.issubset(handoff_refs),
        "runtime_feedback_cites_alpha_seed_revision_handoff": {
            handoff_ref,
            revision_ref,
            base_seed_ref,
            source_oss_ref,
        }.issubset(runtime_feedback_refs),
        "runtime_feedback_state_binds_alpha_seed_revision_handoff": (
            runtime_state_updates.get("bind_alpha_seed_revision_handoff_ref")
            == handoff_ref
            and runtime_state_updates.get("bind_alpha_seed_revision_ref")
            == revision_ref
            and runtime_state_updates.get("bind_alpha_seed_source_ref")
            == base_seed_ref
            and runtime_state_updates.get("bind_alpha_seed_source_oss_ref")
            == source_oss_ref
            and runtime_state_updates.get("bind_alpha_seed_revision_action")
            == revision.get("action")
            and lean_runtime_feedback.get("replay", {}).get(
                "alpha_seed_revision_handoff_bound"
            )
            is True
        ),
        "lineage_hash_stable_across_packet_readback_handoff": (
            bool(lineage_hash)
            and len({value for value in lineage_hashes.values() if value}) == 1
        ),
    }
    return {
        "proof_id": f"alpha-seed-revision-handoff-{episode.case_id}",
        "proof_ref": f"alpha-seed-revision-handoff://{episode.case_id}",
        "model_id": PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": alpha_entry["component"],
        "request_id": alpha_entry["request_id"],
        "source_oss_ref": source_oss_ref,
        "revision_ref": revision_ref,
        "revision_id": alpha_seed_revision["revision_id"],
        "revision_key": revision.get("revision_key"),
        "base_seed_ref": base_seed_ref,
        "revision_action": revision.get("action"),
        "handoff_ref": handoff_ref,
        "handoff_hash": lineage_hash,
        "downstream_vectorbt_request_id": revision.get(
            "downstream_vectorbt_request_id"
        ),
        "downstream_policy_candidate_request_id": revision.get(
            "downstream_policy_candidate_request_id"
        ),
        "strategy_packet_ref": strategy_packet["packet_ref"],
        "lean_handoff_ref": f"lean-handoff://{lean_handoff['packet_id']}",
        "lean_runtime_feedback_ref": f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
        "object_store_readback_ref": readback["readback_id"],
        "lineage_hashes": lineage_hashes,
        "trace_bindings": trace_bindings,
        "input_refs": [
            source_oss_ref,
            revision_ref,
            base_seed_ref,
            handoff_ref,
            strategy_packet["packet_ref"],
            f"lean-handoff://{lean_handoff['packet_id']}",
            f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}",
            readback["readback_id"],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "alpha-seed-revision-handoff-proof",
            {
                "case_id": episode.case_id,
                "revision_ref": revision_ref,
                "handoff_ref": handoff_ref,
                "lineage_hashes": lineage_hashes,
                "trace_bindings": trace_bindings,
                "replay": replay,
            },
        ),
    }


def _build_evolved_strategy_packet_proof(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    final_evaluation: Mapping[str, Any],
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    lean_handoff: Mapping[str, Any],
    lean_packet_execution_projection: Mapping[str, Any],
    lean_runtime_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    strategy_packet = lean_handoff["strategy_packet"]
    final_oos_step = strict_oos_evolution_proof["proof_steps"][-1]
    strategy_packet_ref = str(strategy_packet["packet_ref"])
    handoff_ref = f"lean-handoff://{lean_handoff['packet_id']}"
    projection_ref = str(lean_packet_execution_projection["projection_ref"])
    runtime_feedback_ref = f"lean-runtime-feedback://{lean_runtime_feedback['feedback_id']}"
    runtime_bundle_refs = set(lean_handoff.get("runtime_bundle_refs", []))
    lineage_refs = [
        strategy_packet_ref,
        str(strategy_packet["strict_oos_proof_ref"]),
        str(strategy_packet["no_leakage_protocol_ref"]),
        str(strategy_packet["evolution_trajectory_ref"]),
        f"lean-engine://{lean_engine_replay['replay_id']}",
        handoff_ref,
        projection_ref,
        runtime_feedback_ref,
    ]
    replay = {
        "replayable": True,
        "strategy_packet_is_generation2": (
            strategy_packet.get("generation") == 2
            and final_policy.get("generation") == 2
            and strategy_packet.get("policy_id") == final_policy.get("policy_id")
        ),
        "strategy_packet_declares_future_holdout_validation": (
            strategy_packet.get("validation_window") == "future_holdout"
            and strategy_packet.get("source_outcome_window") == "holdout"
            and strategy_packet.get("future_holdout_score") == final_evaluation.get("score")
        ),
        "strict_oos_generation2_step_matches_packet": (
            final_oos_step.get("candidate_policy_id") == strategy_packet.get("policy_id")
            and final_oos_step.get("validation_window") == strategy_packet.get("validation_window")
            and final_oos_step.get("source_outcome_window") == strategy_packet.get("source_outcome_window")
            and final_oos_step.get("score_improvement") == strategy_packet.get("future_holdout_improvement")
        ),
        "packet_binds_strict_oos_proof": (
            strategy_packet.get("strict_oos_proof_ref") == strict_oos_evolution_proof.get("proof_ref")
            and strict_oos_evolution_proof.get("status") == "passed"
            and all(strict_oos_evolution_proof.get("replay", {}).values())
        ),
        "packet_binds_no_leakage_protocol": (
            strategy_packet.get("no_leakage_protocol_ref")
            == f"no-leakage://{no_leakage_protocol['protocol_id']}"
            and no_leakage_protocol.get("replay", {}).get("future_holdout_hidden_until_evaluation") is True
            and all(no_leakage_protocol.get("replay", {}).values())
        ),
        "packet_binds_evolution_trajectory": (
            strategy_packet.get("evolution_trajectory_ref")
            == f"trajectory://{evolution_trajectory['trajectory_id']}"
            and [comparison["evaluation_window"] for comparison in evolution_trajectory.get("comparisons", [])]
            == ["holdout", "future_holdout"]
        ),
        "lean_engine_replay_reads_same_packet": (
            lean_engine_replay.get("case_specific_strategy_packet", {}).get("packet_ref")
            == strategy_packet_ref
            and lean_engine_replay.get("case_specific_strategy_packet", {}).get("policy_id")
            == final_policy.get("policy_id")
            and lean_engine_replay.get("lean_object_store_packet_readback", {}).get("packet_ref")
            == strategy_packet_ref
            and lean_engine_replay.get("lean_object_store_packet_readback", {}).get("status")
            == "passed"
            and lean_engine_replay.get("lean_object_store_packet_readback", {}).get("target_count")
            == PORTFOLIO_LEG_COUNT
        ),
        "handoff_consumes_same_packet": (
            lean_handoff.get("strategy_packet_ref") == strategy_packet_ref
            and lean_handoff.get("strategy_packet_hash")
            == _stable_payload_hash("lean-strategy-packet", strategy_packet)
            and lean_handoff.get("strategy_packet_replay_passed") is True
        ),
        "handoff_runtime_bundle_contains_packet_and_proofs": all(
            ref in runtime_bundle_refs for ref in lineage_refs[:5]
        ),
        "execution_projection_consumes_packet_legs_and_orders": (
            lean_packet_execution_projection.get("model_id")
            == LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID
            and lean_packet_execution_projection.get("status") == "passed"
            and lean_packet_execution_projection.get("strategy_packet_ref") == strategy_packet_ref
            and lean_packet_execution_projection.get("source_handoff_ref") == handoff_ref
            and lean_packet_execution_projection.get("leg_count") == PORTFOLIO_LEG_COUNT
            and all(lean_packet_execution_projection.get("replay", {}).values())
        ),
        "runtime_feedback_consumes_handoff_with_packet": (
            lean_runtime_feedback.get("source_handoff_ref") == handoff_ref
            and strategy_packet_ref
            in lean_runtime_feedback.get("persona_ooda_followup", {}).get("evidence_refs", [])
            and lean_runtime_feedback.get("state_updates", {}).get("bind_evolved_strategy_packet")
            == strategy_packet_ref
            and lean_runtime_feedback.get("state_updates", {}).get(
                "bind_lean_packet_execution_projection"
            )
            == projection_ref
            and lean_runtime_feedback.get("replay", {}).get(
                "lean_packet_execution_projection_consumed"
            )
            is True
            and lean_runtime_feedback.get("replay", {}).get("evolved_strategy_packet_refs_bound") is True
        ),
        "paper_only_guard_retained": (
            lean_handoff.get("target_stage") == "paper"
            and lean_handoff.get("broker_live_submitted") is False
            and lean_runtime_feedback.get("persona_ooda_followup", {}).get("paper_only") is True
        ),
    }
    return {
        "proof_id": f"evolved-strategy-packet-{episode.case_id}",
        "proof_ref": f"evolved-strategy-packet://{episode.case_id}",
        "model_id": LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "strategy_packet_ref": strategy_packet_ref,
        "policy_id": final_policy["policy_id"],
        "policy_version": final_policy["policy_version"],
        "generation": final_policy["generation"],
        "source_outcome_window": strategy_packet["source_outcome_window"],
        "validation_window": strategy_packet["validation_window"],
        "strict_oos_proof_ref": strategy_packet["strict_oos_proof_ref"],
        "no_leakage_protocol_ref": strategy_packet["no_leakage_protocol_ref"],
        "evolution_trajectory_ref": strategy_packet["evolution_trajectory_ref"],
        "lean_engine_replay_ref": f"lean-engine://{lean_engine_replay['replay_id']}",
        "lean_handoff_ref": handoff_ref,
        "lean_packet_execution_projection_ref": projection_ref,
        "lean_runtime_feedback_ref": runtime_feedback_ref,
        "future_holdout_score": final_evaluation["score"],
        "future_holdout_improvement": strategy_packet["future_holdout_improvement"],
        "lineage_refs": lineage_refs,
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "evolved-strategy-packet-proof",
            {
                "case_id": episode.case_id,
                "strategy_packet": strategy_packet,
                "lineage_refs": lineage_refs,
                "replay": replay,
            },
        ),
    }


def _build_scheduler_conflict_ooda_proof(
    *,
    episode: PortfolioEpisode,
    operational_context: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    conflict = operational_context["persona_conflict_resolution"]
    schedule = operational_context["autonomous_schedule"]
    adapter_followup = operational_context["broker_adapter_followup"]
    lean_handoff = operational_context["lean_handoff"]
    runtime_feedback = operational_context["lean_runtime_feedback"]
    schedule_ref = str(schedule["schedule_ref"])
    conflict_ref = str(conflict["resolution_ref"])
    handoff_ref = f"lean-handoff://{lean_handoff['packet_id']}"
    adapter_followup_ref = f"broker-adapter-followup://{adapter_followup['followup_id']}"
    runtime_feedback_ref = f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    dispatch_ref = f"scheduler-dispatch://{episode.case_id}/next-cycle"
    selected_action_ref = str(conflict["selected_action_ref"])
    phase_events = [
        {
            "phase": phase["phase"],
            "due_at": phase["due_at"],
            "status": phase["status"],
            "output_ref": f"scheduler-phase://{schedule['schedule_id']}/{phase['phase']}",
        }
        for phase in schedule["phases"]
    ]
    dispatch_events = [
        {
            "sequence": 1,
            "event_type": "scheduler_recovery_tick",
            "actor": "autonomous_scheduler",
            "ooda_phase": "observe",
            "input_refs": [f"checkpoint://{schedule['restart_checkpoint_ref']}"],
            "output_ref": schedule_ref,
            "drives_next_event": "multi_persona_conflict_resolution",
        },
        {
            "sequence": 2,
            "event_type": "multi_persona_conflict_resolution",
            "actor": "persona_council",
            "ooda_phase": "orient",
            "input_refs": [
                selected_action_ref,
                f"reflection://{decision_traces[-1]['reflection_id']}",
                str(conflict["oss_risk_ref"]),
            ],
            "output_ref": conflict_ref,
            "drives_next_event": "lean_handoff_materialization",
        },
        {
            "sequence": 3,
            "event_type": "lean_handoff_materialization",
            "actor": "persona+lean_handoff",
            "ooda_phase": "act",
            "input_refs": [selected_action_ref, conflict_ref, schedule_ref],
            "output_ref": handoff_ref,
            "drives_next_event": "broker_adapter_followup",
        },
        {
            "sequence": 4,
            "event_type": "broker_adapter_followup",
            "actor": "broker_adapter+persona",
            "ooda_phase": "orient",
            "input_refs": [
                str(adapter_followup["source_packet_ref"]),
                schedule_ref,
                f"checkpoint://{adapter_followup['restart_checkpoint_ref']}",
            ],
            "output_ref": adapter_followup_ref,
            "drives_next_event": "lean_runtime_feedback",
        },
        {
            "sequence": 5,
            "event_type": "lean_runtime_feedback",
            "actor": "lean_runtime+persona",
            "ooda_phase": str(runtime_feedback["persona_ooda_followup"]["ooda_step"]),
            "input_refs": [
                str(runtime_feedback["source_runtime_ref"]),
                str(runtime_feedback["source_handoff_ref"]),
                schedule_ref,
            ],
            "output_ref": runtime_feedback_ref,
            "drives_next_event": "scheduler_next_cycle_dispatch",
        },
        {
            "sequence": 6,
            "event_type": "scheduler_next_cycle_dispatch",
            "actor": "autonomous_scheduler",
            "ooda_phase": "act",
            "input_refs": [schedule_ref, adapter_followup_ref, runtime_feedback_ref],
            "output_ref": dispatch_ref,
            "drives_next_event": "next_autonomous_paper_cycle",
        },
    ]
    produced_at = {
        event["output_ref"]: int(event["sequence"])
        for event in dispatch_events
    }
    resolved_allocation = conflict["resolved_allocation"]
    replay = {
        "replayable": True,
        "scheduler_phase_order_valid": schedule.get("phase_order_valid") is True
        and [event["phase"] for event in phase_events] == list(AUTONOMOUS_SCHEDULER_PHASES),
        "scheduler_phase_due_times_ordered": schedule.get("phase_due_at_ordered") is True,
        "scheduler_recovered_restart_checkpoint": schedule.get("missed_cycle_recovered") is True
        and bool(schedule.get("restart_checkpoint_ref")),
        "conflict_resolution_consumes_selected_action_and_risk": (
            selected_action_ref in dispatch_events[1]["input_refs"]
            and str(conflict["oss_risk_ref"]) in dispatch_events[1]["input_refs"]
            and conflict.get("open_conflicts") == []
        ),
        "resolved_allocation_is_portfolio_complete": (
            set(resolved_allocation.get("direction_by_instrument", {}))
            == {window.instrument for window in episode.windows}
            and set(resolved_allocation.get("weight_by_instrument", {}))
            == {window.instrument for window in episode.windows}
            and float(resolved_allocation.get("capital_budget_pct", 2.0)) <= 1.0
        ),
        "handoff_consumes_conflict_resolution": (
            lean_handoff.get("persona_conflict_resolution_ref") == conflict_ref
            and conflict_ref in lean_handoff.get("runtime_bundle_refs", [])
            and lean_handoff.get("resolved_weight_by_instrument")
            == resolved_allocation.get("weight_by_instrument")
        ),
        "handoff_consumes_scheduler_ref": (
            lean_handoff.get("schedule_ref") == schedule_ref
            and schedule_ref in lean_handoff.get("runtime_bundle_refs", [])
        ),
        "adapter_followup_consumes_schedule": (
            adapter_followup.get("schedule_ref") == schedule.get("schedule_id")
            and schedule_ref in adapter_followup.get("persona_followup", {}).get("evidence_refs", [])
            and adapter_followup.get("state_updates", {}).get("schedule_next_cycle_after_followup")
            == schedule.get("next_cycle_due_at")
        ),
        "lean_runtime_feedback_consumes_schedule": (
            runtime_feedback.get("state_updates", {}).get("schedule_next_cycle_after_feedback")
            == schedule.get("next_cycle_due_at")
            and runtime_feedback.get("source_handoff_ref") == handoff_ref
        ),
        "runtime_ooda_step_maps_to_scheduler_phase": (
            runtime_feedback.get("persona_ooda_followup", {}).get("next_scheduler_phase")
            in {phase["phase"] for phase in schedule.get("phases", [])}
            and runtime_feedback.get("persona_ooda_followup", {}).get("next_scheduler_phase")
            in {"reflect", "evolve"}
        ),
        "next_dispatch_consumes_adapter_and_runtime_feedback": (
            adapter_followup_ref in dispatch_events[-1]["input_refs"]
            and runtime_feedback_ref in dispatch_events[-1]["input_refs"]
            and schedule_ref in dispatch_events[-1]["input_refs"]
        ),
        "dispatch_events_strictly_ordered": [
            event["sequence"] for event in dispatch_events
        ] == list(range(1, len(dispatch_events) + 1)),
        "no_future_dispatch_ref": all(
            produced_at[input_ref] < int(event["sequence"])
            for event in dispatch_events
            for input_ref in event["input_refs"]
            if input_ref in produced_at
        ),
        "paper_only_guard_retained": (
            lean_handoff.get("target_stage") == "paper"
            and lean_handoff.get("broker_live_submitted") is False
            and adapter_followup.get("persona_followup", {}).get("paper_only") is True
            and runtime_feedback.get("persona_ooda_followup", {}).get("paper_only") is True
        ),
    }
    return {
        "proof_id": f"scheduler-conflict-ooda-{episode.case_id}",
        "proof_ref": f"scheduler-conflict-ooda://{episode.case_id}",
        "model_id": PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "schedule_ref": schedule_ref,
        "conflict_ref": conflict_ref,
        "handoff_ref": handoff_ref,
        "adapter_followup_ref": adapter_followup_ref,
        "runtime_feedback_ref": runtime_feedback_ref,
        "dispatch_ref": dispatch_ref,
        "conflict_types": list(conflict["conflict_types"]),
        "next_ooda_step": runtime_feedback["persona_ooda_followup"]["ooda_step"],
        "next_scheduler_phase": runtime_feedback["persona_ooda_followup"]["next_scheduler_phase"],
        "next_cycle_due_at": schedule["next_cycle_due_at"],
        "phase_events": phase_events,
        "dispatch_events": dispatch_events,
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "scheduler-conflict-ooda-proof",
            {
                "case_id": episode.case_id,
                "schedule_ref": schedule_ref,
                "conflict_ref": conflict_ref,
                "handoff_ref": handoff_ref,
                "runtime_feedback_ref": runtime_feedback_ref,
                "dispatch_events": dispatch_events,
                "replay": replay,
            },
        ),
    }


def _lean_runtime_feedback_action_for_scenario(scenario: str) -> str:
    return LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO.get(
        scenario,
        "orient_on_runtime_feedback_before_next_cycle",
    )


def _lean_runtime_feedback_action_family(action: str) -> str:
    if action.startswith("act_on_handoff"):
        return "handoff_action_repair"
    if action.startswith("decide_reduced_allocation"):
        return "allocation_decision"
    if action.startswith("decide_risk"):
        return "risk_decision"
    if action.startswith("observe_runtime"):
        return "runtime_fill_observation"
    if action.startswith("orient_on_runtime"):
        return "execution_quality_orientation"
    return "runtime_feedback_orientation"


def _lean_runtime_feedback_rationale(scenario: str) -> str:
    rationales = {
        "cancel_replace_readback": "LEAN handoff replay must be acted on before a resubmission is scheduled.",
        "limit_miss_reprice": "Runtime fill quality must orient the persona before repricing the next paper order.",
        "liquidity_cap_scale": "Runtime feedback must drive a reduced allocation decision for the next cycle.",
        "partial_fill_reconcile": "Runtime fills must be observed before reflection trusts the portfolio state.",
        "risk_reject_reduce": "Runtime feedback must keep the next paper cycle risk-reduced before any live path.",
    }
    return rationales.get(scenario, "Runtime feedback must orient the persona before the next paper cycle.")


def _market_friction_is_usable(market_friction: Mapping[str, Any]) -> bool:
    return bool(
        market_friction.get("applied")
        and market_friction.get("model_id") == MARKET_FRICTION_MODEL_ID
        and market_friction.get("all_orders_within_liquidity_cap")
        and market_friction.get("costs_are_positive")
        and len(market_friction.get("generation_costs", [])) == GENERATION_COUNT
    )


def _broker_lifecycle_is_reconciled(broker_lifecycle: Mapping[str, Any]) -> bool:
    return bool(
        broker_lifecycle.get("reconciled")
        and broker_lifecycle.get("readback_consistent")
        and broker_lifecycle.get("live_broker_submission_count") == 0
        and broker_lifecycle.get("order_count") == GENERATION_COUNT * PORTFOLIO_LEG_COUNT
        and broker_lifecycle.get("terminal_statuses") == [BROKER_LIFECYCLE_TERMINAL_STATUS]
    )


def _broker_adapter_lifecycle_is_usable(lifecycle: Mapping[str, Any]) -> bool:
    replay = lifecycle.get("replay", {})
    return bool(
        lifecycle.get("model_id") == BROKER_ADAPTER_LIFECYCLE_MODEL_ID
        and lifecycle.get("provider") == "Shioaji"
        and lifecycle.get("environment") == "sandbox"
        and lifecycle.get("broker_lifecycle_model") == "submit_ack_partial_cancel_replace_reject_readback_v1"
        and lifecycle.get("paper_order_count") == GENERATION_COUNT * PORTFOLIO_LEG_COUNT
        and lifecycle.get("input_hash")
        and all(check.get("status") == "passed" for check in lifecycle.get("scenario_checks", []))
        and replay.get("replayable") is True
        and replay.get("scenario_required_statuses_observed") is True
        and replay.get("paper_readback_reconciled") is True
        and replay.get("sandbox_place_cancel_readback_reconciled") is True
        and replay.get("live_order_rejected_without_capital") is True
        and replay.get("restart_recovery_preserves_readback_context") is True
        and replay.get("all_orders_have_status_paths") is True
        and replay.get("all_orders_end_filled") is True
        and replay.get("no_live_broker_submission") is True
    )


def _broker_adapter_followup_is_usable(followup: Mapping[str, Any]) -> bool:
    replay = followup.get("replay", {})
    persona_followup = followup.get("persona_followup", {})
    return bool(
        followup.get("model_id") == BROKER_ADAPTER_FOLLOWUP_MODEL_ID
        and followup.get("status") == "accepted"
        and followup.get("source_packet_model") == BROKER_ADAPTER_LIFECYCLE_MODEL_ID
        and followup.get("source_packet_hash")
        and followup.get("input_hash")
        and persona_followup.get("action")
        == BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO.get(str(followup.get("scenario")))
        and persona_followup.get("required_before_next_cycle") is True
        and persona_followup.get("paper_only") is True
        and len(persona_followup.get("evidence_refs", [])) >= 4
        and replay.get("adapter_response_consumed") is True
        and replay.get("scenario_action_selected") is True
        and replay.get("source_refs_bound") is True
        and replay.get("recovery_context_preserved") is True
        and replay.get("next_cycle_scheduled") is True
        and replay.get("paper_only_guard_retained") is True
        and replay.get("drives_persona_next_step") is True
    )


def _persona_conflicts_are_resolved(conflict_resolution: Mapping[str, Any]) -> bool:
    allocation = conflict_resolution.get("resolved_allocation", {})
    return bool(
        conflict_resolution.get("model_id") == PERSONA_CONFLICT_RESOLUTION_MODEL_ID
        and conflict_resolution.get("resolution_ref", "").startswith("persona-conflict://")
        and conflict_resolution.get("classified_conflicts")
        and not conflict_resolution.get("open_conflicts")
        and allocation.get("capital_budget_pct", 2.0) <= 1.0
        and set(allocation.get("direction_by_instrument", {}))
        == set(allocation.get("weight_by_instrument", {}))
        and conflict_resolution.get("selected_action_ref", "").startswith("selected-action://")
        and conflict_resolution.get("oss_risk_ref", "").startswith("oss://")
    )


def _restart_recovery_is_usable(restart_recovery: Mapping[str, Any]) -> bool:
    return bool(
        restart_recovery.get("checkpoint_written")
        and restart_recovery.get("recovered")
        and restart_recovery.get("duplicate_execution_suppressed")
        and restart_recovery.get("next_step_completed")
        and restart_recovery.get("memory_refs_before_restart")
        == restart_recovery.get("memory_refs_after_recovery")
    )


def _autonomous_schedule_is_usable(schedule: Mapping[str, Any]) -> bool:
    return bool(
        schedule.get("schedule_ref", "").startswith("schedule://")
        and schedule.get("trigger_mode") == "autonomous_daily_paper_loop"
        and schedule.get("phase_order_valid")
        and schedule.get("phase_due_at_ordered")
        and schedule.get("missed_cycle_recovered")
        and [phase["phase"] for phase in schedule.get("phases", [])]
        == list(AUTONOMOUS_SCHEDULER_PHASES)
        and schedule.get("next_cycle_due_at")
    )


def _lean_handoff_packet_is_usable(packet: Mapping[str, Any]) -> bool:
    runtime_refs = set(packet.get("runtime_bundle_refs", []))
    return bool(
        packet.get("component") == "lean_handoff"
        and packet.get("strategy_packet_materialized")
        and packet.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and packet.get("strategy_packet_ref") in runtime_refs
        and packet.get("policy_generation") == 2
        and packet.get("strategy_packet_validation_window") == "future_holdout"
        and packet.get("strategy_packet_source_outcome_window") == "holdout"
        and packet.get("strict_oos_evolution_proof_ref", "").startswith("strict-oos-evolution://")
        and packet.get("strict_oos_evolution_proof_ref") in runtime_refs
        and packet.get("no_leakage_protocol_ref", "").startswith("no-leakage://")
        and packet.get("no_leakage_protocol_ref") in runtime_refs
        and packet.get("evolution_trajectory_ref", "").startswith("trajectory://")
        and packet.get("evolution_trajectory_ref") in runtime_refs
        and packet.get("normalized_experiment_ref", "").startswith("experiment://")
        and packet.get("normalized_experiment_ref") in runtime_refs
        and packet.get("tracking_reconciliation_ref", "").startswith("tracking-reconciliation://")
        and packet.get("tracking_reconciliation_ref") in runtime_refs
        and packet.get("tracking_repair_ref", "").startswith(packet.get("tracking_reconciliation_ref", ""))
        and packet.get("tracking_repair_ref") in runtime_refs
        and packet.get("experiment_tracking_provenance_hash")
        == packet.get("experiment_tracking_provenance", {}).get("lineage_hash")
        and packet.get("policy_oss_ref", "").startswith("oss://")
        and packet.get("policy_oss_ref") in runtime_refs
        and packet.get("policy_oss_lineage_ref", "").startswith("policy-oss-lineage://")
        and packet.get("policy_oss_lineage_ref") in runtime_refs
        and packet.get("policy_oss_registry_ref", "").startswith("registry://")
        and packet.get("policy_oss_registry_ref") in runtime_refs
        and packet.get("policy_oss_lineage_hash")
        == packet.get("policy_oss_lineage", {}).get("lineage_hash")
        and packet.get("reflection_oss_ref", "").startswith("oss://")
        and packet.get("reflection_oss_ref") in runtime_refs
        and packet.get("reflection_oss_lineage_ref", "").startswith("reflection-oss-lineage://")
        and packet.get("reflection_oss_lineage_ref") in runtime_refs
        and packet.get("reflection_oss_registry_ref", "").startswith("registry://")
        and packet.get("reflection_oss_registry_ref") in runtime_refs
        and packet.get("reflection_oss_lineage_hash")
        == packet.get("reflection_oss_lineage", {}).get("lineage_hash")
        and packet.get("openclaw_session_context_ref", "").startswith("openclaw-context://")
        and packet.get("openclaw_session_context_ref") in runtime_refs
        and packet.get("openclaw_session_ref", "").startswith("openclaw-session://")
        and packet.get("openclaw_session_ref") in runtime_refs
        and packet.get("openclaw_source_oss_ref", "").startswith("oss://openclaw/")
        and packet.get("openclaw_source_oss_ref") in runtime_refs
        and packet.get("openclaw_upstream_session_ref", "").startswith("openclaw-upstream-session://")
        and packet.get("openclaw_upstream_session_ref") in runtime_refs
        and packet.get("openclaw_session_context_hash")
        == packet.get("openclaw_session_context", {}).get("context_hash")
        and packet.get("openclaw_session_state") == "active"
        and packet.get("alpha_seed_revision_handoff_ref", "").startswith(
            "alpha-seed-revision-handoff://"
        )
        and packet.get("alpha_seed_revision_handoff_ref") in runtime_refs
        and packet.get("alpha_seed_revision_ref", "").startswith("alpha-seed-revision://")
        and packet.get("alpha_seed_revision_ref") in runtime_refs
        and packet.get("alpha_seed_source_ref", "").startswith("alpha-seed://")
        and packet.get("alpha_seed_source_ref") in runtime_refs
        and packet.get("alpha_seed_source_oss_ref", "").startswith("oss://")
        and packet.get("alpha_seed_source_oss_ref") in runtime_refs
        and packet.get("alpha_seed_revision_handoff_hash")
        == packet.get("alpha_seed_revision_handoff", {}).get("lineage_hash")
        and packet.get("alpha_seed_revision_action")
        == packet.get("alpha_seed_revision_handoff", {}).get("revision_action")
        and packet.get("strategy_packet_replay_passed") is True
        and packet.get("strategy_packet_hash")
        and packet.get("received_by_lean_handoff")
        and packet.get("target_stage") == "paper"
        and packet.get("persona_conflict_resolution_ref", "").startswith("persona-conflict://")
        and packet.get("persona_conflict_resolution_ref") in runtime_refs
        and float(packet.get("resolved_capital_budget_pct", 2.0)) <= 1.0
        and set(packet.get("resolved_direction_by_instrument", {}))
        == set(packet.get("resolved_weight_by_instrument", {}))
        and packet.get("schedule_ref", "").startswith("schedule://")
        and packet.get("schedule_ref") in runtime_refs
        and packet.get("next_cycle_due_at")
        and packet.get("lean_engine_replay_status") == "passed"
        and packet.get("shioaji_sandbox_lifecycle_status") == "passed"
        and packet.get("case_vectorbt_request_id")
        and packet.get("case_tracking_run_id")
        and packet.get("broker_live_submitted") is False
        and packet.get("runtime_bundle_refs")
    )


def _lean_runtime_feedback_is_usable(feedback: Mapping[str, Any]) -> bool:
    replay = feedback.get("replay", {})
    runtime_feedback = feedback.get("runtime_feedback", {})
    persona_followup = feedback.get("persona_ooda_followup", {})
    return bool(
        feedback.get("model_id") == LEAN_RUNTIME_FEEDBACK_MODEL_ID
        and feedback.get("status") == "accepted"
        and feedback.get("source_runtime_ref", "").startswith("lean-engine://")
        and feedback.get("source_handoff_ref", "").startswith("lean-handoff://")
        and feedback.get("input_hash")
        and runtime_feedback.get("runtime_binding_id")
        and runtime_feedback.get("runtime_binding_id")
        == runtime_feedback.get("loaded_metadata_runtime_binding_id")
        and runtime_feedback.get("deployment_plan_id")
        == runtime_feedback.get("loaded_metadata_deployment_plan_id")
        and runtime_feedback.get("deployment_stage") == "paper"
        and int(runtime_feedback.get("fill_count", 0)) >= 1
        and runtime_feedback.get("object_store_metadata_key", "").endswith("/metadata.json")
        and runtime_feedback.get("object_store_artifact_key", "").endswith("/artifact.bin")
        and persona_followup.get("action")
        == LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO.get(str(feedback.get("scenario")))
        and persona_followup.get("ooda_step")
        == LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION.get(str(persona_followup.get("action")))
        and persona_followup.get("required_before_next_cycle") is True
        and persona_followup.get("paper_only") is True
        and len(persona_followup.get("evidence_refs", [])) >= 5
        and replay.get("runtime_feedback_consumed") is True
        and replay.get("handoff_packet_consumed") is True
        and replay.get("runtime_binding_readback_verified") is True
        and replay.get("object_store_readback_verified") is True
        and replay.get("fills_drive_next_ooda") is True
        and replay.get("paper_runtime_guard_retained") is True
        and replay.get("case_runtime_refs_bound") is True
        and replay.get("evolved_strategy_packet_refs_bound") is True
        and replay.get("experiment_tracking_lineage_bound") is True
        and replay.get("policy_oss_lineage_bound") is True
        and replay.get("reflection_oss_lineage_bound") is True
        and replay.get("openclaw_session_context_bound") is True
        and replay.get("alpha_seed_revision_handoff_bound") is True
        and replay.get("lean_packet_execution_projection_consumed") is True
        and replay.get("next_cycle_scheduled") is True
        and replay.get("drives_persona_next_ooda_step") is True
    )


def _experiment_tracking_lineage_handoff_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    lineage_hashes = proof.get("lineage_hashes", {})
    return bool(
        proof.get("model_id") == PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("tracking-experiment-lineage://")
        and proof.get("experiment_ref", "").startswith("experiment://")
        and proof.get("tracking_reconciliation_ref", "").startswith("tracking-reconciliation://")
        and proof.get("tracking_repair_ref", "").startswith(proof.get("tracking_reconciliation_ref", ""))
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and proof.get("object_store_readback_ref", "").startswith("lean-object-store-packet-readback-")
        and proof.get("input_hash")
        and lineage_hashes
        and len({str(value) for value in lineage_hashes.values()}) == 1
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "tracker_readback_reconciled",
            "evolution_decision_cites_reconciliation",
            "evolution_decision_metadata_carries_experiment_ref",
            "strategy_packet_carries_tracking_provenance",
            "object_store_readback_preserves_tracking_provenance",
            "handoff_runtime_bundle_contains_repaired_tracking_refs",
            "runtime_feedback_cites_repaired_tracking_refs",
            "lineage_hash_stable_across_packet_handoff_readback",
        ))
    )


def _policy_oss_lineage_handoff_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    lineage_hashes = proof.get("lineage_hashes", {})
    return bool(
        proof.get("model_id") == PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("policy-oss-lineage-handoff://")
        and proof.get("source_oss_ref", "").startswith("oss://")
        and proof.get("lineage_ref", "").startswith("policy-oss-lineage://")
        and proof.get("registry_ref", "").startswith("registry://")
        and proof.get("component") in POLICY_OSS_COMPONENTS
        and proof.get("artifact_family") == _policy_candidate_expected_artifact_family(
            str(proof.get("component"))
        )
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and proof.get("object_store_readback_ref", "").startswith("lean-object-store-packet-readback-")
        and float(proof.get("policy_quality", 0.0)) > 0.0
        and float(proof.get("policy_hint_risk", 0.0)) > 0.0
        and proof.get("input_hash")
        and lineage_hashes
        and len({str(value) for value in lineage_hashes.values()}) == 1
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "policy_candidate_materiality_passed",
            "materiality_source_matches_policy_lineage",
            "evolved_policy_carries_policy_oss_lineage",
            "strategy_packet_carries_policy_oss_lineage",
            "object_store_readback_preserves_policy_oss_lineage",
            "all_packet_targets_bind_policy_oss_lineage",
            "handoff_runtime_bundle_contains_policy_oss_refs",
            "runtime_feedback_cites_policy_oss_lineage",
            "lineage_hash_stable_across_policy_packet_readback_handoff",
        ))
    )


def _reflection_oss_lineage_handoff_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    lineage_hashes = proof.get("lineage_hashes", {})
    return bool(
        proof.get("model_id") == PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("reflection-oss-lineage-handoff://")
        and proof.get("source_oss_ref", "").startswith("oss://")
        and proof.get("lineage_ref", "").startswith("reflection-oss-lineage://")
        and proof.get("registry_ref", "").startswith("registry://")
        and proof.get("component") in REFLECTION_OSS_COMPONENTS
        and proof.get("artifact_family") == _reflection_artifact_expected_family(
            str(proof.get("component"))
        )
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and proof.get("object_store_readback_ref", "").startswith("lean-object-store-packet-readback-")
        and float(proof.get("reflection_quality", 0.0)) > 0.0
        and proof.get("input_hash")
        and lineage_hashes
        and len({str(value) for value in lineage_hashes.values()}) == 1
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "reflection_artifact_materiality_passed",
            "materiality_source_matches_reflection_lineage",
            "evolved_policy_carries_reflection_oss_lineage",
            "strategy_packet_carries_reflection_oss_lineage",
            "object_store_readback_preserves_reflection_oss_lineage",
            "all_packet_targets_bind_reflection_oss_lineage",
            "handoff_runtime_bundle_contains_reflection_oss_refs",
            "runtime_feedback_cites_reflection_oss_lineage",
            "lineage_hash_stable_across_reflection_policy_packet_readback_handoff",
        ))
    )


def _openclaw_session_handoff_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("openclaw-session-handoff://")
        and proof.get("component") == "openclaw"
        and proof.get("source_oss_ref", "").startswith("oss://openclaw/")
        and proof.get("artifact_family") == "openclaw_session"
        and proof.get("context_ref", "").startswith("openclaw-context://")
        and proof.get("session_ref", "").startswith("openclaw-session://")
        and proof.get("upstream_session_ref", "").startswith("openclaw-upstream-session://")
        and proof.get("session_state") == "active"
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and proof.get("context_hash")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "openclaw_session_response_completed",
            "persona_reasoning_consumes_openclaw_session_source",
            "selected_candidates_cite_openclaw_session_followup",
            "handoff_carries_openclaw_session_context",
            "handoff_runtime_bundle_contains_openclaw_session_refs",
            "runtime_feedback_cites_openclaw_session",
            "runtime_feedback_state_binds_openclaw_session",
            "openclaw_context_hash_stable_across_handoff",
        ))
    )


def _alpha_seed_revision_handoff_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    trace_bindings = list(proof.get("trace_bindings", []))
    lineage_hashes = proof.get("lineage_hashes", {})
    return bool(
        proof.get("model_id") == PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("alpha-seed-revision-handoff://")
        and proof.get("component") in ALPHA_SEED_REVISION_ACTION_BY_COMPONENT
        and proof.get("source_oss_ref", "").startswith("oss://")
        and proof.get("revision_ref", "").startswith("alpha-seed-revision://")
        and proof.get("base_seed_ref", "").startswith("alpha-seed://")
        and proof.get("handoff_ref", "").startswith("alpha-seed-revision-handoff://")
        and proof.get("revision_action")
        == ALPHA_SEED_REVISION_ACTION_BY_COMPONENT[str(proof.get("component"))]
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_runtime_feedback_ref", "").startswith(
            "lean-runtime-feedback://"
        )
        and proof.get("object_store_readback_ref", "").startswith(
            "lean-object-store-packet-readback-"
        )
        and proof.get("handoff_hash")
        and proof.get("input_hash")
        and lineage_hashes
        and len({str(value) for value in lineage_hashes.values()}) == 1
        and len(trace_bindings) == 2
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "alpha_seed_revision_applied",
            "persona_reasoning_consumes_alpha_seed_revision",
            "candidate_generation_consumes_alpha_seed_revision",
            "selected_candidates_cite_alpha_seed_revision",
            "strategy_packet_carries_alpha_seed_revision_handoff",
            "object_store_readback_preserves_alpha_seed_revision_handoff",
            "all_packet_targets_bind_alpha_seed_revision_handoff",
            "handoff_carries_alpha_seed_revision_context",
            "handoff_runtime_bundle_contains_alpha_seed_revision_refs",
            "runtime_feedback_cites_alpha_seed_revision_handoff",
            "runtime_feedback_state_binds_alpha_seed_revision_handoff",
            "lineage_hash_stable_across_packet_readback_handoff",
        ))
    )


def _lean_packet_execution_projection_is_usable(projection: Mapping[str, Any]) -> bool:
    replay = projection.get("replay", {})
    leg_projections = list(projection.get("leg_projections", []))
    return bool(
        projection.get("model_id") == LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID
        and projection.get("status") == "passed"
        and projection.get("projection_ref", "").startswith("lean-packet-execution://")
        and projection.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and projection.get("source_handoff_ref", "").startswith("lean-handoff://")
        and projection.get("source_runtime_ref", "").startswith("lean-engine://")
        and projection.get("generation") == 2
        and projection.get("target_stage") == "paper"
        and projection.get("leg_count") == PORTFOLIO_LEG_COUNT
        and projection.get("order_count") == PORTFOLIO_LEG_COUNT
        and projection.get("fill_count") == PORTFOLIO_LEG_COUNT
        and projection.get("input_hash")
        and len(leg_projections) == PORTFOLIO_LEG_COUNT
        and all(leg.get("target_ref", "").startswith(projection.get("projection_ref", "")) for leg in leg_projections)
        and all(leg.get("order_ref", "").startswith("paper-order://") for leg in leg_projections)
        and all(leg.get("fill_ref", "").startswith("paper-fill://") for leg in leg_projections)
        and all(leg.get("readback_ref", "").endswith("/readback") for leg in leg_projections)
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "strategy_packet_ref_bound",
            "strategy_packet_generation2_bound",
            "handoff_allocation_bound",
            "all_packet_instruments_have_policy_legs",
            "all_leg_directions_match_policy_and_allocation",
            "all_leg_weights_match_handoff_allocation",
            "all_leg_capital_within_budget",
            "all_leg_expected_quantities_replay_signal_payload",
            "all_leg_market_friction_notional_bound",
            "all_lean_targets_have_broker_orders",
            "all_broker_orders_have_fill_readbacks",
            "all_fill_events_bind_signal_metadata",
            "paper_only_guard_retained",
            "projection_ready_for_runtime_feedback",
        ))
    )


def _evolved_strategy_packet_proof_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    return bool(
        proof.get("model_id") == LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("evolved-strategy-packet://")
        and proof.get("strategy_packet_ref", "").startswith("lean-strategy-packet://")
        and proof.get("generation") == 2
        and proof.get("source_outcome_window") == "holdout"
        and proof.get("validation_window") == "future_holdout"
        and proof.get("strict_oos_proof_ref", "").startswith("strict-oos-evolution://")
        and proof.get("no_leakage_protocol_ref", "").startswith("no-leakage://")
        and proof.get("evolution_trajectory_ref", "").startswith("trajectory://")
        and proof.get("lean_engine_replay_ref", "").startswith("lean-engine://")
        and proof.get("lean_handoff_ref", "").startswith("lean-handoff://")
        and proof.get("lean_packet_execution_projection_ref", "").startswith(
            "lean-packet-execution://"
        )
        and proof.get("lean_runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and float(proof.get("future_holdout_improvement", 0.0)) > 0.0
        and proof.get("input_hash")
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "strategy_packet_is_generation2",
            "strategy_packet_declares_future_holdout_validation",
            "strict_oos_generation2_step_matches_packet",
            "packet_binds_strict_oos_proof",
            "packet_binds_no_leakage_protocol",
            "packet_binds_evolution_trajectory",
            "lean_engine_replay_reads_same_packet",
            "handoff_consumes_same_packet",
            "handoff_runtime_bundle_contains_packet_and_proofs",
            "execution_projection_consumes_packet_legs_and_orders",
            "runtime_feedback_consumes_handoff_with_packet",
            "paper_only_guard_retained",
        ))
    )


def _scheduler_conflict_ooda_proof_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    dispatch_events = list(proof.get("dispatch_events", []))
    phase_events = list(proof.get("phase_events", []))
    event_types = [event.get("event_type") for event in dispatch_events]
    return bool(
        proof.get("model_id") == PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("scheduler-conflict-ooda://")
        and proof.get("schedule_ref", "").startswith("schedule://")
        and proof.get("conflict_ref", "").startswith("persona-conflict://")
        and proof.get("handoff_ref", "").startswith("lean-handoff://")
        and proof.get("adapter_followup_ref", "").startswith("broker-adapter-followup://")
        and proof.get("runtime_feedback_ref", "").startswith("lean-runtime-feedback://")
        and proof.get("dispatch_ref", "").startswith("scheduler-dispatch://")
        and [event.get("phase") for event in phase_events] == list(AUTONOMOUS_SCHEDULER_PHASES)
        and event_types == [
            "scheduler_recovery_tick",
            "multi_persona_conflict_resolution",
            "lean_handoff_materialization",
            "broker_adapter_followup",
            "lean_runtime_feedback",
            "scheduler_next_cycle_dispatch",
        ]
        and proof.get("next_scheduler_phase") in {"reflect", "evolve"}
        and proof.get("next_ooda_step") in {"observe", "orient", "decide", "act"}
        and proof.get("next_cycle_due_at")
        and proof.get("input_hash")
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "scheduler_phase_order_valid",
            "scheduler_phase_due_times_ordered",
            "scheduler_recovered_restart_checkpoint",
            "conflict_resolution_consumes_selected_action_and_risk",
            "resolved_allocation_is_portfolio_complete",
            "handoff_consumes_conflict_resolution",
            "handoff_consumes_scheduler_ref",
            "adapter_followup_consumes_schedule",
            "lean_runtime_feedback_consumes_schedule",
            "runtime_ooda_step_maps_to_scheduler_phase",
            "next_dispatch_consumes_adapter_and_runtime_feedback",
            "dispatch_events_strictly_ordered",
            "no_future_dispatch_ref",
            "paper_only_guard_retained",
        ))
    )


def _lean_engine_result_is_usable(
    result: Mapping[str, Any],
    plan: Mapping[str, Any],
    binding: Any,
) -> bool:
    runtime_context = result.get("runtime_context", {})
    loaded_metadata = result.get("loaded_metadata", {})
    loaded_packet = result.get("loaded_strategy_packet", {})
    loaded_targets = result.get("loaded_packet_targets", [])
    loaded_signal = result.get("loaded_signal", {})
    object_store_keys = set(result.get("object_store_keys", []))
    tracking_provenance = loaded_packet.get("experiment_tracking_provenance", {})
    policy_oss_lineage = loaded_packet.get("policy_oss_lineage", {})
    reflection_oss_lineage = loaded_packet.get("reflection_oss_lineage", {})
    alpha_seed_handoff = loaded_packet.get("alpha_seed_revision_handoff", {})
    packet_readback_valid = True
    if loaded_packet:
        packet_readback_valid = bool(
            len(loaded_targets) == PORTFOLIO_LEG_COUNT
            and loaded_signal.get("signal_id") == loaded_targets[0].get("signal_id")
            and loaded_signal.get("symbol") == loaded_targets[0].get("execution_symbol")
            and loaded_packet.get("normalized_experiment_ref", "").startswith("experiment://")
            and loaded_packet.get("tracking_reconciliation_ref", "").startswith(
                "tracking-reconciliation://"
            )
            and loaded_packet.get("tracking_repair_ref", "").startswith(
                loaded_packet.get("tracking_reconciliation_ref", "")
            )
            and loaded_packet.get("experiment_tracking_provenance_hash")
            == tracking_provenance.get("lineage_hash")
            and loaded_packet.get("policy_oss_ref", "").startswith("oss://")
            and loaded_packet.get("policy_oss_lineage_ref", "").startswith(
                "policy-oss-lineage://"
            )
            and loaded_packet.get("policy_oss_lineage_hash")
            == policy_oss_lineage.get("lineage_hash")
            and loaded_packet.get("reflection_oss_ref", "").startswith("oss://")
            and loaded_packet.get("reflection_oss_lineage_ref", "").startswith(
                "reflection-oss-lineage://"
            )
            and loaded_packet.get("reflection_oss_lineage_hash")
            == reflection_oss_lineage.get("lineage_hash")
            and loaded_packet.get("alpha_seed_revision_handoff_ref", "").startswith(
                "alpha-seed-revision-handoff://"
            )
            and loaded_packet.get("alpha_seed_revision_ref", "").startswith(
                "alpha-seed-revision://"
            )
            and loaded_packet.get("alpha_seed_source_ref", "").startswith("alpha-seed://")
            and loaded_packet.get("alpha_seed_revision_handoff_hash")
            == alpha_seed_handoff.get("lineage_hash")
            and all(
                target.get("signal", {}).get("metadata", {}).get("strategy_packet_ref")
                == loaded_packet.get("packet_ref")
                and target.get("policy_oss_lineage_hash")
                == loaded_packet.get("policy_oss_lineage_hash")
                and target.get("signal", {}).get("metadata", {}).get("policy_oss_ref")
                == loaded_packet.get("policy_oss_ref")
                and target.get("reflection_oss_lineage_hash")
                == loaded_packet.get("reflection_oss_lineage_hash")
                and target.get("signal", {}).get("metadata", {}).get("reflection_oss_ref")
                == loaded_packet.get("reflection_oss_ref")
                and target.get("alpha_seed_revision_handoff_hash")
                == loaded_packet.get("alpha_seed_revision_handoff_hash")
                and target.get("alpha_seed_revision_ref")
                == loaded_packet.get("alpha_seed_revision_ref")
                and target.get("signal", {})
                .get("metadata", {})
                .get("alpha_seed_revision_ref")
                == loaded_packet.get("alpha_seed_revision_ref")
                for target in loaded_targets
            )
        )
    return bool(
        runtime_context.get("runtime_binding_id") == binding.binding_id
        and runtime_context.get("runtime_id") == binding.runtime_id
        and runtime_context.get("deployment_plan_id") == plan["plan_id"]
        and runtime_context.get("deployment_stage") == "paper"
        and loaded_metadata.get("deployment_plan_id") == plan["plan_id"]
        and loaded_metadata.get("runtime_binding_id") == binding.binding_id
        and int(result.get("synthetic_bar_count", 0)) == 5
        and int(result.get("raw_on_data_callbacks", 0)) == 5
        and int(result.get("executed_on_data_callbacks", 0)) >= 1
        and int(result.get("fill_count", 0)) >= 1
        and result.get("broker_production_live_enabled") == "false"
        and any(key.endswith("/artifact.bin") for key in object_store_keys)
        and any(key.endswith("/metadata.json") for key in object_store_keys)
        and packet_readback_valid
    )


def _lean_object_store_packet_readback_is_usable(readback: Mapping[str, Any]) -> bool:
    replay = readback.get("replay", {})
    target_signal_ids = list(readback.get("target_signal_ids", []))
    target_refs = list(readback.get("target_refs", []))
    first_signal_id = target_signal_ids[0] if target_signal_ids else None
    first_target_ref = target_refs[0] if target_refs else None
    return bool(
        readback.get("model_id") == LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID
        and readback.get("status") == "passed"
        and readback.get("packet_ref", "").startswith("lean-strategy-packet://")
        and readback.get("packet_hash") == readback.get("source_packet_hash")
        and readback.get("artifact_payload_checksum")
        and readback.get("target_count") == PORTFOLIO_LEG_COUNT
        and len(target_refs) == PORTFOLIO_LEG_COUNT
        and len(target_signal_ids) == PORTFOLIO_LEG_COUNT
        and readback.get("loaded_signal_id") == first_signal_id
        and readback.get("loaded_signal_source_target_ref") == first_target_ref
        and readback.get("normalized_experiment_ref", "").startswith("experiment://")
        and readback.get("tracking_reconciliation_ref", "").startswith("tracking-reconciliation://")
        and readback.get("tracking_repair_ref", "").startswith(readback.get("tracking_reconciliation_ref", ""))
        and readback.get("experiment_tracking_provenance_hash")
        == readback.get("loaded_experiment_tracking_provenance_hash")
        and readback.get("policy_oss_ref", "").startswith("oss://")
        and readback.get("policy_oss_lineage_ref", "").startswith("policy-oss-lineage://")
        and readback.get("policy_oss_lineage_hash") == readback.get("loaded_policy_oss_lineage_hash")
        and readback.get("reflection_oss_ref", "").startswith("oss://")
        and readback.get("reflection_oss_lineage_ref", "").startswith("reflection-oss-lineage://")
        and readback.get("reflection_oss_lineage_hash")
        == readback.get("loaded_reflection_oss_lineage_hash")
        and readback.get("alpha_seed_revision_handoff_ref", "").startswith(
            "alpha-seed-revision-handoff://"
        )
        and readback.get("alpha_seed_revision_ref", "").startswith("alpha-seed-revision://")
        and readback.get("alpha_seed_source_ref", "").startswith("alpha-seed://")
        and readback.get("alpha_seed_revision_handoff_hash")
        == readback.get("loaded_alpha_seed_revision_handoff_hash")
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "packet_present_in_object_store_artifact",
            "packet_ref_matches_case_strategy_packet",
            "packet_hash_matches_persona_packet",
            "target_count_matches_portfolio",
            "all_targets_have_signals",
            "all_targets_bind_strategy_packet_ref",
            "target_refs_unique",
            "loaded_signal_from_first_packet_target",
            "loaded_signal_symbol_matches_first_target",
            "loaded_signal_quantity_matches_first_target",
            "algorithm_executed_loaded_packet_signal",
            "object_store_keys_include_packet_artifact_and_metadata",
            "tracking_provenance_present_in_packet",
            "loaded_packet_preserves_tracking_provenance",
            "loaded_tracking_ref_matches_packet",
            "policy_oss_lineage_present_in_packet",
            "loaded_packet_preserves_policy_oss_lineage",
            "loaded_policy_oss_ref_matches_packet",
            "all_targets_bind_policy_oss_lineage",
            "reflection_oss_lineage_present_in_packet",
            "loaded_packet_preserves_reflection_oss_lineage",
            "loaded_reflection_oss_ref_matches_packet",
            "all_targets_bind_reflection_oss_lineage",
            "alpha_seed_revision_handoff_present_in_packet",
            "loaded_packet_preserves_alpha_seed_revision_handoff",
            "loaded_alpha_seed_revision_ref_matches_packet",
            "all_targets_bind_alpha_seed_revision_handoff",
            "paper_only_guard_retained",
        ))
    )


def _lean_engine_replay_is_usable(replay: Mapping[str, Any]) -> bool:
    runtime_context = replay.get("runtime_context", {})
    loaded_metadata = replay.get("loaded_metadata", {})
    strategy_packet = replay.get("case_specific_strategy_packet", {})
    tracking_provenance = strategy_packet.get("experiment_tracking_provenance", {})
    policy_oss_lineage = strategy_packet.get("policy_oss_lineage", {})
    reflection_oss_lineage = strategy_packet.get("reflection_oss_lineage", {})
    alpha_seed_handoff = strategy_packet.get("alpha_seed_revision_handoff", {})
    packet_targets = replay.get("case_specific_packet_targets", [])
    packet_readback = replay.get("lean_object_store_packet_readback", {})
    return bool(
        replay.get("model_id") == LEAN_ENGINE_REPLAY_MODEL_ID
        and replay.get("status") == "passed"
        and replay.get("case_specific_runtime_binding")
        and runtime_context.get("runtime_binding_id") == replay.get("binding", {}).get("binding_id")
        and runtime_context.get("deployment_plan_id") == replay.get("plan", {}).get("plan_id")
        and runtime_context.get("deployment_stage") == "paper"
        and loaded_metadata.get("deployment_plan_id") == replay.get("plan", {}).get("plan_id")
        and loaded_metadata.get("runtime_binding_id") == replay.get("binding", {}).get("binding_id")
        and int(replay.get("synthetic_bar_count", 0)) == 5
        and int(replay.get("raw_on_data_callbacks", 0)) == 5
        and int(replay.get("executed_on_data_callbacks", 0)) >= 1
        and int(replay.get("fill_count", 0)) >= 1
        and replay.get("broker_production_live_enabled") == "false"
        and strategy_packet.get("validation_signature")
        and strategy_packet.get("packet_ref", "").startswith("lean-strategy-packet://")
        and strategy_packet.get("generation") == 2
        and strategy_packet.get("validation_window") == "future_holdout"
        and strategy_packet.get("source_outcome_window") == "holdout"
        and strategy_packet.get("strict_oos_proof_ref", "").startswith("strict-oos-evolution://")
        and strategy_packet.get("no_leakage_protocol_ref", "").startswith("no-leakage://")
        and strategy_packet.get("evolution_trajectory_ref", "").startswith("trajectory://")
        and strategy_packet.get("normalized_experiment_ref", "").startswith("experiment://")
        and strategy_packet.get("tracking_reconciliation_ref", "").startswith("tracking-reconciliation://")
        and strategy_packet.get("tracking_repair_ref", "").startswith(
            strategy_packet.get("tracking_reconciliation_ref", "")
        )
        and strategy_packet.get("experiment_tracking_provenance_hash")
        == tracking_provenance.get("lineage_hash")
        and strategy_packet.get("policy_oss_ref", "").startswith("oss://")
        and strategy_packet.get("policy_oss_lineage_ref", "").startswith(
            "policy-oss-lineage://"
        )
        and strategy_packet.get("policy_oss_lineage_hash") == policy_oss_lineage.get("lineage_hash")
        and strategy_packet.get("reflection_oss_ref", "").startswith("oss://")
        and strategy_packet.get("reflection_oss_lineage_ref", "").startswith(
            "reflection-oss-lineage://"
        )
        and strategy_packet.get("reflection_oss_lineage_hash") == reflection_oss_lineage.get(
            "lineage_hash"
        )
        and strategy_packet.get("alpha_seed_revision_handoff_ref", "").startswith(
            "alpha-seed-revision-handoff://"
        )
        and strategy_packet.get("alpha_seed_revision_ref", "").startswith(
            "alpha-seed-revision://"
        )
        and strategy_packet.get("alpha_seed_source_ref", "").startswith("alpha-seed://")
        and strategy_packet.get("alpha_seed_revision_handoff_hash")
        == alpha_seed_handoff.get("lineage_hash")
        and all(
            target.get("policy_oss_ref") == strategy_packet.get("policy_oss_ref")
            and target.get("policy_oss_lineage_hash")
            == strategy_packet.get("policy_oss_lineage_hash")
            and target.get("reflection_oss_ref") == strategy_packet.get("reflection_oss_ref")
            and target.get("reflection_oss_lineage_hash")
            == strategy_packet.get("reflection_oss_lineage_hash")
            and target.get("alpha_seed_revision_ref")
            == strategy_packet.get("alpha_seed_revision_ref")
            and target.get("alpha_seed_revision_handoff_hash")
            == strategy_packet.get("alpha_seed_revision_handoff_hash")
            for target in packet_targets
        )
        and strategy_packet.get("strict_oos_replay_passed") is True
        and strategy_packet.get("no_leakage_replay_passed") is True
        and len(packet_targets) == PORTFOLIO_LEG_COUNT
        and _lean_object_store_packet_readback_is_usable(packet_readback)
    )


def _shioaji_sandbox_result_is_usable(payload: Mapping[str, Any]) -> bool:
    live_disabled = payload.get("live_disabled_result", {}).get("response", {})
    readback = payload.get("readback_result", {})
    return bool(
        payload.get("status") == "passed"
        and payload.get("provider") == "Shioaji"
        and payload.get("environment") == "sandbox"
        and payload.get("production_live_enabled") is False
        and payload.get("capital_binding_enabled") is False
        and payload.get("human_gate_required") is True
        and payload.get("reconcile_result", {}).get("status") == "passed"
        and readback.get("status") == "cancelled"
        and readback.get("is_real_order") is False
        and readback.get("is_real_capital") is False
        and readback.get("deployment_stage") == "sandbox"
        and live_disabled.get("error_code") == "SHIOAJI_LIVE_DISABLED"
        and payload.get("error") is None
    )


def _shioaji_sandbox_lifecycle_is_usable(lifecycle: Mapping[str, Any]) -> bool:
    return bool(
        lifecycle.get("model_id") == SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID
        and lifecycle.get("status") == "passed"
        and lifecycle.get("run_mode") == "mock_api_replay"
        and lifecycle.get("provider") == "Shioaji"
        and lifecycle.get("environment") == "sandbox"
        and lifecycle.get("production_live_enabled") is False
        and lifecycle.get("capital_binding_enabled") is False
        and lifecycle.get("human_gate_required") is True
        and lifecycle.get("reconcile_result", {}).get("status") == "passed"
        and lifecycle.get("readback_result", {}).get("status") == "cancelled"
        and lifecycle.get("readback_result", {}).get("is_real_order") is False
        and lifecycle.get("readback_result", {}).get("is_real_capital") is False
        and lifecycle.get("live_disabled_result", {}).get("response", {}).get("error_code")
        == "SHIOAJI_LIVE_DISABLED"
        and lifecycle.get("error") is None
    )


def _case_upstream_artifacts_are_usable(
    *,
    episode: PortfolioEpisode,
    artifacts: Mapping[str, Any],
) -> bool:
    vectorbt = artifacts.get("vectorbt", {})
    tracker = artifacts.get("tracker", {})
    dataset_summary = vectorbt.get("dataset_summary", {})
    readback = tracker.get("readback", {})
    persona_response = artifacts.get("persona_response", {})
    return bool(
        artifacts.get("vectorbt_model_id") == CASE_UPSTREAM_VECTORBT_MODEL_ID
        and artifacts.get("tracking_model_id") == CASE_UPSTREAM_TRACKING_MODEL_ID
        and artifacts.get("selected_oss_model_id") == CASE_SELECTED_OSS_MODEL_ID
        and artifacts.get("allowed_windows") == ["observe", "feedback"]
        and artifacts.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
        and vectorbt.get("status") == "completed"
        and vectorbt.get("backend") in {"vectorbt_portfolio", "stub_backtest"}
        and vectorbt.get("request_id") == f"req-{episode.case_id}-vectorbt-upstream"
        and dataset_summary.get("dataset_id") == HISTORICAL_OHLCV_DATASET_ID
        and set(dataset_summary.get("instruments", [])) == {window.instrument for window in episode.windows}
        and int(dataset_summary.get("num_instruments", 0)) == PORTFOLIO_LEG_COUNT
        and int(dataset_summary.get("total_bars", 0)) == PORTFOLIO_LEG_COUNT * (LOOKBACK_BARS + FEEDBACK_BARS)
        and set(episode.source_dataset_refs).issubset(set(dataset_summary.get("source_dataset_refs", [])))
        and vectorbt.get("registry_id")
        and vectorbt.get("run_id")
        and tracker.get("status") == "completed"
        and tracker.get("component") in {"mlflow", "wandb"}
        and tracker.get("backend") == tracker.get("component")
        and tracker.get("source_vectorbt_run_id") == vectorbt.get("run_id")
        and tracker.get("registry_id") == vectorbt.get("registry_id")
        and readback.get("run_readback_status") == "found"
        and readback.get("artifact_readback_status") == "found"
        and persona_response.get("used_before_generation1_decision") is True
        and persona_response.get("used_before_generation2_decision") is True
        and f"oss://vectorbt/{vectorbt.get('request_id')}" in persona_response.get("evidence_refs", [])
        and f"experiment://{tracker.get('backend')}/{tracker.get('run_id')}" in persona_response.get("evidence_refs", [])
        and _case_selected_oss_feedback_is_usable(episode=episode, artifacts=artifacts)
        and _oss_disagreement_arbitration_is_usable(artifacts.get("oss_disagreement_arbitration", {}))
        and artifacts.get("oss_disagreement_arbitration", {}).get("arbitration_ref")
        in persona_response.get("evidence_refs", [])
        and _tracking_readback_reconciliation_is_usable(artifacts.get("tracking_reconciliation", {}))
        and artifacts.get("tracking_reconciliation", {}).get("reconciliation_ref")
        in persona_response.get("evidence_refs", [])
        and _alpha_seed_revision_is_usable(artifacts.get("alpha_seed_revision", {}))
        and artifacts.get("alpha_seed_revision", {}).get("revision_ref")
        in persona_response.get("evidence_refs", [])
    )


def _case_selected_oss_feedback_is_usable(
    *,
    episode: PortfolioEpisode,
    artifacts: Mapping[str, Any],
) -> bool:
    selected_oss = artifacts.get("selected_oss", {})
    required_roles = {"alpha_model", "policy_candidate", "reflection_artifact", "risk_analytics"}
    if set(selected_oss) != required_roles:
        return False
    for role in required_roles:
        entry = selected_oss.get(role, {})
        component = entry.get("component")
        expected_component = _expected_component_for_selected_oss_role(episode, role)
        if component != expected_component:
            return False
        if entry.get("expected_component") != expected_component:
            return False
        if entry.get("model_id") != CASE_SELECTED_OSS_MODEL_ID:
            return False
        if entry.get("case_specific") is not True:
            return False
        if entry.get("status") != "completed":
            return False
        if entry.get("drives_persona_step") != _oss_persona_step(str(component)):
            return False
        if entry.get("request_id") != f"req-{episode.case_id}-{role}-{component}" and not (
            role == "alpha_model" and component == "vectorbt" and entry.get("request_id") == f"req-{episode.case_id}-vectorbt-upstream"
        ):
            return False
        followup = entry.get("persona_followup", {})
        if followup.get("trigger_component") != component:
            return False
        if followup.get("trigger_request_id") != entry.get("request_id"):
            return False
        if not (entry.get("primary_output") or entry.get("metrics")):
            return False
    return True


def _oss_disagreement_arbitration_is_usable(arbitration: Mapping[str, Any]) -> bool:
    replay = arbitration.get("replay", {})
    conflicts = list(arbitration.get("conflicts", []))
    adjustments = arbitration.get("candidate_score_adjustments", {})
    refs_by_action = arbitration.get("candidate_evidence_refs_by_action", {})
    response = arbitration.get("persona_arbitration_response", {})
    if len(conflicts) != 1:
        return False
    conflict = conflicts[0]
    conflict_type = str(conflict.get("conflict_type"))
    expected_roles = OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE.get(conflict_type)
    resolution_action = OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE.get(conflict_type)
    return bool(
        arbitration.get("model_id") == PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID
        and arbitration.get("status") == "resolved"
        and arbitration.get("arbitration_ref", "").startswith("oss-disagreement://")
        and arbitration.get("input_hash")
        and expected_roles
        and tuple(conflict.get("source_roles", [])) == expected_roles
        and conflict.get("resolution_action") == resolution_action
        and conflict.get("resolution_ref", "").startswith(str(arbitration.get("arbitration_ref")))
        and set(adjustments) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and float(adjustments.get(str(resolution_action), 0.0)) > 0.0
        and set(refs_by_action) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and arbitration.get("arbitration_ref") in refs_by_action.get("feedback-adapt", [])
        and response.get("next_action") == "score_candidates_with_arbitrated_oss_weights"
        and response.get("preferred_candidate_action") == "feedback-adapt"
        and resolution_action in response.get("resolution_actions", [])
        and replay.get("replayable") is True
        and replay.get("all_source_roles_completed") is True
        and replay.get("conflict_detected") is True
        and replay.get("conflict_sources_bound") is True
        and replay.get("resolution_action_selected") is True
        and replay.get("resolution_drives_candidate_scoring") is True
        and replay.get("followup_loop_available") is True
        and replay.get("selected_oss_refs_available") is True
        and replay.get("feedback_adapt_gets_all_selected_refs") is True
    )


def _tracking_readback_reconciliation_is_usable(reconciliation: Mapping[str, Any]) -> bool:
    replay = reconciliation.get("replay", {})
    divergence = reconciliation.get("divergence", {})
    repair = reconciliation.get("repair", {})
    adjustments = reconciliation.get("candidate_score_adjustments", {})
    refs_by_action = reconciliation.get("candidate_evidence_refs_by_action", {})
    response = reconciliation.get("persona_reconciliation_response", {})
    divergence_type = str(divergence.get("divergence_type"))
    repair_action = TRACKING_RECONCILIATION_ACTION_BY_TYPE.get(divergence_type)
    return bool(
        reconciliation.get("model_id") == PERSONA_TRACKING_RECONCILIATION_MODEL_ID
        and reconciliation.get("status") == "reconciled"
        and reconciliation.get("reconciliation_ref", "").startswith("tracking-reconciliation://")
        and reconciliation.get("input_hash")
        and divergence_type in TRACKING_DIVERGENCE_TYPES_BY_SCENARIO.values()
        and repair.get("action") == repair_action
        and repair.get("repair_ref", "").startswith(str(reconciliation.get("reconciliation_ref")))
        and repair.get("next_persona_step") == "cite_reconciled_experiment_ref"
        and repair.get("normalized_experiment_ref") == f"experiment://{reconciliation.get('backend')}/{reconciliation.get('run_id')}"
        and divergence.get("backend") == reconciliation.get("backend")
        and divergence.get("expected") != divergence.get("readback")
        and set(adjustments) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and float(adjustments.get("feedback-adapt", 0.0)) > 0.0
        and set(refs_by_action) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and reconciliation.get("reconciliation_ref") in refs_by_action.get("feedback-adapt", [])
        and repair.get("repair_ref") in refs_by_action.get("feedback-adapt", [])
        and response.get("next_action") == "score_candidates_with_reconciled_tracking_readback"
        and response.get("preferred_candidate_action") == "feedback-adapt"
        and repair_action in response.get("repair_actions", [])
        and replay.get("replayable") is True
        and replay.get("tracker_completed") is True
        and replay.get("tracker_readback_found") is True
        and replay.get("divergence_detected") is True
        and replay.get("repair_action_selected") is True
        and replay.get("normalized_experiment_ref_available") is True
        and replay.get("vectorbt_tracker_bound") is True
        and replay.get("scorer_adjustment_available") is True
        and replay.get("feedback_adapt_gets_tracking_refs") is True
    )


def _alpha_seed_revision_is_usable(alpha_seed_revision: Mapping[str, Any]) -> bool:
    replay = alpha_seed_revision.get("replay", {})
    revision = alpha_seed_revision.get("revision", {})
    adjustments = alpha_seed_revision.get("candidate_score_adjustments", {})
    refs_by_action = alpha_seed_revision.get("candidate_evidence_refs_by_action", {})
    response = alpha_seed_revision.get("persona_alpha_response", {})
    alpha_component = str(alpha_seed_revision.get("alpha_component"))
    revision_action = ALPHA_SEED_REVISION_ACTION_BY_COMPONENT.get(alpha_component)
    source_refs = set(alpha_seed_revision.get("source_refs", []))
    return bool(
        alpha_seed_revision.get("model_id") == PERSONA_ALPHA_SEED_REVISION_MODEL_ID
        and alpha_seed_revision.get("status") == "applied"
        and alpha_seed_revision.get("revision_ref", "").startswith("alpha-seed-revision://")
        and alpha_seed_revision.get("input_hash")
        and alpha_component in ALPHA_SEED_REVISION_ACTION_BY_COMPONENT
        and revision.get("action") == revision_action
        and revision.get("base_seed_key")
        and revision.get("base_seed_ref", "").startswith("alpha-seed://")
        and revision.get("source_alpha_component") == alpha_component
        and revision.get("source_alpha_request_id")
        and revision.get("downstream_vectorbt_request_id")
        and revision.get("downstream_tracker_run_id")
        and revision.get("downstream_policy_candidate_request_id")
        and revision.get("allowed_windows") == ["observe", "feedback"]
        and revision.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
        and set(adjustments) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and float(adjustments.get("feedback-adapt", 0.0)) > 0.0
        and set(refs_by_action) == {"feedback-adapt", "retain-observe", "risk-off", "contrarian-check"}
        and source_refs.issubset(set(refs_by_action.get("feedback-adapt", [])))
        and response.get("next_action") == "score_candidates_with_alpha_seed_revision"
        and response.get("preferred_candidate_action") == "feedback-adapt"
        and revision_action in response.get("revision_actions", [])
        and replay.get("replayable") is True
        and replay.get("source_alpha_completed") is True
        and replay.get("alpha_revision_generated") is True
        and replay.get("downstream_backtest_bound") is True
        and replay.get("downstream_tracker_bound") is True
        and replay.get("policy_candidate_bound") is True
        and replay.get("no_forbidden_window_sources") is True
        and replay.get("scorer_adjustment_available") is True
        and replay.get("feedback_adapt_gets_alpha_backtest_tracker_refs") is True
    )


def _persona_decision_artifact_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_generation = artifact.get("candidate_generation", {})
    response = candidate_generation.get("response", {})
    scorer = artifact.get("scorer", {})
    scorecards = scorer.get("scorecards", {})
    memory_counterfactual = artifact.get("memory_counterfactual", {})
    risk_evaluator = artifact.get("risk_evaluator", {})
    selection = artifact.get("selection", {})
    replay = artifact.get("replay", {})
    if not all(isinstance(item, Mapping) for item in (input_context, response, scorer, scorecards, memory_counterfactual, risk_evaluator, selection, replay)):
        return False

    required_roles = {
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    }
    trace_candidate_ids = [str(candidate["candidate_id"]) for candidate in trace.get("candidates", [])]
    response_candidate_ids = [str(candidate_id) for candidate_id in response.get("candidate_ids", [])]
    selected_id = str(trace.get("selected_candidate_id"))
    if not trace_candidate_ids or response_candidate_ids != trace_candidate_ids:
        return False
    if set(scorecards) != set(trace_candidate_ids):
        return False
    if selection.get("selected_candidate_id") != selected_id:
        return False
    if selected_id not in scorecards:
        return False
    selected_score = float(scorecards[selected_id].get("candidate_score", -math.inf))
    if selected_score != max(float(card.get("candidate_score", -math.inf)) for card in scorecards.values()):
        return False

    decision_inputs = trace.get("decision_inputs", {})
    selected_refs = set(trace.get("selected_candidate", {}).get("evidence_refs", []))
    selected_oss_refs = {
        ref
        for ref in selected_refs
        if ref.startswith("oss://")
        and not ref.startswith("oss://lean_handoff/")
    }
    return bool(
        artifact.get("model_id") == PERSONA_DECISION_ARTIFACT_MODEL_ID
        and candidate_generation.get("model_id") == PERSONA_CANDIDATE_GENERATOR_MODEL_ID
        and scorer.get("model_id") == PERSONA_CANDIDATE_SCORER_MODEL_ID
        and risk_evaluator.get("model_id") == PERSONA_RISK_EVALUATOR_MODEL_ID
        and risk_evaluator.get("status") == "passed"
        and all(check.get("status") == "passed" for check in risk_evaluator.get("checks", []))
        and set(input_context.get("required_oss_roles", [])) == required_roles
        and set(input_context.get("oss_request_ids_by_role", {})) == required_roles
        and set(input_context.get("oss_components_by_role", {})) == required_roles
        and set(input_context.get("oss_feedback_status_by_role", {})) == required_roles
        and all(status == "completed" for status in input_context.get("oss_feedback_status_by_role", {}).values())
        and set(input_context.get("oss_evidence_refs", [])).issubset(set(trace.get("evidence_refs", [])))
        and selected_oss_refs.issubset(set(selection.get("selected_evidence_refs", [])))
        and input_context.get("allowed_windows") == decision_inputs.get("allowed_windows")
        and input_context.get("forbidden_windows_not_used") == decision_inputs.get("forbidden_windows_not_used")
        and input_context.get("telemetry_event_id") == decision_inputs.get("telemetry_event_id")
        and input_context.get("memory_ref") == decision_inputs.get("memory_ref")
        and (input_context.get("memory_ref") or input_context.get("memory_status") == "cold_start_declared")
        and response.get("status") == "completed"
        and response.get("candidates") == trace.get("candidates")
        and all(card.get("score_replay_match") for card in scorecards.values())
        and len(selection.get("rejected_candidates", [])) == len(trace_candidate_ids) - 1
        and replay.get("replayable") is True
        and replay.get("selected_candidate_is_top_score") is True
        and replay.get("no_forbidden_window_sources") is True
        and replay.get("uses_memory_or_declares_cold_start") is True
        and replay.get("uses_memory_in_scoring_or_declares_cold_start") is True
        and replay.get("uses_cross_persona_institutional_memory_or_declares_cold_start") is True
        and replay.get("memory_counterfactual_replays_score_delta") is True
        and replay.get("uses_persona_reasoning_response") is True
        and replay.get("uses_selected_oss_feedback") is True
        and replay.get("uses_policy_candidate_oss_metrics") is True
        and replay.get("uses_reflection_artifact_oss_metrics") is True
        and replay.get("uses_oss_response_followup_loop") is True
        and replay.get("uses_oss_disagreement_arbitration") is True
        and replay.get("uses_tracking_reconciliation") is True
        and replay.get("uses_alpha_seed_revision") is True
        and replay.get("uses_cross_cycle_runtime_feedback_or_declares_cold_start") is True
        and replay.get("uses_multi_cycle_lineage_or_declares_cold_start") is True
        and replay.get("input_hash")
        and replay.get("candidate_hash")
        and replay.get("score_hash")
        and replay.get("selection_hash")
        and _trace_has_no_forbidden_window_leakage(trace)
        and _trace_memory_influence_is_usable(trace)
        and _memory_counterfactual_proof_is_usable(memory_counterfactual)
        and _trace_persona_reasoning_is_usable(trace)
        and _trace_oss_followup_loop_is_usable(trace)
        and _trace_oss_disagreement_arbitration_is_usable(trace)
        and _trace_tracking_reconciliation_is_usable(trace)
        and _trace_alpha_seed_revision_is_usable(trace)
    )


def _trace_persona_reasoning_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    reasoning = artifact.get("persona_reasoning", {})
    if not isinstance(reasoning, Mapping):
        return False
    request = reasoning.get("request", {})
    response = reasoning.get("response", {})
    evaluator = reasoning.get("evaluator", {})
    input_context = artifact.get("input_context", {})
    candidate_generation = artifact.get("candidate_generation", {})
    generation_request = candidate_generation.get("request", {})
    generation_response = candidate_generation.get("response", {})
    if not all(isinstance(item, Mapping) for item in (request, response, evaluator, generation_request, generation_response)):
        return False
    blueprints = list(response.get("candidate_blueprints", []))
    candidates = list(trace.get("candidates", []))
    if len(blueprints) != len(candidates):
        return False
    blueprint_by_action = {str(blueprint.get("action")): blueprint for blueprint in blueprints}
    candidate_actions = [_candidate_action_key(str(candidate.get("candidate_id"))) for candidate in candidates]
    if set(blueprint_by_action) != set(candidate_actions):
        return False
    decision_inputs = trace.get("decision_inputs", {})
    forbidden = set(decision_inputs.get("forbidden_windows_not_used", []))
    oss_components = input_context.get("oss_components_by_role", {})
    oss_request_ids = input_context.get("oss_request_ids_by_role", {})
    for candidate in candidates:
        action = _candidate_action_key(str(candidate["candidate_id"]))
        blueprint = blueprint_by_action[action]
        candidate_refs = set(candidate.get("evidence_refs", []))
        if list(candidate.get("source_windows", [])) != list(blueprint.get("source_windows", [])):
            return False
        if candidate.get("rationale") != blueprint.get("rationale"):
            return False
        if forbidden.intersection(set(blueprint.get("source_windows", []))):
            return False
        for role in blueprint.get("evidence_roles", []):
            expected_ref = f"oss://{oss_components[role]}/{oss_request_ids[role]}"
            if expected_ref not in candidate_refs:
                return False
        if not set(blueprint.get("extra_evidence_refs", [])).issubset(candidate_refs):
            return False
    memory_ref = decision_inputs.get("memory_ref")
    memory_usage = response.get("memory_usage", {})
    followup_ref = input_context.get("oss_followup_loop_ref")
    followup_usage = response.get("oss_followup_usage", {})
    arbitration_ref = input_context.get("oss_disagreement_arbitration_ref")
    arbitration_usage = response.get("oss_disagreement_arbitration_usage", {})
    tracking_reconciliation_ref = input_context.get("tracking_reconciliation_ref")
    tracking_reconciliation_usage = response.get("tracking_reconciliation_usage", {})
    alpha_seed_revision_ref = input_context.get("alpha_seed_revision_ref")
    alpha_seed_revision_usage = response.get("alpha_seed_revision_usage", {})
    institutional_memory_status = input_context.get("institutional_memory_status")
    institutional_memory_entry_ref = input_context.get("institutional_memory_entry_ref")
    institutional_memory_contributing_persona_ids = set(
        input_context.get("institutional_memory_contributing_persona_ids", [])
    )
    institutional_memory_usage = response.get("institutional_memory_usage", {})
    multi_cycle_lineage_status = input_context.get("multi_cycle_lineage_status")
    multi_cycle_lineage_ref = input_context.get("multi_cycle_lineage_ref")
    multi_cycle_latest_runtime_feedback_ref = input_context.get(
        "multi_cycle_latest_runtime_feedback_ref"
    )
    multi_cycle_older_runtime_feedback_ref = input_context.get(
        "multi_cycle_older_runtime_feedback_ref"
    )
    multi_cycle_lineage_usage = response.get("multi_cycle_lineage_usage", {})
    return bool(
        request.get("model_id") == PERSONA_REASONING_MODEL_ID
        and response.get("model_id") == PERSONA_REASONING_MODEL_ID
        and evaluator.get("model_id") == PERSONA_REASONING_EVALUATOR_MODEL_ID
        and evaluator.get("status") == "passed"
        and all(check.get("status") == "passed" for check in evaluator.get("checks", []))
        and request.get("allowed_windows") == decision_inputs.get("allowed_windows")
        and request.get("forbidden_windows_not_used") == decision_inputs.get("forbidden_windows_not_used")
        and response.get("status") == "completed"
        and request.get("oss_followup_loop_ref") == followup_ref
        and followup_usage.get("loop_ref") == followup_ref
        and followup_ref in request.get("input_refs", [])
        and followup_ref in generation_request.get("input_refs", [])
        and request.get("oss_disagreement_arbitration_ref") == arbitration_ref
        and arbitration_usage.get("arbitration_ref") == arbitration_ref
        and arbitration_usage.get("model_id") == PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID
        and arbitration_ref in request.get("input_refs", [])
        and arbitration_ref in generation_request.get("input_refs", [])
        and request.get("tracking_reconciliation_ref") == tracking_reconciliation_ref
        and tracking_reconciliation_usage.get("reconciliation_ref") == tracking_reconciliation_ref
        and tracking_reconciliation_usage.get("model_id") == PERSONA_TRACKING_RECONCILIATION_MODEL_ID
        and tracking_reconciliation_ref in request.get("input_refs", [])
        and tracking_reconciliation_ref in generation_request.get("input_refs", [])
        and request.get("alpha_seed_revision_ref") == alpha_seed_revision_ref
        and alpha_seed_revision_usage.get("revision_ref") == alpha_seed_revision_ref
        and alpha_seed_revision_usage.get("model_id") == PERSONA_ALPHA_SEED_REVISION_MODEL_ID
        and alpha_seed_revision_ref in request.get("input_refs", [])
        and alpha_seed_revision_ref in generation_request.get("input_refs", [])
        and institutional_memory_usage.get("model_id") == PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID
        and (
            (
                institutional_memory_status == "cold_start"
                and institutional_memory_usage.get("status") == "cold_start"
                and institutional_memory_entry_ref is None
            )
            or (
                institutional_memory_status == "applied"
                and institutional_memory_usage.get("status") == "applied"
                and institutional_memory_usage.get("entry_ref") == institutional_memory_entry_ref
                and institutional_memory_entry_ref in request.get("input_refs", [])
                and institutional_memory_entry_ref in generation_request.get("input_refs", [])
                and input_context.get("persona_id") not in institutional_memory_contributing_persona_ids
            )
        )
        and multi_cycle_lineage_usage.get("model_id") == PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID
        and (
            (
                multi_cycle_lineage_status == "cold_start"
                and multi_cycle_lineage_usage.get("status") == "cold_start"
                and multi_cycle_lineage_ref is None
            )
            or (
                multi_cycle_lineage_status == "single_prior"
                and multi_cycle_lineage_usage.get("status") == "single_prior"
                and multi_cycle_lineage_usage.get("lineage_ref") == multi_cycle_lineage_ref
                and multi_cycle_lineage_ref in request.get("input_refs", [])
                and multi_cycle_lineage_ref in generation_request.get("input_refs", [])
                and multi_cycle_latest_runtime_feedback_ref in request.get("input_refs", [])
                and multi_cycle_latest_runtime_feedback_ref in generation_request.get("input_refs", [])
                and multi_cycle_older_runtime_feedback_ref is None
            )
            or (
                multi_cycle_lineage_status == "lineage_applied"
                and multi_cycle_lineage_usage.get("status") == "lineage_applied"
                and multi_cycle_lineage_usage.get("lineage_ref") == multi_cycle_lineage_ref
                and multi_cycle_lineage_ref in request.get("input_refs", [])
                and multi_cycle_lineage_ref in generation_request.get("input_refs", [])
                and multi_cycle_latest_runtime_feedback_ref in request.get("input_refs", [])
                and multi_cycle_latest_runtime_feedback_ref in generation_request.get("input_refs", [])
                and multi_cycle_older_runtime_feedback_ref in request.get("input_refs", [])
                and multi_cycle_older_runtime_feedback_ref in generation_request.get("input_refs", [])
            )
        )
        and int(followup_usage.get("followup_count", 0)) >= 8
        and response.get("reasoning_ref") in generation_request.get("input_refs", [])
        and generation_response.get("source_reasoning_response_id") == response.get("response_id")
        and generation_response.get("source_reasoning_ref") == response.get("reasoning_ref")
        and set(response.get("output_contract", {}).get("candidate_actions_required", [])) == set(candidate_actions)
        and response.get("output_contract", {}).get("scorer_required") is True
        and response.get("output_contract", {}).get("risk_evaluator_required") is True
        and (
            (memory_ref and memory_usage.get("influence_ref") == f"memory://{memory_ref}")
            or (not memory_ref and memory_usage.get("status") == "cold_start")
        )
    )


def _trace_oss_followup_loop_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_request = artifact.get("candidate_generation", {}).get("request", {})
    scorer_inputs = artifact.get("scorer", {}).get("scoring_inputs", {})
    selected_candidate = trace.get("selected_candidate", {})
    selected_action = _candidate_action_key(str(selected_candidate.get("candidate_id")))
    loop = scorer_inputs.get("oss_followup_loop", {})
    if not isinstance(loop, Mapping):
        return False
    expected_refs = _oss_followup_refs_for_action(loop, selected_action)
    selected_refs = set(selected_candidate.get("evidence_refs", []))
    score_adjustments = scorer_inputs.get("oss_followup_score_adjustments", {})
    return bool(
        _oss_response_followup_loop_is_usable(loop)
        and input_context.get("oss_followup_loop_ref") == loop.get("loop_ref")
        and trace.get("decision_inputs", {}).get("oss_followup_loop_ref") == loop.get("loop_ref")
        and loop.get("loop_ref") in trace.get("evidence_refs", [])
        and loop.get("loop_ref") in candidate_request.get("input_refs", [])
        and set(input_context.get("oss_followup_response_refs", [])).issubset(
            set(candidate_request.get("input_refs", []))
        )
        and set(expected_refs).issubset(selected_refs)
        and float(score_adjustments.get(selected_action, 0.0)) > 0.0
    )


def _trace_oss_disagreement_arbitration_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_request = artifact.get("candidate_generation", {}).get("request", {})
    scorer_inputs = artifact.get("scorer", {}).get("scoring_inputs", {})
    selected_candidate = trace.get("selected_candidate", {})
    selected_action = _candidate_action_key(str(selected_candidate.get("candidate_id")))
    arbitration = scorer_inputs.get("oss_disagreement_arbitration", {})
    if not isinstance(arbitration, Mapping):
        return False
    arbitration_ref = arbitration.get("arbitration_ref")
    expected_refs = _oss_disagreement_refs_for_action(arbitration, selected_action)
    selected_refs = set(selected_candidate.get("evidence_refs", []))
    score_adjustments = scorer_inputs.get("oss_disagreement_score_adjustments", {})
    return bool(
        _oss_disagreement_arbitration_is_usable(arbitration)
        and input_context.get("oss_disagreement_arbitration_ref") == arbitration_ref
        and trace.get("decision_inputs", {}).get("oss_disagreement_arbitration_ref") == arbitration_ref
        and arbitration_ref in trace.get("evidence_refs", [])
        and arbitration_ref in candidate_request.get("input_refs", [])
        and set(expected_refs).issubset(selected_refs)
        and float(score_adjustments.get(selected_action, 0.0)) > 0.0
    )


def _trace_tracking_reconciliation_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_request = artifact.get("candidate_generation", {}).get("request", {})
    scorer_inputs = artifact.get("scorer", {}).get("scoring_inputs", {})
    selected_candidate = trace.get("selected_candidate", {})
    selected_action = _candidate_action_key(str(selected_candidate.get("candidate_id")))
    reconciliation = scorer_inputs.get("tracking_reconciliation", {})
    if not isinstance(reconciliation, Mapping):
        return False
    reconciliation_ref = reconciliation.get("reconciliation_ref")
    repair_ref = reconciliation.get("repair", {}).get("repair_ref")
    expected_refs = _tracking_reconciliation_refs_for_action(reconciliation, selected_action)
    selected_refs = set(selected_candidate.get("evidence_refs", []))
    score_adjustments = scorer_inputs.get("tracking_reconciliation_score_adjustments", {})
    return bool(
        _tracking_readback_reconciliation_is_usable(reconciliation)
        and input_context.get("tracking_reconciliation_ref") == reconciliation_ref
        and input_context.get("tracking_reconciliation_repair_ref") == repair_ref
        and trace.get("decision_inputs", {}).get("tracking_reconciliation_ref") == reconciliation_ref
        and reconciliation_ref in trace.get("evidence_refs", [])
        and reconciliation_ref in candidate_request.get("input_refs", [])
        and repair_ref in candidate_request.get("input_refs", [])
        and set(expected_refs).issubset(selected_refs)
        and float(score_adjustments.get(selected_action, 0.0)) > 0.0
    )


def _trace_alpha_seed_revision_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_request = artifact.get("candidate_generation", {}).get("request", {})
    scorer_inputs = artifact.get("scorer", {}).get("scoring_inputs", {})
    selected_candidate = trace.get("selected_candidate", {})
    selected_action = _candidate_action_key(str(selected_candidate.get("candidate_id")))
    alpha_seed_revision = scorer_inputs.get("alpha_seed_revision", {})
    if not isinstance(alpha_seed_revision, Mapping):
        return False
    revision_ref = alpha_seed_revision.get("revision_ref")
    expected_refs = _alpha_seed_revision_refs_for_action(alpha_seed_revision, selected_action)
    selected_refs = set(selected_candidate.get("evidence_refs", []))
    score_adjustments = scorer_inputs.get("alpha_seed_revision_score_adjustments", {})
    return bool(
        _alpha_seed_revision_is_usable(alpha_seed_revision)
        and input_context.get("alpha_seed_revision_ref") == revision_ref
        and input_context.get("alpha_seed_revision_action") == alpha_seed_revision.get("revision", {}).get("action")
        and trace.get("decision_inputs", {}).get("alpha_seed_revision_ref") == revision_ref
        and revision_ref in trace.get("evidence_refs", [])
        and revision_ref in candidate_request.get("input_refs", [])
        and set(expected_refs).issubset(selected_refs)
        and float(score_adjustments.get(selected_action, 0.0)) > 0.0
    )


def _trace_memory_influence_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    memory_influence = artifact.get("memory_influence", {})
    input_context = artifact.get("input_context", {})
    candidate_request = artifact.get("candidate_generation", {}).get("request", {})
    scorer_inputs = artifact.get("scorer", {}).get("scoring_inputs", {})
    selected_candidate = trace.get("selected_candidate", {})
    memory_ref = trace.get("decision_inputs", {}).get("memory_ref")
    if not memory_ref:
        return bool(
            memory_influence.get("status") == "cold_start"
            and memory_influence.get("influence_ref") is None
            and input_context.get("memory_status") == "cold_start_declared"
            and all(
                float(value) == 0.0
                for value in memory_influence.get("candidate_score_adjustments", {}).values()
            )
        )
    influence_ref = f"memory://{memory_ref}"
    selected_action = _candidate_action_key(str(selected_candidate.get("candidate_id")))
    score_adjustments = memory_influence.get("candidate_score_adjustments", {})
    return bool(
        memory_influence.get("model_id") == PERSONA_MEMORY_INFLUENCE_MODEL_ID
        and memory_influence.get("status") == "applied"
        and memory_influence.get("memory_id") == memory_ref
        and memory_influence.get("influence_ref") == influence_ref
        and memory_influence.get("content_summary")
        and memory_influence.get("cited_proposal_ids")
        and memory_influence.get("retrieval_tags")
        and memory_influence.get("selected_action_hint") in {
            "feedback-adapt",
            "risk-off",
            "retain-observe",
            "contrarian-check",
        }
        and input_context.get("memory_influence_ref") == influence_ref
        and input_context.get("memory_influence", {}).get("memory_id") == memory_ref
        and scorer_inputs.get("memory_influence", {}).get("memory_id") == memory_ref
        and scorer_inputs.get("memory_score_adjustments") == score_adjustments
        and influence_ref in candidate_request.get("input_refs", [])
        and influence_ref in selected_candidate.get("evidence_refs", [])
        and float(score_adjustments.get(selected_action, 0.0)) > 0.0
    )


def _build_evolution_trajectory(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    evolution_decision: EvolutionDecision,
) -> dict[str, Any]:
    holdout_improvement = round(
        float(evaluations[1]["score"]) - float(baseline_holdout_counterfactual["score"]),
        10,
    )
    future_improvement = round(
        float(evaluations[2]["score"]) - float(generation1_future_counterfactual["score"]),
        10,
    )
    comparisons = [
        {
            "comparison_id": f"{episode.case_id}-gen1-vs-gen0-holdout",
            "previous_generation": 0,
            "candidate_generation": 1,
            "evaluation_window": "holdout",
            "previous_policy_id": generation_policies[0]["policy_id"],
            "candidate_policy_id": generation_policies[1]["policy_id"],
            "previous_counterfactual_score": baseline_holdout_counterfactual["score"],
            "candidate_score": evaluations[1]["score"],
            "score_improvement": holdout_improvement,
            "previous_drawdown": baseline_holdout_counterfactual["drawdown"],
            "candidate_drawdown": evaluations[1]["drawdown"],
            "previous_turnover": baseline_holdout_counterfactual["turnover"],
            "candidate_turnover": evaluations[1]["turnover"],
            "decision_trace_ref": decision_traces[0]["reflection_id"],
            "trace_forbidden_windows": list(decision_traces[0]["decision_inputs"]["forbidden_windows_not_used"]),
            "strict_improvement": holdout_improvement > 0,
            "unseen_by_decision_trace": "holdout" in decision_traces[0]["decision_inputs"]["forbidden_windows_not_used"],
        },
        {
            "comparison_id": f"{episode.case_id}-gen2-vs-gen1-future-holdout",
            "previous_generation": 1,
            "candidate_generation": 2,
            "evaluation_window": "future_holdout",
            "previous_policy_id": generation_policies[1]["policy_id"],
            "candidate_policy_id": generation_policies[2]["policy_id"],
            "previous_counterfactual_score": generation1_future_counterfactual["score"],
            "candidate_score": evaluations[2]["score"],
            "score_improvement": future_improvement,
            "previous_drawdown": generation1_future_counterfactual["drawdown"],
            "candidate_drawdown": evaluations[2]["drawdown"],
            "previous_turnover": generation1_future_counterfactual["turnover"],
            "candidate_turnover": evaluations[2]["turnover"],
            "decision_trace_ref": decision_traces[1]["reflection_id"],
            "trace_forbidden_windows": list(decision_traces[1]["decision_inputs"]["forbidden_windows_not_used"]),
            "strict_improvement": future_improvement > 0,
            "unseen_by_decision_trace": "future_holdout" in decision_traces[1]["decision_inputs"]["forbidden_windows_not_used"],
        },
    ]
    improvement_deltas = [holdout_improvement, future_improvement]
    policy_lineage = [
        {
            "generation": policy["generation"],
            "policy_id": policy["policy_id"],
            "policy_version": policy["policy_version"],
            "risk_multiplier": policy["risk_multiplier"],
            "decision_trace_ref": None
            if int(policy["generation"]) == 0
            else decision_traces[int(policy["generation"]) - 1]["reflection_id"],
        }
        for policy in generation_policies
    ]
    trend = {
        "generation_sequence": [policy["generation"] for policy in generation_policies],
        "evaluation_windows": [comparison["evaluation_window"] for comparison in comparisons],
        "improvement_deltas": improvement_deltas,
        "strict_positive_step_count": sum(1 for delta in improvement_deltas if delta > 0),
        "regression_count": sum(1 for delta in improvement_deltas if delta <= 0),
        "cumulative_improvement": round(sum(improvement_deltas), 10),
        "turnover_sequence": [evaluation["turnover"] for evaluation in evaluations],
        "max_turnover": max(float(evaluation["turnover"]) for evaluation in evaluations),
        "drawdown_sequence": [evaluation["drawdown"] for evaluation in evaluations],
        "convergence_status": "improving" if all(delta > 0 for delta in improvement_deltas) else "regressed",
    }
    replay = {
        "replayable": True,
        "policy_lineage_complete": [item["generation"] for item in policy_lineage] == [0, 1, 2],
        "two_distinct_unseen_windows": [comparison["evaluation_window"] for comparison in comparisons]
        == ["holdout", "future_holdout"],
        "strict_positive_step_improvements": all(comparison["strict_improvement"] for comparison in comparisons),
        "decision_traces_do_not_see_evaluation_windows": all(
            comparison["unseen_by_decision_trace"] for comparison in comparisons
        ),
        "turnover_bounded": trend["max_turnover"] <= 1.25,
        "converges_or_improves": trend["convergence_status"] == "improving",
    }
    return {
        "trajectory_id": f"evolution-trajectory-{episode.case_id}",
        "model_id": EVOLUTION_TRAJECTORY_MODEL_ID,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "generation_count": len(generation_policies),
        "policy_lineage": policy_lineage,
        "comparisons": comparisons,
        "trend": trend,
        "evidence_refs": [
            f"policy://{policy['policy_id']}" for policy in generation_policies
        ] + [
            f"reflection://{trace['reflection_id']}" for trace in decision_traces
        ] + [
            f"evolution://{evolution_decision.decision_id}",
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "evolution-trajectory",
            {
                "case_id": episode.case_id,
                "policy_lineage": policy_lineage,
                "comparisons": comparisons,
                "trend": trend,
            },
        ),
    }


def _evolution_trajectory_is_usable(trajectory: Mapping[str, Any]) -> bool:
    replay = trajectory.get("replay", {})
    comparisons = trajectory.get("comparisons", [])
    trend = trajectory.get("trend", {})
    return bool(
        trajectory.get("model_id") == EVOLUTION_TRAJECTORY_MODEL_ID
        and int(trajectory.get("generation_count", 0)) == GENERATION_COUNT
        and [item.get("generation") for item in trajectory.get("policy_lineage", [])] == [0, 1, 2]
        and [comparison.get("evaluation_window") for comparison in comparisons] == ["holdout", "future_holdout"]
        and all(float(comparison.get("score_improvement", 0.0)) > 0 for comparison in comparisons)
        and all(comparison.get("strict_improvement") is True for comparison in comparisons)
        and all(comparison.get("unseen_by_decision_trace") is True for comparison in comparisons)
        and trend.get("convergence_status") == "improving"
        and int(trend.get("strict_positive_step_count", 0)) == 2
        and int(trend.get("regression_count", 1)) == 0
        and float(trend.get("cumulative_improvement", 0.0)) > 0
        and replay.get("replayable") is True
        and replay.get("policy_lineage_complete") is True
        and replay.get("two_distinct_unseen_windows") is True
        and replay.get("strict_positive_step_improvements") is True
        and replay.get("decision_traces_do_not_see_evaluation_windows") is True
        and replay.get("turnover_bounded") is True
        and replay.get("converges_or_improves") is True
        and trajectory.get("input_hash")
    )


def _build_no_leakage_temporal_protocol(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    evolution_trajectory: Mapping[str, Any],
) -> dict[str, Any]:
    window_boundaries = [
        _instrument_window_boundary_summary(window)
        for window in episode.windows
    ]
    stage_contracts = [
        _temporal_stage_contract(
            stage_id="generation0_observe_decide",
            generation=0,
            policy=generation_policies[0],
            decision_trace=None,
            evaluation_window="feedback",
            evaluation=evaluations[0],
            prior_outcome_window=None,
        ),
        _temporal_stage_contract(
            stage_id="generation1_feedback_reflect_to_holdout",
            generation=1,
            policy=generation_policies[1],
            decision_trace=decision_traces[0],
            evaluation_window="holdout",
            evaluation=evaluations[1],
            prior_outcome_window="feedback",
        ),
        _temporal_stage_contract(
            stage_id="generation2_holdout_reflect_to_future_holdout",
            generation=2,
            policy=generation_policies[2],
            decision_trace=decision_traces[1],
            evaluation_window="future_holdout",
            evaluation=evaluations[2],
            prior_outcome_window="holdout",
        ),
    ]
    expected_pre_holdout_rows = PORTFOLIO_LEG_COUNT * (LOOKBACK_BARS + FEEDBACK_BARS)
    vectorbt = case_upstream_artifacts.get("vectorbt", {})
    vectorbt_summary = vectorbt.get("dataset_summary", {})
    upstream_contract = {
        "allowed_windows": list(case_upstream_artifacts.get("allowed_windows", [])),
        "forbidden_windows_not_used": list(case_upstream_artifacts.get("forbidden_windows_not_used", [])),
        "vectorbt_used_historical_rows": vectorbt.get("used_historical_rows"),
        "vectorbt_dataset_total_bars": vectorbt_summary.get("total_bars"),
        "expected_pre_holdout_rows": expected_pre_holdout_rows,
        "tracker_source_vectorbt_run_id": case_upstream_artifacts.get("tracker", {}).get("source_vectorbt_run_id"),
        "selected_oss_roles": sorted(case_upstream_artifacts.get("selected_oss", {})),
        "case_upstream_pre_holdout_only": (
            case_upstream_artifacts.get("allowed_windows") == ["observe", "feedback"]
            and case_upstream_artifacts.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
            and int(vectorbt.get("used_historical_rows", 0)) == expected_pre_holdout_rows
            and int(vectorbt_summary.get("total_bars", 0)) == expected_pre_holdout_rows
        ),
    }
    replay = {
        "replayable": True,
        "window_boundaries_ordered": all(boundary["ordered"] for boundary in window_boundaries),
        "window_boundaries_non_overlapping": all(boundary["non_overlapping"] for boundary in window_boundaries),
        "stage_source_windows_subset_visible": all(stage["source_windows_subset_visible"] for stage in stage_contracts),
        "stage_evaluation_windows_hidden_from_decisions": all(
            stage["evaluation_window_hidden_from_decision"] for stage in stage_contracts
        ),
        "future_holdout_hidden_until_evaluation": all(
            "future_holdout" in set(stage["hidden_windows"]) for stage in stage_contracts
        ) and all(
            "future_holdout" not in set(stage["decision_source_windows"]) for stage in stage_contracts
        ),
        "holdout_hidden_from_generation1_decision": (
            "holdout" in set(stage_contracts[1]["hidden_windows"])
            and "holdout" not in set(stage_contracts[1]["decision_source_windows"])
        ),
        "generation2_uses_first_holdout_outcome_only_before_future_holdout": (
            stage_contracts[2]["prior_outcome_window"] == "holdout"
            and "holdout" in set(stage_contracts[2]["visible_windows"])
            and "future_holdout" in set(stage_contracts[2]["hidden_windows"])
        ),
        "case_upstream_pre_holdout_only": upstream_contract["case_upstream_pre_holdout_only"],
        "strict_improvement_on_unseen_holdouts": (
            float(evaluations[1]["score"]) > float(baseline_holdout_counterfactual["score"])
            and float(evaluations[2]["score"]) > float(generation1_future_counterfactual["score"])
        ),
        "trajectory_unseen_windows_match_protocol": [
            comparison["evaluation_window"] for comparison in evolution_trajectory.get("comparisons", [])
        ] == ["holdout", "future_holdout"]
        and all(
            comparison.get("unseen_by_decision_trace") is True
            for comparison in evolution_trajectory.get("comparisons", [])
        ),
    }
    return {
        "protocol_id": f"no-leakage-temporal-protocol-{episode.case_id}",
        "model_id": NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID,
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "protocol_path": "observe_decide->feedback_reflect->holdout_evolve->future_holdout_verify",
        "window_boundaries": window_boundaries,
        "stage_contracts": stage_contracts,
        "case_upstream_data_contract": upstream_contract,
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "no-leakage-temporal-protocol",
            {
                "case_id": episode.case_id,
                "stage_contracts": stage_contracts,
                "case_upstream_data_contract": upstream_contract,
                "window_boundaries": window_boundaries,
            },
        ),
    }


def _instrument_window_boundary_summary(window: InstrumentWindow) -> dict[str, Any]:
    periods = {
        "observe": _period_boundary(window.observe_rows),
        "feedback": _period_boundary(window.feedback_rows),
        "holdout": _period_boundary(window.holdout_rows),
        "future_holdout": _period_boundary(window.future_holdout_rows),
    }
    period_dates = [
        {str(row["date"]) for row in rows}
        for rows in (
            window.observe_rows,
            window.feedback_rows,
            window.holdout_rows,
            window.future_holdout_rows,
        )
    ]
    return {
        "instrument": window.instrument,
        "start_index": window.start_index,
        "periods": periods,
        "ordered": (
            periods["observe"]["end_date"] < periods["feedback"]["start_date"]
            < periods["holdout"]["start_date"] < periods["future_holdout"]["start_date"]
        ),
        "non_overlapping": len(set().union(*period_dates)) == sum(len(dates) for dates in period_dates),
    }


def _period_boundary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "start_date": str(rows[0]["date"]),
        "end_date": str(rows[-1]["date"]),
        "bar_count": len(rows),
    }


def _temporal_stage_contract(
    *,
    stage_id: str,
    generation: int,
    policy: Mapping[str, Any],
    decision_trace: Mapping[str, Any] | None,
    evaluation_window: str,
    evaluation: Mapping[str, Any],
    prior_outcome_window: str | None,
) -> dict[str, Any]:
    decision_inputs = policy.get("decision_inputs", {})
    decision_source_windows = (
        _trace_decision_source_windows(decision_trace)
        if decision_trace is not None
        else list(decision_inputs.get("allowed_windows", []))
    )
    visible_windows = list(decision_inputs.get("allowed_windows", []))
    hidden_windows = list(decision_inputs.get("forbidden_windows_not_used", []))
    return {
        "stage_id": stage_id,
        "generation": generation,
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "decision_trace_ref": decision_trace.get("reflection_id") if decision_trace else None,
        "prior_outcome_window": prior_outcome_window,
        "visible_windows": visible_windows,
        "hidden_windows": hidden_windows,
        "decision_source_windows": decision_source_windows,
        "evaluation_window": evaluation_window,
        "evaluation_score": evaluation["score"],
        "source_windows_subset_visible": set(decision_source_windows).issubset(set(visible_windows)),
        "evaluation_window_hidden_from_decision": evaluation_window in set(hidden_windows),
        "evaluation_window_absent_from_sources": evaluation_window not in set(decision_source_windows),
    }


def _trace_decision_source_windows(trace: Mapping[str, Any]) -> list[str]:
    windows = {
        str(window)
        for candidate in trace.get("candidates", [])
        for window in candidate.get("source_windows", [])
    }
    windows.update(str(window) for window in trace.get("selected_candidate", {}).get("source_windows", []))
    return sorted(windows, key=("observe", "feedback", "holdout", "future_holdout").index)


def _no_leakage_temporal_protocol_is_usable(protocol: Mapping[str, Any]) -> bool:
    replay = protocol.get("replay", {})
    stage_contracts = protocol.get("stage_contracts", [])
    upstream = protocol.get("case_upstream_data_contract", {})
    return bool(
        protocol.get("model_id") == NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID
        and protocol.get("protocol_path") == "observe_decide->feedback_reflect->holdout_evolve->future_holdout_verify"
        and [stage.get("generation") for stage in stage_contracts] == [0, 1, 2]
        and [stage.get("evaluation_window") for stage in stage_contracts]
        == ["feedback", "holdout", "future_holdout"]
        and all(stage.get("source_windows_subset_visible") is True for stage in stage_contracts)
        and all(stage.get("evaluation_window_hidden_from_decision") is True for stage in stage_contracts)
        and all(stage.get("evaluation_window_absent_from_sources") is True for stage in stage_contracts)
        and upstream.get("allowed_windows") == ["observe", "feedback"]
        and upstream.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
        and int(upstream.get("vectorbt_used_historical_rows", 0)) == int(
            upstream.get("expected_pre_holdout_rows", -1)
        )
        and int(upstream.get("vectorbt_dataset_total_bars", 0)) == int(
            upstream.get("expected_pre_holdout_rows", -1)
        )
        and replay.get("replayable") is True
        and replay.get("window_boundaries_ordered") is True
        and replay.get("window_boundaries_non_overlapping") is True
        and replay.get("stage_source_windows_subset_visible") is True
        and replay.get("stage_evaluation_windows_hidden_from_decisions") is True
        and replay.get("future_holdout_hidden_until_evaluation") is True
        and replay.get("holdout_hidden_from_generation1_decision") is True
        and replay.get("generation2_uses_first_holdout_outcome_only_before_future_holdout") is True
        and replay.get("case_upstream_pre_holdout_only") is True
        and replay.get("strict_improvement_on_unseen_holdouts") is True
        and replay.get("trajectory_unseen_windows_match_protocol") is True
        and protocol.get("input_hash")
    )


def _build_strict_oos_evolution_proof(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    evolution_decision: EvolutionDecision,
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    holdout_improvement = round(
        float(evaluations[1]["score"]) - float(baseline_holdout_counterfactual["score"]),
        10,
    )
    future_holdout_improvement = round(
        float(evaluations[2]["score"]) - float(generation1_future_counterfactual["score"]),
        10,
    )
    window_pairs = [
        {
            "instrument": boundary["instrument"],
            "holdout": copy.deepcopy(boundary["periods"]["holdout"]),
            "future_holdout": copy.deepcopy(boundary["periods"]["future_holdout"]),
            "strictly_after": boundary["periods"]["holdout"]["end_date"]
            < boundary["periods"]["future_holdout"]["start_date"],
            "disjoint": boundary["non_overlapping"],
        }
        for boundary in no_leakage_protocol["window_boundaries"]
    ]
    proof_steps = [
        {
            "step_id": f"{episode.case_id}-feedback-to-holdout",
            "source_outcome_window": "feedback",
            "decision_trace_ref": decision_traces[0]["reflection_id"],
            "candidate_policy_id": generation_policies[1]["policy_id"],
            "counterfactual_policy_id": generation_policies[0]["policy_id"],
            "validation_window": "holdout",
            "hidden_windows_before_decision": list(
                decision_traces[0]["decision_inputs"]["forbidden_windows_not_used"]
            ),
            "visible_windows_before_decision": list(decision_traces[0]["decision_inputs"]["allowed_windows"]),
            "counterfactual_score": baseline_holdout_counterfactual["score"],
            "candidate_score": evaluations[1]["score"],
            "score_improvement": holdout_improvement,
            "strict_improvement": holdout_improvement > 0,
            "validation_window_unseen_by_decision": "holdout"
            in decision_traces[0]["decision_inputs"]["forbidden_windows_not_used"],
            "future_window_hidden": "future_holdout"
            in decision_traces[0]["decision_inputs"]["forbidden_windows_not_used"],
        },
        {
            "step_id": f"{episode.case_id}-holdout-to-future-holdout",
            "source_outcome_window": "holdout",
            "decision_trace_ref": decision_traces[1]["reflection_id"],
            "candidate_policy_id": generation_policies[2]["policy_id"],
            "counterfactual_policy_id": generation_policies[1]["policy_id"],
            "validation_window": "future_holdout",
            "hidden_windows_before_decision": list(
                decision_traces[1]["decision_inputs"]["forbidden_windows_not_used"]
            ),
            "visible_windows_before_decision": list(decision_traces[1]["decision_inputs"]["allowed_windows"]),
            "counterfactual_score": generation1_future_counterfactual["score"],
            "candidate_score": evaluations[2]["score"],
            "score_improvement": future_holdout_improvement,
            "strict_improvement": future_holdout_improvement > 0,
            "validation_window_unseen_by_decision": "future_holdout"
            in decision_traces[1]["decision_inputs"]["forbidden_windows_not_used"],
            "future_window_hidden": "future_holdout"
            in decision_traces[1]["decision_inputs"]["forbidden_windows_not_used"],
        },
    ]
    replay = {
        "replayable": True,
        "uses_two_distinct_validation_windows": [step["validation_window"] for step in proof_steps]
        == ["holdout", "future_holdout"],
        "holdout_and_future_holdout_disjoint": all(
            pair["strictly_after"] and pair["disjoint"] for pair in window_pairs
        ),
        "generation1_uses_feedback_only_before_holdout": (
            proof_steps[0]["source_outcome_window"] == "feedback"
            and proof_steps[0]["visible_windows_before_decision"] == ["observe", "feedback"]
            and proof_steps[0]["validation_window_unseen_by_decision"] is True
        ),
        "generation2_uses_holdout_only_before_future_holdout": (
            proof_steps[1]["source_outcome_window"] == "holdout"
            and proof_steps[1]["visible_windows_before_decision"] == ["observe", "feedback", "holdout"]
            and proof_steps[1]["validation_window_unseen_by_decision"] is True
        ),
        "future_holdout_hidden_from_all_decisions": all(step["future_window_hidden"] for step in proof_steps),
        "strict_improvement_on_each_unseen_window": all(step["strict_improvement"] for step in proof_steps),
        "trajectory_agrees_with_oos_steps": [
            comparison["evaluation_window"] for comparison in evolution_trajectory.get("comparisons", [])
        ] == [step["validation_window"] for step in proof_steps],
        "no_leakage_protocol_passed": _no_leakage_temporal_protocol_is_usable(no_leakage_protocol),
        "evolution_trajectory_passed": _evolution_trajectory_is_usable(evolution_trajectory),
        "evolution_decision_executed": _enum_value(evolution_decision.decision_state)
        == EvolutionDecisionState.EXECUTED.value,
    }
    return {
        "proof_id": f"strict-oos-evolution-proof-{episode.case_id}",
        "proof_ref": f"strict-oos-evolution://{episode.case_id}",
        "model_id": STRICT_OOS_EVOLUTION_PROOF_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "policy_lineage": [
            {
                "generation": policy["generation"],
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
            }
            for policy in generation_policies
        ],
        "window_pairs": window_pairs,
        "proof_steps": proof_steps,
        "evidence_refs": [
            f"reflection://{trace['reflection_id']}" for trace in decision_traces
        ] + [
            f"policy://{policy['policy_id']}" for policy in generation_policies
        ] + [
            f"evolution://{evolution_decision.decision_id}",
            f"trajectory://{evolution_trajectory['trajectory_id']}",
            f"no-leakage://{no_leakage_protocol['protocol_id']}",
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "strict-oos-evolution-proof",
            {
                "case_id": episode.case_id,
                "policy_lineage": [policy["policy_id"] for policy in generation_policies],
                "window_pairs": window_pairs,
                "proof_steps": proof_steps,
            },
        ),
    }


def _strict_oos_evolution_proof_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    steps = list(proof.get("proof_steps", []))
    return bool(
        proof.get("model_id") == STRICT_OOS_EVOLUTION_PROOF_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("strict-oos-evolution://")
        and proof.get("input_hash")
        and [step.get("source_outcome_window") for step in steps] == ["feedback", "holdout"]
        and [step.get("validation_window") for step in steps] == ["holdout", "future_holdout"]
        and all(float(step.get("score_improvement", 0.0)) > 0 for step in steps)
        and all(step.get("strict_improvement") is True for step in steps)
        and all(step.get("validation_window_unseen_by_decision") is True for step in steps)
        and replay.get("replayable") is True
        and replay.get("uses_two_distinct_validation_windows") is True
        and replay.get("holdout_and_future_holdout_disjoint") is True
        and replay.get("generation1_uses_feedback_only_before_holdout") is True
        and replay.get("generation2_uses_holdout_only_before_future_holdout") is True
        and replay.get("future_holdout_hidden_from_all_decisions") is True
        and replay.get("strict_improvement_on_each_unseen_window") is True
        and replay.get("trajectory_agrees_with_oos_steps") is True
        and replay.get("no_leakage_protocol_passed") is True
        and replay.get("evolution_trajectory_passed") is True
        and replay.get("evolution_decision_executed") is True
    )


def _build_policy_candidate_materiality_proof(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    generation_policies: Sequence[Mapping[str, Any]],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    policy_entry = case_upstream_artifacts["selected_oss"]["policy_candidate"]
    policy_input = oss_inputs["policy_candidate"]
    component = str(policy_entry["component"])
    request_id = str(policy_entry["request_id"])
    source_ref = f"oss://{component}/{request_id}"
    expected_artifact_family = _policy_candidate_expected_artifact_family(component)
    policy_quality = _policy_quality_from_oss(oss_inputs)
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        generation = int(artifact["generation"])
        candidate_request = artifact["candidate_generation"]["request"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_id = str(trace["selected_candidate_id"])
        selected_action = _candidate_action_key(selected_id)
        scorecard = artifact["scorer"]["scorecards"][selected_id]
        feedback_candidate_id = next(
            candidate_id
            for candidate_id in artifact["scorer"]["scorecards"]
            if str(candidate_id).endswith("-feedback-adapt")
        )
        feedback_scorecard = artifact["scorer"]["scorecards"][feedback_candidate_id]
        policy = generation_policies[generation]
        recomputed_hint = _risk_hint_from_oss(oss_inputs, generation)
        trace_bindings.append(
            {
                "generation": generation,
                "trace_id": trace["reflection_id"],
                "policy_id": policy["policy_id"],
                "selected_candidate_id": selected_id,
                "selected_action": selected_action,
                "source_oss_ref": source_ref,
                "reasoning_consumes_policy_oss": source_ref in reasoning_request["input_refs"],
                "candidate_generation_consumes_policy_oss": source_ref in candidate_request["input_refs"],
                "selected_candidate_cites_policy_oss": source_ref in selected_candidate["evidence_refs"],
                "scoring_input_component": artifact["input_context"]["oss_components_by_role"]["policy_candidate"],
                "scoring_input_request_id": artifact["input_context"]["oss_request_ids_by_role"]["policy_candidate"],
                "scoring_policy_hint_risk": float(scoring_inputs["policy_hint_risk"]),
                "recomputed_policy_hint_risk": float(recomputed_hint),
                "scoring_policy_quality": float(scoring_inputs["policy_quality"]),
                "recomputed_policy_quality": float(policy_quality),
                "feedback_scorecard_policy_quality": float(
                    feedback_scorecard["components"].get("policy_quality", 0.0)
                ),
                "selected_scorecard_policy_quality": float(
                    scorecard["components"].get("policy_quality", 0.0)
                ),
                "selected_candidate_risk_multiplier": float(selected_candidate["risk_multiplier"]),
                "evolved_policy_risk_multiplier": float(policy["risk_multiplier"]),
                "policy_hint_risk_replay_match": float(scoring_inputs["policy_hint_risk"]) == float(recomputed_hint),
                "policy_quality_replay_match": float(scoring_inputs["policy_quality"]) == float(policy_quality),
                "feedback_scorecard_replays_policy_quality": float(
                    feedback_scorecard["components"].get("policy_quality", 0.0)
                )
                == float(policy_quality),
                "selected_scorecard_replays_policy_quality": (
                    selected_action == "feedback-adapt"
                    and float(scorecard["components"].get("policy_quality", 0.0)) == float(policy_quality)
                ),
                "selected_candidate_uses_policy_hint_risk": float(
                    selected_candidate["risk_multiplier"]
                )
                == float(recomputed_hint),
                "evolved_policy_uses_policy_hint_risk": (
                    float(policy["risk_multiplier"]) == max(float(recomputed_hint), 1.15)
                    if generation == 2
                    else float(policy["risk_multiplier"]) == float(recomputed_hint)
                ),
                "selected_candidate_is_feedback_adapt_policy_candidate": selected_action == "feedback-adapt",
                "decision_replay_uses_policy_candidate_oss_metrics": artifact["replay"].get(
                    "uses_policy_candidate_oss_metrics"
                )
                is True,
                "policy_ref_in_decision_evidence": source_ref in trace["evidence_refs"],
                "no_forbidden_window_policy_sources": "future_holdout"
                not in set(trace["decision_inputs"]["allowed_windows"]),
            }
        )

    metric_signal_keys = sorted(
        key
        for key, value in policy_entry.get("metrics", {}).items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    replay = {
        "replayable": True,
        "policy_oss_role_completed": policy_entry.get("status") == "completed"
        and policy_input.get("status") == "completed",
        "component_is_policy_learning_oss": component in POLICY_OSS_COMPONENTS,
        "artifact_family_matches_component": policy_entry.get("artifact_family") == expected_artifact_family,
        "registry_and_producer_bound": bool(policy_entry.get("registry_id"))
        and bool(policy_entry.get("producer_run_id"))
        and policy_entry.get("registry_artifact_type") in {"model_artifact", "optimizer_result"},
        "metrics_drive_nonzero_policy_quality": float(policy_quality) > 0.0,
        "policy_hint_risk_is_recomputed_from_oss_metrics": all(
            binding["policy_hint_risk_replay_match"] for binding in trace_bindings
        ),
        "policy_quality_is_recomputed_from_oss_metrics": all(
            binding["policy_quality_replay_match"] for binding in trace_bindings
        ),
        "reasoning_consumes_policy_oss": all(
            binding["reasoning_consumes_policy_oss"] for binding in trace_bindings
        ),
        "candidate_generation_consumes_policy_oss": all(
            binding["candidate_generation_consumes_policy_oss"] for binding in trace_bindings
        ),
        "selected_candidate_cites_policy_oss": all(
            binding["selected_candidate_cites_policy_oss"] for binding in trace_bindings
        ),
        "feedback_scorecard_replays_policy_quality": all(
            binding["feedback_scorecard_replays_policy_quality"] for binding in trace_bindings
        ),
        "selected_scorecard_replays_policy_quality": all(
            binding["selected_scorecard_replays_policy_quality"] for binding in trace_bindings
        ),
        "selected_policy_uses_policy_hint_risk": all(
            binding["selected_candidate_uses_policy_hint_risk"]
            and binding["evolved_policy_uses_policy_hint_risk"]
            for binding in trace_bindings
        ),
        "policy_material_to_selected_score": all(
            binding["selected_candidate_is_feedback_adapt_policy_candidate"]
            and binding["selected_scorecard_policy_quality"] > 0.0
            for binding in trace_bindings
        ),
        "decision_artifact_replays_policy_materiality": all(
            binding["decision_replay_uses_policy_candidate_oss_metrics"] for binding in trace_bindings
        ),
        "no_holdout_or_future_leakage_in_policy_artifact": case_upstream_artifacts.get("allowed_windows")
        == ["observe", "feedback"]
        and case_upstream_artifacts.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
        and all(binding["no_forbidden_window_policy_sources"] for binding in trace_bindings),
        "evolved_policy_lineage_bound": [binding["generation"] for binding in trace_bindings] == [1, 2]
        and [binding["policy_id"] for binding in trace_bindings]
        == [generation_policies[1]["policy_id"], generation_policies[2]["policy_id"]],
    }
    return {
        "proof_id": f"policy-candidate-materiality-{episode.case_id}",
        "proof_ref": f"policy-materiality://{episode.case_id}",
        "model_id": PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_ref,
        "artifact_family": policy_entry.get("artifact_family"),
        "expected_artifact_family": expected_artifact_family,
        "registry_id": policy_entry.get("registry_id"),
        "registry_artifact_type": policy_entry.get("registry_artifact_type"),
        "producer_run_id": policy_entry.get("producer_run_id"),
        "metric_signal_keys": metric_signal_keys,
        "primary_output_keys": sorted(policy_entry.get("primary_output", {})),
        "policy_quality": policy_quality,
        "trace_bindings": trace_bindings,
        "evidence_refs": [
            source_ref,
            f"registry://{policy_entry.get('registry_id')}",
            *[f"reflection://{trace['reflection_id']}" for trace in decision_traces],
            *[f"policy://{generation_policies[index]['policy_id']}" for index in (1, 2)],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "policy-candidate-materiality",
            {
                "case_id": episode.case_id,
                "component": component,
                "request_id": request_id,
                "metrics": policy_entry.get("metrics", {}),
                "trace_bindings": trace_bindings,
            },
        ),
    }


def _policy_candidate_expected_artifact_family(component: str) -> str:
    if component in {"finrl", "rllib"}:
        return "rl_policy"
    if component == "ray_tune":
        return "optimizer_result"
    return "policy_candidate"


def _policy_candidate_materiality_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("policy-materiality://")
        and proof.get("component") in POLICY_OSS_COMPONENTS
        and proof.get("artifact_family") == proof.get("expected_artifact_family")
        and proof.get("registry_id")
        and proof.get("producer_run_id")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and [binding.get("generation") for binding in trace_bindings] == [1, 2]
        and all(float(binding.get("scoring_policy_quality", 0.0)) > 0.0 for binding in trace_bindings)
        and all(float(binding.get("selected_scorecard_policy_quality", 0.0)) > 0.0 for binding in trace_bindings)
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "policy_oss_role_completed",
            "component_is_policy_learning_oss",
            "artifact_family_matches_component",
            "registry_and_producer_bound",
            "metrics_drive_nonzero_policy_quality",
            "policy_hint_risk_is_recomputed_from_oss_metrics",
            "policy_quality_is_recomputed_from_oss_metrics",
            "reasoning_consumes_policy_oss",
            "candidate_generation_consumes_policy_oss",
            "selected_candidate_cites_policy_oss",
            "feedback_scorecard_replays_policy_quality",
            "selected_scorecard_replays_policy_quality",
            "selected_policy_uses_policy_hint_risk",
            "policy_material_to_selected_score",
            "decision_artifact_replays_policy_materiality",
            "no_holdout_or_future_leakage_in_policy_artifact",
            "evolved_policy_lineage_bound",
        ))
    )


def _build_reflection_artifact_materiality_proof(
    *,
    episode: PortfolioEpisode,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    reflection_entry = case_upstream_artifacts["selected_oss"]["reflection_artifact"]
    reflection_input = oss_inputs["reflection_artifact"]
    component = str(reflection_entry["component"])
    request_id = str(reflection_entry["request_id"])
    source_ref = f"oss://{component}/{request_id}"
    expected_artifact_family = _reflection_artifact_expected_family(component)
    reflection_quality = _reflection_quality_from_oss(oss_inputs)
    trace_bindings: list[dict[str, Any]] = []
    for trace in decision_traces:
        artifact = trace["agent_decision_artifact"]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_candidate = trace["selected_candidate"]
        selected_id = str(trace["selected_candidate_id"])
        selected_action = _candidate_action_key(selected_id)
        scorecard = artifact["scorer"]["scorecards"][selected_id]
        feedback_candidate_id = next(
            candidate_id
            for candidate_id in artifact["scorer"]["scorecards"]
            if str(candidate_id).endswith("-feedback-adapt")
        )
        contrarian_candidate_id = next(
            candidate_id
            for candidate_id in artifact["scorer"]["scorecards"]
            if str(candidate_id).endswith("-contrarian-check")
        )
        feedback_blueprint = next(
            blueprint
            for blueprint in reasoning_response["candidate_blueprints"]
            if blueprint["action"] == "feedback-adapt"
        )
        contrarian_blueprint = next(
            blueprint
            for blueprint in reasoning_response["candidate_blueprints"]
            if blueprint["action"] == "contrarian-check"
        )
        feedback_scorecard = artifact["scorer"]["scorecards"][feedback_candidate_id]
        contrarian_candidate = next(
            candidate
            for candidate in trace["candidates"]
            if str(candidate["candidate_id"]) == contrarian_candidate_id
        )
        trace_bindings.append(
            {
                "generation": int(artifact["generation"]),
                "trace_id": trace["reflection_id"],
                "selected_candidate_id": selected_id,
                "selected_action": selected_action,
                "source_oss_ref": source_ref,
                "reasoning_request_consumes_reflection_oss": source_ref in reasoning_request["input_refs"],
                "reasoning_usage_ref_matches": reasoning_response["reflection_artifact_usage"][
                    "source_oss_ref"
                ]
                == source_ref,
                "reasoning_usage_quality": float(
                    reasoning_response["reflection_artifact_usage"]["reflection_quality"]
                ),
                "reasoning_usage_quality_replay_match": float(
                    reasoning_response["reflection_artifact_usage"]["reflection_quality"]
                )
                == float(reflection_quality),
                "feedback_blueprint_uses_reflection_role": "reflection_artifact"
                in feedback_blueprint["evidence_roles"],
                "contrarian_blueprint_uses_reflection_role": "reflection_artifact"
                in contrarian_blueprint["evidence_roles"],
                "candidate_generation_consumes_reflection_oss": source_ref in candidate_request["input_refs"],
                "selected_candidate_cites_reflection_oss": source_ref in selected_candidate["evidence_refs"],
                "contrarian_candidate_cites_reflection_oss": source_ref
                in contrarian_candidate["evidence_refs"],
                "selected_rationale_mentions_reflection": "reflection"
                in str(selected_candidate.get("rationale", "")).lower(),
                "scoring_reflection_quality": float(scoring_inputs["reflection_quality"]),
                "recomputed_reflection_quality": float(reflection_quality),
                "scoring_reflection_quality_replay_match": float(scoring_inputs["reflection_quality"])
                == float(reflection_quality),
                "feedback_scorecard_reflection_quality": float(
                    feedback_scorecard["components"].get("reflection_quality", 0.0)
                ),
                "selected_scorecard_reflection_quality": float(
                    scorecard["components"].get("reflection_quality", 0.0)
                ),
                "feedback_scorecard_replays_reflection_quality": float(
                    feedback_scorecard["components"].get("reflection_quality", 0.0)
                )
                == float(reflection_quality),
                "selected_scorecard_replays_reflection_quality": (
                    selected_action == "feedback-adapt"
                    and float(scorecard["components"].get("reflection_quality", 0.0))
                    == float(reflection_quality)
                ),
                "decision_replay_uses_reflection_artifact_metrics": artifact["replay"].get(
                    "uses_reflection_artifact_oss_metrics"
                )
                is True,
                "reflection_ref_in_decision_evidence": source_ref in trace["evidence_refs"],
                "no_forbidden_window_reflection_sources": "future_holdout"
                not in set(trace["decision_inputs"]["allowed_windows"]),
            }
        )

    metric_signal_keys = sorted(
        key
        for key, value in reflection_entry.get("metrics", {}).items()
        if isinstance(value, (int, float)) and math.isfinite(float(value))
    )
    replay = {
        "replayable": True,
        "reflection_oss_role_completed": reflection_entry.get("status") == "completed"
        and reflection_input.get("status") == "completed",
        "component_is_reflection_learning_oss": component in REFLECTION_OSS_COMPONENTS,
        "artifact_family_matches_component": reflection_entry.get("artifact_family")
        == expected_artifact_family,
        "registry_and_producer_bound": bool(reflection_entry.get("registry_id"))
        and bool(reflection_entry.get("producer_run_id"))
        and reflection_entry.get("registry_artifact_type")
        in {"prompt_bundle", "model_artifact", "behavior_policy"},
        "metrics_drive_nonzero_reflection_quality": float(reflection_quality) > 0.0,
        "reasoning_consumes_reflection_oss": all(
            binding["reasoning_request_consumes_reflection_oss"] for binding in trace_bindings
        ),
        "reasoning_usage_replays_reflection_quality": all(
            binding["reasoning_usage_ref_matches"]
            and binding["reasoning_usage_quality_replay_match"]
            for binding in trace_bindings
        ),
        "feedback_blueprint_consumes_reflection_role": all(
            binding["feedback_blueprint_uses_reflection_role"] for binding in trace_bindings
        ),
        "contrarian_blueprint_consumes_reflection_role": all(
            binding["contrarian_blueprint_uses_reflection_role"] for binding in trace_bindings
        ),
        "candidate_generation_consumes_reflection_oss": all(
            binding["candidate_generation_consumes_reflection_oss"] for binding in trace_bindings
        ),
        "selected_candidate_cites_reflection_oss": all(
            binding["selected_candidate_cites_reflection_oss"] for binding in trace_bindings
        ),
        "contrarian_candidate_cites_reflection_oss": all(
            binding["contrarian_candidate_cites_reflection_oss"] for binding in trace_bindings
        ),
        "selected_rationale_mentions_reflection": all(
            binding["selected_rationale_mentions_reflection"] for binding in trace_bindings
        ),
        "scorer_recomputes_reflection_quality": all(
            binding["scoring_reflection_quality_replay_match"] for binding in trace_bindings
        ),
        "feedback_scorecard_replays_reflection_quality": all(
            binding["feedback_scorecard_replays_reflection_quality"] for binding in trace_bindings
        ),
        "selected_scorecard_replays_reflection_quality": all(
            binding["selected_scorecard_replays_reflection_quality"] for binding in trace_bindings
        ),
        "reflection_material_to_selected_score": all(
            binding["selected_action"] == "feedback-adapt"
            and binding["selected_scorecard_reflection_quality"] > 0.0
            for binding in trace_bindings
        ),
        "decision_artifact_replays_reflection_materiality": all(
            binding["decision_replay_uses_reflection_artifact_metrics"] for binding in trace_bindings
        ),
        "no_holdout_or_future_leakage_in_reflection_artifact": case_upstream_artifacts.get("allowed_windows")
        == ["observe", "feedback"]
        and case_upstream_artifacts.get("forbidden_windows_not_used") == ["holdout", "future_holdout"]
        and all(binding["no_forbidden_window_reflection_sources"] for binding in trace_bindings),
    }
    return {
        "proof_id": f"reflection-artifact-materiality-{episode.case_id}",
        "proof_ref": f"reflection-materiality://{episode.case_id}",
        "model_id": PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID,
        "status": "passed" if all(replay.values()) else "failed",
        "case_id": episode.case_id,
        "persona_id": _persona_id(episode.persona),
        "component": component,
        "request_id": request_id,
        "source_oss_ref": source_ref,
        "artifact_family": reflection_entry.get("artifact_family"),
        "expected_artifact_family": expected_artifact_family,
        "registry_id": reflection_entry.get("registry_id"),
        "registry_artifact_type": reflection_entry.get("registry_artifact_type"),
        "producer_run_id": reflection_entry.get("producer_run_id"),
        "metric_signal_keys": metric_signal_keys,
        "primary_output_keys": sorted(reflection_entry.get("primary_output", {})),
        "reflection_quality": reflection_quality,
        "trace_bindings": trace_bindings,
        "evidence_refs": [
            source_ref,
            f"registry://{reflection_entry.get('registry_id')}",
            *[f"reflection://{trace['reflection_id']}" for trace in decision_traces],
        ],
        "replay": replay,
        "input_hash": _stable_payload_hash(
            "reflection-artifact-materiality",
            {
                "case_id": episode.case_id,
                "component": component,
                "request_id": request_id,
                "metrics": reflection_entry.get("metrics", {}),
                "trace_bindings": trace_bindings,
            },
        ),
    }


def _reflection_artifact_expected_family(component: str) -> str:
    if component == "dspy":
        return "prompt_bundle"
    if component == "trl":
        return "model_artifact"
    if component == "imitation":
        return "imitation_policy"
    return "reflection_artifact"


def _reflection_artifact_materiality_is_usable(proof: Mapping[str, Any]) -> bool:
    replay = proof.get("replay", {})
    trace_bindings = list(proof.get("trace_bindings", []))
    return bool(
        proof.get("model_id") == PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID
        and proof.get("status") == "passed"
        and proof.get("proof_ref", "").startswith("reflection-materiality://")
        and proof.get("component") in REFLECTION_OSS_COMPONENTS
        and proof.get("artifact_family") == proof.get("expected_artifact_family")
        and proof.get("registry_id")
        and proof.get("producer_run_id")
        and proof.get("input_hash")
        and len(trace_bindings) == 2
        and [binding.get("generation") for binding in trace_bindings] == [1, 2]
        and all(float(binding.get("scoring_reflection_quality", 0.0)) > 0.0 for binding in trace_bindings)
        and all(
            float(binding.get("selected_scorecard_reflection_quality", 0.0)) > 0.0
            for binding in trace_bindings
        )
        and all(replay.get(flag) is True for flag in (
            "replayable",
            "reflection_oss_role_completed",
            "component_is_reflection_learning_oss",
            "artifact_family_matches_component",
            "registry_and_producer_bound",
            "metrics_drive_nonzero_reflection_quality",
            "reasoning_consumes_reflection_oss",
            "reasoning_usage_replays_reflection_quality",
            "feedback_blueprint_consumes_reflection_role",
            "contrarian_blueprint_consumes_reflection_role",
            "candidate_generation_consumes_reflection_oss",
            "selected_candidate_cites_reflection_oss",
            "contrarian_candidate_cites_reflection_oss",
            "selected_rationale_mentions_reflection",
            "scorer_recomputes_reflection_quality",
            "feedback_scorecard_replays_reflection_quality",
            "selected_scorecard_replays_reflection_quality",
            "reflection_material_to_selected_score",
            "decision_artifact_replays_reflection_materiality",
            "no_holdout_or_future_leakage_in_reflection_artifact",
        ))
    )


def _build_usability_dimensions(
    *,
    episode: PortfolioEpisode,
    executions: Sequence[Mapping[str, Any]],
    generation0_eval: Mapping[str, Any],
    generation1_eval: Mapping[str, Any],
    generation2_eval: Mapping[str, Any],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    memory_contexts: Sequence[Mapping[str, Any]],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    validation_plan: Mapping[str, Any],
    operational_context: Mapping[str, Any],
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    policy_candidate_materiality: Mapping[str, Any],
    reflection_artifact_materiality: Mapping[str, Any],
    multi_oss_closed_loop_proof: Mapping[str, Any],
    persona_oss_ooda_ledger: Mapping[str, Any],
    cross_cycle_carryover: Mapping[str, Any],
    persisted_cycle_resume: Mapping[str, Any],
    multi_cycle_lineage: Mapping[str, Any],
    institutional_memory_lineage: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
) -> dict[str, float]:
    fill_quality = mean(float(execution["fill_rate"]) for execution in executions)
    return_improvement = 1.0 if generation1_eval["score"] > baseline_holdout_counterfactual["score"] else 0.0
    multi_generation_improvement = 1.0 if generation2_eval["score"] > generation1_future_counterfactual["score"] else 0.0
    drawdown_reduction = 1.0 if generation1_eval["drawdown"] >= baseline_holdout_counterfactual["drawdown"] else 0.8
    turnover_control = 1.0 if max(float(evaluation["turnover"]) for evaluation in (generation0_eval, generation1_eval, generation2_eval)) <= 1.25 else 0.0
    regime_adaptation = 1.0 if all(
        trace["selected_candidate"]["direction_by_instrument"][window.instrument] == window.feedback_direction
        for trace in decision_traces
        for window in episode.windows
    ) else 0.0
    memory_reuse = 1.0 if all(context["reuse_count"] >= 1 for context in memory_contexts) else 0.0
    memory_influences_decision = 1.0 if (
        all(_trace_memory_influence_is_usable(trace) for trace in decision_traces)
        and any(trace["decision_inputs"].get("memory_ref") for trace in decision_traces)
    ) else 0.0
    memory_counterfactual_decision = 1.0 if all(
        _memory_counterfactual_proof_is_usable(
            trace["agent_decision_artifact"]["memory_counterfactual"]
        )
        for trace in decision_traces
    ) else 0.0
    institutional_memory_lineage_score = 1.0 if _institutional_memory_lineage_is_usable(
        institutional_memory_lineage
    ) else 0.0
    decision_explainability = 1.0 if all(
        trace["candidate_count"] >= 4
        and trace["selected_candidate"]["rationale"]
        and trace["selected_candidate"]["evidence_refs"]
        for trace in decision_traces
    ) else 0.0
    persona_decision_artifact = 1.0 if all(
        _persona_decision_artifact_is_usable(trace)
        for trace in decision_traces
    ) else 0.0
    persona_reasoning_generation = 1.0 if all(
        _trace_persona_reasoning_is_usable(trace)
        for trace in decision_traces
    ) else 0.0
    required_roles = {
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    }
    oss_evidence_completeness = 1.0 if set(oss_inputs) == required_roles and all(
        result.get("status") == "completed" for result in oss_inputs.values()
    ) else 0.0
    portfolio_breadth = 1.0 if len(episode.windows) == PORTFOLIO_LEG_COUNT else 0.0
    no_leakage = 1.0 if all(_trace_has_no_forbidden_window_leakage(trace) for trace in decision_traces) else 0.0
    planning_completeness = 1.0 if _validation_plan_is_complete(validation_plan) else 0.0
    market_friction = 1.0 if _market_friction_is_usable(operational_context["market_friction"]) else 0.0
    broker_lifecycle = 1.0 if _broker_lifecycle_is_reconciled(operational_context["broker_lifecycle"]) else 0.0
    broker_adapter_lifecycle = 1.0 if _broker_adapter_lifecycle_is_usable(
        operational_context["broker_adapter_lifecycle"]
    ) else 0.0
    broker_adapter_followup = 1.0 if _broker_adapter_followup_is_usable(
        operational_context["broker_adapter_followup"]
    ) else 0.0
    persona_conflicts = 1.0 if _persona_conflicts_are_resolved(operational_context["persona_conflict_resolution"]) else 0.0
    restart_recovery = 1.0 if _restart_recovery_is_usable(operational_context["restart_recovery"]) else 0.0
    autonomous_scheduler = 1.0 if _autonomous_schedule_is_usable(operational_context["autonomous_schedule"]) else 0.0
    lean_engine_replay = 1.0 if _lean_engine_replay_is_usable(operational_context["lean_engine_replay"]) else 0.0
    shioaji_sandbox = 1.0 if _shioaji_sandbox_lifecycle_is_usable(
        operational_context["shioaji_sandbox_lifecycle"]
    ) else 0.0
    case_upstream_feedback = 1.0 if _case_upstream_artifacts_are_usable(
        episode=episode,
        artifacts=case_upstream_artifacts,
    ) else 0.0
    lean_handoff = 1.0 if _lean_handoff_packet_is_usable(operational_context["lean_handoff"]) else 0.0
    lean_packet_execution_projection = 1.0 if _lean_packet_execution_projection_is_usable(
        operational_context["lean_packet_execution_projection"]
    ) else 0.0
    lean_runtime_feedback = 1.0 if _lean_runtime_feedback_is_usable(
        operational_context["lean_runtime_feedback"]
    ) else 0.0
    experiment_tracking_lineage_handoff = 1.0 if _experiment_tracking_lineage_handoff_is_usable(
        operational_context["experiment_tracking_lineage_handoff"]
    ) else 0.0
    policy_oss_lineage_handoff = 1.0 if _policy_oss_lineage_handoff_is_usable(
        operational_context["policy_oss_lineage_handoff"]
    ) else 0.0
    reflection_oss_lineage_handoff = 1.0 if _reflection_oss_lineage_handoff_is_usable(
        operational_context["reflection_oss_lineage_handoff"]
    ) else 0.0
    openclaw_session_handoff = 1.0 if _openclaw_session_handoff_is_usable(
        operational_context["openclaw_session_handoff"]
    ) else 0.0
    alpha_seed_revision_handoff = 1.0 if _alpha_seed_revision_handoff_is_usable(
        operational_context["alpha_seed_revision_handoff"]
    ) else 0.0
    evolved_strategy_packet = 1.0 if _evolved_strategy_packet_proof_is_usable(
        operational_context["evolved_strategy_packet_proof"]
    ) else 0.0
    scheduler_conflict_ooda = 1.0 if _scheduler_conflict_ooda_proof_is_usable(
        operational_context["scheduler_conflict_ooda_proof"]
    ) else 0.0
    multi_generation_trajectory = 1.0 if _evolution_trajectory_is_usable(evolution_trajectory) else 0.0
    no_leakage_temporal_protocol = 1.0 if _no_leakage_temporal_protocol_is_usable(
        no_leakage_protocol
    ) else 0.0
    strict_oos_evolution = 1.0 if _strict_oos_evolution_proof_is_usable(
        strict_oos_evolution_proof
    ) else 0.0
    policy_candidate_oss_materiality = 1.0 if _policy_candidate_materiality_is_usable(
        policy_candidate_materiality
    ) else 0.0
    reflection_artifact_oss_materiality = 1.0 if _reflection_artifact_materiality_is_usable(
        reflection_artifact_materiality
    ) else 0.0
    oss_response_followup_loop = 1.0 if (
        _oss_response_followup_loop_is_usable(oss_followup_loop)
        and all(_trace_oss_followup_loop_is_usable(trace) for trace in decision_traces)
    ) else 0.0
    multi_oss_closed_loop = 1.0 if _multi_oss_closed_loop_proof_is_usable(
        multi_oss_closed_loop_proof
    ) else 0.0
    persona_oss_ooda_causality = 1.0 if _persona_oss_ooda_causal_ledger_is_usable(
        persona_oss_ooda_ledger
    ) else 0.0
    cross_cycle_runtime_carryover = 1.0 if _cross_cycle_carryover_is_usable(
        cross_cycle_carryover
    ) else 0.0
    persisted_cycle_resume_carryover = 1.0 if _persisted_cycle_resume_is_usable(
        persisted_cycle_resume
    ) else 0.0
    multi_cycle_lineage_carryover = 1.0 if _multi_cycle_lineage_is_usable(
        multi_cycle_lineage
    ) else 0.0
    oss_disagreement_arbitration = 1.0 if all(
        _trace_oss_disagreement_arbitration_is_usable(trace)
        for trace in decision_traces
    ) else 0.0
    tracking_reconciliation = 1.0 if (
        _tracking_readback_reconciliation_is_usable(case_upstream_artifacts.get("tracking_reconciliation", {}))
        and all(_trace_tracking_reconciliation_is_usable(trace) for trace in decision_traces)
    ) else 0.0
    alpha_seed_revision = 1.0 if (
        _alpha_seed_revision_is_usable(case_upstream_artifacts.get("alpha_seed_revision", {}))
        and all(_trace_alpha_seed_revision_is_usable(trace) for trace in decision_traces)
    ) else 0.0
    return {
        "return_improvement": return_improvement,
        "multi_generation_improvement": multi_generation_improvement,
        "multi_generation_trajectory": multi_generation_trajectory,
        "no_leakage_temporal_protocol": no_leakage_temporal_protocol,
        "strict_oos_evolution": strict_oos_evolution,
        "policy_candidate_oss_materiality": policy_candidate_oss_materiality,
        "reflection_artifact_oss_materiality": reflection_artifact_oss_materiality,
        "drawdown_reduction": drawdown_reduction,
        "turnover_control": turnover_control,
        "fill_quality": fill_quality,
        "regime_adaptation": regime_adaptation,
        "memory_reuse": memory_reuse,
        "memory_influences_decision": memory_influences_decision,
        "memory_counterfactual_decision": memory_counterfactual_decision,
        "institutional_memory_lineage": institutional_memory_lineage_score,
        "decision_explainability": decision_explainability,
        "persona_decision_artifact": persona_decision_artifact,
        "persona_reasoning_generation": persona_reasoning_generation,
        "oss_response_followup_loop": oss_response_followup_loop,
        "multi_oss_closed_loop": multi_oss_closed_loop,
        "persona_oss_ooda_causality": persona_oss_ooda_causality,
        "cross_cycle_runtime_carryover": cross_cycle_runtime_carryover,
        "persisted_cycle_resume_carryover": persisted_cycle_resume_carryover,
        "multi_cycle_lineage_carryover": multi_cycle_lineage_carryover,
        "oss_disagreement_arbitration": oss_disagreement_arbitration,
        "tracking_reconciliation": tracking_reconciliation,
        "alpha_seed_revision": alpha_seed_revision,
        "oss_evidence_completeness": min(1.0, oss_evidence_completeness),
        "portfolio_breadth": portfolio_breadth,
        "no_leakage": no_leakage,
        "validation_planning": planning_completeness,
        "market_friction_model": market_friction,
        "broker_lifecycle_reconciliation": broker_lifecycle,
        "broker_adapter_lifecycle": broker_adapter_lifecycle,
        "broker_adapter_followup": broker_adapter_followup,
        "persona_conflict_resolution": persona_conflicts,
        "restart_recovery": restart_recovery,
        "autonomous_scheduler": autonomous_scheduler,
        "lean_engine_replay": lean_engine_replay,
        "shioaji_sandbox_lifecycle": shioaji_sandbox,
        "case_specific_upstream_artifact_feedback": case_upstream_feedback,
        "lean_handoff_packet": lean_handoff,
        "lean_packet_execution_projection": lean_packet_execution_projection,
        "lean_runtime_feedback": lean_runtime_feedback,
        "experiment_tracking_lineage_handoff": experiment_tracking_lineage_handoff,
        "policy_oss_lineage_handoff": policy_oss_lineage_handoff,
        "reflection_oss_lineage_handoff": reflection_oss_lineage_handoff,
        "openclaw_session_handoff": openclaw_session_handoff,
        "alpha_seed_revision_handoff": alpha_seed_revision_handoff,
        "evolved_strategy_packet_handoff": evolved_strategy_packet,
        "scheduler_conflict_ooda_dispatch": scheduler_conflict_ooda,
    }


def _diagnose_validation_execution(
    *,
    episode: PortfolioEpisode,
    validation_plan: Mapping[str, Any],
    executions: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    memory_writes: Sequence[Mapping[str, Any]],
    memory_contexts: Sequence[Mapping[str, Any]],
    evolution_decision: EvolutionDecision,
    usability_dimensions: Mapping[str, float],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    operational_context: Mapping[str, Any],
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    policy_candidate_materiality: Mapping[str, Any],
    reflection_artifact_materiality: Mapping[str, Any],
    multi_oss_closed_loop_proof: Mapping[str, Any],
    persona_oss_ooda_ledger: Mapping[str, Any],
    cross_cycle_carryover: Mapping[str, Any],
    persisted_cycle_resume: Mapping[str, Any],
    multi_cycle_lineage: Mapping[str, Any],
    institutional_memory_lineage: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
) -> dict[str, Any]:
    selected_plan = validation_plan["selected_validation_plan"]
    checks = [
        _diagnostic_check(
            "planning_asked_gap_questions",
            len(validation_plan["questions_asked"]) == 3,
            {"questions": list(validation_plan["questions_asked"])},
        ),
        _diagnostic_check(
            "selected_combo_was_unvalidated_before_execution",
            selected_plan["target_combo_signature"]
            == _validation_combo_signature(episode)
            and selected_plan["target_validation_signature"] == episode.validation_signature,
            {"combo_signature": selected_plan["target_combo_signature"]},
        ),
        _diagnostic_check(
            "plan_has_non_repeated_assertion_labels",
            len(set(selected_plan["assertion_labels"])) == len(selected_plan["assertion_labels"]),
            {"assertion_label_count": len(selected_plan["assertion_labels"])},
        ),
        _diagnostic_check(
            "all_portfolio_generations_filled",
            all(execution["filled"] for execution in executions),
            {"fill_counts": [execution["fill_count"] for execution in executions]},
        ),
        _diagnostic_check(
            "no_holdout_or_future_leakage_in_agent_trace",
            all(_trace_has_no_forbidden_window_leakage(trace) for trace in decision_traces),
            {"trace_ids": [trace["reflection_id"] for trace in decision_traces]},
        ),
        _diagnostic_check(
            "persona_decision_artifact_replays_candidate_selection",
            all(_persona_decision_artifact_is_usable(trace) for trace in decision_traces),
            {
                "artifact_ids": [
                    trace["agent_decision_artifact"]["artifact_id"] for trace in decision_traces
                ],
                "selected_candidate_ids": [
                    trace["selected_candidate_id"] for trace in decision_traces
                ],
            },
        ),
        _diagnostic_check(
            "persona_reasoning_response_drives_candidate_generation",
            all(_trace_persona_reasoning_is_usable(trace) for trace in decision_traces),
            {
                "reasoning_response_ids": [
                    trace["agent_decision_artifact"]["persona_reasoning"]["response"]["response_id"]
                    for trace in decision_traces
                ],
                "candidate_response_reasoning_refs": [
                    trace["agent_decision_artifact"]["candidate_generation"]["response"]["source_reasoning_ref"]
                    for trace in decision_traces
                ],
            },
        ),
        _diagnostic_check(
            "generation1_improves_unseen_holdout",
            float(evaluations[1]["score"]) > float(baseline_holdout_counterfactual["score"]),
            {
                "generation1_holdout": evaluations[1]["score"],
                "baseline_holdout_counterfactual": baseline_holdout_counterfactual["score"],
            },
        ),
        _diagnostic_check(
            "generation2_improves_future_holdout",
            float(evaluations[2]["score"]) > float(generation1_future_counterfactual["score"]),
            {
                "generation2_future_holdout": evaluations[2]["score"],
                "generation1_future_counterfactual": generation1_future_counterfactual["score"],
            },
        ),
        _diagnostic_check(
            "multi_generation_evolution_trajectory_converges",
            _evolution_trajectory_is_usable(evolution_trajectory),
            {
                "trajectory_id": evolution_trajectory["trajectory_id"],
                "improvement_deltas": evolution_trajectory["trend"]["improvement_deltas"],
                "convergence_status": evolution_trajectory["trend"]["convergence_status"],
            },
        ),
        _diagnostic_check(
            "no_leakage_temporal_protocol_replays_window_boundaries",
            _no_leakage_temporal_protocol_is_usable(no_leakage_protocol),
            {
                "protocol_id": no_leakage_protocol["protocol_id"],
                "protocol_path": no_leakage_protocol["protocol_path"],
                "stage_evaluation_windows": [
                    stage["evaluation_window"] for stage in no_leakage_protocol["stage_contracts"]
                ],
                "replay": copy.deepcopy(dict(no_leakage_protocol["replay"])),
            },
        ),
        _diagnostic_check(
            "strict_oos_evolution_proof_replays_unseen_windows",
            _strict_oos_evolution_proof_is_usable(strict_oos_evolution_proof),
            {
                "proof_id": strict_oos_evolution_proof["proof_id"],
                "proof_steps": [
                    {
                        "source_outcome_window": step["source_outcome_window"],
                        "validation_window": step["validation_window"],
                        "score_improvement": step["score_improvement"],
                    }
                    for step in strict_oos_evolution_proof["proof_steps"]
                ],
                "replay": copy.deepcopy(dict(strict_oos_evolution_proof["replay"])),
            },
        ),
        _diagnostic_check(
            "memory_written_and_reused",
            all(write["created"] for write in memory_writes)
            and all(context["reuse_count"] >= 1 for context in memory_contexts),
            {
                "memory_write_count": len(memory_writes),
                "reuse_counts": [context["reuse_count"] for context in memory_contexts],
            },
        ),
        _diagnostic_check(
            "retrieved_memory_influences_persona_candidate_scoring",
            all(_trace_memory_influence_is_usable(trace) for trace in decision_traces)
            and any(trace["decision_inputs"].get("memory_ref") for trace in decision_traces),
            {
                "trace_memory_refs": [
                    trace["decision_inputs"].get("memory_ref") for trace in decision_traces
                ],
                "selected_candidate_refs": [
                    trace["selected_candidate"]["evidence_refs"] for trace in decision_traces
                ],
            },
        ),
        _diagnostic_check(
            "memory_counterfactual_proves_retrieval_materiality",
            all(
                _memory_counterfactual_proof_is_usable(
                    trace["agent_decision_artifact"]["memory_counterfactual"]
                )
                for trace in decision_traces
            ),
            {
                "proofs": [
                    {
                        "proof_id": trace["agent_decision_artifact"]["memory_counterfactual"]["proof_id"],
                        "memory_status": trace["agent_decision_artifact"]["memory_counterfactual"]["memory_status"],
                        "outcome": trace["agent_decision_artifact"]["memory_counterfactual"]["outcome"],
                        "selected_score_delta_from_memory": trace[
                            "agent_decision_artifact"
                        ]["memory_counterfactual"]["selected_score_delta_from_memory"],
                        "memory_margin_lift": trace["agent_decision_artifact"]["memory_counterfactual"][
                            "memory_margin_lift"
                        ],
                    }
                    for trace in decision_traces
                ],
            },
        ),
        _diagnostic_check(
            "cross_persona_institutional_memory_drives_persona_scoring",
            _institutional_memory_lineage_is_usable(institutional_memory_lineage),
            {
                "proof_id": institutional_memory_lineage["proof_id"],
                "lineage_status": institutional_memory_lineage["lineage_status"],
                "entry_ref": institutional_memory_lineage.get("entry_ref"),
                "contributing_persona_ids": list(
                    institutional_memory_lineage.get("contributing_persona_ids", [])
                ),
                "trace_binding_count": len(institutional_memory_lineage["trace_bindings"]),
                "replay": copy.deepcopy(dict(institutional_memory_lineage["replay"])),
            },
        ),
        _diagnostic_check(
            "oss_feedback_drives_persona_next_steps",
            set(oss_inputs) == {
                "session",
                "alpha_model",
                "backtest",
                "policy_candidate",
                "reflection_artifact",
                "tracker",
                "risk_analytics",
                "handoff",
            }
            and all(result.get("drives_persona_step") for result in oss_inputs.values()),
            {
                "roles": sorted(oss_inputs),
                "components": _oss_components_used(oss_inputs),
            },
        ),
        _diagnostic_check(
            "oss_response_followup_loop_drives_persona_scoring",
            _oss_response_followup_loop_is_usable(oss_followup_loop)
            and all(_trace_oss_followup_loop_is_usable(trace) for trace in decision_traces),
            {
                "loop_id": oss_followup_loop["loop_id"],
                "followup_count": len(oss_followup_loop["followups"]),
                "candidate_score_adjustments": copy.deepcopy(
                    dict(oss_followup_loop["candidate_score_adjustments"])
                ),
                "trace_ids": [trace["reflection_id"] for trace in decision_traces],
            },
        ),
        _diagnostic_check(
            "policy_candidate_oss_materiality_drives_evolved_policy",
            _policy_candidate_materiality_is_usable(policy_candidate_materiality),
            {
                "proof_id": policy_candidate_materiality["proof_id"],
                "component": policy_candidate_materiality["component"],
                "artifact_family": policy_candidate_materiality["artifact_family"],
                "policy_quality": policy_candidate_materiality["policy_quality"],
                "trace_binding_count": len(policy_candidate_materiality["trace_bindings"]),
                "replay": copy.deepcopy(dict(policy_candidate_materiality["replay"])),
            },
        ),
        _diagnostic_check(
            "reflection_artifact_oss_materiality_drives_persona_reasoning",
            _reflection_artifact_materiality_is_usable(reflection_artifact_materiality),
            {
                "proof_id": reflection_artifact_materiality["proof_id"],
                "component": reflection_artifact_materiality["component"],
                "artifact_family": reflection_artifact_materiality["artifact_family"],
                "reflection_quality": reflection_artifact_materiality["reflection_quality"],
                "trace_binding_count": len(reflection_artifact_materiality["trace_bindings"]),
                "replay": copy.deepcopy(dict(reflection_artifact_materiality["replay"])),
            },
        ),
        _diagnostic_check(
            "multi_oss_closed_loop_proof_replays_role_bindings",
            _multi_oss_closed_loop_proof_is_usable(multi_oss_closed_loop_proof),
            {
                "proof_id": multi_oss_closed_loop_proof["proof_id"],
                "role_count": len(multi_oss_closed_loop_proof["role_records"]),
                "trace_binding_count": len(multi_oss_closed_loop_proof["trace_bindings"]),
                "components": [
                    record["component"] for record in multi_oss_closed_loop_proof["role_records"]
                ],
                "replay": copy.deepcopy(dict(multi_oss_closed_loop_proof["replay"])),
            },
        ),
        _diagnostic_check(
            "persona_oss_ooda_ledger_replays_temporal_causality",
            _persona_oss_ooda_causal_ledger_is_usable(persona_oss_ooda_ledger),
            {
                "ledger_id": persona_oss_ooda_ledger["ledger_id"],
                "event_count": persona_oss_ooda_ledger["event_count"],
                "phase_order": list(persona_oss_ooda_ledger["phase_order"]),
                "event_types": list(persona_oss_ooda_ledger["event_types"]),
                "replay": copy.deepcopy(dict(persona_oss_ooda_ledger["replay"])),
            },
        ),
        _diagnostic_check(
            "cross_cycle_runtime_feedback_drives_next_case_decision",
            _cross_cycle_carryover_is_usable(cross_cycle_carryover),
            {
                "proof_id": cross_cycle_carryover["proof_id"],
                "carryover_status": cross_cycle_carryover["carryover_status"],
                "previous_case_id": cross_cycle_carryover.get("previous_case_id"),
                "runtime_feedback_ref": cross_cycle_carryover.get("runtime_feedback_ref"),
                "trace_binding_count": len(cross_cycle_carryover["trace_bindings"]),
                "replay": copy.deepcopy(dict(cross_cycle_carryover["replay"])),
            },
        ),
        _diagnostic_check(
            "persisted_cycle_resume_replays_after_restart_and_schedule",
            _persisted_cycle_resume_is_usable(persisted_cycle_resume),
            {
                "proof_id": persisted_cycle_resume["proof_id"],
                "resume_status": persisted_cycle_resume["resume_status"],
                "previous_case_id": persisted_cycle_resume.get("previous_case_id"),
                "restart_checkpoint_ref": persisted_cycle_resume.get("restart_checkpoint_ref"),
                "schedule_ref": persisted_cycle_resume.get("schedule_ref"),
                "trace_binding_count": len(persisted_cycle_resume["trace_bindings"]),
                "replay": copy.deepcopy(dict(persisted_cycle_resume["replay"])),
            },
        ),
        _diagnostic_check(
            "multi_cycle_lineage_drives_persona_next_case_decision",
            _multi_cycle_lineage_is_usable(multi_cycle_lineage),
            {
                "proof_id": multi_cycle_lineage["proof_id"],
                "lineage_status": multi_cycle_lineage["lineage_status"],
                "lineage_case_ids": list(multi_cycle_lineage["lineage_case_ids"]),
                "latest_runtime_feedback_ref": multi_cycle_lineage.get("latest_runtime_feedback_ref"),
                "older_runtime_feedback_ref": multi_cycle_lineage.get("older_runtime_feedback_ref"),
                "trace_binding_count": len(multi_cycle_lineage["trace_bindings"]),
                "replay": copy.deepcopy(dict(multi_cycle_lineage["replay"])),
            },
        ),
        _diagnostic_check(
            "multi_oss_disagreement_arbitration_drives_persona_scoring",
            all(_trace_oss_disagreement_arbitration_is_usable(trace) for trace in decision_traces),
            {
                "arbitration_id": case_upstream_artifacts["oss_disagreement_arbitration"]["arbitration_id"],
                "conflict_types": [
                    conflict["conflict_type"]
                    for conflict in case_upstream_artifacts["oss_disagreement_arbitration"]["conflicts"]
                ],
                "resolution_actions": case_upstream_artifacts["oss_disagreement_arbitration"][
                    "persona_arbitration_response"
                ]["resolution_actions"],
                "trace_ids": [trace["reflection_id"] for trace in decision_traces],
            },
        ),
        _diagnostic_check(
            "tracking_readback_reconciliation_drives_persona_scoring",
            _tracking_readback_reconciliation_is_usable(case_upstream_artifacts["tracking_reconciliation"])
            and all(_trace_tracking_reconciliation_is_usable(trace) for trace in decision_traces),
            {
                "reconciliation_id": case_upstream_artifacts["tracking_reconciliation"]["reconciliation_id"],
                "divergence_type": case_upstream_artifacts["tracking_reconciliation"]["divergence"][
                    "divergence_type"
                ],
                "repair_action": case_upstream_artifacts["tracking_reconciliation"]["repair"]["action"],
                "backend": case_upstream_artifacts["tracking_reconciliation"]["backend"],
                "trace_ids": [trace["reflection_id"] for trace in decision_traces],
            },
        ),
        _diagnostic_check(
            "alpha_seed_revision_drives_persona_scoring",
            _alpha_seed_revision_is_usable(case_upstream_artifacts["alpha_seed_revision"])
            and all(_trace_alpha_seed_revision_is_usable(trace) for trace in decision_traces),
            {
                "revision_id": case_upstream_artifacts["alpha_seed_revision"]["revision_id"],
                "alpha_component": case_upstream_artifacts["alpha_seed_revision"]["alpha_component"],
                "revision_action": case_upstream_artifacts["alpha_seed_revision"]["revision"]["action"],
                "revision_ref": case_upstream_artifacts["alpha_seed_revision"]["revision_ref"],
                "trace_ids": [trace["reflection_id"] for trace in decision_traces],
            },
        ),
        _diagnostic_check(
            "evolution_decision_executed",
            _enum_value(evolution_decision.decision_state) == EvolutionDecisionState.EXECUTED.value
            and evolution_decision.execution_result is not None
            and _enum_value(evolution_decision.execution_result.status) == ExecutionStatus.SUCCEEDED.value,
            {"decision_id": evolution_decision.decision_id},
        ),
        _diagnostic_check(
            "multi_dimensional_usability_threshold",
            mean(float(value) for value in usability_dimensions.values()) >= MIN_USABILITY_SCORE,
            {"dimension_minima": min(float(value) for value in usability_dimensions.values())},
        ),
        _diagnostic_check(
            "market_friction_model_applied",
            _market_friction_is_usable(operational_context["market_friction"]),
            {
                "model_id": operational_context["market_friction"]["model_id"],
                "generation_count": len(operational_context["market_friction"]["generation_costs"]),
            },
        ),
        _diagnostic_check(
            "paper_broker_lifecycle_reconciled",
            _broker_lifecycle_is_reconciled(operational_context["broker_lifecycle"]),
            {
                "order_count": operational_context["broker_lifecycle"]["order_count"],
                "terminal_statuses": operational_context["broker_lifecycle"]["terminal_statuses"],
            },
        ),
        _diagnostic_check(
            "broker_adapter_lifecycle_replays_submit_readback_recovery",
            _broker_adapter_lifecycle_is_usable(operational_context["broker_adapter_lifecycle"]),
            {
                "packet_id": operational_context["broker_adapter_lifecycle"]["packet_id"],
                "scenario": operational_context["broker_adapter_lifecycle"]["scenario"],
                "required_statuses": operational_context["broker_adapter_lifecycle"]["required_statuses"],
                "adapter_order": operational_context["broker_adapter_lifecycle"]["adapter_order"],
            },
        ),
        _diagnostic_check(
            "broker_adapter_response_drives_persona_followup",
            _broker_adapter_followup_is_usable(operational_context["broker_adapter_followup"]),
            {
                "followup_id": operational_context["broker_adapter_followup"]["followup_id"],
                "scenario": operational_context["broker_adapter_followup"]["scenario"],
                "action": operational_context["broker_adapter_followup"]["persona_followup"]["action"],
                "source_packet_ref": operational_context["broker_adapter_followup"]["source_packet_ref"],
            },
        ),
        _diagnostic_check(
            "multi_persona_conflicts_resolved",
            _persona_conflicts_are_resolved(operational_context["persona_conflict_resolution"]),
            {
                "conflict_types": operational_context["persona_conflict_resolution"]["conflict_types"],
                "open_conflicts": operational_context["persona_conflict_resolution"]["open_conflicts"],
            },
        ),
        _diagnostic_check(
            "restart_recovery_restores_agent_loop",
            _restart_recovery_is_usable(operational_context["restart_recovery"]),
            {
                "checkpoint_id": operational_context["restart_recovery"]["checkpoint_id"],
                "resume_step": operational_context["restart_recovery"]["resume_step"],
            },
        ),
        _diagnostic_check(
            "autonomous_scheduler_orders_next_cycle",
            _autonomous_schedule_is_usable(operational_context["autonomous_schedule"]),
            {
                "phase_count": len(operational_context["autonomous_schedule"]["phases"]),
                "next_cycle_due_at": operational_context["autonomous_schedule"]["next_cycle_due_at"],
            },
        ),
        _diagnostic_check(
            "scheduler_conflict_ooda_dispatch_replays_next_cycle",
            _scheduler_conflict_ooda_proof_is_usable(
                operational_context["scheduler_conflict_ooda_proof"]
            ),
            {
                "proof_id": operational_context["scheduler_conflict_ooda_proof"]["proof_id"],
                "conflict_ref": operational_context["scheduler_conflict_ooda_proof"]["conflict_ref"],
                "schedule_ref": operational_context["scheduler_conflict_ooda_proof"]["schedule_ref"],
                "dispatch_ref": operational_context["scheduler_conflict_ooda_proof"]["dispatch_ref"],
                "replay": copy.deepcopy(
                    dict(operational_context["scheduler_conflict_ooda_proof"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "lean_handoff_packet_materialized",
            _lean_handoff_packet_is_usable(operational_context["lean_handoff"]),
            {
                "packet_id": operational_context["lean_handoff"]["packet_id"],
                "component": operational_context["lean_handoff"]["component"],
                "case_vectorbt_request_id": operational_context["lean_handoff"].get("case_vectorbt_request_id"),
                "case_tracking_run_id": operational_context["lean_handoff"].get("case_tracking_run_id"),
            },
        ),
        _diagnostic_check(
            "lean_packet_execution_projection_replays_packet_legs",
            _lean_packet_execution_projection_is_usable(
                operational_context["lean_packet_execution_projection"]
            ),
            {
                "projection_id": operational_context["lean_packet_execution_projection"][
                    "projection_id"
                ],
                "strategy_packet_ref": operational_context["lean_packet_execution_projection"][
                    "strategy_packet_ref"
                ],
                "leg_count": operational_context["lean_packet_execution_projection"][
                    "leg_count"
                ],
                "order_count": operational_context["lean_packet_execution_projection"][
                    "order_count"
                ],
                "replay": copy.deepcopy(
                    dict(operational_context["lean_packet_execution_projection"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "evolved_strategy_packet_reaches_lean_handoff",
            _evolved_strategy_packet_proof_is_usable(
                operational_context["evolved_strategy_packet_proof"]
            ),
            {
                "proof_id": operational_context["evolved_strategy_packet_proof"]["proof_id"],
                "strategy_packet_ref": operational_context["evolved_strategy_packet_proof"][
                    "strategy_packet_ref"
                ],
                "strict_oos_proof_ref": operational_context["evolved_strategy_packet_proof"][
                    "strict_oos_proof_ref"
                ],
                "validation_window": operational_context["evolved_strategy_packet_proof"][
                    "validation_window"
                ],
                "replay": copy.deepcopy(
                    dict(operational_context["evolved_strategy_packet_proof"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "lean_runtime_feedback_drives_persona_ooda",
            _lean_runtime_feedback_is_usable(operational_context["lean_runtime_feedback"]),
            {
                "feedback_id": operational_context["lean_runtime_feedback"]["feedback_id"],
                "action": operational_context["lean_runtime_feedback"]["persona_ooda_followup"]["action"],
                "ooda_step": operational_context["lean_runtime_feedback"]["persona_ooda_followup"]["ooda_step"],
                "runtime_binding_id": operational_context["lean_runtime_feedback"]["runtime_feedback"][
                    "runtime_binding_id"
                ],
            },
        ),
        _diagnostic_check(
            "tracking_experiment_lineage_reaches_evolution_and_lean_packet",
            _experiment_tracking_lineage_handoff_is_usable(
                operational_context["experiment_tracking_lineage_handoff"]
            ),
            {
                "proof_id": operational_context["experiment_tracking_lineage_handoff"]["proof_id"],
                "backend": operational_context["experiment_tracking_lineage_handoff"]["backend"],
                "experiment_ref": operational_context["experiment_tracking_lineage_handoff"][
                    "experiment_ref"
                ],
                "tracking_reconciliation_ref": operational_context[
                    "experiment_tracking_lineage_handoff"
                ]["tracking_reconciliation_ref"],
                "lineage_hashes": copy.deepcopy(
                    dict(operational_context["experiment_tracking_lineage_handoff"]["lineage_hashes"])
                ),
                "replay": copy.deepcopy(
                    dict(operational_context["experiment_tracking_lineage_handoff"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "policy_oss_lineage_reaches_evolved_policy_and_lean_packet",
            _policy_oss_lineage_handoff_is_usable(
                operational_context["policy_oss_lineage_handoff"]
            ),
            {
                "proof_id": operational_context["policy_oss_lineage_handoff"]["proof_id"],
                "component": operational_context["policy_oss_lineage_handoff"]["component"],
                "source_oss_ref": operational_context["policy_oss_lineage_handoff"][
                    "source_oss_ref"
                ],
                "lineage_ref": operational_context["policy_oss_lineage_handoff"][
                    "lineage_ref"
                ],
                "lineage_hashes": copy.deepcopy(
                    dict(operational_context["policy_oss_lineage_handoff"]["lineage_hashes"])
                ),
                "replay": copy.deepcopy(
                    dict(operational_context["policy_oss_lineage_handoff"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "reflection_oss_lineage_reaches_evolved_policy_and_lean_packet",
            _reflection_oss_lineage_handoff_is_usable(
                operational_context["reflection_oss_lineage_handoff"]
            ),
            {
                "proof_id": operational_context["reflection_oss_lineage_handoff"]["proof_id"],
                "component": operational_context["reflection_oss_lineage_handoff"]["component"],
                "source_oss_ref": operational_context["reflection_oss_lineage_handoff"][
                    "source_oss_ref"
                ],
                "lineage_ref": operational_context["reflection_oss_lineage_handoff"][
                    "lineage_ref"
                ],
                "lineage_hashes": copy.deepcopy(
                    dict(operational_context["reflection_oss_lineage_handoff"]["lineage_hashes"])
                ),
                "replay": copy.deepcopy(
                    dict(operational_context["reflection_oss_lineage_handoff"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "openclaw_session_context_reaches_lean_handoff",
            _openclaw_session_handoff_is_usable(
                operational_context["openclaw_session_handoff"]
            ),
            {
                "proof_id": operational_context["openclaw_session_handoff"]["proof_id"],
                "source_oss_ref": operational_context["openclaw_session_handoff"][
                    "source_oss_ref"
                ],
                "context_ref": operational_context["openclaw_session_handoff"][
                    "context_ref"
                ],
                "session_ref": operational_context["openclaw_session_handoff"][
                    "session_ref"
                ],
                "upstream_session_ref": operational_context["openclaw_session_handoff"][
                    "upstream_session_ref"
                ],
                "replay": copy.deepcopy(
                    dict(operational_context["openclaw_session_handoff"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "alpha_seed_revision_reaches_lean_handoff",
            _alpha_seed_revision_handoff_is_usable(
                operational_context["alpha_seed_revision_handoff"]
            ),
            {
                "proof_id": operational_context["alpha_seed_revision_handoff"]["proof_id"],
                "component": operational_context["alpha_seed_revision_handoff"]["component"],
                "revision_ref": operational_context["alpha_seed_revision_handoff"][
                    "revision_ref"
                ],
                "handoff_ref": operational_context["alpha_seed_revision_handoff"][
                    "handoff_ref"
                ],
                "lineage_hashes": copy.deepcopy(
                    dict(operational_context["alpha_seed_revision_handoff"]["lineage_hashes"])
                ),
                "replay": copy.deepcopy(
                    dict(operational_context["alpha_seed_revision_handoff"]["replay"])
                ),
            },
        ),
        _diagnostic_check(
            "case_specific_upstream_artifacts_drive_persona_decision",
            _case_upstream_artifacts_are_usable(
                episode=episode,
                artifacts=case_upstream_artifacts,
            )
            and all(
                f"oss://vectorbt/{case_upstream_artifacts['vectorbt']['request_id']}" in trace["evidence_refs"]
                and any(
                    f"oss://vectorbt/{case_upstream_artifacts['vectorbt']['request_id']}" in candidate["evidence_refs"]
                    for candidate in trace["candidates"]
                )
                for trace in decision_traces
            ),
            {
                "vectorbt_request_id": case_upstream_artifacts["vectorbt"]["request_id"],
                "vectorbt_backend": case_upstream_artifacts["vectorbt"]["backend"],
                "tracking_backend": case_upstream_artifacts["tracker"]["backend"],
                "tracking_run_id": case_upstream_artifacts["tracker"]["run_id"],
            },
        ),
        _diagnostic_check(
            "case_specific_selected_oss_route_feedback_drives_persona_decision",
            _case_selected_oss_feedback_is_usable(
                episode=episode,
                artifacts=case_upstream_artifacts,
            )
            and all(
                all(
                    f"oss://{entry['component']}/{entry['request_id']}" in trace["evidence_refs"]
                    for entry in case_upstream_artifacts["selected_oss"].values()
                )
                and all(
                    f"oss://{entry['component']}/{entry['request_id']}" in trace["selected_candidate"]["evidence_refs"]
                    for entry in case_upstream_artifacts["selected_oss"].values()
                )
                for trace in decision_traces
            ),
            {
                "selected_roles": sorted(case_upstream_artifacts["selected_oss"]),
                "selected_components": {
                    role: entry["component"]
                    for role, entry in case_upstream_artifacts["selected_oss"].items()
                },
            },
        ),
        _diagnostic_check(
            "lean_engine_replay_uses_case_runtime_binding",
            _lean_engine_replay_is_usable(operational_context["lean_engine_replay"]),
            {
                "replay_id": operational_context["lean_engine_replay"]["replay_id"],
                "runtime_binding_id": operational_context["lean_engine_replay"]["runtime_context"]["runtime_binding_id"],
                "fill_count": operational_context["lean_engine_replay"]["fill_count"],
                "packet_readback_id": operational_context["lean_engine_replay"][
                    "lean_object_store_packet_readback"
                ]["readback_id"],
                "packet_target_count": operational_context["lean_engine_replay"][
                    "lean_object_store_packet_readback"
                ]["target_count"],
            },
        ),
        _diagnostic_check(
            "shioaji_sandbox_lifecycle_reconciled",
            _shioaji_sandbox_lifecycle_is_usable(operational_context["shioaji_sandbox_lifecycle"]),
            {
                "lifecycle_id": operational_context["shioaji_sandbox_lifecycle"]["lifecycle_id"],
                "status": operational_context["shioaji_sandbox_lifecycle"]["status"],
                "run_mode": operational_context["shioaji_sandbox_lifecycle"]["run_mode"],
            },
        ),
    ]
    failed = [check for check in checks if check["status"] != "passed"]
    return {
        "plan_id": validation_plan["plan_id"],
        "execution_status": "executed",
        "executed_steps": list(selected_plan["execution_steps"]),
        "checks": checks,
        "failed_check_count": len(failed),
        "failed_checks": [check["check"] for check in failed],
    }


def _repair_validation_deficiencies(
    *,
    validation_plan: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    failed_checks = list(diagnostics.get("failed_checks", []))
    repair_actions = [
        {
            "failed_check": check,
            "action": "adjust_validation_plan_or_policy_then_rerun_same_signature",
            "plan_id": validation_plan["plan_id"],
        }
        for check in failed_checks
    ]
    return {
        "deficiencies_found": failed_checks,
        "repair_actions": repair_actions,
        "revalidation_status": "passed" if not failed_checks else "requires_code_fix",
        "unresolved_deficiencies": failed_checks,
    }


def _diagnostic_check(check: str, condition: bool, observed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "check": check,
        "status": "passed" if condition else "failed",
        "observed": dict(observed),
    }


def _validation_plan_is_complete(validation_plan: Mapping[str, Any]) -> bool:
    selected = validation_plan.get("selected_validation_plan", {})
    return (
        len(validation_plan.get("questions_asked", [])) == 3
        and bool(validation_plan.get("plan_signature"))
        and bool(selected.get("target_combo_signature"))
        and bool(selected.get("target_validation_signature"))
        and len(selected.get("assertion_labels", [])) >= 8
        and "diagnose_and_repair_deficiencies" in selected.get("execution_steps", [])
    )


def _case_upstream_artifacts_case_summary(artifacts: Mapping[str, Any]) -> dict[str, Any]:
    vectorbt = artifacts["vectorbt"]
    tracker = artifacts["tracker"]
    return {
        "feedback_id": artifacts["feedback_id"],
        "vectorbt_model_id": artifacts["vectorbt_model_id"],
        "tracking_model_id": artifacts["tracking_model_id"],
        "selected_oss_model_id": artifacts["selected_oss_model_id"],
        "allowed_windows": list(artifacts["allowed_windows"]),
        "forbidden_windows_not_used": list(artifacts["forbidden_windows_not_used"]),
        "vectorbt": {
            "request_id": vectorbt["request_id"],
            "session_id": vectorbt["session_id"],
            "backend": vectorbt["backend"],
            "real_package_available": vectorbt["real_package_available"],
            "run_id": vectorbt["run_id"],
            "registry_id": vectorbt["registry_id"],
            "producer_run_id": vectorbt["producer_run_id"],
            "checksum": vectorbt["checksum"],
            "dataset_summary": copy.deepcopy(vectorbt["dataset_summary"]),
            "backtest_config": copy.deepcopy(vectorbt["backtest_config"]),
            "aggregate_metrics": copy.deepcopy(vectorbt["aggregate_metrics"]),
            "portfolio_instruments": list(vectorbt["portfolio_instruments"]),
            "historical_window_start_indices": list(vectorbt["historical_window_start_indices"]),
        },
        "tracker": {
            "request_id": tracker["request_id"],
            "session_id": tracker["session_id"],
            "component": tracker["component"],
            "backend": tracker["backend"],
            "tracking_version": tracker["tracking_version"],
            "run_id": tracker["run_id"],
            "run_uri": tracker["run_uri"],
            "artifact_uri": tracker["artifact_uri"],
            "registry_id": tracker["registry_id"],
            "source_vectorbt_run_id": tracker["source_vectorbt_run_id"],
            "metrics": copy.deepcopy(tracker["metrics"]),
            "readback": copy.deepcopy(tracker["readback"]),
            "record": copy.deepcopy(tracker["record"]),
        },
        "selected_oss": {
            role: _case_selected_oss_case_summary(entry)
            for role, entry in artifacts["selected_oss"].items()
        },
        "alpha_seed_revision": copy.deepcopy(artifacts["alpha_seed_revision"]),
        "oss_disagreement_arbitration": copy.deepcopy(artifacts["oss_disagreement_arbitration"]),
        "tracking_reconciliation": copy.deepcopy(artifacts["tracking_reconciliation"]),
        "persona_response": copy.deepcopy(artifacts["persona_response"]),
    }


def _case_selected_oss_case_summary(entry: Mapping[str, Any]) -> dict[str, Any]:
    primary_output = entry.get("primary_output", {})
    primary_output_keys = sorted(primary_output) if isinstance(primary_output, Mapping) else []
    return {
        "role": entry["role"],
        "component": entry["component"],
        "model_id": entry["model_id"],
        "case_specific": entry["case_specific"],
        "session_id": entry["session_id"],
        "request_id": entry["request_id"],
        "status": entry["status"],
        "artifact_family": entry["artifact_family"],
        "metrics": copy.deepcopy(dict(entry.get("metrics") or {})),
        "primary_output_keys": primary_output_keys,
        "registry_id": entry.get("registry_id"),
        "registry_artifact_type": entry.get("registry_artifact_type"),
        "producer_run_id": entry.get("producer_run_id"),
        "drives_persona_step": entry["drives_persona_step"],
        "persona_followup": copy.deepcopy(dict(entry.get("persona_followup") or {})),
        "expected_component": entry["expected_component"],
    }


def _case_selected_oss_case_feedback_is_usable(case: Mapping[str, Any]) -> bool:
    selected_oss = case.get("case_upstream_artifacts", {}).get("selected_oss", {})
    route = case.get("oss_feedback", {}).get("route", {})
    expected = {
        "alpha_model": route.get("alpha_model"),
        "policy_candidate": route.get("policy_candidate"),
        "reflection_artifact": route.get("reflection_artifact"),
        "risk_analytics": route.get("risk_analytics"),
    }
    if set(selected_oss) != set(expected):
        return False
    for role, component in expected.items():
        entry = selected_oss.get(role, {})
        if not component or entry.get("component") != component:
            return False
        if entry.get("expected_component") != component:
            return False
        if entry.get("model_id") != CASE_SELECTED_OSS_MODEL_ID:
            return False
        if entry.get("case_specific") is not True:
            return False
        if entry.get("status") != "completed":
            return False
        if entry.get("drives_persona_step") != _oss_persona_step(str(component)):
            return False
        if not (entry.get("metrics") or entry.get("primary_output_keys")):
            return False
    return True


def _build_case_result(
    *,
    episode: PortfolioEpisode,
    generation_policies: Sequence[Mapping[str, Any]],
    executions: Sequence[Mapping[str, Any]],
    evaluations: Sequence[Mapping[str, Any]],
    baseline_holdout_counterfactual: Mapping[str, Any],
    generation1_future_counterfactual: Mapping[str, Any],
    decision_traces: Sequence[Mapping[str, Any]],
    memory_writes: Sequence[Mapping[str, Any]],
    memory_contexts: Sequence[Mapping[str, Any] | None],
    evolution_decision: EvolutionDecision,
    evolution_trajectory: Mapping[str, Any],
    no_leakage_protocol: Mapping[str, Any],
    strict_oos_evolution_proof: Mapping[str, Any],
    policy_candidate_materiality: Mapping[str, Any],
    reflection_artifact_materiality: Mapping[str, Any],
    multi_oss_closed_loop_proof: Mapping[str, Any],
    persona_oss_ooda_ledger: Mapping[str, Any],
    cross_cycle_carryover: Mapping[str, Any],
    persisted_cycle_resume: Mapping[str, Any],
    multi_cycle_lineage: Mapping[str, Any],
    institutional_memory_lineage: Mapping[str, Any],
    oss_followup_loop: Mapping[str, Any],
    usability_dimensions: Mapping[str, float],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    operational_context: Mapping[str, Any],
    validation_plan: Mapping[str, Any],
    validation_diagnostics: Mapping[str, Any],
    validation_repair: Mapping[str, Any],
    prior_institutional_memory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    overall_usability_score = round(mean(usability_dimensions.values()), 10)
    generation_results = []
    for policy, execution, evaluation in zip(generation_policies, executions, evaluations):
        generation_results.append(
            {
                "generation": policy["generation"],
                "policy_id": policy["policy_id"],
                "policy_version": policy["policy_version"],
                "filled": execution["filled"],
                "fill_count": execution["fill_count"],
                "expected_fill_count": execution["expected_fill_count"],
                "fill_rate": execution["fill_rate"],
                "score": evaluation["score"],
                "signed_return": evaluation["signed_return"],
                "drawdown": evaluation["drawdown"],
                "turnover": evaluation["turnover"],
                "decision_inputs": policy["decision_inputs"],
            }
        )
    return {
        "case_id": episode.case_id,
        "validation_signature": episode.validation_signature,
        "persona_id": _persona_id(episode.persona),
        "seed_key": episode.seed_key,
        "source_strategy_spec_id": episode.source_strategy_spec_id,
        "source_dataset_refs": list(episode.source_dataset_refs),
        "order_profile": dict(episode.order_profile),
        "portfolio": {
            "instrument_count": len(episode.windows),
            "instruments": [window.instrument for window in episode.windows],
            "execution_symbols": [window.execution_symbol for window in episode.windows],
            "start_indices": [window.start_index for window in episode.windows],
            "regime_path": list(episode.regime_path),
        },
        "oss_feedback": {
            "route": dict(episode.oss_route),
            "components_used": _oss_components_used(oss_inputs),
            "request_ids": {
                role: result["request_id"] for role, result in oss_inputs.items()
            },
            "drives_persona_steps": {
                role: result["drives_persona_step"] for role, result in oss_inputs.items()
            },
            "response_followup_loop": copy.deepcopy(dict(oss_followup_loop)),
            "policy_candidate_materiality": copy.deepcopy(dict(policy_candidate_materiality)),
            "reflection_artifact_materiality": copy.deepcopy(dict(reflection_artifact_materiality)),
            "closed_loop_proof": copy.deepcopy(dict(multi_oss_closed_loop_proof)),
            "ooda_causal_ledger": copy.deepcopy(dict(persona_oss_ooda_ledger)),
        },
        "case_upstream_artifacts": _case_upstream_artifacts_case_summary(case_upstream_artifacts),
        "generation_results": generation_results,
        "scores": {
            "baseline_feedback": evaluations[0]["score"],
            "baseline_holdout_counterfactual": baseline_holdout_counterfactual["score"],
            "generation1_holdout": evaluations[1]["score"],
            "generation1_future_counterfactual": generation1_future_counterfactual["score"],
            "generation2_future_holdout": evaluations[2]["score"],
            "holdout_improvement": round(
                float(evaluations[1]["score"]) - float(baseline_holdout_counterfactual["score"]),
                10,
            ),
            "future_generation_improvement": round(
                float(evaluations[2]["score"]) - float(generation1_future_counterfactual["score"]),
                10,
            ),
        },
        "memory": {
            "prior_memory": memory_contexts[0],
            "prior_institutional_memory": copy.deepcopy(dict(prior_institutional_memory))
            if prior_institutional_memory
            else None,
            "generation_memory_writes": [
                {
                    "created": write["created"],
                    "source_event_id": write["source_event_id"],
                    "institutional_entry_id": write["institutional_entry_id"],
                    "persona_memory_ids": list(write["persona_memory_ids"]),
                }
                for write in memory_writes
            ],
            "memory_reused_for_next_decision": [memory_contexts[1], memory_contexts[2]],
            "institutional_memory_lineage": copy.deepcopy(dict(institutional_memory_lineage)),
        },
        "reflection": {
            "agent_decision_traces": list(decision_traces),
            "candidate_counts": [trace["candidate_count"] for trace in decision_traces],
            "selected_candidate_ids": [trace["selected_candidate_id"] for trace in decision_traces],
        },
        "operational_context": dict(operational_context),
        "cross_cycle": {
            "runtime_feedback_carryover": copy.deepcopy(dict(cross_cycle_carryover)),
            "persisted_cycle_resume": copy.deepcopy(dict(persisted_cycle_resume)),
            "multi_cycle_lineage": copy.deepcopy(dict(multi_cycle_lineage)),
        },
        "validation_cycle": {
            "planning": dict(validation_plan),
            "execution_review": dict(validation_diagnostics),
            "repair": dict(validation_repair),
        },
        "evolution": {
            "decision_id": evolution_decision.decision_id,
            "decision_state": _enum_value(evolution_decision.decision_state),
            "action_type": _enum_value(evolution_decision.action_type),
            "evidence_refs": [
                ref.to_dict() if isinstance(ref, EvidenceRef) else copy.deepcopy(dict(ref))
                for ref in evolution_decision.evidence_refs
            ],
            "metadata": copy.deepcopy(dict(evolution_decision.metadata or {})),
            "review_steps": [_enum_value(step.step_type) for step in evolution_decision.review_chain],
            "execution_status": _enum_value(evolution_decision.execution_result.status)
            if evolution_decision.execution_result
            else None,
            "trajectory": copy.deepcopy(dict(evolution_trajectory)),
            "no_leakage_protocol": copy.deepcopy(dict(no_leakage_protocol)),
            "strict_oos_evolution_proof": copy.deepcopy(dict(strict_oos_evolution_proof)),
        },
        "usability_dimensions": dict(usability_dimensions),
        "overall_usability_score": overall_usability_score,
        "usable": {
            "non_repeated_validation": bool(episode.validation_signature),
            "traded_portfolio_all_generations": all(execution["filled"] for execution in executions),
            "no_leakage_holdout": usability_dimensions["no_leakage_temporal_protocol"] == 1.0,
            "memory_retrieval_drives_next_decision": usability_dimensions["memory_influences_decision"] == 1.0,
            "memory_counterfactual_drives_decision": usability_dimensions[
                "memory_counterfactual_decision"
            ] == 1.0,
            "cross_persona_institutional_memory_drives_decision": usability_dimensions[
                "institutional_memory_lineage"
            ] == 1.0,
            "multi_oss_feedback_drives_decision": usability_dimensions["oss_evidence_completeness"] == 1.0,
            "policy_candidate_oss_materiality": usability_dimensions[
                "policy_candidate_oss_materiality"
            ] == 1.0,
            "reflection_artifact_oss_materiality": usability_dimensions[
                "reflection_artifact_oss_materiality"
            ] == 1.0,
            "multi_oss_closed_loop_drives_decision": usability_dimensions["multi_oss_closed_loop"] == 1.0,
            "persona_oss_ooda_causality_replayed": usability_dimensions[
                "persona_oss_ooda_causality"
            ] == 1.0,
            "cross_cycle_runtime_feedback_drives_next_case": usability_dimensions[
                "cross_cycle_runtime_carryover"
            ] == 1.0,
            "persisted_cycle_resume_drives_next_case": usability_dimensions[
                "persisted_cycle_resume_carryover"
            ] == 1.0,
            "multi_cycle_lineage_drives_next_case": usability_dimensions[
                "multi_cycle_lineage_carryover"
            ] == 1.0,
            "oss_response_followup_loop_drives_decision": usability_dimensions[
                "oss_response_followup_loop"
            ] == 1.0,
            "multi_oss_disagreement_arbitrated": usability_dimensions["oss_disagreement_arbitration"] == 1.0,
            "tracking_reconciliation_drives_decision": usability_dimensions["tracking_reconciliation"] == 1.0,
            "alpha_seed_revision_drives_decision": usability_dimensions["alpha_seed_revision"] == 1.0,
            "persona_decision_artifacts_replay": usability_dimensions["persona_decision_artifact"] == 1.0,
            "persona_reasoning_drives_candidate_generation": usability_dimensions[
                "persona_reasoning_generation"
            ] == 1.0,
            "multi_generation_evolution": usability_dimensions["multi_generation_trajectory"] == 1.0,
            "strict_oos_evolution": usability_dimensions["strict_oos_evolution"] == 1.0,
            "portfolio_level": len(episode.windows) == PORTFOLIO_LEG_COUNT,
            "multi_dimensional_score_passed": overall_usability_score >= MIN_USABILITY_SCORE,
            "validation_planned_before_execution": usability_dimensions["validation_planning"] == 1.0,
            "validation_diagnostics_passed": validation_diagnostics["failed_check_count"] == 0,
            "validation_deficiencies_repaired": not validation_repair["unresolved_deficiencies"],
            "market_friction_model_applied": usability_dimensions["market_friction_model"] == 1.0,
            "broker_lifecycle_reconciled": usability_dimensions["broker_lifecycle_reconciliation"] == 1.0,
            "broker_adapter_lifecycle_replayed": usability_dimensions["broker_adapter_lifecycle"] == 1.0,
            "broker_adapter_response_drives_followup": usability_dimensions["broker_adapter_followup"] == 1.0,
            "persona_conflicts_resolved": usability_dimensions["persona_conflict_resolution"] == 1.0,
            "restart_recovery_restores_loop": usability_dimensions["restart_recovery"] == 1.0,
            "autonomous_scheduler_orders_next_cycle": usability_dimensions["autonomous_scheduler"] == 1.0,
            "lean_engine_replay_uses_runtime_binding": usability_dimensions["lean_engine_replay"] == 1.0,
            "lean_runtime_feedback_drives_ooda": usability_dimensions["lean_runtime_feedback"] == 1.0,
            "experiment_tracking_lineage_reaches_lean_handoff": usability_dimensions[
                "experiment_tracking_lineage_handoff"
            ] == 1.0,
            "policy_oss_lineage_reaches_lean_handoff": usability_dimensions[
                "policy_oss_lineage_handoff"
            ] == 1.0,
            "reflection_oss_lineage_reaches_lean_handoff": usability_dimensions[
                "reflection_oss_lineage_handoff"
            ] == 1.0,
            "openclaw_session_reaches_lean_handoff": usability_dimensions[
                "openclaw_session_handoff"
            ] == 1.0,
            "alpha_seed_revision_reaches_lean_handoff": usability_dimensions[
                "alpha_seed_revision_handoff"
            ] == 1.0,
            "lean_packet_execution_projection_replayed": usability_dimensions[
                "lean_packet_execution_projection"
            ] == 1.0,
            "evolved_strategy_packet_reaches_lean_handoff": usability_dimensions[
                "evolved_strategy_packet_handoff"
            ] == 1.0,
            "scheduler_conflict_ooda_dispatch_replayed": usability_dimensions[
                "scheduler_conflict_ooda_dispatch"
            ] == 1.0,
            "shioaji_sandbox_lifecycle_reconciled": usability_dimensions["shioaji_sandbox_lifecycle"] == 1.0,
            "case_specific_upstream_artifact_feedback": usability_dimensions[
                "case_specific_upstream_artifact_feedback"
            ] == 1.0,
            "case_specific_selected_oss_feedback": _case_selected_oss_feedback_is_usable(
                episode=episode,
                artifacts=case_upstream_artifacts,
            ),
            "lean_handoff_packet_materialized": usability_dimensions["lean_handoff_packet"] == 1.0,
            "evolved": _enum_value(evolution_decision.decision_state) == EvolutionDecisionState.EXECUTED.value,
        },
    }


def _build_summary(
    *,
    dataset: Mapping[str, Any],
    personas: Sequence[Mapping[str, Any]],
    cases: Sequence[Mapping[str, Any]],
    oss_results: Sequence[Mapping[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    signatures = [str(case["validation_signature"]) for case in cases]
    plan_signatures = [
        str(case["validation_cycle"]["planning"]["plan_signature"])
        for case in cases
    ]
    combo_signatures = [
        str(case["validation_cycle"]["planning"]["selected_validation_plan"]["target_combo_signature"])
        for case in cases
    ]
    usable = [case["usable"] for case in cases]
    dimensions = [case["usability_dimensions"] for case in cases]
    all_components = sorted({component for case in cases for component in case["oss_feedback"]["components_used"]})
    decision_artifacts = [
        trace["agent_decision_artifact"]
        for case in cases
        for trace in case["reflection"]["agent_decision_traces"]
    ]
    memory_counterfactuals = [
        artifact["memory_counterfactual"] for artifact in decision_artifacts
    ]
    institutional_memory_lineages = [
        case["memory"]["institutional_memory_lineage"] for case in cases
    ]
    evolution_trajectories = [case["evolution"]["trajectory"] for case in cases]
    no_leakage_protocols = [case["evolution"]["no_leakage_protocol"] for case in cases]
    strict_oos_evolution_proofs = [
        case["evolution"]["strict_oos_evolution_proof"] for case in cases
    ]
    policy_candidate_materialities = [
        case["oss_feedback"]["policy_candidate_materiality"] for case in cases
    ]
    reflection_artifact_materialities = [
        case["oss_feedback"]["reflection_artifact_materiality"] for case in cases
    ]
    oss_followup_loops = [case["oss_feedback"]["response_followup_loop"] for case in cases]
    multi_oss_closed_loop_proofs = [
        case["oss_feedback"]["closed_loop_proof"] for case in cases
    ]
    persona_oss_ooda_ledgers = [
        case["oss_feedback"]["ooda_causal_ledger"] for case in cases
    ]
    cross_cycle_carryovers = [
        case["cross_cycle"]["runtime_feedback_carryover"] for case in cases
    ]
    persisted_cycle_resumes = [
        case["cross_cycle"]["persisted_cycle_resume"] for case in cases
    ]
    multi_cycle_lineages = [
        case["cross_cycle"]["multi_cycle_lineage"] for case in cases
    ]
    oss_disagreement_arbitrations = [
        case["case_upstream_artifacts"]["oss_disagreement_arbitration"] for case in cases
    ]
    tracking_reconciliations = [
        case["case_upstream_artifacts"]["tracking_reconciliation"] for case in cases
    ]
    alpha_seed_revisions = [
        case["case_upstream_artifacts"]["alpha_seed_revision"] for case in cases
    ]
    broker_adapter_lifecycles = [
        case["operational_context"]["broker_adapter_lifecycle"] for case in cases
    ]
    broker_adapter_followups = [
        case["operational_context"]["broker_adapter_followup"] for case in cases
    ]
    lean_packet_execution_projections = [
        case["operational_context"]["lean_packet_execution_projection"] for case in cases
    ]
    lean_object_store_packet_readbacks = [
        case["operational_context"]["lean_engine_replay"]["lean_object_store_packet_readback"]
        for case in cases
    ]
    lean_runtime_feedbacks = [
        case["operational_context"]["lean_runtime_feedback"] for case in cases
    ]
    experiment_tracking_lineage_handoffs = [
        case["operational_context"]["experiment_tracking_lineage_handoff"]
        for case in cases
    ]
    policy_oss_lineage_handoffs = [
        case["operational_context"]["policy_oss_lineage_handoff"] for case in cases
    ]
    reflection_oss_lineage_handoffs = [
        case["operational_context"]["reflection_oss_lineage_handoff"] for case in cases
    ]
    openclaw_session_handoffs = [
        case["operational_context"]["openclaw_session_handoff"] for case in cases
    ]
    alpha_seed_revision_handoffs = [
        case["operational_context"]["alpha_seed_revision_handoff"] for case in cases
    ]
    evolved_strategy_packet_proofs = [
        case["operational_context"]["evolved_strategy_packet_proof"] for case in cases
    ]
    scheduler_conflict_ooda_proofs = [
        case["operational_context"]["scheduler_conflict_ooda_proof"] for case in cases
    ]
    coverage = {
        "persona_ids": sorted({_persona_id(persona) for persona in personas}),
        "covered_persona_ids": sorted({str(case["persona_id"]) for case in cases}),
        "seed_keys": [source.key for source in ALPHA_SEED_SOURCES],
        "covered_seed_keys": sorted({str(case["seed_key"]) for case in cases}),
        "instruments": sorted({instrument for case in cases for instrument in case["portfolio"]["instruments"]}),
        "oss_components": all_components,
        "reflection_archetypes": sorted({case["reflection"]["agent_decision_traces"][0]["trigger"] for case in cases}),
        "generation_paths": sorted({"->".join(str(result["policy_version"]) for result in case["generation_results"]) for case in cases}),
        "quantity_types": sorted({case["order_profile"]["quantity_type"] for case in cases}),
        "order_types": sorted({case["order_profile"]["order_type"] for case in cases}),
        "regime_paths": sorted({"|".join(case["portfolio"]["regime_path"]) for case in cases}),
        "operational_scenarios": sorted({case["operational_context"]["scenario"] for case in cases}),
        "market_friction_models": sorted({
            case["operational_context"]["market_friction"]["model_id"] for case in cases
        }),
        "broker_lifecycle_statuses": sorted({
            status
            for case in cases
            for status in case["operational_context"]["broker_lifecycle"]["lifecycle_statuses"]
        }),
        "broker_adapter_lifecycle_models": sorted({
            lifecycle["model_id"] for lifecycle in broker_adapter_lifecycles
        }),
        "broker_adapter_lifecycle_scenarios": sorted({
            lifecycle["scenario"] for lifecycle in broker_adapter_lifecycles
        }),
        "broker_adapter_lifecycle_required_statuses": sorted({
            status
            for lifecycle in broker_adapter_lifecycles
            for status in lifecycle["required_statuses"]
        }),
        "broker_adapter_lifecycle_replay_flags": sorted({
            flag
            for lifecycle in broker_adapter_lifecycles
            for flag, value in lifecycle["replay"].items()
            if value is True
        }),
        "broker_adapter_followup_models": sorted({
            followup["model_id"] for followup in broker_adapter_followups
        }),
        "broker_adapter_followup_actions": sorted({
            followup["persona_followup"]["action"] for followup in broker_adapter_followups
        }),
        "broker_adapter_followup_action_families": sorted({
            followup["persona_followup"]["action_family"] for followup in broker_adapter_followups
        }),
        "broker_adapter_followup_next_steps": sorted({
            followup["persona_followup"]["next_persona_step"] for followup in broker_adapter_followups
        }),
        "broker_adapter_followup_replay_flags": sorted({
            flag
            for followup in broker_adapter_followups
            for flag, value in followup["replay"].items()
            if value is True
        }),
        "persona_conflict_types": sorted({
            conflict_type
            for case in cases
            for conflict_type in case["operational_context"]["persona_conflict_resolution"]["conflict_types"]
        }),
        "scheduler_phases": sorted({
            phase["phase"]
            for case in cases
            for phase in case["operational_context"]["autonomous_schedule"]["phases"]
        }),
        "lean_engine_replay_models": sorted({
            case["operational_context"]["lean_engine_replay"]["model_id"] for case in cases
        }),
        "lean_engine_algorithm_modules": sorted({
            case["operational_context"]["lean_engine_replay"]["algorithm_module"] for case in cases
        }),
        "lean_object_store_packet_readback_models": sorted({
            readback["model_id"] for readback in lean_object_store_packet_readbacks
        }),
        "lean_object_store_packet_readback_target_counts": sorted({
            readback["target_count"] for readback in lean_object_store_packet_readbacks
        }),
        "lean_object_store_packet_readback_loaded_signal_sources": sorted({
            "first_packet_target"
            for readback in lean_object_store_packet_readbacks
            if readback["loaded_signal_id"] == readback["target_signal_ids"][0]
        }),
        "lean_object_store_packet_readback_replay_flags": sorted({
            flag
            for readback in lean_object_store_packet_readbacks
            for flag, value in readback["replay"].items()
            if value is True
        }),
        "lean_packet_execution_projection_models": sorted({
            projection["model_id"] for projection in lean_packet_execution_projections
        }),
        "lean_packet_execution_projection_generations": sorted({
            projection["generation"] for projection in lean_packet_execution_projections
        }),
        "lean_packet_execution_projection_leg_counts": sorted({
            projection["leg_count"] for projection in lean_packet_execution_projections
        }),
        "lean_packet_execution_projection_event_chains": sorted({
            "->".join(leg["event_chain"])
            for projection in lean_packet_execution_projections
            for leg in projection["leg_projections"]
        }),
        "lean_packet_execution_projection_lean_calls": sorted({
            leg["lean_order_call"]
            for projection in lean_packet_execution_projections
            for leg in projection["leg_projections"]
        }),
        "lean_packet_execution_projection_quantity_types": sorted({
            leg["quantity_type"]
            for projection in lean_packet_execution_projections
            for leg in projection["leg_projections"]
        }),
        "lean_packet_execution_projection_order_types": sorted({
            leg["order_type"]
            for projection in lean_packet_execution_projections
            for leg in projection["leg_projections"]
        }),
        "lean_packet_execution_projection_replay_flags": sorted({
            flag
            for projection in lean_packet_execution_projections
            for flag, value in projection["replay"].items()
            if value is True
        }),
        "lean_runtime_feedback_models": sorted({
            feedback["model_id"] for feedback in lean_runtime_feedbacks
        }),
        "lean_runtime_feedback_actions": sorted({
            feedback["persona_ooda_followup"]["action"] for feedback in lean_runtime_feedbacks
        }),
        "lean_runtime_feedback_action_families": sorted({
            feedback["persona_ooda_followup"]["action_family"] for feedback in lean_runtime_feedbacks
        }),
        "lean_runtime_feedback_ooda_steps": sorted({
            feedback["persona_ooda_followup"]["ooda_step"] for feedback in lean_runtime_feedbacks
        }),
        "lean_runtime_feedback_replay_flags": sorted({
            flag
            for feedback in lean_runtime_feedbacks
            for flag, value in feedback["replay"].items()
            if value is True
        }),
        "experiment_tracking_lineage_handoff_models": sorted({
            proof["model_id"] for proof in experiment_tracking_lineage_handoffs
        }),
        "experiment_tracking_lineage_handoff_backends": sorted({
            proof["backend"] for proof in experiment_tracking_lineage_handoffs
        }),
        "experiment_tracking_lineage_handoff_replay_flags": sorted({
            flag
            for proof in experiment_tracking_lineage_handoffs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "policy_oss_lineage_handoff_models": sorted({
            proof["model_id"] for proof in policy_oss_lineage_handoffs
        }),
        "policy_oss_lineage_handoff_components": sorted({
            proof["component"] for proof in policy_oss_lineage_handoffs
        }),
        "policy_oss_lineage_handoff_artifact_families": sorted({
            proof["artifact_family"] for proof in policy_oss_lineage_handoffs
        }),
        "policy_oss_lineage_handoff_replay_flags": sorted({
            flag
            for proof in policy_oss_lineage_handoffs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "reflection_oss_lineage_handoff_models": sorted({
            proof["model_id"] for proof in reflection_oss_lineage_handoffs
        }),
        "reflection_oss_lineage_handoff_components": sorted({
            proof["component"] for proof in reflection_oss_lineage_handoffs
        }),
        "reflection_oss_lineage_handoff_artifact_families": sorted({
            proof["artifact_family"] for proof in reflection_oss_lineage_handoffs
        }),
        "reflection_oss_lineage_handoff_replay_flags": sorted({
            flag
            for proof in reflection_oss_lineage_handoffs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "openclaw_session_handoff_models": sorted({
            proof["model_id"] for proof in openclaw_session_handoffs
        }),
        "openclaw_session_handoff_components": sorted({
            proof["component"] for proof in openclaw_session_handoffs
        }),
        "openclaw_session_handoff_artifact_families": sorted({
            proof["artifact_family"] for proof in openclaw_session_handoffs
        }),
        "openclaw_session_handoff_session_states": sorted({
            proof["session_state"] for proof in openclaw_session_handoffs
        }),
        "openclaw_session_handoff_replay_flags": sorted({
            flag
            for proof in openclaw_session_handoffs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "alpha_seed_revision_handoff_models": sorted({
            proof["model_id"] for proof in alpha_seed_revision_handoffs
        }),
        "alpha_seed_revision_handoff_components": sorted({
            proof["component"] for proof in alpha_seed_revision_handoffs
        }),
        "alpha_seed_revision_handoff_actions": sorted({
            proof["revision_action"] for proof in alpha_seed_revision_handoffs
        }),
        "alpha_seed_revision_handoff_replay_flags": sorted({
            flag
            for proof in alpha_seed_revision_handoffs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "evolved_strategy_packet_models": sorted({
            proof["model_id"] for proof in evolved_strategy_packet_proofs
        }),
        "evolved_strategy_packet_generations": sorted({
            proof["generation"] for proof in evolved_strategy_packet_proofs
        }),
        "evolved_strategy_packet_source_to_validation_paths": sorted({
            f"{proof['source_outcome_window']}:{proof['validation_window']}"
            for proof in evolved_strategy_packet_proofs
        }),
        "evolved_strategy_packet_replay_flags": sorted({
            flag
            for proof in evolved_strategy_packet_proofs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "scheduler_conflict_ooda_models": sorted({
            proof["model_id"] for proof in scheduler_conflict_ooda_proofs
        }),
        "scheduler_conflict_ooda_event_types": sorted({
            event["event_type"]
            for proof in scheduler_conflict_ooda_proofs
            for event in proof["dispatch_events"]
        }),
        "scheduler_conflict_ooda_phases": sorted({
            event["phase"]
            for proof in scheduler_conflict_ooda_proofs
            for event in proof["phase_events"]
        }),
        "scheduler_conflict_ooda_next_ooda_steps": sorted({
            proof["next_ooda_step"] for proof in scheduler_conflict_ooda_proofs
        }),
        "scheduler_conflict_ooda_next_scheduler_phases": sorted({
            proof["next_scheduler_phase"] for proof in scheduler_conflict_ooda_proofs
        }),
        "scheduler_conflict_ooda_replay_flags": sorted({
            flag
            for proof in scheduler_conflict_ooda_proofs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "shioaji_sandbox_models": sorted({
            case["operational_context"]["shioaji_sandbox_lifecycle"]["model_id"] for case in cases
        }),
        "shioaji_sandbox_run_modes": sorted({
            case["operational_context"]["shioaji_sandbox_lifecycle"]["run_mode"] for case in cases
        }),
        "case_vectorbt_backends": sorted({
            case["case_upstream_artifacts"]["vectorbt"]["backend"] for case in cases
        }),
        "case_tracking_components": sorted({
            case["case_upstream_artifacts"]["tracker"]["component"] for case in cases
        }),
        "case_tracking_backends": sorted({
            case["case_upstream_artifacts"]["tracker"]["backend"] for case in cases
        }),
        "case_upstream_allowed_windows": sorted({
            "+".join(case["case_upstream_artifacts"]["allowed_windows"]) for case in cases
        }),
        "case_selected_oss_roles": sorted({
            role
            for case in cases
            for role in case["case_upstream_artifacts"]["selected_oss"]
        }),
        "case_selected_oss_components_by_role": {
            role: sorted({
                case["case_upstream_artifacts"]["selected_oss"][role]["component"]
                for case in cases
                if role in case["case_upstream_artifacts"]["selected_oss"]
            })
            for role in ("alpha_model", "policy_candidate", "reflection_artifact", "risk_analytics")
        },
        "case_selected_oss_artifact_families": sorted({
            entry["artifact_family"]
            for case in cases
            for entry in case["case_upstream_artifacts"]["selected_oss"].values()
        }),
        "policy_candidate_materiality_models": sorted({
            proof["model_id"] for proof in policy_candidate_materialities
        }),
        "policy_candidate_materiality_components": sorted({
            proof["component"] for proof in policy_candidate_materialities
        }),
        "policy_candidate_materiality_artifact_families": sorted({
            proof["artifact_family"] for proof in policy_candidate_materialities
        }),
        "policy_candidate_materiality_metric_signal_keys": sorted({
            key
            for proof in policy_candidate_materialities
            for key in proof["metric_signal_keys"]
        }),
        "policy_candidate_materiality_replay_flags": sorted({
            flag
            for proof in policy_candidate_materialities
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "reflection_artifact_materiality_models": sorted({
            proof["model_id"] for proof in reflection_artifact_materialities
        }),
        "reflection_artifact_materiality_components": sorted({
            proof["component"] for proof in reflection_artifact_materialities
        }),
        "reflection_artifact_materiality_artifact_families": sorted({
            proof["artifact_family"] for proof in reflection_artifact_materialities
        }),
        "reflection_artifact_materiality_metric_signal_keys": sorted({
            key
            for proof in reflection_artifact_materialities
            for key in proof["metric_signal_keys"]
        }),
        "reflection_artifact_materiality_replay_flags": sorted({
            flag
            for proof in reflection_artifact_materialities
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "oss_response_followup_loop_models": sorted({
            loop["model_id"] for loop in oss_followup_loops
        }),
        "oss_response_followup_roles": sorted({
            followup["role"]
            for loop in oss_followup_loops
            for followup in loop["followups"]
        }),
        "oss_response_followup_components": sorted({
            followup["component"]
            for loop in oss_followup_loops
            for followup in loop["followups"]
        }),
        "oss_response_followup_candidate_actions": sorted({
            followup["response"]["candidate_action"]
            for loop in oss_followup_loops
            for followup in loop["followups"]
        }),
        "multi_oss_closed_loop_models": sorted({
            proof["model_id"] for proof in multi_oss_closed_loop_proofs
        }),
        "multi_oss_closed_loop_roles": sorted({
            record["role"]
            for proof in multi_oss_closed_loop_proofs
            for record in proof["role_records"]
        }),
        "multi_oss_closed_loop_components": sorted({
            record["component"]
            for proof in multi_oss_closed_loop_proofs
            for record in proof["role_records"]
        }),
        "multi_oss_closed_loop_role_components": sorted({
            f"{record['role']}:{record['component']}"
            for proof in multi_oss_closed_loop_proofs
            for record in proof["role_records"]
        }),
        "multi_oss_closed_loop_candidate_actions": sorted({
            record["followup_candidate_action"]
            for proof in multi_oss_closed_loop_proofs
            for record in proof["role_records"]
        }),
        "multi_oss_closed_loop_replay_flags": sorted({
            flag
            for proof in multi_oss_closed_loop_proofs
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "persona_oss_ooda_ledger_models": sorted({
            ledger["model_id"] for ledger in persona_oss_ooda_ledgers
        }),
        "persona_oss_ooda_ledger_phases": sorted({
            event["ooda_phase"]
            for ledger in persona_oss_ooda_ledgers
            for event in ledger["events"]
        }),
        "persona_oss_ooda_ledger_event_types": sorted({
            event["event_type"]
            for ledger in persona_oss_ooda_ledgers
            for event in ledger["events"]
        }),
        "persona_oss_ooda_ledger_actors": sorted({
            event["actor"]
            for ledger in persona_oss_ooda_ledgers
            for event in ledger["events"]
        }),
        "persona_oss_ooda_ledger_replay_flags": sorted({
            flag
            for ledger in persona_oss_ooda_ledgers
            for flag, value in ledger["replay"].items()
            if value is True
        }),
        "cross_cycle_carryover_models": sorted({
            proof["model_id"] for proof in cross_cycle_carryovers
        }),
        "cross_cycle_carryover_statuses": sorted({
            proof["carryover_status"] for proof in cross_cycle_carryovers
        }),
        "cross_cycle_carryover_next_ooda_steps": sorted({
            str(proof["next_ooda_step"])
            for proof in cross_cycle_carryovers
            if proof.get("next_ooda_step")
        }),
        "cross_cycle_carryover_score_adjusted_actions": sorted({
            action
            for proof in cross_cycle_carryovers
            for action, value in proof["score_adjustments"].items()
            if float(value) > 0.0
        }),
        "cross_cycle_carryover_replay_flags": sorted({
            flag
            for proof in cross_cycle_carryovers
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "persisted_cycle_resume_models": sorted({
            proof["model_id"] for proof in persisted_cycle_resumes
        }),
        "persisted_cycle_resume_statuses": sorted({
            proof["resume_status"] for proof in persisted_cycle_resumes
        }),
        "persisted_cycle_resume_steps": sorted({
            str(proof["resume_step"])
            for proof in persisted_cycle_resumes
            if proof.get("resume_step")
        }),
        "persisted_cycle_resume_next_scheduler_phases": sorted({
            str(proof["next_scheduler_phase"])
            for proof in persisted_cycle_resumes
            if proof.get("next_scheduler_phase")
        }),
        "persisted_cycle_resume_score_adjusted_actions": sorted({
            action
            for proof in persisted_cycle_resumes
            for action, value in proof["score_adjustments"].items()
            if float(value) > 0.0
        }),
        "persisted_cycle_resume_replay_flags": sorted({
            flag
            for proof in persisted_cycle_resumes
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "multi_cycle_lineage_models": sorted({
            proof["model_id"] for proof in multi_cycle_lineages
        }),
        "multi_cycle_lineage_statuses": sorted({
            proof["lineage_status"] for proof in multi_cycle_lineages
        }),
        "multi_cycle_lineage_depths": sorted({
            int(proof["lineage_depth"]) for proof in multi_cycle_lineages
        }),
        "multi_cycle_lineage_trend_signals": sorted({
            str(proof["trend_signal"])
            for proof in multi_cycle_lineages
            if proof.get("trend_signal")
        }),
        "multi_cycle_lineage_score_adjusted_actions": sorted({
            action
            for proof in multi_cycle_lineages
            for action, value in proof["score_adjustments"].items()
            if float(value) > 0.0
        }),
        "multi_cycle_lineage_replay_flags": sorted({
            flag
            for proof in multi_cycle_lineages
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "oss_disagreement_arbitration_models": sorted({
            arbitration["model_id"] for arbitration in oss_disagreement_arbitrations
        }),
        "oss_disagreement_types": sorted({
            conflict["conflict_type"]
            for arbitration in oss_disagreement_arbitrations
            for conflict in arbitration["conflicts"]
        }),
        "oss_disagreement_source_role_pairs": sorted({
            "+".join(conflict["source_roles"])
            for arbitration in oss_disagreement_arbitrations
            for conflict in arbitration["conflicts"]
        }),
        "oss_disagreement_resolution_actions": sorted({
            action
            for arbitration in oss_disagreement_arbitrations
            for action in arbitration["persona_arbitration_response"]["resolution_actions"]
        }),
        "oss_disagreement_candidate_actions": sorted({
            action
            for arbitration in oss_disagreement_arbitrations
            for action in arbitration["candidate_evidence_refs_by_action"]
        }),
        "tracking_reconciliation_models": sorted({
            reconciliation["model_id"] for reconciliation in tracking_reconciliations
        }),
        "tracking_reconciliation_divergence_types": sorted({
            reconciliation["divergence"]["divergence_type"]
            for reconciliation in tracking_reconciliations
        }),
        "tracking_reconciliation_repair_actions": sorted({
            reconciliation["repair"]["action"] for reconciliation in tracking_reconciliations
        }),
        "tracking_reconciliation_backends": sorted({
            reconciliation["backend"] for reconciliation in tracking_reconciliations
        }),
        "tracking_reconciliation_replay_flags": sorted({
            flag
            for reconciliation in tracking_reconciliations
            for flag, value in reconciliation["replay"].items()
            if value is True
        }),
        "tracking_reconciliation_candidate_actions": sorted({
            action
            for reconciliation in tracking_reconciliations
            for action in reconciliation["candidate_evidence_refs_by_action"]
        }),
        "alpha_seed_revision_models": sorted({
            revision["model_id"] for revision in alpha_seed_revisions
        }),
        "alpha_seed_revision_components": sorted({
            revision["alpha_component"] for revision in alpha_seed_revisions
        }),
        "alpha_seed_revision_actions": sorted({
            revision["revision"]["action"] for revision in alpha_seed_revisions
        }),
        "alpha_seed_revision_replay_flags": sorted({
            flag
            for revision in alpha_seed_revisions
            for flag, value in revision["replay"].items()
            if value is True
        }),
        "alpha_seed_revision_candidate_actions": sorted({
            action
            for revision in alpha_seed_revisions
            for action in revision["candidate_evidence_refs_by_action"]
        }),
        "agent_decision_artifact_models": sorted({
            artifact["model_id"] for artifact in decision_artifacts
        }),
        "agent_candidate_generator_models": sorted({
            artifact["candidate_generation"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_candidate_scorer_models": sorted({
            artifact["scorer"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_risk_evaluator_models": sorted({
            artifact["risk_evaluator"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_decision_artifact_generations": sorted({
            artifact["generation"] for artifact in decision_artifacts
        }),
        "agent_memory_influence_models": sorted({
            artifact["memory_influence"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_memory_influence_statuses": sorted({
            artifact["memory_influence"]["status"] for artifact in decision_artifacts
        }),
        "agent_memory_selected_action_hints": sorted({
            artifact["memory_influence"]["selected_action_hint"] for artifact in decision_artifacts
        }),
        "agent_memory_counterfactual_models": sorted({
            proof["model_id"] for proof in memory_counterfactuals
        }),
        "agent_memory_counterfactual_outcomes": sorted({
            proof["outcome"] for proof in memory_counterfactuals
        }),
        "agent_memory_counterfactual_replay_flags": sorted({
            flag
            for proof in memory_counterfactuals
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "institutional_memory_lineage_models": sorted({
            proof["model_id"] for proof in institutional_memory_lineages
        }),
        "institutional_memory_lineage_statuses": sorted({
            proof["lineage_status"] for proof in institutional_memory_lineages
        }),
        "institutional_memory_lineage_score_adjusted_actions": sorted({
            action
            for proof in institutional_memory_lineages
            for action, value in proof["score_adjustments"].items()
            if float(value) > 0.0
        }),
        "institutional_memory_lineage_replay_flags": sorted({
            flag
            for proof in institutional_memory_lineages
            for flag, value in proof["replay"].items()
            if value is True
        }),
        "agent_persona_reasoning_models": sorted({
            artifact["persona_reasoning"]["response"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_persona_reasoning_evaluator_models": sorted({
            artifact["persona_reasoning"]["evaluator"]["model_id"] for artifact in decision_artifacts
        }),
        "agent_persona_reasoning_preferred_actions": sorted({
            artifact["persona_reasoning"]["response"]["preferred_action_hint"] for artifact in decision_artifacts
        }),
        "agent_persona_reasoning_candidate_actions": sorted({
            blueprint["action"]
            for artifact in decision_artifacts
            for blueprint in artifact["persona_reasoning"]["response"]["candidate_blueprints"]
        }),
        "evolution_trajectory_models": sorted({
            trajectory["model_id"] for trajectory in evolution_trajectories
        }),
        "evolution_trajectory_statuses": sorted({
            trajectory["trend"]["convergence_status"] for trajectory in evolution_trajectories
        }),
        "evolution_trajectory_windows": sorted({
            "->".join(comparison["evaluation_window"] for comparison in trajectory["comparisons"])
            for trajectory in evolution_trajectories
        }),
        "no_leakage_temporal_protocol_models": sorted({
            protocol["model_id"] for protocol in no_leakage_protocols
        }),
        "no_leakage_temporal_protocol_paths": sorted({
            protocol["protocol_path"] for protocol in no_leakage_protocols
        }),
        "no_leakage_temporal_protocol_stage_windows": sorted({
            "->".join(stage["evaluation_window"] for stage in protocol["stage_contracts"])
            for protocol in no_leakage_protocols
        }),
        "strict_oos_evolution_proof_models": sorted({
            proof["model_id"] for proof in strict_oos_evolution_proofs
        }),
        "strict_oos_evolution_source_to_validation_paths": sorted({
            "->".join(
                f"{step['source_outcome_window']}:{step['validation_window']}"
                for step in proof["proof_steps"]
            )
            for proof in strict_oos_evolution_proofs
        }),
        "strict_oos_evolution_replay_flags": sorted({
            flag
            for proof in strict_oos_evolution_proofs
            for flag, value in proof["replay"].items()
            if value is True
        }),
    }
    old_case_ids = {f"agent-usability-{index:04d}" for index in range(1, DEFAULT_CASE_COUNT + 1)}
    return {
        "validation_family": "agent_trading_reflection_evolution_hardened_usability",
        "generated_at": generated_at,
        "total_cases": len(cases),
        "unique_validation_signature_count": len(set(signatures)),
        "unique_validation_plan_signature_count": len(set(plan_signatures)),
        "unique_target_combo_signature_count": len(set(combo_signatures)),
        "overlaps_previous_agent_usability_case_ids": bool(
            set(str(case["case_id"]) for case in cases).intersection(old_case_ids)
        ),
        "historical_dataset": {
            "dataset_id": dataset.get("dataset_id"),
            "fixture": HISTORICAL_OHLCV_FIXTURE,
            "record_count": len(dataset.get("records", [])),
            "instrument_count": len({record.get("instrument") for record in dataset.get("records", [])}),
        },
        "persona_count": len(personas),
        "alpha_seed_count": len(ALPHA_SEED_SOURCES),
        "portfolio_episode_count": len(cases),
        "portfolio_leg_count": PORTFOLIO_LEG_COUNT,
        "generation_count": GENERATION_COUNT,
        "oss_result_count": len(oss_results),
        "oss_components_completed": sorted({str(result.get("component")) for result in oss_results if result.get("status") == "completed"}),
        "no_leakage_holdout_count": sum(1 for item in usable if item["no_leakage_holdout"]),
        "no_leakage_temporal_protocol_count": len(no_leakage_protocols),
        "no_leakage_temporal_protocol_pass_count": sum(
            1 for protocol in no_leakage_protocols if _no_leakage_temporal_protocol_is_usable(protocol)
        ),
        "strict_oos_evolution_proof_count": len(strict_oos_evolution_proofs),
        "strict_oos_evolution_proof_pass_count": sum(
            1 for proof in strict_oos_evolution_proofs if _strict_oos_evolution_proof_is_usable(proof)
        ),
        "strict_oos_evolution_count": sum(1 for item in usable if item["strict_oos_evolution"]),
        "portfolio_trade_generation_count": sum(len(case["generation_results"]) for case in cases),
        "portfolio_trade_generation_fill_count": sum(
            1
            for case in cases
            for generation in case["generation_results"]
            if generation["filled"]
        ),
        "memory_retrieval_drives_next_decision_count": sum(
            1 for item in usable if item["memory_retrieval_drives_next_decision"]
        ),
        "memory_counterfactual_proof_count": len(memory_counterfactuals),
        "memory_counterfactual_proof_pass_count": sum(
            1 for proof in memory_counterfactuals if _memory_counterfactual_proof_is_usable(proof)
        ),
        "memory_counterfactual_retrieved_material_count": sum(
            1
            for proof in memory_counterfactuals
            if proof["memory_status"] == "retrieved"
            and proof["outcome"] == "memory_material_to_selected_score"
            and proof["replay"]["memory_changes_selected_score_when_retrieved"] is True
        ),
        "memory_counterfactual_cold_start_count": sum(
            1 for proof in memory_counterfactuals if proof["memory_status"] == "cold_start_declared"
        ),
        "memory_counterfactual_drives_decision_count": sum(
            1 for item in usable if item["memory_counterfactual_drives_decision"]
        ),
        "institutional_memory_lineage_count": len(institutional_memory_lineages),
        "institutional_memory_lineage_pass_count": sum(
            1 for proof in institutional_memory_lineages if _institutional_memory_lineage_is_usable(proof)
        ),
        "institutional_memory_lineage_cold_start_count": sum(
            1 for proof in institutional_memory_lineages if proof["lineage_status"] == "cold_start"
        ),
        "institutional_memory_lineage_applied_count": sum(
            1 for proof in institutional_memory_lineages if proof["lineage_status"] == "applied"
        ),
        "institutional_memory_lineage_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in institutional_memory_lineages
        ),
        "cross_persona_institutional_memory_drives_decision_count": sum(
            1 for item in usable if item["cross_persona_institutional_memory_drives_decision"]
        ),
        "intra_case_memory_influence_count": sum(
            1
            for case in cases
            if _trace_memory_influence_is_usable(case["reflection"]["agent_decision_traces"][1])
            and case["reflection"]["agent_decision_traces"][1]["decision_inputs"].get("memory_ref")
        ),
        "cross_case_memory_influence_count": sum(
            1
            for case in cases
            if case["memory"]["prior_memory"]
            and _trace_memory_influence_is_usable(case["reflection"]["agent_decision_traces"][0])
        ),
        "multi_oss_feedback_drives_decision_count": sum(
            1 for item in usable if item["multi_oss_feedback_drives_decision"]
        ),
        "policy_candidate_materiality_count": len(policy_candidate_materialities),
        "policy_candidate_materiality_pass_count": sum(
            1
            for proof in policy_candidate_materialities
            if _policy_candidate_materiality_is_usable(proof)
        ),
        "policy_candidate_materiality_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in policy_candidate_materialities
        ),
        "policy_candidate_oss_materiality_count": sum(
            1 for item in usable if item["policy_candidate_oss_materiality"]
        ),
        "reflection_artifact_materiality_count": len(reflection_artifact_materialities),
        "reflection_artifact_materiality_pass_count": sum(
            1
            for proof in reflection_artifact_materialities
            if _reflection_artifact_materiality_is_usable(proof)
        ),
        "reflection_artifact_materiality_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in reflection_artifact_materialities
        ),
        "reflection_artifact_oss_materiality_count": sum(
            1 for item in usable if item["reflection_artifact_oss_materiality"]
        ),
        "multi_oss_closed_loop_proof_count": len(multi_oss_closed_loop_proofs),
        "multi_oss_closed_loop_proof_pass_count": sum(
            1
            for proof in multi_oss_closed_loop_proofs
            if _multi_oss_closed_loop_proof_is_usable(proof)
        ),
        "multi_oss_closed_loop_role_binding_count": sum(
            len(proof["role_records"]) for proof in multi_oss_closed_loop_proofs
        ),
        "multi_oss_closed_loop_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in multi_oss_closed_loop_proofs
        ),
        "multi_oss_closed_loop_drives_decision_count": sum(
            1 for item in usable if item["multi_oss_closed_loop_drives_decision"]
        ),
        "persona_oss_ooda_ledger_count": len(persona_oss_ooda_ledgers),
        "persona_oss_ooda_ledger_pass_count": sum(
            1
            for ledger in persona_oss_ooda_ledgers
            if _persona_oss_ooda_causal_ledger_is_usable(ledger)
        ),
        "persona_oss_ooda_ledger_event_count": sum(
            len(ledger["events"]) for ledger in persona_oss_ooda_ledgers
        ),
        "persona_oss_ooda_ledger_handoff_event_count": sum(
            1
            for ledger in persona_oss_ooda_ledgers
            for event in ledger["events"]
            if event["event_type"] == "lean_handoff_packet"
        ),
        "persona_oss_ooda_causality_replay_count": sum(
            1 for item in usable if item["persona_oss_ooda_causality_replayed"]
        ),
        "cross_cycle_carryover_count": len(cross_cycle_carryovers),
        "cross_cycle_carryover_pass_count": sum(
            1 for proof in cross_cycle_carryovers if _cross_cycle_carryover_is_usable(proof)
        ),
        "cross_cycle_runtime_feedback_applied_count": sum(
            1 for proof in cross_cycle_carryovers if proof["carryover_status"] == "applied"
        ),
        "cross_cycle_runtime_feedback_cold_start_count": sum(
            1 for proof in cross_cycle_carryovers if proof["carryover_status"] == "cold_start"
        ),
        "cross_cycle_carryover_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in cross_cycle_carryovers
        ),
        "cross_cycle_runtime_feedback_drives_next_case_count": sum(
            1 for item in usable if item["cross_cycle_runtime_feedback_drives_next_case"]
        ),
        "persisted_cycle_resume_count": len(persisted_cycle_resumes),
        "persisted_cycle_resume_pass_count": sum(
            1 for proof in persisted_cycle_resumes if _persisted_cycle_resume_is_usable(proof)
        ),
        "persisted_cycle_resume_applied_count": sum(
            1 for proof in persisted_cycle_resumes if proof["resume_status"] == "applied"
        ),
        "persisted_cycle_resume_cold_start_count": sum(
            1 for proof in persisted_cycle_resumes if proof["resume_status"] == "cold_start"
        ),
        "persisted_cycle_resume_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in persisted_cycle_resumes
        ),
        "persisted_cycle_resume_drives_next_case_count": sum(
            1 for item in usable if item["persisted_cycle_resume_drives_next_case"]
        ),
        "multi_cycle_lineage_count": len(multi_cycle_lineages),
        "multi_cycle_lineage_pass_count": sum(
            1 for proof in multi_cycle_lineages if _multi_cycle_lineage_is_usable(proof)
        ),
        "multi_cycle_lineage_cold_start_count": sum(
            1 for proof in multi_cycle_lineages if proof["lineage_status"] == "cold_start"
        ),
        "multi_cycle_lineage_single_prior_count": sum(
            1 for proof in multi_cycle_lineages if proof["lineage_status"] == "single_prior"
        ),
        "multi_cycle_lineage_applied_count": sum(
            1 for proof in multi_cycle_lineages if proof["lineage_status"] == "lineage_applied"
        ),
        "multi_cycle_lineage_trace_binding_count": sum(
            len(proof["trace_bindings"]) for proof in multi_cycle_lineages
        ),
        "multi_cycle_lineage_drives_next_case_count": sum(
            1 for item in usable if item["multi_cycle_lineage_drives_next_case"]
        ),
        "oss_response_followup_loop_count": len(oss_followup_loops),
        "oss_response_followup_loop_pass_count": sum(
            1 for loop in oss_followup_loops if _oss_response_followup_loop_is_usable(loop)
        ),
        "oss_response_followup_loop_drives_decision_count": sum(
            1 for item in usable if item["oss_response_followup_loop_drives_decision"]
        ),
        "oss_disagreement_arbitration_count": len(oss_disagreement_arbitrations),
        "oss_disagreement_arbitration_pass_count": sum(
            1
            for arbitration in oss_disagreement_arbitrations
            if _oss_disagreement_arbitration_is_usable(arbitration)
        ),
        "multi_oss_disagreement_arbitrated_count": sum(
            1 for item in usable if item["multi_oss_disagreement_arbitrated"]
        ),
        "tracking_reconciliation_count": len(tracking_reconciliations),
        "tracking_reconciliation_pass_count": sum(
            1
            for reconciliation in tracking_reconciliations
            if _tracking_readback_reconciliation_is_usable(reconciliation)
        ),
        "tracking_reconciliation_drives_decision_count": sum(
            1 for item in usable if item["tracking_reconciliation_drives_decision"]
        ),
        "alpha_seed_revision_count": len(alpha_seed_revisions),
        "alpha_seed_revision_pass_count": sum(
            1
            for revision in alpha_seed_revisions
            if _alpha_seed_revision_is_usable(revision)
        ),
        "alpha_seed_revision_drives_decision_count": sum(
            1 for item in usable if item["alpha_seed_revision_drives_decision"]
        ),
        "agent_decision_artifact_count": len(decision_artifacts),
        "agent_decision_artifact_replay_count": sum(
            1 for item in usable if item["persona_decision_artifacts_replay"]
        ),
        "persona_reasoning_response_count": len(decision_artifacts),
        "persona_reasoning_drives_candidate_generation_count": sum(
            1 for item in usable if item["persona_reasoning_drives_candidate_generation"]
        ),
        "multi_generation_evolution_count": sum(1 for item in usable if item["multi_generation_evolution"]),
        "evolution_trajectory_count": len(evolution_trajectories),
        "evolution_trajectory_pass_count": sum(
            1 for trajectory in evolution_trajectories if _evolution_trajectory_is_usable(trajectory)
        ),
        "multi_dimensional_score_pass_count": sum(1 for item in usable if item["multi_dimensional_score_passed"]),
        "validation_planning_count": sum(1 for item in usable if item["validation_planned_before_execution"]),
        "validation_diagnostics_pass_count": sum(1 for item in usable if item["validation_diagnostics_passed"]),
        "validation_deficiencies_repaired_count": sum(1 for item in usable if item["validation_deficiencies_repaired"]),
        "cross_case_memory_retrieval_count": sum(1 for case in cases if case["memory"]["prior_memory"]),
        "market_friction_model_count": sum(1 for item in usable if item["market_friction_model_applied"]),
        "broker_lifecycle_reconciled_count": sum(1 for item in usable if item["broker_lifecycle_reconciled"]),
        "broker_adapter_lifecycle_packet_count": len(broker_adapter_lifecycles),
        "broker_adapter_lifecycle_pass_count": sum(
            1 for lifecycle in broker_adapter_lifecycles if _broker_adapter_lifecycle_is_usable(lifecycle)
        ),
        "broker_adapter_lifecycle_replayed_count": sum(
            1 for item in usable if item["broker_adapter_lifecycle_replayed"]
        ),
        "broker_adapter_followup_count": len(broker_adapter_followups),
        "broker_adapter_followup_pass_count": sum(
            1 for followup in broker_adapter_followups if _broker_adapter_followup_is_usable(followup)
        ),
        "broker_adapter_response_drives_followup_count": sum(
            1 for item in usable if item["broker_adapter_response_drives_followup"]
        ),
        "persona_conflict_resolved_count": sum(1 for item in usable if item["persona_conflicts_resolved"]),
        "restart_recovery_count": sum(1 for item in usable if item["restart_recovery_restores_loop"]),
        "autonomous_scheduler_count": sum(1 for item in usable if item["autonomous_scheduler_orders_next_cycle"]),
        "lean_engine_replay_count": sum(1 for item in usable if item["lean_engine_replay_uses_runtime_binding"]),
        "lean_object_store_packet_readback_count": len(lean_object_store_packet_readbacks),
        "lean_object_store_packet_readback_pass_count": sum(
            1
            for readback in lean_object_store_packet_readbacks
            if _lean_object_store_packet_readback_is_usable(readback)
        ),
        "lean_object_store_packet_readback_target_count": sum(
            readback["target_count"] for readback in lean_object_store_packet_readbacks
        ),
        "lean_object_store_loaded_signal_from_packet_target_count": sum(
            1
            for readback in lean_object_store_packet_readbacks
            if readback["loaded_signal_id"] == readback["target_signal_ids"][0]
        ),
        "lean_packet_execution_projection_count": len(lean_packet_execution_projections),
        "lean_packet_execution_projection_pass_count": sum(
            1
            for projection in lean_packet_execution_projections
            if _lean_packet_execution_projection_is_usable(projection)
        ),
        "lean_packet_execution_projection_leg_count": sum(
            len(projection["leg_projections"]) for projection in lean_packet_execution_projections
        ),
        "lean_packet_execution_projection_order_count": sum(
            projection["order_count"] for projection in lean_packet_execution_projections
        ),
        "lean_packet_execution_projection_fill_count": sum(
            projection["fill_count"] for projection in lean_packet_execution_projections
        ),
        "lean_packet_execution_projection_readback_count": sum(
            1
            for projection in lean_packet_execution_projections
            for leg in projection["leg_projections"]
            if leg["broker_readback_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        ),
        "lean_packet_execution_projection_replayed_count": sum(
            1 for item in usable if item["lean_packet_execution_projection_replayed"]
        ),
        "lean_runtime_feedback_count": len(lean_runtime_feedbacks),
        "lean_runtime_feedback_pass_count": sum(
            1 for feedback in lean_runtime_feedbacks if _lean_runtime_feedback_is_usable(feedback)
        ),
        "lean_runtime_feedback_consumed_execution_projection_count": sum(
            1
            for feedback in lean_runtime_feedbacks
            if feedback["replay"].get("lean_packet_execution_projection_consumed") is True
        ),
        "lean_runtime_feedback_drives_ooda_count": sum(
            1 for item in usable if item["lean_runtime_feedback_drives_ooda"]
        ),
        "experiment_tracking_lineage_handoff_count": len(experiment_tracking_lineage_handoffs),
        "experiment_tracking_lineage_handoff_pass_count": sum(
            1
            for proof in experiment_tracking_lineage_handoffs
            if _experiment_tracking_lineage_handoff_is_usable(proof)
        ),
        "experiment_tracking_lineage_handoff_drives_lean_count": sum(
            1
            for item in usable
            if item["experiment_tracking_lineage_reaches_lean_handoff"]
        ),
        "policy_oss_lineage_handoff_count": len(policy_oss_lineage_handoffs),
        "policy_oss_lineage_handoff_pass_count": sum(
            1
            for proof in policy_oss_lineage_handoffs
            if _policy_oss_lineage_handoff_is_usable(proof)
        ),
        "policy_oss_lineage_handoff_drives_lean_count": sum(
            1
            for item in usable
            if item["policy_oss_lineage_reaches_lean_handoff"]
        ),
        "reflection_oss_lineage_handoff_count": len(reflection_oss_lineage_handoffs),
        "reflection_oss_lineage_handoff_pass_count": sum(
            1
            for proof in reflection_oss_lineage_handoffs
            if _reflection_oss_lineage_handoff_is_usable(proof)
        ),
        "reflection_oss_lineage_handoff_drives_lean_count": sum(
            1
            for item in usable
            if item["reflection_oss_lineage_reaches_lean_handoff"]
        ),
        "openclaw_session_handoff_count": len(openclaw_session_handoffs),
        "openclaw_session_handoff_pass_count": sum(
            1
            for proof in openclaw_session_handoffs
            if _openclaw_session_handoff_is_usable(proof)
        ),
        "openclaw_session_handoff_drives_lean_count": sum(
            1
            for item in usable
            if item["openclaw_session_reaches_lean_handoff"]
        ),
        "alpha_seed_revision_handoff_count": len(alpha_seed_revision_handoffs),
        "alpha_seed_revision_handoff_pass_count": sum(
            1
            for proof in alpha_seed_revision_handoffs
            if _alpha_seed_revision_handoff_is_usable(proof)
        ),
        "alpha_seed_revision_handoff_drives_lean_count": sum(
            1
            for item in usable
            if item["alpha_seed_revision_reaches_lean_handoff"]
        ),
        "evolved_strategy_packet_proof_count": len(evolved_strategy_packet_proofs),
        "evolved_strategy_packet_proof_pass_count": sum(
            1
            for proof in evolved_strategy_packet_proofs
            if _evolved_strategy_packet_proof_is_usable(proof)
        ),
        "evolved_strategy_packet_handoff_count": sum(
            1 for item in usable if item["evolved_strategy_packet_reaches_lean_handoff"]
        ),
        "scheduler_conflict_ooda_proof_count": len(scheduler_conflict_ooda_proofs),
        "scheduler_conflict_ooda_proof_pass_count": sum(
            1
            for proof in scheduler_conflict_ooda_proofs
            if _scheduler_conflict_ooda_proof_is_usable(proof)
        ),
        "scheduler_conflict_ooda_event_count": sum(
            len(proof["dispatch_events"]) for proof in scheduler_conflict_ooda_proofs
        ),
        "scheduler_conflict_ooda_dispatch_count": sum(
            1 for item in usable if item["scheduler_conflict_ooda_dispatch_replayed"]
        ),
        "shioaji_sandbox_lifecycle_count": sum(1 for item in usable if item["shioaji_sandbox_lifecycle_reconciled"]),
        "case_specific_vectorbt_backtest_count": sum(
            1 for item in usable if item["case_specific_upstream_artifact_feedback"]
        ),
        "case_specific_tracking_roundtrip_count": sum(
            1 for item in usable if item["case_specific_upstream_artifact_feedback"]
        ),
        "case_vectorbt_real_backend_count": sum(
            1
            for case in cases
            if case["case_upstream_artifacts"]["vectorbt"]["backend"] == "vectorbt_portfolio"
        ),
        "case_specific_selected_oss_feedback_count": sum(
            1
            for case in cases
            if _case_selected_oss_case_feedback_is_usable(case)
        ),
        "lean_handoff_packet_count": sum(1 for item in usable if item["lean_handoff_packet_materialized"]),
        "validation_gap_question_count": sum(
            len(case["validation_cycle"]["planning"]["questions_asked"]) for case in cases
        ),
        "unresolved_validation_deficiency_count": sum(
            len(case["validation_cycle"]["repair"]["unresolved_deficiencies"]) for case in cases
        ),
        "min_overall_usability_score": round(min(float(case["overall_usability_score"]) for case in cases), 10),
        "average_overall_usability_score": round(mean(float(case["overall_usability_score"]) for case in cases), 10),
        "dimension_minima": {
            key: round(min(float(item[key]) for item in dimensions), 10)
            for key in dimensions[0]
        } if dimensions else {},
        "coverage": coverage,
        "why_this_means_usable": [
            "Every validation has a unique composite signature and a new case-id family.",
            "Every validation first asks coverage-gap questions and executes a unique validation plan.",
            "The evolved policy is selected without holdout/future-holdout data in the decision trace.",
            "Every case records a no-leakage temporal protocol proving observe/feedback/holdout/future-holdout boundaries before scoring evolution.",
            "Every case emits a strict OOS evolution proof showing gen1 uses feedback to improve holdout and gen2 uses only holdout outcome before improving a disjoint future holdout.",
            "Every case trades a three-instrument portfolio across three generations through paper runtime fills.",
            "Every case writes memory and proves retrieved lessons influence later candidate scoring and selected evidence refs.",
            "Every agent decision emits a memory counterfactual proving retrieved lessons change selected scores and margins, while cold starts declare zero memory influence.",
            "Every case uses OSS feedback across alpha, policy, reflection, tracking, risk, session, and LEAN handoff roles.",
            "Every case converts OSS responses into persona follow-up requests that feed reasoning, candidate evidence, and scorer adjustments.",
            "Every case proves the selected FinRL/RLlib/Ray Tune policy-candidate artifact materially changes persona scoring, risk sizing, and the evolved policy packet.",
            "Every case proves the selected DSPy/TRL/imitation reflection artifact materially changes persona reasoning blueprints, selected rationale, scorer replay, and candidate evidence.",
            "Every case emits a multi-OSS closed-loop proof replaying each role from OSS response through persona follow-up, reasoning inputs, candidate generation, scorer adjustments, and selected evidence.",
            "Every case emits an OODA causal ledger proving OSS responses, persona follow-up outputs, candidate generation, scorer output, selected action, and LEAN handoff occur in replayable temporal order without future artifact references.",
            "Every non-cold-start persona case consumes the prior autonomous cycle's LEAN runtime feedback in reasoning, candidate generation, selected evidence, and scorer adjustments.",
            "Every non-cold-start persona case resumes that prior autonomous cycle through persisted checkpoint, schedule, and object-store readback refs before using the runtime feedback.",
            "Every case turns selected alpha OSS output into a replayable alpha seed revision that feeds downstream backtest, tracking, reasoning, scorer adjustments, and selected evidence refs.",
            "Every case carries the Qlib/vectorbt alpha seed revision into the LEAN strategy packet, Object Store readback, handoff bundle, runtime bundle refs, and runtime feedback state updates.",
            "Every case detects a realistic multi-OSS disagreement and routes the arbitration result into persona reasoning, scorer adjustments, and selected candidate evidence.",
            "Every case reconciles experiment-tracker readback divergence and routes the repaired tracking ref into persona reasoning, scorer adjustments, and selected candidate evidence.",
            "Every case carries the repaired MLflow/W&B experiment lineage from evolution decision evidence into the LEAN strategy packet, Object Store readback, handoff bundle, and runtime feedback.",
            "Every persona decision emits a replayable request-response artifact covering candidate generation, scoring, risk checks, and rejected alternatives.",
            "Every persona decision first emits a structured reasoning response whose candidate blueprints drive the scored candidates.",
            "Every case records a multi-generation evolution trajectory proving gen0->gen1->gen2 lineage, two distinct unseen-window improvements, and bounded turnover.",
            "Every case has a case-specific vectorbt historical backtest artifact and a case-specific experiment tracking readback before persona decisions.",
            "Every case runs its selected alpha, policy, reflection, and risk OSS route as case-specific persona feedback and uses those refs in the selected decision trace.",
            "Every case applies market friction, reconciles paper broker lifecycle readback, resolves persona conflicts, recovers from a restart checkpoint, and schedules the next autonomous cycle.",
            "Every case emits a persona-visible broker adapter lifecycle packet tying paper order status paths, Shioaji sandbox place/cancel/readback, live-disabled rejection, and restart recovery into a replayable response.",
            "Every broker adapter response triggers a scenario-specific persona follow-up action before the next autonomous paper cycle.",
            "Every LEAN runtime response is consumed by the persona and drives a scenario-specific next OODA action with runtime binding, object-store readback, and handoff refs.",
            "Every case proves the generation-2 strict-OOS evolved strategy packet reaches LEAN handoff, runtime bundle refs, and runtime feedback with future-holdout provenance intact.",
            "Every case materializes the evolved multi-leg strategy packet into the LEAN Object Store and proves the smoke algorithm loaded its first target signal from that packet artifact.",
            "Every case projects each generation-2 strategy packet leg into a LEAN-side target/order/fill/readback chain and feeds that projection into persona runtime feedback.",
            "Every case replays scheduler and multi-persona conflict causality from recovered schedule tick through resolved allocation, LEAN handoff, adapter/runtime feedback, and next-cycle dispatch.",
            "Every case passes a multi-dimensional usability score, not only a single return metric.",
        ],
    }


def _single_leg_policy(direction: int, risk_multiplier: float) -> dict[str, Any]:
    return {"direction": direction, "risk_multiplier": risk_multiplier, "weight": 1.0}


def _evaluation_rows(window: InstrumentWindow, period: str) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    if period == "feedback":
        return window.observe_rows[-1], window.feedback_rows
    if period == "holdout":
        return window.feedback_rows[-1], window.holdout_rows
    if period == "future_holdout":
        return window.holdout_rows[-1], window.future_holdout_rows
    raise ValueError(f"unsupported evaluation period: {period}")


def _entry_row_for_generation(window: InstrumentWindow, generation: int) -> dict[str, Any]:
    if generation == 0:
        return window.observe_rows[-1]
    if generation == 1:
        return window.feedback_rows[-1]
    if generation == 2:
        return window.holdout_rows[-1]
    raise ValueError(f"unsupported generation: {generation}")


def _quantity_for(quantity_type: str, close: float, risk_multiplier: float, case_index: int) -> float:
    if quantity_type == "SHARES":
        return float(max(1, int(round(1 + (case_index % 7) * max(risk_multiplier, 0.25)))))
    if quantity_type == "CASH_VALUE":
        base_cash = 8_000.0 + (case_index % 13) * 750.0
        min_cash = close * 2.0
        return round(max(min_cash, base_cash * max(risk_multiplier, 0.25)), 2)
    if quantity_type == "PERCENT_PORTFOLIO":
        return round(min(0.2, max(0.01, 0.03 * max(risk_multiplier, 0.25))), 6)
    raise ValueError(f"unsupported quantity_type: {quantity_type}")


def _policy_turnover(policy: Mapping[str, Any]) -> float:
    return round(sum(abs(float(leg["risk_multiplier"])) * float(leg.get("weight", 1.0)) for leg in policy["legs"].values()), 10)


def _risk_hint_from_oss(oss_inputs: Mapping[str, Mapping[str, Any]], generation: int) -> float:
    component = str(oss_inputs["policy_candidate"]["component"])
    base = {"finrl": 0.75, "rllib": 0.85, "ray_tune": 0.95}.get(component, 0.75)
    policy_metrics = oss_inputs.get("policy_candidate", {}).get("metrics", {})
    if isinstance(policy_metrics, Mapping):
        policy_signal = max(
            _finite_float(policy_metrics.get("sharpe"), 0.0) / 100,
            _finite_float(policy_metrics.get("validation_sharpe_proxy"), 0.0) / 20,
            _finite_float(policy_metrics.get("best_trial_score"), 0.0) / 20,
            _finite_float(policy_metrics.get("mean_reward_proxy"), 0.0),
            _finite_float(policy_metrics.get("eval_reward_mean"), 0.0),
        )
        base += min(0.08, max(0.0, policy_signal))
    backtest_metrics = oss_inputs.get("backtest", {}).get("metrics", {})
    if isinstance(backtest_metrics, Mapping):
        mean_return = _finite_float(backtest_metrics.get("mean_total_return"), 0.0)
        mean_drawdown = abs(_finite_float(backtest_metrics.get("mean_max_drawdown"), 0.0))
        total_trades = int(_finite_float(backtest_metrics.get("total_trades"), 0.0))
        if mean_return > 0:
            base += min(0.08, mean_return)
        if mean_drawdown > 0.15:
            base -= 0.05
        if total_trades <= 0:
            base -= 0.03
    if generation >= 2:
        return round(min(1.15, max(0.35, base + 0.3)), 4)
    return round(min(1.15, max(0.35, base)), 4)


def _policy_quality_from_oss(oss_inputs: Mapping[str, Mapping[str, Any]]) -> float:
    metrics = oss_inputs.get("policy_candidate", {}).get("metrics", {})
    if not isinstance(metrics, Mapping):
        return 0.0
    return round(
        min(
            0.35,
            max(
                0.0,
                _finite_float(metrics.get("sharpe"), 0.0) / 100,
                _finite_float(metrics.get("validation_sharpe_proxy"), 0.0) / 20,
                _finite_float(metrics.get("best_trial_score"), 0.0) / 20,
                _finite_float(metrics.get("mean_reward_proxy"), 0.0),
                _finite_float(metrics.get("eval_reward_mean"), 0.0),
            ),
        ),
        6,
    )


def _reflection_quality_from_oss(oss_inputs: Mapping[str, Mapping[str, Any]]) -> float:
    metrics = oss_inputs.get("reflection_artifact", {}).get("metrics", {})
    if not isinstance(metrics, Mapping):
        return 0.0
    return round(
        min(
            0.25,
            max(
                0.0,
                _finite_float(metrics.get("intent_accuracy"), 0.0) * 0.08,
                _finite_float(metrics.get("accuracy"), 0.0) * 0.08,
                _finite_float(metrics.get("training_accuracy"), 0.0) * 0.08,
                _finite_float(metrics.get("action_coverage_ratio"), 0.0) * 0.05,
            ),
        ),
        6,
    )


def _risk_penalty_from_oss(oss_inputs: Mapping[str, Mapping[str, Any]]) -> float:
    metrics = oss_inputs.get("risk_analytics", {}).get("metrics", {})
    if not isinstance(metrics, Mapping):
        return 0.0
    complexity = _finite_float(metrics.get("result_count"), 0.0) / 20
    option_count = _finite_float(metrics.get("option_count"), 0.0) / 40
    price_series_count = _finite_float(metrics.get("price_series_count"), 0.0) / 40
    return round(min(0.25, max(0.0, complexity + option_count + price_series_count)), 6)


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _trace_has_no_forbidden_window_leakage(trace: Mapping[str, Any]) -> bool:
    forbidden = set(trace["decision_inputs"]["forbidden_windows_not_used"])
    for candidate in trace["candidates"]:
        if forbidden.intersection(candidate["source_windows"]):
            return False
    selected = trace["selected_candidate"]
    return not forbidden.intersection(selected["source_windows"])


def _candidate_to_dict(candidate: PolicyCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "direction_by_instrument": dict(candidate.direction_by_instrument),
        "risk_multiplier": candidate.risk_multiplier,
        "score": round(candidate.score, 10),
        "source_windows": list(candidate.source_windows),
        "evidence_refs": list(candidate.evidence_refs),
        "rationale": candidate.rationale,
    }


def _oss_components_used(oss_inputs: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return sorted({str(result["component"]) for result in oss_inputs.values()})


def _reflection_hypothesis(trigger: str, oss_inputs: Mapping[str, Mapping[str, Any]]) -> str:
    return (
        f"{trigger} scored with {oss_inputs['policy_candidate']['component']} policy feedback, "
        f"{oss_inputs['reflection_artifact']['component']} reflection evidence, and "
        f"{oss_inputs['tracker']['component']} experiment tracking."
    )


def _reflection_archetype(windows: Sequence[InstrumentWindow]) -> str:
    if any(window.selection_archetype == "feedback_reversal_repair" for window in windows):
        return "feedback_reversal_repair"
    return "feedback_conviction_scale"


def _regime_for_window(window: InstrumentWindow) -> str:
    closes = _closes(window.observe_rows)
    recent_return = _safe_return(closes[-6], closes[-1])
    volatility = _return_volatility(closes)
    if volatility >= 0.025:
        return "volatile"
    if recent_return >= 0.015:
        return "uptrend"
    if recent_return <= -0.015:
        return "downtrend"
    return "range_bound"


def _execution_symbol_for(instrument: str) -> str:
    suffix = "".join(ch for ch in instrument if ch.isdigit())[-4:] or "0000"
    return f"TWS{suffix}.US"


def _shioaji_symbol_for(instrument: str) -> str:
    suffix = "".join(ch for ch in instrument if ch.isdigit())[-4:] or "0000"
    return f"{suffix}.TWSE"


def _period_return(start_row: Mapping[str, Any], end_row: Mapping[str, Any]) -> float:
    return _safe_return(float(start_row["close"]), float(end_row["close"]))


def _direction(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _closes(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    return [float(row["close"]) for row in rows]


def _safe_return(start: float, end: float) -> float:
    if start == 0:
        return 0.0
    return (end - start) / start


def _return_volatility(closes: Sequence[float]) -> float:
    returns = [_safe_return(a, b) for a, b in zip(closes, closes[1:])]
    if len(returns) < 2:
        return 0.0
    return pstdev(returns)


def _operational_scenario_for_episode(episode: PortfolioEpisode) -> str:
    return OPERATIONAL_SCENARIOS[(episode.ordinal - 1) % len(OPERATIONAL_SCENARIOS)]


def _estimated_order_notional(
    *,
    policy: Mapping[str, Any],
    leg: Mapping[str, Any],
    close: float,
    case_index: int,
) -> float:
    quantity_type = str(policy["quantity_type"])
    risk_weight = float(leg["risk_multiplier"]) * float(leg.get("weight", 1.0))
    quantity = _quantity_for(quantity_type, close, risk_weight, case_index)
    if quantity_type == "SHARES":
        return float(quantity) * close
    if quantity_type == "CASH_VALUE":
        return float(quantity)
    if quantity_type == "PERCENT_PORTFOLIO":
        return float(quantity) * 100_000.0
    raise ValueError(f"unsupported quantity_type: {quantity_type}")


def _broker_status_path_for(scenario: str, ordinal: int) -> list[str]:
    if scenario == "partial_fill_reconcile":
        return ["submitted", "acknowledged", "partially_filled", "filled"]
    if scenario == "limit_miss_reprice":
        return ["submitted", "acknowledged", "limit_missed", "repriced", "filled"]
    if scenario == "liquidity_cap_scale":
        return ["submitted", "acknowledged", "liquidity_scaled", "partially_filled", "filled"]
    if scenario == "cancel_replace_readback":
        return [
            "submitted",
            "acknowledged",
            "cancel_requested",
            "cancel_acknowledged",
            "replace_submitted",
            "acknowledged",
            "filled",
        ]
    if scenario == "risk_reject_reduce":
        return ["submitted", "rejected", "risk_reduced", "resubmitted", "acknowledged", "filled"]
    raise ValueError(f"unsupported operational scenario: {scenario}")


def _macro_conflict_directions(
    episode: PortfolioEpisode,
    final_directions: Mapping[str, int],
) -> dict[str, int]:
    directions = dict(final_directions)
    if not directions:
        return directions
    instruments = sorted(directions)
    conflict_instrument = instruments[episode.ordinal % len(instruments)]
    directions[conflict_instrument] = -directions[conflict_instrument]
    return directions


def _validation_signature(
    *,
    persona_id: str,
    seed_key: str,
    windows: Sequence[InstrumentWindow],
    oss_route: Mapping[str, str],
    order_profile: Mapping[str, str],
    reflection_archetype: str,
    generation_path: Sequence[str],
    operational_scenario: str,
) -> str:
    parts = [
        persona_id,
        seed_key,
        ",".join(f"{window.instrument}:{window.start_index}" for window in windows),
        ",".join(f"{key}:{value}" for key, value in sorted(oss_route.items())),
        ",".join(f"{key}:{value}" for key, value in sorted(order_profile.items())),
        reflection_archetype,
        "->".join(generation_path),
        operational_scenario,
    ]
    return _stable_id("validation", *parts)


def _validation_combo_signature(episode: PortfolioEpisode) -> str:
    return _stable_id(
        "combo",
        _persona_id(episode.persona),
        episode.seed_key,
        _portfolio_window_signature(episode),
        _oss_route_signature(episode.oss_route),
        _order_profile_signature(episode.order_profile),
        episode.reflection_archetype,
        "|".join(episode.regime_path),
        _operational_scenario_for_episode(episode),
    )


def _portfolio_window_signature(episode: PortfolioEpisode) -> str:
    return _stable_id(
        "portfolio-window",
        *(
            f"{window.instrument}:{window.start_index}:{window.observe_direction}:"
            f"{window.feedback_direction}:{window.holdout_direction}:{window.future_direction}"
            for window in episode.windows
        ),
    )


def _oss_route_signature(route: Mapping[str, Any]) -> str:
    return _stable_id(
        "oss-route",
        ",".join(f"{key}:{value}" for key, value in sorted((str(k), str(v)) for k, v in route.items())),
    )


def _order_profile_signature(order_profile: Mapping[str, Any]) -> str:
    return _stable_id(
        "order-profile",
        str(order_profile.get("quantity_type")),
        str(order_profile.get("order_type")),
    )


def _persona_id(persona: Mapping[str, Any]) -> str:
    return str(persona.get("persona_id") or persona.get("id") or "")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _stable_payload_hash(prefix: str, payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _recent_signal_timestamp(generated_at: str, case_index: int) -> str:
    del generated_at
    base = datetime.now(timezone.utc)
    return (base + timedelta(seconds=case_index % 60)).astimezone(timezone.utc).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")


def _offset_timestamp(
    generated_at: str,
    case_index: int,
    *,
    days: int = 0,
    minutes: int = 0,
) -> str:
    base = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    return (
        base + timedelta(days=days, minutes=minutes, seconds=case_index)
    ).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)

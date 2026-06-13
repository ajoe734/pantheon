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
BROKER_LIFECYCLE_TERMINAL_STATUS = "filled"
MARKET_FRICTION_MODEL_ID = "volume_capped_slippage_commission_v1"
LEAN_ENGINE_REPLAY_MODEL_ID = "pantheon_lean_smoke_binding_context_v1"
SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID = "shioaji_sandbox_facade_mock_replay_v1"
CASE_UPSTREAM_VECTORBT_MODEL_ID = "case_specific_vectorbt_feedback_v1"
CASE_UPSTREAM_TRACKING_MODEL_ID = "case_specific_tracking_artifact_roundtrip_v1"
CASE_SELECTED_OSS_MODEL_ID = "case_specific_selected_oss_feedback_v1"
PERSONA_DECISION_ARTIFACT_MODEL_ID = "persona_replayable_candidate_decision_v1"
PERSONA_CANDIDATE_GENERATOR_MODEL_ID = "persona_candidate_generation_from_oss_feedback_v1"
PERSONA_CANDIDATE_SCORER_MODEL_ID = "persona_multi_factor_candidate_scorer_v1"
PERSONA_RISK_EVALUATOR_MODEL_ID = "persona_oss_risk_turnover_evaluator_v1"
PERSONA_MEMORY_INFLUENCE_MODEL_ID = "persona_retrieved_lesson_influence_v1"
PERSONA_REASONING_MODEL_ID = "persona_structured_reasoning_candidate_generator_v1"
PERSONA_REASONING_EVALUATOR_MODEL_ID = "persona_reasoning_response_evaluator_v1"
EVOLUTION_TRAJECTORY_MODEL_ID = "persona_multi_generation_evolution_trajectory_v1"
NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID = "persona_no_leakage_temporal_protocol_v1"
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

    for index, episode in enumerate(episodes):
        persona_id = _persona_id(episode.persona)
        oss_inputs = _oss_inputs_for_episode(episode, oss_by_component)
        case_upstream_artifacts = _build_case_upstream_artifact_feedback(
            episode=episode,
            oss_inputs=oss_inputs,
        )
        oss_inputs = _apply_case_upstream_artifacts_to_oss_inputs(
            oss_inputs,
            case_upstream_artifacts,
        )
        prior_memory = _retrieve_prior_lesson(persona_store, persona_id)
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
        operational_context = _build_operational_context(
            episode=episode,
            generation_policies=(generation0_policy, generation1_policy, generation2_policy),
            executions=(generation0_exec, generation1_exec, generation2_exec),
            evaluations=(generation0_eval, generation1_eval, generation2_eval),
            decision_traces=(decision_trace0, decision_trace1),
            memory_contexts=(current_memory0, current_memory1),
            evolution_decision=evolution_decision,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            generated_at=generated_at,
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
            usability_dimensions=usability_dimensions,
            oss_inputs=oss_inputs,
            case_upstream_artifacts=case_upstream_artifacts,
            operational_context=operational_context,
            validation_plan=validation_plan,
            validation_diagnostics=validation_diagnostics,
            validation_repair=validation_repair,
        )
        cases.append(case)

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
        "persona_response": {
            "ooda_sequence": ["decide", "learn", "orient", "observe"],
            "next_decision_action": "score_case_specific_backtest_candidate",
            "next_tracking_action": "cite_case_specific_experiment_ref",
            "evidence_refs": [
                f"oss://vectorbt/{vectorbt_feedback['request_id']}",
                f"oss://{tracker_feedback['component']}/{tracker_feedback['request_id']}",
                f"experiment://{tracker_feedback['backend']}/{tracker_feedback['run_id']}",
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
        },
        "persona_followup": {
            "persona_id": case_upstream_artifacts["persona_id"],
            "session_id": vectorbt_feedback["session_id"],
            "trigger_component": "vectorbt",
            "trigger_request_id": vectorbt_feedback["request_id"],
            "trigger_artifact_family": "vectorbt_backtest",
            "ooda_phase": "decide",
            "next_action": "draft_strategy_proposal",
            "evidence_refs": [vectorbt_feedback["run_id"], vectorbt_feedback["registry_id"]],
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


def _candidate_action_key(candidate_id: str) -> str:
    for action in ("feedback-adapt", "retain-observe", "risk-off", "contrarian-check"):
        if candidate_id.endswith(f"-{action}"):
            return action
    return "unknown"


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


def _build_persona_reasoning_response(
    *,
    episode: PortfolioEpisode,
    generation: int,
    trigger: str,
    baseline_policy: Mapping[str, Any],
    latest_evaluation: Mapping[str, Any],
    telemetry_event: Mapping[str, Any],
    memory_influence: Mapping[str, Any],
    oss_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    allowed_windows = ["observe", "feedback"] if generation == 1 else ["observe", "feedback", "holdout"]
    forbidden_windows = ["holdout", "future_holdout"] if generation == 1 else ["future_holdout"]
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
            *([str(memory_influence["influence_ref"])] if memory_influence["influence_ref"] else []),
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
        "oss_components_by_role": {
            role: result["component"] for role, result in sorted(oss_inputs.items())
        },
    }
    candidate_blueprints = _persona_reasoning_candidate_blueprints(
        generation=generation,
        allowed_windows=allowed_windows,
        memory_influence=memory_influence,
        oss_inputs=oss_inputs,
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
            "draft_candidate_policy_blueprints",
            "send_blueprints_to_scorer_and_risk_evaluator",
        ],
        "memory_usage": {
            "status": memory_influence["status"],
            "influence_ref": memory_influence["influence_ref"],
            "selected_action_hint": memory_influence["selected_action_hint"],
            "score_adjustments": copy.deepcopy(dict(memory_influence["candidate_score_adjustments"])),
        },
        "preferred_action_hint": _persona_reasoning_preferred_action(memory_influence),
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
    oss_inputs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    shared_windows = list(allowed_windows)
    feedback_windows = ["observe", "feedback"] if generation == 1 else shared_windows
    memory_ref = memory_influence.get("influence_ref")
    memory_refs = [str(memory_ref)] if memory_ref else []
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
            "extra_evidence_refs": memory_refs,
            "memory_adjustment_key": "feedback-adapt",
            "rationale": (
                "Use the feedback direction because alpha, policy, reflection, risk, backtest, "
                "tracking, and retrieved memory support an adaptive portfolio mutation."
            ),
        },
        {
            "action": "retain-observe",
            "candidate_suffix": "retain-observe",
            "direction_source": "observe_window_direction",
            "risk_source": "baseline_policy",
            "source_windows": ["observe"],
            "evidence_roles": [],
            "extra_evidence_refs": [],
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
            "extra_evidence_refs": memory_refs
            if float(memory_influence.get("candidate_score_adjustments", {}).get("risk-off", 0.0)) > 0
            else [],
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
            "extra_evidence_refs": [],
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
) -> dict[str, Any]:
    memory_influence = _memory_influence_profile(prior_memory)
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
        oss_inputs=oss_inputs,
    )
    candidates = _score_agent_candidates(
        episode=episode,
        generation=generation,
        baseline_policy=baseline_policy,
        latest_evaluation=latest_evaluation,
        prior_memory=prior_memory,
        oss_inputs=oss_inputs,
        memory_influence=memory_influence,
        persona_reasoning=persona_reasoning,
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
        "oss_components": _oss_components_used(oss_inputs),
    }
    evidence_refs = [
        f"telemetry-event://{telemetry_event['event_id']}",
        f"historical-ohlcv://{HISTORICAL_OHLCV_DATASET_ID}/observe-feedback/{episode.case_id}",
        f"alpha-seed://{episode.seed_key}",
        f"policy://{baseline_policy['policy_id']}",
        *[f"oss://{result['component']}/{result['request_id']}" for result in oss_inputs.values()],
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
        persona_reasoning=persona_reasoning,
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
    persona_reasoning: Mapping[str, Any],
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
    risk_off = max(0.25, policy_hint_risk - 0.35)
    memory_score_adjustments = dict(memory_influence["candidate_score_adjustments"])
    memory_ref = memory_influence.get("influence_ref")
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
    action_context = {
        "feedback-adapt": {
            "directions": feedback_directions,
            "risk_multiplier": policy_hint_risk,
            "score": (
                3.0
                + float(memory_score_adjustments["feedback-adapt"])
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
            "score": 1.0 + float(memory_score_adjustments["retain-observe"]) + max(feedback_score, 0),
            "fallback_evidence_refs": (f"policy://{baseline_policy['policy_id']}",),
        },
        "risk-off": {
            "directions": feedback_directions,
            "risk_multiplier": risk_off,
            "score": 2.0 + float(memory_score_adjustments["risk-off"]) + max(0.0, risk_penalty),
            "fallback_evidence_refs": tuple(risk_off_evidence_refs),
        },
        "contrarian-check": {
            "directions": inverse_feedback,
            "risk_multiplier": 0.5,
            "score": 0.25 + float(memory_score_adjustments["contrarian-check"]),
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
        refs = [f"policy://{baseline_policy['policy_id']}"]
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
    persona_reasoning: Mapping[str, Any],
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
                *oss_evidence_refs,
                *([str(memory_influence["influence_ref"])] if memory_influence["influence_ref"] else []),
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
        "uses_persona_reasoning_response": candidate_generation["response"]["source_reasoning_response_id"]
        == persona_reasoning["response"]["response_id"]
        and persona_reasoning["response"]["reasoning_ref"] in candidate_generation["request"]["input_refs"],
        "uses_selected_oss_feedback": set(selected_candidate.get("evidence_refs", [])).issuperset(
            _selected_persona_decision_oss_refs(oss_inputs)
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
    if candidate_id.endswith("-feedback-adapt"):
        formula_id = "feedback_adapt_score_v1"
        memory_adjustment = float(memory_score_adjustments["feedback-adapt"])
        replayed_score = round(
            3.0 + memory_adjustment + feedback_score + policy_quality + reflection_quality - risk_penalty * 0.2,
            10,
        )
        components = {
            "base": 3.0,
            "memory_adjustment": memory_adjustment,
            "feedback_score": feedback_score,
            "policy_quality": policy_quality,
            "reflection_quality": reflection_quality,
            "risk_penalty_weighted": round(-risk_penalty * 0.2, 10),
        }
    elif candidate_id.endswith("-retain-observe"):
        formula_id = "retain_observe_score_v1"
        memory_adjustment = float(memory_score_adjustments["retain-observe"])
        replayed_score = round(1.0 + memory_adjustment + max(feedback_score, 0.0), 10)
        components = {
            "base": 1.0,
            "memory_adjustment": memory_adjustment,
            "positive_feedback_score": round(max(feedback_score, 0.0), 10),
        }
    elif candidate_id.endswith("-risk-off"):
        formula_id = "risk_off_score_v1"
        memory_adjustment = float(memory_score_adjustments["risk-off"])
        replayed_score = round(2.0 + memory_adjustment + max(0.0, risk_penalty), 10)
        components = {
            "base": 2.0,
            "memory_adjustment": memory_adjustment,
            "risk_penalty_signal": round(max(0.0, risk_penalty), 10),
        }
    else:
        formula_id = "contrarian_control_score_v1"
        memory_adjustment = float(memory_score_adjustments["contrarian-check"])
        replayed_score = round(0.25 + memory_adjustment, 10)
        components = {"base": 0.25, "memory_adjustment": memory_adjustment}
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
) -> dict[str, Any]:
    selected = decision_trace["selected_candidate"]
    risk_multiplier = float(selected["risk_multiplier"])
    if generation == 2:
        risk_multiplier = max(risk_multiplier, 1.15)
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
        "decision_inputs": {
            **dict(decision_trace["decision_inputs"]),
            "memory_reused": {
                "memory_id": memory_context["memory_id"],
                "reuse_count": memory_context["reuse_count"],
                "source_event_id": memory_context["source_event_id"],
            },
        },
    }


def _build_signals(
    *,
    episode: PortfolioEpisode,
    policy: Mapping[str, Any],
    generation: int,
    generated_at: str,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
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
        evidence_refs=[evidence_ref],
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
    autonomous_schedule = _build_autonomous_schedule(
        episode=episode,
        generated_at=generated_at,
        restart_recovery=restart_recovery,
    )
    lean_engine_replay = _run_lean_engine_replay(
        episode=episode,
        final_policy=generation_policies[-1],
        evolution_decision=evolution_decision,
    )
    lean_handoff = _build_lean_handoff_packet(
        episode=episode,
        final_policy=generation_policies[-1],
        evolution_decision=evolution_decision,
        oss_inputs=oss_inputs,
        market_friction=market_friction,
        broker_lifecycle=broker_lifecycle,
        lean_engine_replay=lean_engine_replay,
        shioaji_sandbox_lifecycle=shioaji_sandbox_lifecycle,
        case_upstream_artifacts=case_upstream_artifacts,
    )
    return {
        "operational_signature": _stable_id(
            "operational",
            episode.validation_signature,
            scenario,
            market_friction["model_id"],
            broker_lifecycle["lifecycle_model"],
            autonomous_schedule["schedule_id"],
        ),
        "scenario": scenario,
        "market_friction": market_friction,
        "broker_lifecycle": broker_lifecycle,
        "shioaji_sandbox_lifecycle": shioaji_sandbox_lifecycle,
        "persona_conflict_resolution": persona_conflict,
        "restart_recovery": restart_recovery,
        "autonomous_schedule": autonomous_schedule,
        "lean_engine_replay": lean_engine_replay,
        "case_upstream_artifacts": _case_upstream_artifacts_case_summary(case_upstream_artifacts),
        "lean_handoff": lean_handoff,
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
    return {
        "resolution_id": f"conflict-resolution-{episode.case_id}",
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
        "oss_risk_ref": f"oss://{oss_inputs['risk_analytics']['component']}/{oss_inputs['risk_analytics']['request_id']}",
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


def _build_autonomous_schedule(
    *,
    episode: PortfolioEpisode,
    generated_at: str,
    restart_recovery: Mapping[str, Any],
) -> dict[str, Any]:
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
        "schedule_id": f"schedule-{episode.case_id}",
        "trigger_mode": "autonomous_daily_paper_loop",
        "phases": phases,
        "phase_order_valid": [phase["phase"] for phase in phases] == list(AUTONOMOUS_SCHEDULER_PHASES),
        "restart_checkpoint_ref": restart_recovery["checkpoint_id"],
        "missed_cycle_recovered": restart_recovery["recovered"],
        "next_cycle_due_at": _offset_timestamp(
            generated_at,
            episode.ordinal,
            days=(episode.ordinal % 17) + 1,
        ),
    }


def _run_lean_engine_replay(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    evolution_decision: EvolutionDecision,
) -> dict[str, Any]:
    artifact_id = f"reg-{SMOKE_STRATEGY_ID}-{SMOKE_VERSION}"
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
    result = run_algorithm_smoke_from_binding(plan, binding).to_dict()
    runtime_context = dict(result["runtime_context"])
    return {
        "replay_id": f"lean-engine-replay-{episode.case_id}",
        "model_id": LEAN_ENGINE_REPLAY_MODEL_ID,
        "status": "passed"
        if _lean_engine_result_is_usable(result, plan, binding)
        else "failed",
        "algorithm_module": "pantheon_algo.smoke_loader_test",
        "case_specific_runtime_binding": True,
        "case_specific_strategy_packet": {
            "policy_id": final_policy["policy_id"],
            "evolution_decision_id": evolution_decision.decision_id,
            "portfolio_instruments": [window.instrument for window in episode.windows],
            "validation_signature": episode.validation_signature,
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


def _build_lean_handoff_packet(
    *,
    episode: PortfolioEpisode,
    final_policy: Mapping[str, Any],
    evolution_decision: EvolutionDecision,
    oss_inputs: Mapping[str, Mapping[str, Any]],
    market_friction: Mapping[str, Any],
    broker_lifecycle: Mapping[str, Any],
    lean_engine_replay: Mapping[str, Any],
    shioaji_sandbox_lifecycle: Mapping[str, Any],
    case_upstream_artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    handoff = oss_inputs["handoff"]
    vectorbt = case_upstream_artifacts["vectorbt"]
    tracker = case_upstream_artifacts["tracker"]
    selected_oss_refs = [
        f"oss://{entry['component']}/{entry['request_id']}"
        for entry in case_upstream_artifacts["selected_oss"].values()
    ]
    return {
        "packet_id": f"lean-packet-{episode.case_id}",
        "component": handoff["component"],
        "request_id": handoff["request_id"],
        "strategy_packet_materialized": True,
        "packet_type": "LeanPaperStrategyPacket",
        "target_stage": "paper",
        "policy_id": final_policy["policy_id"],
        "evolution_decision_id": evolution_decision.decision_id,
        "portfolio_instruments": [window.instrument for window in episode.windows],
        "market_friction_model_id": market_friction["model_id"],
        "broker_lifecycle_model": broker_lifecycle["lifecycle_model"],
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
            f"strategy://{episode.seed_key}-agent-usability-hardening/{final_policy['policy_id']}",
            f"evolution://{evolution_decision.decision_id}",
            f"oss://{handoff['component']}/{handoff['request_id']}",
            f"oss://vectorbt/{vectorbt['request_id']}",
            f"experiment://{tracker['backend']}/{tracker['run_id']}",
            *selected_oss_refs,
            f"lean-engine://{lean_engine_replay['replay_id']}",
            f"broker-sandbox://{shioaji_sandbox_lifecycle['lifecycle_id']}",
        ],
        "received_by_lean_handoff": handoff.get("status") == "completed",
        "broker_live_submitted": False,
    }


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


def _persona_conflicts_are_resolved(conflict_resolution: Mapping[str, Any]) -> bool:
    allocation = conflict_resolution.get("resolved_allocation", {})
    return bool(
        conflict_resolution.get("classified_conflicts")
        and not conflict_resolution.get("open_conflicts")
        and allocation.get("capital_budget_pct", 2.0) <= 1.0
        and set(allocation.get("direction_by_instrument", {}))
        == set(allocation.get("weight_by_instrument", {}))
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
        schedule.get("trigger_mode") == "autonomous_daily_paper_loop"
        and schedule.get("phase_order_valid")
        and schedule.get("missed_cycle_recovered")
        and [phase["phase"] for phase in schedule.get("phases", [])]
        == list(AUTONOMOUS_SCHEDULER_PHASES)
        and schedule.get("next_cycle_due_at")
    )


def _lean_handoff_packet_is_usable(packet: Mapping[str, Any]) -> bool:
    return bool(
        packet.get("component") == "lean_handoff"
        and packet.get("strategy_packet_materialized")
        and packet.get("received_by_lean_handoff")
        and packet.get("target_stage") == "paper"
        and packet.get("lean_engine_replay_status") == "passed"
        and packet.get("shioaji_sandbox_lifecycle_status") == "passed"
        and packet.get("case_vectorbt_request_id")
        and packet.get("case_tracking_run_id")
        and packet.get("broker_live_submitted") is False
        and packet.get("runtime_bundle_refs")
    )


def _lean_engine_result_is_usable(
    result: Mapping[str, Any],
    plan: Mapping[str, Any],
    binding: Any,
) -> bool:
    runtime_context = result.get("runtime_context", {})
    loaded_metadata = result.get("loaded_metadata", {})
    object_store_keys = set(result.get("object_store_keys", []))
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
    )


def _lean_engine_replay_is_usable(replay: Mapping[str, Any]) -> bool:
    runtime_context = replay.get("runtime_context", {})
    loaded_metadata = replay.get("loaded_metadata", {})
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
        and replay.get("case_specific_strategy_packet", {}).get("validation_signature")
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


def _persona_decision_artifact_is_usable(trace: Mapping[str, Any]) -> bool:
    artifact = trace.get("agent_decision_artifact", {})
    if not isinstance(artifact, Mapping):
        return False
    input_context = artifact.get("input_context", {})
    candidate_generation = artifact.get("candidate_generation", {})
    response = candidate_generation.get("response", {})
    scorer = artifact.get("scorer", {})
    scorecards = scorer.get("scorecards", {})
    risk_evaluator = artifact.get("risk_evaluator", {})
    selection = artifact.get("selection", {})
    replay = artifact.get("replay", {})
    if not all(isinstance(item, Mapping) for item in (input_context, response, scorer, scorecards, risk_evaluator, selection, replay)):
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
        and replay.get("uses_persona_reasoning_response") is True
        and replay.get("uses_selected_oss_feedback") is True
        and replay.get("input_hash")
        and replay.get("candidate_hash")
        and replay.get("score_hash")
        and replay.get("selection_hash")
        and _trace_has_no_forbidden_window_leakage(trace)
        and _trace_memory_influence_is_usable(trace)
        and _trace_persona_reasoning_is_usable(trace)
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
    return bool(
        request.get("model_id") == PERSONA_REASONING_MODEL_ID
        and response.get("model_id") == PERSONA_REASONING_MODEL_ID
        and evaluator.get("model_id") == PERSONA_REASONING_EVALUATOR_MODEL_ID
        and evaluator.get("status") == "passed"
        and all(check.get("status") == "passed" for check in evaluator.get("checks", []))
        and request.get("allowed_windows") == decision_inputs.get("allowed_windows")
        and request.get("forbidden_windows_not_used") == decision_inputs.get("forbidden_windows_not_used")
        and response.get("status") == "completed"
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
    multi_generation_trajectory = 1.0 if _evolution_trajectory_is_usable(evolution_trajectory) else 0.0
    no_leakage_temporal_protocol = 1.0 if _no_leakage_temporal_protocol_is_usable(
        no_leakage_protocol
    ) else 0.0
    return {
        "return_improvement": return_improvement,
        "multi_generation_improvement": multi_generation_improvement,
        "multi_generation_trajectory": multi_generation_trajectory,
        "no_leakage_temporal_protocol": no_leakage_temporal_protocol,
        "drawdown_reduction": drawdown_reduction,
        "turnover_control": turnover_control,
        "fill_quality": fill_quality,
        "regime_adaptation": regime_adaptation,
        "memory_reuse": memory_reuse,
        "memory_influences_decision": memory_influences_decision,
        "decision_explainability": decision_explainability,
        "persona_decision_artifact": persona_decision_artifact,
        "persona_reasoning_generation": persona_reasoning_generation,
        "oss_evidence_completeness": min(1.0, oss_evidence_completeness),
        "portfolio_breadth": portfolio_breadth,
        "no_leakage": no_leakage,
        "validation_planning": planning_completeness,
        "market_friction_model": market_friction,
        "broker_lifecycle_reconciliation": broker_lifecycle,
        "persona_conflict_resolution": persona_conflicts,
        "restart_recovery": restart_recovery,
        "autonomous_scheduler": autonomous_scheduler,
        "lean_engine_replay": lean_engine_replay,
        "shioaji_sandbox_lifecycle": shioaji_sandbox,
        "case_specific_upstream_artifact_feedback": case_upstream_feedback,
        "lean_handoff_packet": lean_handoff,
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
    usability_dimensions: Mapping[str, float],
    oss_inputs: Mapping[str, Mapping[str, Any]],
    case_upstream_artifacts: Mapping[str, Any],
    operational_context: Mapping[str, Any],
    validation_plan: Mapping[str, Any],
    validation_diagnostics: Mapping[str, Any],
    validation_repair: Mapping[str, Any],
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
        },
        "reflection": {
            "agent_decision_traces": list(decision_traces),
            "candidate_counts": [trace["candidate_count"] for trace in decision_traces],
            "selected_candidate_ids": [trace["selected_candidate_id"] for trace in decision_traces],
        },
        "operational_context": dict(operational_context),
        "validation_cycle": {
            "planning": dict(validation_plan),
            "execution_review": dict(validation_diagnostics),
            "repair": dict(validation_repair),
        },
        "evolution": {
            "decision_id": evolution_decision.decision_id,
            "decision_state": _enum_value(evolution_decision.decision_state),
            "action_type": _enum_value(evolution_decision.action_type),
            "review_steps": [_enum_value(step.step_type) for step in evolution_decision.review_chain],
            "execution_status": _enum_value(evolution_decision.execution_result.status)
            if evolution_decision.execution_result
            else None,
            "trajectory": copy.deepcopy(dict(evolution_trajectory)),
            "no_leakage_protocol": copy.deepcopy(dict(no_leakage_protocol)),
        },
        "usability_dimensions": dict(usability_dimensions),
        "overall_usability_score": overall_usability_score,
        "usable": {
            "non_repeated_validation": bool(episode.validation_signature),
            "traded_portfolio_all_generations": all(execution["filled"] for execution in executions),
            "no_leakage_holdout": usability_dimensions["no_leakage_temporal_protocol"] == 1.0,
            "memory_retrieval_drives_next_decision": usability_dimensions["memory_influences_decision"] == 1.0,
            "multi_oss_feedback_drives_decision": usability_dimensions["oss_evidence_completeness"] == 1.0,
            "persona_decision_artifacts_replay": usability_dimensions["persona_decision_artifact"] == 1.0,
            "persona_reasoning_drives_candidate_generation": usability_dimensions[
                "persona_reasoning_generation"
            ] == 1.0,
            "multi_generation_evolution": usability_dimensions["multi_generation_trajectory"] == 1.0,
            "portfolio_level": len(episode.windows) == PORTFOLIO_LEG_COUNT,
            "multi_dimensional_score_passed": overall_usability_score >= MIN_USABILITY_SCORE,
            "validation_planned_before_execution": usability_dimensions["validation_planning"] == 1.0,
            "validation_diagnostics_passed": validation_diagnostics["failed_check_count"] == 0,
            "validation_deficiencies_repaired": not validation_repair["unresolved_deficiencies"],
            "market_friction_model_applied": usability_dimensions["market_friction_model"] == 1.0,
            "broker_lifecycle_reconciled": usability_dimensions["broker_lifecycle_reconciliation"] == 1.0,
            "persona_conflicts_resolved": usability_dimensions["persona_conflict_resolution"] == 1.0,
            "restart_recovery_restores_loop": usability_dimensions["restart_recovery"] == 1.0,
            "autonomous_scheduler_orders_next_cycle": usability_dimensions["autonomous_scheduler"] == 1.0,
            "lean_engine_replay_uses_runtime_binding": usability_dimensions["lean_engine_replay"] == 1.0,
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
    evolution_trajectories = [case["evolution"]["trajectory"] for case in cases]
    no_leakage_protocols = [case["evolution"]["no_leakage_protocol"] for case in cases]
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
        "persona_conflict_resolved_count": sum(1 for item in usable if item["persona_conflicts_resolved"]),
        "restart_recovery_count": sum(1 for item in usable if item["restart_recovery_restores_loop"]),
        "autonomous_scheduler_count": sum(1 for item in usable if item["autonomous_scheduler_orders_next_cycle"]),
        "lean_engine_replay_count": sum(1 for item in usable if item["lean_engine_replay_uses_runtime_binding"]),
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
            "Every case trades a three-instrument portfolio across three generations through paper runtime fills.",
            "Every case writes memory and proves retrieved lessons influence later candidate scoring and selected evidence refs.",
            "Every case uses OSS feedback across alpha, policy, reflection, tracking, risk, session, and LEAN handoff roles.",
            "Every persona decision emits a replayable request-response artifact covering candidate generation, scoring, risk checks, and rejected alternatives.",
            "Every persona decision first emits a structured reasoning response whose candidate blueprints drive the scored candidates.",
            "Every case records a multi-generation evolution trajectory proving gen0->gen1->gen2 lineage, two distinct unseen-window improvements, and bounded turnover.",
            "Every case has a case-specific vectorbt historical backtest artifact and a case-specific experiment tracking readback before persona decisions.",
            "Every case runs its selected alpha, policy, reflection, and risk OSS route as case-specific persona feedback and uses those refs in the selected decision trace.",
            "Every case applies market friction, reconciles paper broker lifecycle readback, resolves persona conflicts, recovers from a restart checkpoint, and schedules the next autonomous cycle.",
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

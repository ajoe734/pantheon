"""E2E: persona agents plan, trade, reflect, and evolve across 3000 cases."""

from __future__ import annotations

import importlib.util

from services.persona.agent_usability_validation import (
    AUTONOMOUS_SCHEDULER_PHASES,
    BROKER_LIFECYCLE_TERMINAL_STATUS,
    CASE_SELECTED_OSS_MODEL_ID,
    CASE_UPSTREAM_TRACKING_MODEL_ID,
    CASE_UPSTREAM_VECTORBT_MODEL_ID,
    DEFAULT_CASE_COUNT,
    FEEDBACK_BARS,
    GENERATION_COUNT,
    HISTORICAL_OHLCV_DATASET_ID,
    LEAN_ENGINE_REPLAY_MODEL_ID,
    LOOKBACK_BARS,
    MARKET_FRICTION_MODEL_ID,
    MIN_USABILITY_SCORE,
    OPERATIONAL_SCENARIOS,
    ORDER_TYPES,
    SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID,
    OSS_REQUIRED_COMPONENTS,
    PORTFOLIO_LEG_COUNT,
    QUANTITY_TYPES,
    run_agent_usability_validations,
)
from services.persona.ooda_cycle_runtime import ALPHA_SEED_SOURCES


EXPECTED_GAP_QUESTIONS = [
    "which_validation_axes_are_still_uncovered",
    "which_covered_axes_can_be_deepened_with_a_new_market_window",
    "which_realistic_persona_oss_alpha_portfolio_combinations_are_plausible_but_unvalidated",
]


def test_persona_agents_plan_trade_reflect_and_evolve_across_3000_unique_cases() -> None:
    run = run_agent_usability_validations(case_count=DEFAULT_CASE_COUNT)
    summary = run.summary
    cases = list(run.cases)

    assert summary["total_cases"] == DEFAULT_CASE_COUNT
    assert len(cases) == DEFAULT_CASE_COUNT
    assert summary["unique_validation_signature_count"] == DEFAULT_CASE_COUNT
    assert summary["unique_validation_plan_signature_count"] == DEFAULT_CASE_COUNT
    assert summary["unique_target_combo_signature_count"] == DEFAULT_CASE_COUNT
    assert summary["overlaps_previous_agent_usability_case_ids"] is False
    assert len({case["case_id"] for case in cases}) == DEFAULT_CASE_COUNT
    assert len({case["validation_signature"] for case in cases}) == DEFAULT_CASE_COUNT

    assert summary["historical_dataset"]["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
    assert summary["historical_dataset"]["record_count"] == 26800
    assert summary["historical_dataset"]["instrument_count"] == 50
    assert summary["alpha_seed_count"] == len(ALPHA_SEED_SOURCES)
    assert summary["portfolio_episode_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_leg_count"] == PORTFOLIO_LEG_COUNT
    assert summary["generation_count"] == GENERATION_COUNT

    assert summary["oss_result_count"] == len(OSS_REQUIRED_COMPONENTS)
    assert set(summary["oss_components_completed"]) == set(OSS_REQUIRED_COMPONENTS)
    assert summary["no_leakage_holdout_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_trade_generation_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["portfolio_trade_generation_fill_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["memory_retrieval_drives_next_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_oss_feedback_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_generation_evolution_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_dimensional_score_pass_count"] == DEFAULT_CASE_COUNT

    assert summary["validation_planning_count"] == DEFAULT_CASE_COUNT
    assert summary["validation_diagnostics_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["validation_deficiencies_repaired_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_case_memory_retrieval_count"] == DEFAULT_CASE_COUNT - summary["persona_count"]
    assert summary["market_friction_model_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_lifecycle_reconciled_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_conflict_resolved_count"] == DEFAULT_CASE_COUNT
    assert summary["restart_recovery_count"] == DEFAULT_CASE_COUNT
    assert summary["autonomous_scheduler_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_engine_replay_count"] == DEFAULT_CASE_COUNT
    assert summary["shioaji_sandbox_lifecycle_count"] == DEFAULT_CASE_COUNT
    assert summary["case_specific_vectorbt_backtest_count"] == DEFAULT_CASE_COUNT
    assert summary["case_specific_tracking_roundtrip_count"] == DEFAULT_CASE_COUNT
    assert summary["case_specific_selected_oss_feedback_count"] == DEFAULT_CASE_COUNT
    if importlib.util.find_spec("vectorbt") is not None:
        assert summary["case_vectorbt_real_backend_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_handoff_packet_count"] == DEFAULT_CASE_COUNT
    assert summary["validation_gap_question_count"] == DEFAULT_CASE_COUNT * len(EXPECTED_GAP_QUESTIONS)
    assert summary["unresolved_validation_deficiency_count"] == 0
    assert summary["min_overall_usability_score"] >= MIN_USABILITY_SCORE

    coverage = summary["coverage"]
    assert coverage["covered_persona_ids"] == coverage["persona_ids"]
    assert coverage["covered_seed_keys"] == sorted(source.key for source in ALPHA_SEED_SOURCES)
    assert len(coverage["instruments"]) == 50
    assert set(coverage["oss_components"]) == set(OSS_REQUIRED_COMPONENTS)
    assert set(coverage["quantity_types"]) == set(QUANTITY_TYPES)
    assert set(coverage["order_types"]) == set(ORDER_TYPES)
    assert coverage["generation_paths"] == [
        "observe_only_baseline->feedback_memory_scored_agent_decision->holdout_refined_second_generation"
    ]
    assert coverage["reflection_archetypes"]
    assert coverage["regime_paths"]
    assert set(coverage["operational_scenarios"]) == set(OPERATIONAL_SCENARIOS)
    assert coverage["market_friction_models"] == [MARKET_FRICTION_MODEL_ID]
    assert set(coverage["broker_lifecycle_statuses"]) == {
        "acknowledged",
        "cancel_acknowledged",
        "cancel_requested",
        "filled",
        "limit_missed",
        "liquidity_scaled",
        "partially_filled",
        "rejected",
        "replace_submitted",
        "repriced",
        "resubmitted",
        "risk_reduced",
        "submitted",
    }
    assert set(coverage["persona_conflict_types"]) == {
        "direction_conflict",
        "execution_constraint_conflict",
        "weight_conflict",
    }
    assert set(coverage["scheduler_phases"]) == set(AUTONOMOUS_SCHEDULER_PHASES)
    assert coverage["lean_engine_replay_models"] == [LEAN_ENGINE_REPLAY_MODEL_ID]
    assert coverage["lean_engine_algorithm_modules"] == ["pantheon_algo.smoke_loader_test"]
    assert coverage["shioaji_sandbox_models"] == [SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID]
    assert coverage["shioaji_sandbox_run_modes"] == ["mock_api_replay"]
    assert set(coverage["case_vectorbt_backends"]).issubset({"stub_backtest", "vectorbt_portfolio"})
    if importlib.util.find_spec("vectorbt") is not None:
        assert coverage["case_vectorbt_backends"] == ["vectorbt_portfolio"]
    assert coverage["case_tracking_components"] == ["mlflow", "wandb"]
    assert coverage["case_tracking_backends"] == ["mlflow", "wandb"]
    assert coverage["case_upstream_allowed_windows"] == ["observe+feedback"]
    assert coverage["case_selected_oss_roles"] == [
        "alpha_model",
        "policy_candidate",
        "reflection_artifact",
        "risk_analytics",
    ]
    assert coverage["case_selected_oss_components_by_role"] == {
        "alpha_model": ["qlib", "vectorbt"],
        "policy_candidate": ["finrl", "ray_tune", "rllib"],
        "reflection_artifact": ["dspy", "imitation", "trl"],
        "risk_analytics": ["quantlib", "statsmodels"],
    }
    assert set(coverage["case_selected_oss_artifact_families"]) == {
        "imitation_policy",
        "model_artifact",
        "optimizer_result",
        "pricing_report",
        "prompt_bundle",
        "qlib_alpha",
        "regime_report",
        "rl_policy",
        "vectorbt_backtest",
    }

    plan_signatures: set[str] = set()
    combo_signatures: set[str] = set()
    portfolio_window_signatures: set[str] = set()
    for case in cases:
        assert not case["case_id"].startswith("agent-usability-")
        _assert_unique_planned_validation_cycle(
            case,
            plan_signatures=plan_signatures,
            combo_signatures=combo_signatures,
            portfolio_window_signatures=portfolio_window_signatures,
        )
        _assert_portfolio_generations(case)
        _assert_agent_decision_traces_are_no_leakage(case)
        _assert_memory_and_oss_closed_loop(case)
        _assert_case_specific_upstream_artifacts(case)
        _assert_operational_context(case)
        _assert_evolution_and_scores(case)
        assert all(case["usable"].values())


def _assert_unique_planned_validation_cycle(
    case: dict,
    *,
    plan_signatures: set[str],
    combo_signatures: set[str],
    portfolio_window_signatures: set[str],
) -> None:
    cycle = case["validation_cycle"]
    planning = cycle["planning"]
    selected_plan = planning["selected_validation_plan"]

    assert planning["questions_asked"] == EXPECTED_GAP_QUESTIONS
    assert planning["unvalidated_axes_before"]["validation_signature"] is True
    assert planning["unvalidated_axes_before"]["portfolio_window_tuple"] is True
    assert planning["unvalidated_axes_before"]["persona_seed_portfolio_oss_order_combo"] is True
    assert planning["deepening_targets"]
    assert len(planning["plausible_unvalidated_combinations"]) >= 3
    assert planning["plausible_unvalidated_combinations"][0]["selected_for_execution"] is True
    assert planning["plausible_unvalidated_combinations"][0]["status_before"] == "unvalidated"

    assert selected_plan["target_validation_signature"] == case["validation_signature"]
    assert selected_plan["target_combo_signature"] not in combo_signatures
    assert planning["plan_signature"] not in plan_signatures
    assert selected_plan["target_portfolio_window_signature"] not in portfolio_window_signatures
    assert len(set(selected_plan["assertion_labels"])) == len(selected_plan["assertion_labels"])
    assert selected_plan["operational_scenario"] in OPERATIONAL_SCENARIOS
    assert any(
        label == f"operational_scenario:{selected_plan['operational_scenario']}"
        for label in selected_plan["assertion_labels"]
    )
    assert "apply_market_friction_model" in selected_plan["execution_steps"]
    assert "request_case_specific_upstream_artifacts" in selected_plan["execution_steps"]
    assert "reconcile_paper_broker_lifecycle" in selected_plan["execution_steps"]
    assert "resolve_multi_persona_conflicts" in selected_plan["execution_steps"]
    assert "recover_after_midloop_restart" in selected_plan["execution_steps"]
    assert "schedule_next_autonomous_cycle" in selected_plan["execution_steps"]
    assert "materialize_lean_handoff_packet" in selected_plan["execution_steps"]
    assert "diagnose_and_repair_deficiencies" in selected_plan["execution_steps"]

    plan_signatures.add(planning["plan_signature"])
    combo_signatures.add(selected_plan["target_combo_signature"])
    portfolio_window_signatures.add(selected_plan["target_portfolio_window_signature"])

    execution_review = cycle["execution_review"]
    assert execution_review["execution_status"] == "executed"
    assert execution_review["executed_steps"] == selected_plan["execution_steps"]
    assert execution_review["failed_check_count"] == 0
    assert all(check["status"] == "passed" for check in execution_review["checks"])

    repair = cycle["repair"]
    assert repair["deficiencies_found"] == []
    assert repair["repair_actions"] == []
    assert repair["revalidation_status"] == "passed"
    assert repair["unresolved_deficiencies"] == []


def _assert_portfolio_generations(case: dict) -> None:
    assert case["portfolio"]["instrument_count"] == PORTFOLIO_LEG_COUNT
    assert len(case["portfolio"]["instruments"]) == PORTFOLIO_LEG_COUNT
    assert len(set(case["portfolio"]["instruments"])) == PORTFOLIO_LEG_COUNT
    assert case["order_profile"]["quantity_type"] in QUANTITY_TYPES
    assert case["order_profile"]["order_type"] in ORDER_TYPES

    generation_results = case["generation_results"]
    assert len(generation_results) == GENERATION_COUNT
    assert [result["generation"] for result in generation_results] == [0, 1, 2]
    assert [result["policy_version"] for result in generation_results] == [
        "observe_only_baseline",
        "feedback_memory_scored_agent_decision",
        "holdout_refined_second_generation",
    ]
    for result in generation_results:
        assert result["filled"] is True
        assert result["fill_count"] == PORTFOLIO_LEG_COUNT
        assert result["expected_fill_count"] == PORTFOLIO_LEG_COUNT
        assert result["fill_rate"] == 1.0

    assert generation_results[0]["decision_inputs"]["allowed_windows"] == ["observe"]
    assert set(generation_results[0]["decision_inputs"]["forbidden_windows_not_used"]) == {
        "feedback",
        "holdout",
        "future_holdout",
    }


def _assert_agent_decision_traces_are_no_leakage(case: dict) -> None:
    traces = case["reflection"]["agent_decision_traces"]
    assert len(traces) == 2
    assert traces[0]["decision_inputs"]["allowed_windows"] == ["observe", "feedback"]
    assert set(traces[0]["decision_inputs"]["forbidden_windows_not_used"]) == {
        "holdout",
        "future_holdout",
    }
    assert traces[1]["decision_inputs"]["allowed_windows"] == ["observe", "feedback", "holdout"]
    assert traces[1]["decision_inputs"]["forbidden_windows_not_used"] == ["future_holdout"]

    for trace in traces:
        forbidden = set(trace["decision_inputs"]["forbidden_windows_not_used"])
        assert trace["candidate_count"] >= 4
        assert trace["selected_candidate_id"] == trace["selected_candidate"]["candidate_id"]
        for candidate in trace["candidates"]:
            assert not forbidden.intersection(candidate["source_windows"])
        assert not forbidden.intersection(trace["selected_candidate"]["source_windows"])
        assert trace["selected_candidate"]["evidence_refs"]
        assert trace["evidence_refs"]


def _assert_memory_and_oss_closed_loop(case: dict) -> None:
    memory = case["memory"]
    assert len(memory["generation_memory_writes"]) == 2
    assert all(write["created"] is True for write in memory["generation_memory_writes"])
    assert all(write["institutional_entry_id"] for write in memory["generation_memory_writes"])
    assert all(write["persona_memory_ids"] for write in memory["generation_memory_writes"])
    assert len(memory["memory_reused_for_next_decision"]) == 2
    assert all(context["reuse_count"] >= 1 for context in memory["memory_reused_for_next_decision"])
    if memory["prior_memory"]:
        first_trace = case["reflection"]["agent_decision_traces"][0]
        assert first_trace["decision_inputs"]["memory_ref"] == memory["prior_memory"]["memory_id"]

    oss_feedback = case["oss_feedback"]
    assert set(oss_feedback["request_ids"]) == {
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    }
    assert set(oss_feedback["components_used"]).issubset(set(OSS_REQUIRED_COMPONENTS))
    assert "lean_handoff" in oss_feedback["components_used"]
    assert "vectorbt" in oss_feedback["components_used"]
    assert oss_feedback["drives_persona_steps"]["handoff"] == "evolved_strategy_handoff"


def _assert_case_specific_upstream_artifacts(case: dict) -> None:
    artifacts = case["case_upstream_artifacts"]
    vectorbt = artifacts["vectorbt"]
    tracker = artifacts["tracker"]
    selected_oss = artifacts["selected_oss"]
    persona_response = artifacts["persona_response"]

    assert artifacts["vectorbt_model_id"] == CASE_UPSTREAM_VECTORBT_MODEL_ID
    assert artifacts["tracking_model_id"] == CASE_UPSTREAM_TRACKING_MODEL_ID
    assert artifacts["selected_oss_model_id"] == CASE_SELECTED_OSS_MODEL_ID
    assert artifacts["allowed_windows"] == ["observe", "feedback"]
    assert artifacts["forbidden_windows_not_used"] == ["holdout", "future_holdout"]

    assert vectorbt["request_id"] == case["oss_feedback"]["request_ids"]["backtest"]
    assert vectorbt["request_id"] == f"req-{case['case_id']}-vectorbt-upstream"
    assert vectorbt["backend"] in {"stub_backtest", "vectorbt_portfolio"}
    if importlib.util.find_spec("vectorbt") is not None:
        assert vectorbt["backend"] == "vectorbt_portfolio"
        assert vectorbt["real_package_available"] is True
    assert vectorbt["run_id"]
    assert vectorbt["registry_id"]
    assert vectorbt["producer_run_id"] == vectorbt["run_id"]
    assert vectorbt["checksum"].startswith("sha256:")

    dataset_summary = vectorbt["dataset_summary"]
    assert dataset_summary["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
    assert dataset_summary["num_instruments"] == PORTFOLIO_LEG_COUNT
    assert dataset_summary["total_bars"] == PORTFOLIO_LEG_COUNT * (LOOKBACK_BARS + FEEDBACK_BARS)
    assert set(dataset_summary["instruments"]) == set(case["portfolio"]["instruments"])
    assert set(case["source_dataset_refs"]).issubset(set(dataset_summary["source_dataset_refs"]))
    assert vectorbt["portfolio_instruments"] == case["portfolio"]["instruments"]
    assert vectorbt["historical_window_start_indices"] == case["portfolio"]["start_indices"]
    assert vectorbt["aggregate_metrics"]["num_instruments"] == PORTFOLIO_LEG_COUNT
    assert "total_trades" in vectorbt["aggregate_metrics"]
    assert vectorbt["backtest_config"]["strategy_params"]["short_window"] >= 3
    assert vectorbt["backtest_config"]["strategy_params"]["long_window"] > vectorbt["backtest_config"]["strategy_params"]["short_window"]

    assert tracker["request_id"] == case["oss_feedback"]["request_ids"]["tracker"]
    assert tracker["component"] == case["oss_feedback"]["route"]["tracker"]
    assert tracker["backend"] == tracker["component"]
    assert tracker["run_id"]
    assert tracker["registry_id"] == vectorbt["registry_id"]
    assert tracker["source_vectorbt_run_id"] == vectorbt["run_id"]
    assert tracker["readback"]["run_readback_status"] == "found"
    assert tracker["readback"]["artifact_readback_status"] == "found"
    assert tracker["record"]["artifact_names"] == [
        "artifact_handoff.json",
        "evaluation_summary.json",
        "registry_entry.json",
    ]

    expected_selected_components = {
        "alpha_model": case["oss_feedback"]["route"]["alpha_model"],
        "policy_candidate": case["oss_feedback"]["route"]["policy_candidate"],
        "reflection_artifact": case["oss_feedback"]["route"]["reflection_artifact"],
        "risk_analytics": case["oss_feedback"]["route"]["risk_analytics"],
    }
    assert set(selected_oss) == set(expected_selected_components)
    for role, component in expected_selected_components.items():
        entry = selected_oss[role]
        assert entry["model_id"] == CASE_SELECTED_OSS_MODEL_ID
        assert entry["case_specific"] is True
        assert entry["component"] == component
        assert entry["expected_component"] == component
        assert entry["status"] == "completed"
        assert entry["artifact_family"]
        assert entry["request_id"] == case["oss_feedback"]["request_ids"][role]
        if not (role == "alpha_model" and component == "vectorbt"):
            assert entry["request_id"] == f"req-{case['case_id']}-{role}-{component}"
        else:
            assert entry["request_id"] == vectorbt["request_id"]
        assert entry["persona_followup"]["trigger_component"] == component
        assert entry["persona_followup"]["trigger_request_id"] == entry["request_id"]
        assert entry["drives_persona_step"] == case["oss_feedback"]["drives_persona_steps"][role]
        assert entry["metrics"] or entry["primary_output_keys"]

    assert persona_response["used_before_generation1_decision"] is True
    assert persona_response["used_before_generation2_decision"] is True
    vectorbt_ref = f"oss://vectorbt/{vectorbt['request_id']}"
    tracker_ref = f"oss://{tracker['component']}/{tracker['request_id']}"
    experiment_ref = f"experiment://{tracker['backend']}/{tracker['run_id']}"
    assert vectorbt_ref in persona_response["evidence_refs"]
    assert tracker_ref in persona_response["evidence_refs"]
    assert experiment_ref in persona_response["evidence_refs"]
    for entry in selected_oss.values():
        assert f"oss://{entry['component']}/{entry['request_id']}" in persona_response["evidence_refs"]

    for trace in case["reflection"]["agent_decision_traces"]:
        assert vectorbt_ref in trace["evidence_refs"]
        assert tracker_ref in trace["evidence_refs"]
        selected_refs = trace["selected_candidate"]["evidence_refs"]
        assert vectorbt_ref in selected_refs
        assert tracker_ref in selected_refs
        for entry in selected_oss.values():
            oss_ref = f"oss://{entry['component']}/{entry['request_id']}"
            assert oss_ref in trace["evidence_refs"]
            assert oss_ref in selected_refs

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["case_specific_upstream_artifacts_drive_persona_decision"]["status"] == "passed"
    assert check_by_name["case_specific_selected_oss_route_feedback_drives_persona_decision"]["status"] == "passed"


def _assert_operational_context(case: dict) -> None:
    operational = case["operational_context"]
    assert operational["operational_signature"]
    assert operational["scenario"] in OPERATIONAL_SCENARIOS

    friction = operational["market_friction"]
    assert friction["model_id"] == MARKET_FRICTION_MODEL_ID
    assert friction["applied"] is True
    assert friction["all_orders_within_liquidity_cap"] is True
    assert friction["costs_are_positive"] is True
    assert len(friction["generation_costs"]) == GENERATION_COUNT
    for generation_cost in friction["generation_costs"]:
        assert generation_cost["average_cost_bps"] > 0
        assert generation_cost["net_score_after_costs"] < generation_cost["gross_score"]
        assert len(generation_cost["leg_costs"]) == PORTFOLIO_LEG_COUNT
        for leg_cost in generation_cost["leg_costs"]:
            assert leg_cost["within_liquidity_cap"] is True
            assert 0 < leg_cost["total_cost_bps"]
            assert 0 <= leg_cost["participation"] <= leg_cost["liquidity_cap"]

    lifecycle = operational["broker_lifecycle"]
    assert lifecycle["order_count"] == GENERATION_COUNT * PORTFOLIO_LEG_COUNT
    assert lifecycle["terminal_statuses"] == [BROKER_LIFECYCLE_TERMINAL_STATUS]
    assert lifecycle["reconciled"] is True
    assert lifecycle["readback_consistent"] is True
    assert lifecycle["live_broker_submission_count"] == 0
    for order in lifecycle["orders"]:
        assert order["status_path"][0] == "submitted"
        assert order["terminal_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert order["readback_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert order["live_broker_submitted"] is False

    conflict = operational["persona_conflict_resolution"]
    assert conflict["classified_conflicts"]
    assert "weight_conflict" in conflict["conflict_types"]
    assert conflict["open_conflicts"] == []
    assert conflict["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][-1]["reflection_id"]
    allocation = conflict["resolved_allocation"]
    assert allocation["capital_budget_pct"] <= 1.0
    assert set(allocation["direction_by_instrument"]) == set(case["portfolio"]["instruments"])
    assert set(allocation["weight_by_instrument"]) == set(case["portfolio"]["instruments"])

    recovery = operational["restart_recovery"]
    assert recovery["checkpoint_written"] is True
    assert recovery["recovered"] is True
    assert recovery["duplicate_execution_suppressed"] is True
    assert recovery["next_step_completed"] is True
    assert recovery["resume_step"] == "execute_generation2_future_holdout"
    assert recovery["memory_refs_before_restart"] == recovery["memory_refs_after_recovery"]

    schedule = operational["autonomous_schedule"]
    assert schedule["trigger_mode"] == "autonomous_daily_paper_loop"
    assert schedule["phase_order_valid"] is True
    assert [phase["phase"] for phase in schedule["phases"]] == list(AUTONOMOUS_SCHEDULER_PHASES)
    assert schedule["missed_cycle_recovered"] is True
    assert schedule["next_cycle_due_at"]

    replay = operational["lean_engine_replay"]
    assert replay["model_id"] == LEAN_ENGINE_REPLAY_MODEL_ID
    assert replay["status"] == "passed"
    assert replay["algorithm_module"] == "pantheon_algo.smoke_loader_test"
    assert replay["case_specific_runtime_binding"] is True
    assert replay["case_specific_strategy_packet"]["validation_signature"] == case["validation_signature"]
    assert replay["case_specific_strategy_packet"]["policy_id"] == case["generation_results"][-1]["policy_id"]
    assert replay["plan"]["target_stage"] == "paper"
    assert replay["binding"]["deployment_mode"] == "paper"
    assert replay["runtime_context"]["runtime_binding_id"] == replay["binding"]["binding_id"]
    assert replay["runtime_context"]["runtime_id"] == replay["binding"]["runtime_id"]
    assert replay["runtime_context"]["deployment_plan_id"] == replay["plan"]["plan_id"]
    assert replay["runtime_context"]["deployment_stage"] == "paper"
    assert replay["loaded_metadata"]["deployment_plan_id"] == replay["plan"]["plan_id"]
    assert replay["loaded_metadata"]["runtime_binding_id"] == replay["binding"]["binding_id"]
    assert replay["synthetic_bar_count"] == 5
    assert replay["raw_on_data_callbacks"] == 5
    assert replay["executed_on_data_callbacks"] >= 1
    assert replay["fill_count"] >= 1
    assert replay["broker_production_live_enabled"] == "false"
    assert any(key.endswith("/artifact.bin") for key in replay["object_store_keys"])
    assert any(key.endswith("/metadata.json") for key in replay["object_store_keys"])

    sandbox = operational["shioaji_sandbox_lifecycle"]
    assert sandbox["model_id"] == SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID
    assert sandbox["status"] == "passed"
    assert sandbox["run_mode"] == "mock_api_replay"
    assert sandbox["provider"] == "Shioaji"
    assert sandbox["environment"] == "sandbox"
    assert sandbox["production_live_enabled"] is False
    assert sandbox["capital_binding_enabled"] is False
    assert sandbox["human_gate_required"] is True
    assert sandbox["place_result"]["status"] == "submitted"
    assert sandbox["cancel_result"]["status"] == "cancelled"
    assert sandbox["readback_result"]["status"] == "cancelled"
    assert sandbox["readback_result"]["is_real_order"] is False
    assert sandbox["readback_result"]["is_real_capital"] is False
    assert sandbox["readback_result"]["deployment_stage"] == "sandbox"
    assert sandbox["reconcile_result"]["status"] == "passed"
    assert sandbox["live_disabled_result"]["response"]["error_code"] == "SHIOAJI_LIVE_DISABLED"
    assert sandbox["error"] is None

    operational_artifacts = operational["case_upstream_artifacts"]
    assert operational_artifacts["feedback_id"] == case["case_upstream_artifacts"]["feedback_id"]
    assert operational_artifacts["vectorbt"]["request_id"] == case["case_upstream_artifacts"]["vectorbt"]["request_id"]
    assert operational_artifacts["tracker"]["run_id"] == case["case_upstream_artifacts"]["tracker"]["run_id"]

    handoff = operational["lean_handoff"]
    assert handoff["component"] == "lean_handoff"
    assert handoff["strategy_packet_materialized"] is True
    assert handoff["received_by_lean_handoff"] is True
    assert handoff["lean_engine_replay_id"] == replay["replay_id"]
    assert handoff["lean_engine_replay_status"] == "passed"
    assert handoff["shioaji_sandbox_lifecycle_id"] == sandbox["lifecycle_id"]
    assert handoff["shioaji_sandbox_lifecycle_status"] == "passed"
    assert handoff["case_vectorbt_request_id"] == case["case_upstream_artifacts"]["vectorbt"]["request_id"]
    assert handoff["case_vectorbt_backend"] == case["case_upstream_artifacts"]["vectorbt"]["backend"]
    assert handoff["case_vectorbt_registry_id"] == case["case_upstream_artifacts"]["vectorbt"]["registry_id"]
    assert handoff["case_tracking_request_id"] == case["case_upstream_artifacts"]["tracker"]["request_id"]
    assert handoff["case_tracking_backend"] == case["case_upstream_artifacts"]["tracker"]["backend"]
    assert handoff["case_tracking_run_id"] == case["case_upstream_artifacts"]["tracker"]["run_id"]
    assert handoff["target_stage"] == "paper"
    assert handoff["broker_live_submitted"] is False
    assert set(handoff["portfolio_instruments"]) == set(case["portfolio"]["instruments"])
    for entry in case["case_upstream_artifacts"]["selected_oss"].values():
        assert f"oss://{entry['component']}/{entry['request_id']}" in handoff["runtime_bundle_refs"]
    assert handoff["runtime_bundle_refs"]


def _assert_evolution_and_scores(case: dict) -> None:
    assert case["scores"]["holdout_improvement"] > 0
    assert case["scores"]["future_generation_improvement"] > 0
    assert case["evolution"]["decision_state"] == "executed"
    assert case["evolution"]["execution_status"] == "succeeded"
    assert case["evolution"]["review_steps"] == ["reviewed", "approved", "executed"]
    assert min(case["usability_dimensions"].values()) >= 0.8
    assert case["overall_usability_score"] >= MIN_USABILITY_SCORE
    assert HISTORICAL_OHLCV_DATASET_ID in case["source_dataset_refs"]

"""E2E: persona agents plan, trade, reflect, and evolve across 3000 cases."""

from __future__ import annotations

import importlib.util

from services.persona.agent_usability_validation import (
    AUTONOMOUS_SCHEDULER_PHASES,
    BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO,
    BROKER_ADAPTER_FOLLOWUP_MODEL_ID,
    BROKER_ADAPTER_LIFECYCLE_MODEL_ID,
    BROKER_LIFECYCLE_TERMINAL_STATUS,
    CASE_SELECTED_OSS_MODEL_ID,
    CASE_UPSTREAM_TRACKING_MODEL_ID,
    CASE_UPSTREAM_VECTORBT_MODEL_ID,
    DEFAULT_CASE_COUNT,
    EVOLUTION_TRAJECTORY_MODEL_ID,
    FEEDBACK_BARS,
    FUTURE_HOLDOUT_BARS,
    GENERATION_COUNT,
    HISTORICAL_OHLCV_DATASET_ID,
    HOLDOUT_BARS,
    LEAN_ENGINE_REPLAY_MODEL_ID,
    LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO,
    LEAN_RUNTIME_FEEDBACK_MODEL_ID,
    LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION,
    LOOKBACK_BARS,
    MARKET_FRICTION_MODEL_ID,
    MIN_USABILITY_SCORE,
    NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID,
    OPERATIONAL_SCENARIOS,
    ORDER_TYPES,
    OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID,
    PERSONA_CANDIDATE_GENERATOR_MODEL_ID,
    PERSONA_CANDIDATE_SCORER_MODEL_ID,
    PERSONA_DECISION_ARTIFACT_MODEL_ID,
    PERSONA_MEMORY_INFLUENCE_MODEL_ID,
    PERSONA_REASONING_EVALUATOR_MODEL_ID,
    PERSONA_REASONING_MODEL_ID,
    PERSONA_RISK_EVALUATOR_MODEL_ID,
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
    assert summary["no_leakage_temporal_protocol_count"] == DEFAULT_CASE_COUNT
    assert summary["no_leakage_temporal_protocol_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_trade_generation_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["portfolio_trade_generation_fill_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["memory_retrieval_drives_next_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["intra_case_memory_influence_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_case_memory_influence_count"] == DEFAULT_CASE_COUNT - summary["persona_count"]
    assert summary["multi_oss_feedback_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["agent_decision_artifact_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["agent_decision_artifact_replay_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_reasoning_response_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["persona_reasoning_drives_candidate_generation_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_generation_evolution_count"] == DEFAULT_CASE_COUNT
    assert summary["evolution_trajectory_count"] == DEFAULT_CASE_COUNT
    assert summary["evolution_trajectory_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_dimensional_score_pass_count"] == DEFAULT_CASE_COUNT

    assert summary["validation_planning_count"] == DEFAULT_CASE_COUNT
    assert summary["validation_diagnostics_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["validation_deficiencies_repaired_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_case_memory_retrieval_count"] == DEFAULT_CASE_COUNT - summary["persona_count"]
    assert summary["market_friction_model_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_lifecycle_reconciled_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_lifecycle_packet_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_lifecycle_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_lifecycle_replayed_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_followup_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_followup_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_response_drives_followup_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_conflict_resolved_count"] == DEFAULT_CASE_COUNT
    assert summary["restart_recovery_count"] == DEFAULT_CASE_COUNT
    assert summary["autonomous_scheduler_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_engine_replay_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_drives_ooda_count"] == DEFAULT_CASE_COUNT
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
    assert coverage["broker_adapter_lifecycle_models"] == [BROKER_ADAPTER_LIFECYCLE_MODEL_ID]
    assert set(coverage["broker_adapter_lifecycle_scenarios"]) == set(OPERATIONAL_SCENARIOS)
    assert set(coverage["broker_adapter_lifecycle_required_statuses"]) == {
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
    assert set(coverage["broker_adapter_lifecycle_replay_flags"]) == {
        "all_orders_end_filled",
        "all_orders_have_status_paths",
        "live_order_rejected_without_capital",
        "no_live_broker_submission",
        "paper_readback_reconciled",
        "replayable",
        "restart_recovery_preserves_readback_context",
        "sandbox_place_cancel_readback_reconciled",
        "scenario_required_statuses_observed",
    }
    assert coverage["broker_adapter_followup_models"] == [BROKER_ADAPTER_FOLLOWUP_MODEL_ID]
    assert set(coverage["broker_adapter_followup_actions"]) == set(
        BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO.values()
    )
    assert set(coverage["broker_adapter_followup_action_families"]) == {
        "cancel_replace_recovery",
        "limit_repricing",
        "liquidity_sizing",
        "position_reconciliation",
        "risk_control",
    }
    assert coverage["broker_adapter_followup_next_steps"] == ["execution_feedback_review"]
    assert set(coverage["broker_adapter_followup_replay_flags"]) == {
        "adapter_response_consumed",
        "drives_persona_next_step",
        "next_cycle_scheduled",
        "paper_only_guard_retained",
        "recovery_context_preserved",
        "scenario_action_selected",
        "source_refs_bound",
    }
    assert set(coverage["persona_conflict_types"]) == {
        "direction_conflict",
        "execution_constraint_conflict",
        "weight_conflict",
    }
    assert set(coverage["scheduler_phases"]) == set(AUTONOMOUS_SCHEDULER_PHASES)
    assert coverage["lean_engine_replay_models"] == [LEAN_ENGINE_REPLAY_MODEL_ID]
    assert coverage["lean_engine_algorithm_modules"] == ["pantheon_algo.smoke_loader_test"]
    assert coverage["lean_runtime_feedback_models"] == [LEAN_RUNTIME_FEEDBACK_MODEL_ID]
    assert set(coverage["lean_runtime_feedback_actions"]) == set(
        LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO.values()
    )
    assert set(coverage["lean_runtime_feedback_action_families"]) == {
        "allocation_decision",
        "execution_quality_orientation",
        "handoff_action_repair",
        "risk_decision",
        "runtime_fill_observation",
    }
    assert set(coverage["lean_runtime_feedback_ooda_steps"]) == {"act", "decide", "observe", "orient"}
    assert set(coverage["lean_runtime_feedback_replay_flags"]) == {
        "case_runtime_refs_bound",
        "drives_persona_next_ooda_step",
        "fills_drive_next_ooda",
        "handoff_packet_consumed",
        "next_cycle_scheduled",
        "object_store_readback_verified",
        "paper_runtime_guard_retained",
        "runtime_binding_readback_verified",
        "runtime_feedback_consumed",
    }
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
    assert coverage["oss_response_followup_loop_models"] == [OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID]
    assert coverage["oss_response_followup_roles"] == [
        "alpha_model",
        "backtest",
        "handoff",
        "policy_candidate",
        "reflection_artifact",
        "risk_analytics",
        "session",
        "tracker",
    ]
    assert set(coverage["oss_response_followup_components"]) == set(OSS_REQUIRED_COMPONENTS)
    assert coverage["oss_response_followup_candidate_actions"] == [
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    ]
    assert coverage["agent_decision_artifact_models"] == [PERSONA_DECISION_ARTIFACT_MODEL_ID]
    assert coverage["agent_candidate_generator_models"] == [PERSONA_CANDIDATE_GENERATOR_MODEL_ID]
    assert coverage["agent_candidate_scorer_models"] == [PERSONA_CANDIDATE_SCORER_MODEL_ID]
    assert coverage["agent_risk_evaluator_models"] == [PERSONA_RISK_EVALUATOR_MODEL_ID]
    assert coverage["agent_decision_artifact_generations"] == [1, 2]
    assert coverage["agent_memory_influence_models"] == [PERSONA_MEMORY_INFLUENCE_MODEL_ID]
    assert coverage["agent_memory_influence_statuses"] == ["applied", "cold_start"]
    assert "feedback-adapt" in coverage["agent_memory_selected_action_hints"]
    assert coverage["agent_persona_reasoning_models"] == [PERSONA_REASONING_MODEL_ID]
    assert coverage["agent_persona_reasoning_evaluator_models"] == [PERSONA_REASONING_EVALUATOR_MODEL_ID]
    assert "feedback-adapt" in coverage["agent_persona_reasoning_preferred_actions"]
    assert coverage["agent_persona_reasoning_candidate_actions"] == [
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    ]
    assert coverage["evolution_trajectory_models"] == [EVOLUTION_TRAJECTORY_MODEL_ID]
    assert coverage["evolution_trajectory_statuses"] == ["improving"]
    assert coverage["evolution_trajectory_windows"] == ["holdout->future_holdout"]
    assert coverage["no_leakage_temporal_protocol_models"] == [NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID]
    assert coverage["no_leakage_temporal_protocol_paths"] == [
        "observe_decide->feedback_reflect->holdout_evolve->future_holdout_verify"
    ]
    assert coverage["no_leakage_temporal_protocol_stage_windows"] == ["feedback->holdout->future_holdout"]

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
        _assert_no_leakage_temporal_protocol(case)
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
        artifact = trace["agent_decision_artifact"]
        assert artifact["model_id"] == PERSONA_DECISION_ARTIFACT_MODEL_ID
        assert artifact["persona_id"] == case["persona_id"]
        assert artifact["case_id"] == case["case_id"]
        assert artifact["generation"] in {1, 2}

        input_context = artifact["input_context"]
        assert input_context["allowed_windows"] == trace["decision_inputs"]["allowed_windows"]
        assert input_context["forbidden_windows_not_used"] == trace["decision_inputs"]["forbidden_windows_not_used"]
        assert input_context["telemetry_event_id"] == trace["decision_inputs"]["telemetry_event_id"]
        assert input_context["memory_ref"] == trace["decision_inputs"]["memory_ref"]
        if input_context["memory_ref"]:
            assert input_context["memory_status"] == "retrieved"
        else:
            assert input_context["memory_status"] == "cold_start_declared"
        memory_influence = artifact["memory_influence"]
        assert memory_influence["model_id"] == PERSONA_MEMORY_INFLUENCE_MODEL_ID
        if input_context["memory_ref"]:
            memory_ref = f"memory://{input_context['memory_ref']}"
            assert memory_influence["status"] == "applied"
            assert memory_influence["memory_id"] == input_context["memory_ref"]
            assert memory_influence["influence_ref"] == memory_ref
            assert memory_influence["content_summary"]
            assert memory_influence["cited_proposal_ids"]
            assert memory_influence["retrieval_tags"]
            assert memory_influence["selected_action_hint"] in {
                "feedback-adapt",
                "risk-off",
                "retain-observe",
                "contrarian-check",
            }
            assert input_context["memory_influence_ref"] == memory_ref
            assert input_context["memory_influence"]["memory_id"] == input_context["memory_ref"]
        else:
            memory_ref = None
            assert memory_influence["status"] == "cold_start"
            assert memory_influence["influence_ref"] is None
            assert all(value == 0.0 for value in memory_influence["candidate_score_adjustments"].values())
        assert input_context["oss_request_ids_by_role"] == case["oss_feedback"]["request_ids"]
        assert set(input_context["required_oss_roles"]) == set(case["oss_feedback"]["request_ids"])
        assert set(input_context["oss_evidence_refs"]).issubset(set(trace["evidence_refs"]))
        assert input_context["portfolio_instruments"] == case["portfolio"]["instruments"]
        followup_loop = case["oss_feedback"]["response_followup_loop"]
        followup_refs = [
            followup["response"]["output_ref"]
            for followup in followup_loop["followups"]
        ]
        assert followup_loop["model_id"] == OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID
        assert input_context["oss_followup_loop_ref"] == followup_loop["loop_ref"]
        assert trace["decision_inputs"]["oss_followup_loop_ref"] == followup_loop["loop_ref"]
        assert input_context["oss_followup_response_refs"] == followup_refs
        assert set(input_context["oss_followup_request_ids_by_role"]) == set(case["oss_feedback"]["request_ids"])
        assert followup_loop["loop_ref"] in trace["evidence_refs"]

        persona_reasoning = artifact["persona_reasoning"]
        reasoning_request = persona_reasoning["request"]
        reasoning_response = persona_reasoning["response"]
        reasoning_evaluator = persona_reasoning["evaluator"]
        assert reasoning_request["model_id"] == PERSONA_REASONING_MODEL_ID
        assert reasoning_response["model_id"] == PERSONA_REASONING_MODEL_ID
        assert reasoning_evaluator["model_id"] == PERSONA_REASONING_EVALUATOR_MODEL_ID
        assert reasoning_evaluator["status"] == "passed"
        assert all(check["status"] == "passed" for check in reasoning_evaluator["checks"])
        assert reasoning_request["allowed_windows"] == trace["decision_inputs"]["allowed_windows"]
        assert reasoning_request["forbidden_windows_not_used"] == trace["decision_inputs"]["forbidden_windows_not_used"]
        assert reasoning_request["oss_followup_loop_ref"] == followup_loop["loop_ref"]
        assert followup_loop["loop_ref"] in reasoning_request["input_refs"]
        assert reasoning_response["oss_followup_usage"]["loop_ref"] == followup_loop["loop_ref"]
        assert reasoning_response["oss_followup_usage"]["model_id"] == OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID
        assert reasoning_response["oss_followup_usage"]["followup_count"] == len(followup_loop["followups"])
        assert reasoning_response["oss_followup_usage"]["candidate_score_adjustments"] == followup_loop[
            "candidate_score_adjustments"
        ]
        if memory_ref:
            assert memory_ref in reasoning_request["input_refs"]
            assert reasoning_response["memory_usage"]["influence_ref"] == memory_ref
        else:
            assert reasoning_response["memory_usage"]["status"] == "cold_start"
        blueprint_by_action = {
            blueprint["action"]: blueprint
            for blueprint in reasoning_response["candidate_blueprints"]
        }
        assert set(blueprint_by_action) == {
            "feedback-adapt",
            "retain-observe",
            "risk-off",
            "contrarian-check",
        }

        candidate_generation = artifact["candidate_generation"]
        assert candidate_generation["model_id"] == PERSONA_CANDIDATE_GENERATOR_MODEL_ID
        response = candidate_generation["response"]
        trace_candidate_ids = [candidate["candidate_id"] for candidate in trace["candidates"]]
        assert response["status"] == "completed"
        assert response["source_reasoning_response_id"] == reasoning_response["response_id"]
        assert response["source_reasoning_ref"] == reasoning_response["reasoning_ref"]
        assert reasoning_response["reasoning_ref"] in candidate_generation["request"]["input_refs"]
        assert followup_loop["loop_ref"] in candidate_generation["request"]["input_refs"]
        assert set(followup_refs).issubset(set(candidate_generation["request"]["input_refs"]))
        assert response["candidate_ids"] == trace_candidate_ids
        assert response["candidates"] == trace["candidates"]
        if memory_ref:
            assert memory_ref in candidate_generation["request"]["input_refs"]
        for candidate in trace["candidates"]:
            candidate_action = _candidate_action_from_id(candidate["candidate_id"])
            blueprint = blueprint_by_action[candidate_action]
            assert candidate["source_windows"] == blueprint["source_windows"]
            assert candidate["rationale"] == blueprint["rationale"]
            for role in blueprint["evidence_roles"]:
                expected_ref = (
                    f"oss://{input_context['oss_components_by_role'][role]}/"
                    f"{input_context['oss_request_ids_by_role'][role]}"
                )
                assert expected_ref in candidate["evidence_refs"]
            for extra_ref in blueprint["extra_evidence_refs"]:
                assert extra_ref in candidate["evidence_refs"]

        scorer = artifact["scorer"]
        assert scorer["model_id"] == PERSONA_CANDIDATE_SCORER_MODEL_ID
        assert scorer["scoring_inputs"]["memory_influence"]["status"] == memory_influence["status"]
        assert scorer["scoring_inputs"]["memory_score_adjustments"] == memory_influence["candidate_score_adjustments"]
        assert scorer["scoring_inputs"]["oss_followup_loop"]["loop_ref"] == followup_loop["loop_ref"]
        assert scorer["scoring_inputs"]["oss_followup_score_adjustments"] == followup_loop[
            "candidate_score_adjustments"
        ]
        assert scorer["scoring_inputs"]["persona_reasoning_ref"] == reasoning_response["reasoning_ref"]
        assert scorer["scoring_inputs"]["persona_reasoning_preferred_action"] == reasoning_response[
            "preferred_action_hint"
        ]
        scorecards = scorer["scorecards"]
        assert set(scorecards) == set(trace_candidate_ids)
        assert all(card["score_replay_match"] is True for card in scorecards.values())
        selected_id = trace["selected_candidate_id"]
        selected_score = scorecards[selected_id]["candidate_score"]
        assert selected_score == max(card["candidate_score"] for card in scorecards.values())
        assert scorer["scoring_inputs"]["policy_quality"] >= 0
        assert scorer["scoring_inputs"]["reflection_quality"] >= 0
        selected_action = selected_id.rsplit("-", maxsplit=2)[-2:]
        selected_action_key = "-".join(selected_action)
        if memory_ref:
            assert memory_ref in trace["selected_candidate"]["evidence_refs"]
            assert scorecards[selected_id]["components"]["memory_adjustment"] == memory_influence[
                "candidate_score_adjustments"
            ][selected_action_key]
            assert scorecards[selected_id]["components"]["memory_adjustment"] > 0
        assert scorecards[selected_id]["components"]["oss_followup_adjustment"] == followup_loop[
            "candidate_score_adjustments"
        ][selected_action_key]
        assert scorecards[selected_id]["components"]["oss_followup_adjustment"] > 0
        assert set(followup_loop["candidate_evidence_refs_by_action"][selected_action_key]).issubset(
            set(trace["selected_candidate"]["evidence_refs"])
        )

        risk_evaluator = artifact["risk_evaluator"]
        assert risk_evaluator["model_id"] == PERSONA_RISK_EVALUATOR_MODEL_ID
        assert risk_evaluator["status"] == "passed"
        assert all(check["status"] == "passed" for check in risk_evaluator["checks"])
        assert risk_evaluator["selected_candidate_id"] == selected_id

        selection = artifact["selection"]
        assert selection["selected_candidate_id"] == selected_id
        assert selection["selected_evidence_refs"] == trace["selected_candidate"]["evidence_refs"]
        assert len(selection["rejected_candidates"]) == len(trace_candidate_ids) - 1
        assert all(item["candidate_id"] != selected_id for item in selection["rejected_candidates"])

        replay = artifact["replay"]
        assert replay["replayable"] is True
        assert replay["selected_candidate_is_top_score"] is True
        assert replay["no_forbidden_window_sources"] is True
        assert replay["uses_memory_or_declares_cold_start"] is True
        assert replay["uses_memory_in_scoring_or_declares_cold_start"] is True
        assert replay["uses_selected_oss_feedback"] is True
        assert replay["uses_oss_response_followup_loop"] is True
        assert replay["input_hash"]
        assert replay["candidate_hash"]
        assert replay["score_hash"]
        assert replay["selection_hash"]

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["persona_decision_artifact_replays_candidate_selection"]["status"] == "passed"
    assert check_by_name["persona_reasoning_response_drives_candidate_generation"]["status"] == "passed"
    assert check_by_name["retrieved_memory_influences_persona_candidate_scoring"]["status"] == "passed"
    assert check_by_name["oss_response_followup_loop_drives_persona_scoring"]["status"] == "passed"


def _assert_no_leakage_temporal_protocol(case: dict) -> None:
    protocol = case["evolution"]["no_leakage_protocol"]
    assert protocol["model_id"] == NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID
    assert protocol["case_id"] == case["case_id"]
    assert protocol["persona_id"] == case["persona_id"]
    assert protocol["protocol_path"] == "observe_decide->feedback_reflect->holdout_evolve->future_holdout_verify"
    assert protocol["input_hash"]

    assert len(protocol["window_boundaries"]) == PORTFOLIO_LEG_COUNT
    for boundary in protocol["window_boundaries"]:
        assert boundary["instrument"] in case["portfolio"]["instruments"]
        assert boundary["start_index"] in case["portfolio"]["start_indices"]
        periods = boundary["periods"]
        assert periods["observe"]["bar_count"] == LOOKBACK_BARS
        assert periods["feedback"]["bar_count"] == FEEDBACK_BARS
        assert periods["holdout"]["bar_count"] == HOLDOUT_BARS
        assert periods["future_holdout"]["bar_count"] == FUTURE_HOLDOUT_BARS
        assert periods["observe"]["end_date"] < periods["feedback"]["start_date"]
        assert periods["feedback"]["end_date"] < periods["holdout"]["start_date"]
        assert periods["holdout"]["end_date"] < periods["future_holdout"]["start_date"]
        assert boundary["ordered"] is True
        assert boundary["non_overlapping"] is True

    upstream = protocol["case_upstream_data_contract"]
    expected_pre_holdout_rows = PORTFOLIO_LEG_COUNT * (LOOKBACK_BARS + FEEDBACK_BARS)
    assert upstream["allowed_windows"] == ["observe", "feedback"]
    assert upstream["forbidden_windows_not_used"] == ["holdout", "future_holdout"]
    assert upstream["expected_pre_holdout_rows"] == expected_pre_holdout_rows
    assert upstream["vectorbt_used_historical_rows"] == expected_pre_holdout_rows
    assert upstream["vectorbt_dataset_total_bars"] == expected_pre_holdout_rows
    assert upstream["case_upstream_pre_holdout_only"] is True
    assert set(upstream["selected_oss_roles"]) == {
        "alpha_model",
        "policy_candidate",
        "reflection_artifact",
        "risk_analytics",
    }

    stages = protocol["stage_contracts"]
    assert [stage["stage_id"] for stage in stages] == [
        "generation0_observe_decide",
        "generation1_feedback_reflect_to_holdout",
        "generation2_holdout_reflect_to_future_holdout",
    ]
    assert [stage["generation"] for stage in stages] == [0, 1, 2]
    assert [stage["policy_id"] for stage in stages] == [
        result["policy_id"] for result in case["generation_results"]
    ]
    assert [stage["evaluation_window"] for stage in stages] == ["feedback", "holdout", "future_holdout"]
    assert [stage["evaluation_score"] for stage in stages] == [
        result["score"] for result in case["generation_results"]
    ]

    assert stages[0]["visible_windows"] == ["observe"]
    assert set(stages[0]["hidden_windows"]) == {"feedback", "holdout", "future_holdout"}
    assert stages[0]["decision_trace_ref"] is None
    assert stages[0]["prior_outcome_window"] is None
    assert stages[1]["visible_windows"] == ["observe", "feedback"]
    assert set(stages[1]["hidden_windows"]) == {"holdout", "future_holdout"}
    assert stages[1]["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][0]["reflection_id"]
    assert stages[1]["prior_outcome_window"] == "feedback"
    assert stages[2]["visible_windows"] == ["observe", "feedback", "holdout"]
    assert stages[2]["hidden_windows"] == ["future_holdout"]
    assert stages[2]["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][1]["reflection_id"]
    assert stages[2]["prior_outcome_window"] == "holdout"

    for stage in stages:
        assert set(stage["decision_source_windows"]).issubset(set(stage["visible_windows"]))
        assert stage["evaluation_window"] in stage["hidden_windows"]
        assert stage["evaluation_window"] not in stage["decision_source_windows"]
        assert "future_holdout" not in stage["decision_source_windows"]
        assert stage["source_windows_subset_visible"] is True
        assert stage["evaluation_window_hidden_from_decision"] is True
        assert stage["evaluation_window_absent_from_sources"] is True

    replay = protocol["replay"]
    assert replay["replayable"] is True
    assert replay["window_boundaries_ordered"] is True
    assert replay["window_boundaries_non_overlapping"] is True
    assert replay["stage_source_windows_subset_visible"] is True
    assert replay["stage_evaluation_windows_hidden_from_decisions"] is True
    assert replay["future_holdout_hidden_until_evaluation"] is True
    assert replay["holdout_hidden_from_generation1_decision"] is True
    assert replay["generation2_uses_first_holdout_outcome_only_before_future_holdout"] is True
    assert replay["case_upstream_pre_holdout_only"] is True
    assert replay["strict_improvement_on_unseen_holdouts"] is True
    assert replay["trajectory_unseen_windows_match_protocol"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["no_leakage_temporal_protocol_replays_window_boundaries"]["status"] == "passed"
    assert case["usability_dimensions"]["no_leakage_temporal_protocol"] == 1.0


def _candidate_action_from_id(candidate_id: str) -> str:
    for action in ("feedback-adapt", "retain-observe", "risk-off", "contrarian-check"):
        if candidate_id.endswith(f"-{action}"):
            return action
    raise AssertionError(f"unknown candidate action in {candidate_id}")


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
        assert first_trace["agent_decision_artifact"]["memory_influence"]["memory_id"] == memory["prior_memory"]["memory_id"]
        assert f"memory://{memory['prior_memory']['memory_id']}" in first_trace["selected_candidate"]["evidence_refs"]
    second_trace = case["reflection"]["agent_decision_traces"][1]
    assert second_trace["decision_inputs"]["memory_ref"] == memory["memory_reused_for_next_decision"][0]["memory_id"]
    assert second_trace["agent_decision_artifact"]["memory_influence"]["memory_id"] == memory[
        "memory_reused_for_next_decision"
    ][0]["memory_id"]
    assert f"memory://{memory['memory_reused_for_next_decision'][0]['memory_id']}" in second_trace[
        "selected_candidate"
    ]["evidence_refs"]

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
    followup_loop = oss_feedback["response_followup_loop"]
    assert followup_loop["model_id"] == OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID
    assert followup_loop["case_id"] == case["case_id"]
    assert followup_loop["persona_id"] == case["persona_id"]
    assert followup_loop["source_feedback_id"] == case["case_upstream_artifacts"]["feedback_id"]
    assert followup_loop["required_roles"] == [
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    ]
    assert [followup["role"] for followup in followup_loop["followups"]] == followup_loop["required_roles"]
    assert followup_loop["drives_generations"] == [1, 2]
    assert followup_loop["candidate_score_adjustments"]["feedback-adapt"] > 0
    assert followup_loop["candidate_score_adjustments"]["risk-off"] > 0
    assert followup_loop["input_hash"]
    for followup in followup_loop["followups"]:
        role = followup["role"]
        component = followup["component"]
        if role in oss_feedback["route"]:
            assert component == oss_feedback["route"][role]
        else:
            assert component
        source_ref = f"oss://{component}/{oss_feedback['request_ids'][role]}"
        assert followup["source_oss_ref"] == source_ref
        assert followup["request"]["source_oss_ref"] == source_ref
        assert followup["request"]["source_oss_request_id"] == oss_feedback["request_ids"][role]
        assert followup["request"]["requested_after_oss_response"] is True
        assert followup["request"]["drives_persona_step"] == oss_feedback["drives_persona_steps"][role]
        assert followup["response"]["status"] == "completed"
        assert followup["response"]["output_ref"].startswith("followup://persona/")
        assert followup["response"]["used_by_generations"] == [1, 2]
    replay = followup_loop["replay"]
    assert replay["replayable"] is True
    assert replay["all_required_roles_followed_up"] is True
    assert replay["all_followups_requested_after_oss_response"] is True
    assert replay["all_followups_completed"] is True
    assert replay["responses_drive_candidate_scoring"] is True
    assert replay["feedback_adapt_receives_all_followup_refs"] is True
    assert replay["risk_response_available_to_risk_off"] is True


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

    adapter_lifecycle = operational["broker_adapter_lifecycle"]
    assert adapter_lifecycle["packet_id"] == f"broker-adapter-lifecycle-{case['case_id']}"
    assert adapter_lifecycle["model_id"] == BROKER_ADAPTER_LIFECYCLE_MODEL_ID
    assert adapter_lifecycle["case_id"] == case["case_id"]
    assert adapter_lifecycle["persona_id"] == case["persona_id"]
    assert adapter_lifecycle["provider"] == "Shioaji"
    assert adapter_lifecycle["environment"] == "sandbox"
    assert adapter_lifecycle["scenario"] == operational["scenario"]
    assert adapter_lifecycle["broker_lifecycle_model"] == lifecycle["lifecycle_model"]
    assert adapter_lifecycle["shioaji_lifecycle_ref"] == f"broker-sandbox://{sandbox['lifecycle_id']}"
    assert adapter_lifecycle["restart_checkpoint_ref"] == recovery["checkpoint_id"]
    assert adapter_lifecycle["decision_trace_refs"] == [
        trace["reflection_id"] for trace in case["reflection"]["agent_decision_traces"]
    ]
    assert adapter_lifecycle["paper_order_count"] == GENERATION_COUNT * PORTFOLIO_LEG_COUNT
    assert adapter_lifecycle["paper_order_refs"] == [order["order_id"] for order in lifecycle["orders"]]
    assert set(adapter_lifecycle["required_statuses"]).issubset(set(lifecycle["lifecycle_statuses"]))
    assert adapter_lifecycle["observed_statuses"] == lifecycle["lifecycle_statuses"]
    assert adapter_lifecycle["input_hash"]

    adapter_order = adapter_lifecycle["adapter_order"]
    assert adapter_order["place_order_id"] == sandbox["place_result"]["order_id"]
    assert adapter_order["place_status"] == "submitted"
    assert adapter_order["cancel_status"] == "cancelled"
    assert adapter_order["readback_status"] == "cancelled"
    assert adapter_order["readback_is_real_order"] is False
    assert adapter_order["readback_is_real_capital"] is False
    assert adapter_order["deployment_stage"] == "sandbox"
    assert adapter_order["live_disabled_error_code"] == "SHIOAJI_LIVE_DISABLED"
    assert {check["check"] for check in adapter_lifecycle["scenario_checks"]} == {
        "live_order_rejected_without_capital",
        "paper_orders_reconciled_to_readback",
        "required_statuses_observed",
        "restart_recovery_preserves_readback_context",
        "sandbox_adapter_place_cancel_readback",
    }
    assert all(check["status"] == "passed" for check in adapter_lifecycle["scenario_checks"])
    assert all(adapter_lifecycle["replay"].values())
    assert case["usability_dimensions"]["broker_adapter_lifecycle"] == 1.0
    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["broker_adapter_lifecycle_replays_submit_readback_recovery"]["status"] == "passed"

    adapter_followup = operational["broker_adapter_followup"]
    expected_followup_action = BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO[operational["scenario"]]
    assert adapter_followup["followup_id"] == f"broker-adapter-followup-{case['case_id']}"
    assert adapter_followup["model_id"] == BROKER_ADAPTER_FOLLOWUP_MODEL_ID
    assert adapter_followup["status"] == "accepted"
    assert adapter_followup["case_id"] == case["case_id"]
    assert adapter_followup["persona_id"] == case["persona_id"]
    assert adapter_followup["scenario"] == operational["scenario"]
    assert adapter_followup["source_packet_ref"] == f"broker-adapter://{adapter_lifecycle['packet_id']}"
    assert adapter_followup["source_packet_model"] == BROKER_ADAPTER_LIFECYCLE_MODEL_ID
    assert adapter_followup["source_packet_hash"] == adapter_lifecycle["input_hash"]
    assert adapter_followup["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][-1]["reflection_id"]
    assert adapter_followup["restart_checkpoint_ref"] == recovery["checkpoint_id"]
    assert adapter_followup["schedule_ref"] == schedule["schedule_id"]
    assert adapter_followup["request_response_flow"] == [
        "persona_order_intent",
        "broker_adapter_lifecycle_response",
        "persona_followup_action",
    ]
    persona_followup = adapter_followup["persona_followup"]
    assert persona_followup["action"] == expected_followup_action
    assert persona_followup["next_persona_step"] == "execution_feedback_review"
    assert persona_followup["required_before_next_cycle"] is True
    assert persona_followup["paper_only"] is True
    assert persona_followup["rationale"]
    assert persona_followup["evidence_refs"] == [
        adapter_followup["source_packet_ref"],
        f"reflection://{adapter_followup['decision_trace_ref']}",
        f"checkpoint://{recovery['checkpoint_id']}",
        f"schedule://{schedule['schedule_id']}",
    ]
    assert adapter_followup["state_updates"]["mark_adapter_response_seen"] is True
    assert adapter_followup["state_updates"]["bind_recovery_checkpoint"] == recovery["checkpoint_id"]
    assert adapter_followup["state_updates"]["schedule_next_cycle_after_followup"] == schedule["next_cycle_due_at"]
    assert adapter_followup["state_updates"]["attach_to_decision_trace"] == adapter_followup["decision_trace_ref"]
    assert all(adapter_followup["replay"].values())
    assert adapter_followup["input_hash"]
    assert case["usability_dimensions"]["broker_adapter_followup"] == 1.0
    assert check_by_name["broker_adapter_response_drives_persona_followup"]["status"] == "passed"

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

    runtime_feedback = operational["lean_runtime_feedback"]
    expected_runtime_action = LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO[operational["scenario"]]
    assert runtime_feedback["feedback_id"] == f"lean-runtime-feedback-{case['case_id']}"
    assert runtime_feedback["model_id"] == LEAN_RUNTIME_FEEDBACK_MODEL_ID
    assert runtime_feedback["status"] == "accepted"
    assert runtime_feedback["case_id"] == case["case_id"]
    assert runtime_feedback["persona_id"] == case["persona_id"]
    assert runtime_feedback["scenario"] == operational["scenario"]
    assert runtime_feedback["source_runtime_ref"] == f"lean-engine://{replay['replay_id']}"
    assert runtime_feedback["source_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert runtime_feedback["request_response_flow"] == [
        "persona_strategy_packet",
        "lean_runtime_replay_response",
        "persona_next_ooda_action",
    ]

    runtime_readback = runtime_feedback["runtime_feedback"]
    assert runtime_readback["runtime_id"] == replay["runtime_context"]["runtime_id"]
    assert runtime_readback["runtime_binding_id"] == replay["runtime_context"]["runtime_binding_id"]
    assert runtime_readback["deployment_plan_id"] == replay["runtime_context"]["deployment_plan_id"]
    assert runtime_readback["deployment_stage"] == "paper"
    assert runtime_readback["loaded_metadata_runtime_binding_id"] == replay["loaded_metadata"]["runtime_binding_id"]
    assert runtime_readback["loaded_metadata_deployment_plan_id"] == replay["loaded_metadata"]["deployment_plan_id"]
    assert runtime_readback["fill_count"] == replay["fill_count"]
    assert runtime_readback["executed_on_data_callbacks"] == replay["executed_on_data_callbacks"]
    assert runtime_readback["object_store_metadata_key"].endswith("/metadata.json")
    assert runtime_readback["object_store_artifact_key"].endswith("/artifact.bin")

    ooda_followup = runtime_feedback["persona_ooda_followup"]
    assert ooda_followup["action"] == expected_runtime_action
    assert ooda_followup["ooda_step"] == LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION[expected_runtime_action]
    assert ooda_followup["next_scheduler_phase"] in {"evolve", "reflect"}
    assert ooda_followup["required_before_next_cycle"] is True
    assert ooda_followup["paper_only"] is True
    assert ooda_followup["rationale"]
    assert ooda_followup["evidence_refs"] == [
        runtime_feedback["source_runtime_ref"],
        runtime_feedback["source_handoff_ref"],
        f"runtime-binding://{runtime_readback['runtime_binding_id']}",
        f"object-store://{runtime_readback['object_store_metadata_key']}",
        f"reflection://{case['reflection']['agent_decision_traces'][-1]['reflection_id']}",
    ]
    assert runtime_feedback["state_updates"]["mark_runtime_feedback_seen"] is True
    assert runtime_feedback["state_updates"]["bind_runtime_context"] == runtime_readback["runtime_binding_id"]
    assert runtime_feedback["state_updates"]["verify_object_store_metadata"] == runtime_readback["object_store_metadata_key"]
    assert runtime_feedback["state_updates"]["attach_to_handoff_packet"] == handoff["packet_id"]
    assert runtime_feedback["state_updates"]["attach_to_decision_trace"] == case["reflection"]["agent_decision_traces"][-1]["reflection_id"]
    assert runtime_feedback["state_updates"]["schedule_next_cycle_after_feedback"] == schedule["next_cycle_due_at"]
    assert all(runtime_feedback["replay"].values())
    assert runtime_feedback["input_hash"]
    assert case["usability_dimensions"]["lean_runtime_feedback"] == 1.0
    assert check_by_name["lean_runtime_feedback_drives_persona_ooda"]["status"] == "passed"


def _assert_evolution_and_scores(case: dict) -> None:
    assert case["scores"]["holdout_improvement"] > 0
    assert case["scores"]["future_generation_improvement"] > 0
    assert case["evolution"]["decision_state"] == "executed"
    assert case["evolution"]["execution_status"] == "succeeded"
    assert case["evolution"]["review_steps"] == ["reviewed", "approved", "executed"]
    trajectory = case["evolution"]["trajectory"]
    assert trajectory["model_id"] == EVOLUTION_TRAJECTORY_MODEL_ID
    assert trajectory["case_id"] == case["case_id"]
    assert trajectory["generation_count"] == GENERATION_COUNT
    assert [item["generation"] for item in trajectory["policy_lineage"]] == [0, 1, 2]
    assert [item["policy_id"] for item in trajectory["policy_lineage"]] == [
        result["policy_id"] for result in case["generation_results"]
    ]
    assert trajectory["policy_lineage"][0]["decision_trace_ref"] is None
    assert trajectory["policy_lineage"][1]["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][0]["reflection_id"]
    assert trajectory["policy_lineage"][2]["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][1]["reflection_id"]

    comparisons = trajectory["comparisons"]
    assert [comparison["evaluation_window"] for comparison in comparisons] == ["holdout", "future_holdout"]
    assert comparisons[0]["previous_generation"] == 0
    assert comparisons[0]["candidate_generation"] == 1
    assert comparisons[0]["score_improvement"] == case["scores"]["holdout_improvement"]
    assert comparisons[0]["strict_improvement"] is True
    assert comparisons[0]["unseen_by_decision_trace"] is True
    assert "holdout" in comparisons[0]["trace_forbidden_windows"]
    assert comparisons[1]["previous_generation"] == 1
    assert comparisons[1]["candidate_generation"] == 2
    assert comparisons[1]["score_improvement"] == case["scores"]["future_generation_improvement"]
    assert comparisons[1]["strict_improvement"] is True
    assert comparisons[1]["unseen_by_decision_trace"] is True
    assert "future_holdout" in comparisons[1]["trace_forbidden_windows"]

    trend = trajectory["trend"]
    assert trend["generation_sequence"] == [0, 1, 2]
    assert trend["evaluation_windows"] == ["holdout", "future_holdout"]
    assert trend["improvement_deltas"] == [
        case["scores"]["holdout_improvement"],
        case["scores"]["future_generation_improvement"],
    ]
    assert trend["strict_positive_step_count"] == 2
    assert trend["regression_count"] == 0
    assert trend["cumulative_improvement"] > 0
    assert trend["max_turnover"] <= 1.25
    assert trend["convergence_status"] == "improving"

    replay = trajectory["replay"]
    assert replay["replayable"] is True
    assert replay["policy_lineage_complete"] is True
    assert replay["two_distinct_unseen_windows"] is True
    assert replay["strict_positive_step_improvements"] is True
    assert replay["decision_traces_do_not_see_evaluation_windows"] is True
    assert replay["turnover_bounded"] is True
    assert replay["converges_or_improves"] is True
    assert trajectory["input_hash"]

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["multi_generation_evolution_trajectory_converges"]["status"] == "passed"
    assert min(case["usability_dimensions"].values()) >= 0.8
    assert case["usability_dimensions"]["multi_generation_trajectory"] == 1.0
    assert case["overall_usability_score"] >= MIN_USABILITY_SCORE
    assert HISTORICAL_OHLCV_DATASET_ID in case["source_dataset_refs"]

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
    LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID,
    LEAN_ENGINE_REPLAY_MODEL_ID,
    LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID,
    LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID,
    LEAN_RUNTIME_FEEDBACK_ACTIONS_BY_SCENARIO,
    LEAN_RUNTIME_FEEDBACK_MODEL_ID,
    LEAN_RUNTIME_FEEDBACK_OODA_STEP_BY_ACTION,
    LOOKBACK_BARS,
    MARKET_FRICTION_MODEL_ID,
    MIN_USABILITY_SCORE,
    MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID,
    NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID,
    OPERATIONAL_SCENARIOS,
    ORDER_TYPES,
    PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID,
    STRICT_OOS_EVOLUTION_PROOF_MODEL_ID,
    BLIND_FUTURE_OOS_AUDIT_MODEL_ID,
    FUTURE_BLIND_WINDOW_ADMISSION_MODEL_ID,
    ALPHA_SEED_REVISION_ACTION_BY_COMPONENT,
    OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE,
    OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE,
    OSS_DISAGREEMENT_TYPES_BY_SCENARIO,
    OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID,
    PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID,
    PERSONA_ALPHA_SEED_REVISION_MODEL_ID,
    PERSONA_CANDIDATE_GENERATOR_MODEL_ID,
    PERSONA_CANDIDATE_SCORER_MODEL_ID,
    PERSONA_CONFLICT_RESOLUTION_MODEL_ID,
    PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID,
    PERSONA_DECISION_ARTIFACT_MODEL_ID,
    PERSONA_DEGRADED_OSS_RESPONSE_MODEL_ID,
    PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID,
    PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID,
    PERSONA_MEMORY_INFLUENCE_MODEL_ID,
    PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID,
    PERSONA_MULTI_PERSONA_PROPOSAL_LINEAGE_MODEL_ID,
    PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID,
    PERSONA_OPENCLAW_SESSION_CONTINUITY_MODEL_ID,
    PERSONA_OSS_OODA_LEDGER_MODEL_ID,
    PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID,
    PERSONA_OSS_QUALITY_REPAIR_HANDOFF_MODEL_ID,
    PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID,
    PERSONA_PORTFOLIO_STATE_CARRYOVER_MODEL_ID,
    PERSONA_BROKER_ADAPTER_CARRYOVER_MODEL_ID,
    PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID,
    PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID,
    PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID,
    PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID,
    PERSONA_REASONING_EVALUATOR_MODEL_ID,
    PERSONA_REASONING_MODEL_ID,
    PERSONA_RISK_ANALYTICS_LINEAGE_HANDOFF_MODEL_ID,
    PERSONA_RISK_EVALUATOR_MODEL_ID,
    PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID,
    PERSONA_TRACKING_RECONCILIATION_MODEL_ID,
    SHIOAJI_SANDBOX_LIFECYCLE_MODEL_ID,
    OSS_REQUIRED_COMPONENTS,
    OSS_ROLE_COMPONENT_MATRIX,
    OSS_QUALITY_AFFECTED_ACTION_BY_ROLE,
    OSS_QUALITY_ISSUE_BY_ROLE,
    OSS_QUALITY_REPAIR_ACTION_BY_ROLE,
    OSS_QUALITY_ROLES,
    PORTFOLIO_LEG_COUNT,
    QUANTITY_TYPES,
    TRACKING_DIVERGENCE_TYPES_BY_SCENARIO,
    TRACKING_RECONCILIATION_ACTION_BY_TYPE,
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
    expected_assertion_ref_count = sum(
        len(case["validation_cycle"]["planning"]["selected_validation_plan"]["assertion_refs"])
        for case in cases
    )
    assert summary["validation_assertion_ref_count"] == expected_assertion_ref_count
    assert summary["unique_validation_assertion_ref_count"] == expected_assertion_ref_count
    assert summary["duplicate_validation_assertion_ref_count"] == 0
    assert summary["validation_assertion_label_count"] == sum(
        len(case["validation_cycle"]["planning"]["selected_validation_plan"]["assertion_labels"])
        for case in cases
    )
    assert summary["validation_backlog_queued_item_count"] > 0
    assert (
        summary["validation_backlog_fulfilled_item_count"]
        == summary["validation_backlog_queued_item_count"]
    )
    assert summary["validation_backlog_followthrough_case_count"] > 0
    assert summary["validation_backlog_unfulfilled_open_case_count"] == 0
    assert summary["validation_backlog_terminal_open_item_count"] == 0
    assert set(summary["validation_backlog_statuses"]) == {
        "cold_start",
        "fulfilled_prior_backlog",
        "no_open_prior_backlog",
    }
    assert set(summary["validation_backlog_axes"]) == {
        "order_profile_variant",
        "policy_candidate_variant",
    }
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
    assert summary["future_blind_window_admission_status"] == "passed"
    assert summary["future_blind_window_admission_candidate_count"] > DEFAULT_CASE_COUNT
    assert summary["future_blind_window_admitted_without_future_count"] > (
        summary["future_blind_selected_second_holdout_improvement_window_count"]
    )
    assert summary["future_blind_second_holdout_rejected_count"] > 0
    assert summary["future_blind_selected_second_holdout_improvement_window_count"] >= DEFAULT_CASE_COUNT
    assert summary["future_blind_window_admission_uses_future_holdout"] is False
    assert summary["no_leakage_holdout_count"] == DEFAULT_CASE_COUNT
    assert summary["no_leakage_temporal_protocol_count"] == DEFAULT_CASE_COUNT
    assert summary["no_leakage_temporal_protocol_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["no_leakage_blind_admission_precommit_count"] == (
        DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    )
    assert summary["strict_oos_evolution_proof_count"] == DEFAULT_CASE_COUNT
    assert summary["strict_oos_evolution_proof_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["strict_oos_blind_admission_precommit_binding_count"] == (
        DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    )
    assert summary["strict_oos_evolution_count"] == DEFAULT_CASE_COUNT
    assert summary["blind_future_oos_audit_count"] == DEFAULT_CASE_COUNT
    assert summary["blind_future_oos_audit_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["blind_future_oos_followup_drives_persona_count"] == DEFAULT_CASE_COUNT
    assert summary["blind_future_oos_verdict_improved_count"] > 0
    assert summary["blind_future_oos_verdict_regressed_count"] > 0
    assert (
        summary["blind_future_oos_verdict_improved_count"]
        + summary["blind_future_oos_verdict_regressed_count"]
        == DEFAULT_CASE_COUNT
    )
    assert summary["portfolio_trade_generation_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["portfolio_trade_generation_fill_count"] == DEFAULT_CASE_COUNT * GENERATION_COUNT
    assert summary["memory_retrieval_drives_next_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["memory_counterfactual_proof_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["memory_counterfactual_proof_pass_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["memory_counterfactual_retrieved_material_count"] == (
        DEFAULT_CASE_COUNT * 2 - summary["persona_count"]
    )
    assert summary["memory_counterfactual_cold_start_count"] == summary["persona_count"]
    assert summary["memory_counterfactual_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["institutional_memory_lineage_count"] == DEFAULT_CASE_COUNT
    assert summary["institutional_memory_lineage_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["institutional_memory_lineage_cold_start_count"] == 1
    assert summary["institutional_memory_lineage_applied_count"] == DEFAULT_CASE_COUNT - 1
    assert summary["institutional_memory_lineage_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["cross_persona_institutional_memory_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["intra_case_memory_influence_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_case_memory_influence_count"] == DEFAULT_CASE_COUNT - summary["persona_count"]
    assert summary["multi_oss_feedback_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_candidate_materiality_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_candidate_materiality_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_candidate_materiality_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["policy_candidate_oss_materiality_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_artifact_materiality_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_artifact_materiality_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_artifact_materiality_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["reflection_artifact_oss_materiality_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_oss_closed_loop_proof_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_oss_closed_loop_proof_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_oss_closed_loop_role_binding_count"] == DEFAULT_CASE_COUNT * 8
    assert summary["multi_oss_closed_loop_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    expected_oss_role_components = sorted(
        f"{role}:{component}"
        for role, components in OSS_ROLE_COMPONENT_MATRIX.items()
        for component in components
    )
    assert summary["multi_oss_closed_loop_role_component_matrix_expected_count"] == len(
        expected_oss_role_components
    )
    assert summary["multi_oss_closed_loop_role_component_matrix_observed_count"] == len(
        expected_oss_role_components
    )
    assert summary["multi_oss_closed_loop_role_component_matrix_missing_count"] == 0
    assert summary["multi_oss_closed_loop_role_component_matrix_unexpected_count"] == 0
    assert summary["multi_oss_closed_loop_role_component_min_case_count"] > 0
    assert summary["multi_oss_closed_loop_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_oss_ooda_ledger_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_oss_ooda_ledger_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_oss_ooda_ledger_event_count"] == DEFAULT_CASE_COUNT * 22
    assert summary["persona_oss_ooda_ledger_handoff_event_count"] == DEFAULT_CASE_COUNT
    assert summary["persona_oss_ooda_causality_replay_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_cycle_carryover_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_cycle_carryover_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["cross_cycle_runtime_feedback_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["cross_cycle_runtime_feedback_cold_start_count"] == summary["persona_count"]
    assert summary["cross_cycle_carryover_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["cross_cycle_runtime_feedback_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_state_carryover_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_state_carryover_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_state_carryover_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["portfolio_state_carryover_cold_start_count"] == summary["persona_count"]
    assert summary["portfolio_state_carryover_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["portfolio_state_carryover_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["portfolio_state_carryover_position_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    ) * PORTFOLIO_LEG_COUNT
    assert summary["broker_adapter_carryover_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_carryover_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_carryover_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["broker_adapter_carryover_cold_start_count"] == summary["persona_count"]
    assert summary["broker_adapter_carryover_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["broker_adapter_carryover_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["broker_adapter_carryover_prior_followup_ref_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["openclaw_session_continuity_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_continuity_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_continuity_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["openclaw_session_continuity_cold_start_count"] == summary["persona_count"]
    assert summary["openclaw_session_continuity_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["openclaw_session_continuity_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_continuity_prior_session_ref_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["persisted_cycle_resume_count"] == DEFAULT_CASE_COUNT
    assert summary["persisted_cycle_resume_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["persisted_cycle_resume_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"]
    )
    assert summary["persisted_cycle_resume_cold_start_count"] == summary["persona_count"]
    assert summary["persisted_cycle_resume_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["persisted_cycle_resume_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_cycle_lineage_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_cycle_lineage_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_cycle_lineage_cold_start_count"] == summary["persona_count"]
    assert summary["multi_cycle_lineage_single_prior_count"] == summary["persona_count"]
    assert summary["multi_cycle_lineage_applied_count"] == (
        DEFAULT_CASE_COUNT - summary["persona_count"] * 2
    )
    assert summary["multi_cycle_lineage_trace_binding_count"] == DEFAULT_CASE_COUNT * 2
    assert summary["multi_cycle_lineage_drives_next_case_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_response_followup_loop_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_disagreement_arbitration_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_disagreement_arbitration_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_oss_disagreement_arbitrated_count"] == DEFAULT_CASE_COUNT
    assert summary["tracking_reconciliation_count"] == DEFAULT_CASE_COUNT
    assert summary["tracking_reconciliation_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["tracking_reconciliation_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_drives_decision_count"] == DEFAULT_CASE_COUNT
    assert summary["degraded_oss_response_repair_count"] == DEFAULT_CASE_COUNT
    assert summary["degraded_oss_response_repair_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["degraded_oss_response_repair_drives_decision_count"] == DEFAULT_CASE_COUNT
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
    assert summary["multi_persona_proposal_lineage_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_persona_proposal_lineage_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["multi_persona_proposal_lineage_proposal_count"] == DEFAULT_CASE_COUNT * 4
    assert summary["multi_persona_proposal_lineage_drives_runtime_count"] == DEFAULT_CASE_COUNT
    assert summary["restart_recovery_count"] == DEFAULT_CASE_COUNT
    assert summary["autonomous_scheduler_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_engine_replay_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_object_store_packet_readback_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_object_store_packet_readback_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_object_store_packet_readback_target_count"] == DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    assert summary["lean_object_store_loaded_signal_from_packet_target_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_object_store_all_target_signal_readback_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_object_store_packet_target_execution_count"] == (
        DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    )
    assert summary["lean_object_store_packet_target_execution_pass_count"] == (
        DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    )
    assert summary["lean_object_store_all_packet_targets_executed_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_packet_execution_projection_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_packet_execution_projection_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_packet_execution_projection_leg_count"] == DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    assert summary["lean_packet_execution_projection_order_count"] == DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    assert summary["lean_packet_execution_projection_fill_count"] == DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    assert summary["lean_packet_execution_projection_readback_count"] == DEFAULT_CASE_COUNT * PORTFOLIO_LEG_COUNT
    assert summary["lean_packet_execution_projection_replayed_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_consumed_execution_projection_count"] == DEFAULT_CASE_COUNT
    assert summary["lean_runtime_feedback_drives_ooda_count"] == DEFAULT_CASE_COUNT
    assert summary["experiment_tracking_lineage_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["experiment_tracking_lineage_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["experiment_tracking_lineage_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_oss_lineage_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_oss_lineage_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["policy_oss_lineage_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_oss_lineage_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_oss_lineage_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["reflection_oss_lineage_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["risk_analytics_lineage_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["risk_analytics_lineage_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["risk_analytics_lineage_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["openclaw_session_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["alpha_seed_revision_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_quality_repair_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_quality_repair_handoff_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["oss_quality_repair_handoff_drives_lean_count"] == DEFAULT_CASE_COUNT
    assert summary["evolved_strategy_packet_proof_count"] == DEFAULT_CASE_COUNT
    assert summary["evolved_strategy_packet_proof_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["evolved_strategy_packet_handoff_count"] == DEFAULT_CASE_COUNT
    assert summary["scheduler_conflict_ooda_proof_count"] == DEFAULT_CASE_COUNT
    assert summary["scheduler_conflict_ooda_proof_pass_count"] == DEFAULT_CASE_COUNT
    assert summary["scheduler_conflict_ooda_event_count"] == DEFAULT_CASE_COUNT * 6
    assert summary["scheduler_conflict_ooda_dispatch_count"] == DEFAULT_CASE_COUNT
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
    assert coverage["multi_persona_proposal_lineage_models"] == [
        PERSONA_MULTI_PERSONA_PROPOSAL_LINEAGE_MODEL_ID
    ]
    assert coverage["multi_persona_proposal_roles"] == [
        "alpha_sponsor",
        "execution",
        "macro",
        "risk",
    ]
    assert {"p-risk-analyst", "p-execution-lead", "p-macro-observer"}.issubset(
        set(coverage["multi_persona_proposal_persona_ids"])
    )
    assert set(coverage["multi_persona_proposal_source_roles"]) == {
        "alpha_model",
        "backtest",
        "handoff",
        "policy_candidate",
        "reflection_artifact",
        "risk_analytics",
        "session",
        "tracker",
    }
    assert set(coverage["multi_persona_proposal_conflict_axes"]) == {
        "capital_budget_pct",
        "direction_by_instrument",
        "execution_constraints",
        "weight_by_instrument",
    }
    assert set(coverage["multi_persona_proposal_lineage_replay_flags"]) == {
        "all_proposals_have_refs_and_hashes",
        "each_proposal_cites_selected_action",
        "execution_proposal_cites_handoff_and_tracking",
        "macro_proposal_can_disagree_on_direction",
        "proposal_personas_distinct",
        "proposal_refs_unique",
        "risk_proposal_cites_risk_analytics",
    }
    assert set(coverage["scheduler_phases"]) == set(AUTONOMOUS_SCHEDULER_PHASES)
    assert coverage["lean_engine_replay_models"] == [LEAN_ENGINE_REPLAY_MODEL_ID]
    assert coverage["lean_engine_algorithm_modules"] == ["pantheon_algo.smoke_loader_test"]
    assert coverage["lean_object_store_packet_readback_models"] == [
        LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID
    ]
    assert coverage["lean_object_store_packet_readback_target_counts"] == [PORTFOLIO_LEG_COUNT]
    assert coverage["lean_object_store_packet_readback_loaded_signal_sources"] == [
        "all_packet_targets",
        "first_packet_target",
    ]
    assert set(coverage["lean_object_store_packet_readback_replay_flags"]) == {
        "algorithm_executed_loaded_packet_signal",
        "all_targets_bind_alpha_seed_revision_handoff",
        "all_targets_bind_strategy_packet_ref",
        "all_targets_have_signals",
        "loaded_signal_from_first_packet_target",
        "loaded_signal_quantity_matches_first_target",
        "loaded_signal_symbol_matches_first_target",
        "all_packet_target_signals_loaded_from_object_store",
        "loaded_signal_refs_match_packet_targets",
        "loaded_signal_symbols_match_packet_targets",
        "loaded_signal_quantities_match_packet_targets",
        "loaded_signal_metadata_binds_packet_targets",
        "all_packet_targets_executed_by_smoke",
        "executed_packet_target_refs_match_packet",
        "executed_packet_target_signal_ids_match_packet",
        "executed_packet_target_symbols_match_packet",
        "object_store_keys_include_packet_artifact_and_metadata",
        "packet_hash_matches_persona_packet",
        "packet_present_in_object_store_artifact",
        "packet_ref_matches_case_strategy_packet",
        "paper_only_guard_retained",
        "replayable",
        "target_count_matches_portfolio",
        "target_refs_unique",
        "loaded_packet_preserves_tracking_provenance",
        "loaded_tracking_ref_matches_packet",
        "all_targets_bind_policy_oss_lineage",
        "all_targets_bind_reflection_oss_lineage",
        "all_targets_bind_risk_analytics_lineage",
        "all_targets_bind_oss_quality_repair_lineage",
        "all_targets_bind_multi_persona_proposal_lineage",
        "loaded_packet_preserves_policy_oss_lineage",
        "loaded_packet_preserves_reflection_oss_lineage",
        "loaded_packet_preserves_risk_analytics_lineage",
        "loaded_packet_preserves_oss_quality_repair_lineage",
        "loaded_packet_preserves_multi_persona_proposal_lineage",
        "loaded_packet_preserves_alpha_seed_revision_handoff",
        "loaded_alpha_seed_revision_ref_matches_packet",
        "loaded_policy_oss_ref_matches_packet",
        "loaded_reflection_oss_ref_matches_packet",
        "loaded_risk_analytics_ref_matches_packet",
        "loaded_oss_quality_repair_ref_matches_packet",
        "loaded_multi_persona_proposal_ref_matches_packet",
        "policy_oss_lineage_present_in_packet",
        "reflection_oss_lineage_present_in_packet",
        "risk_analytics_lineage_present_in_packet",
        "oss_quality_repair_lineage_present_in_packet",
        "multi_persona_proposal_lineage_present_in_packet",
        "alpha_seed_revision_handoff_present_in_packet",
        "tracking_provenance_present_in_packet",
    }
    assert coverage["lean_packet_execution_projection_models"] == [
        LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID
    ]
    assert coverage["lean_packet_execution_projection_generations"] == [2]
    assert coverage["lean_packet_execution_projection_leg_counts"] == [PORTFOLIO_LEG_COUNT]
    assert coverage["lean_packet_execution_projection_event_chains"] == [
        "packet_leg_target->lean_target_order->paper_fill_readback"
    ]
    assert set(coverage["lean_packet_execution_projection_lean_calls"]) == {
        "LimitOrder",
        "MarketOrder",
        "SetHoldings",
    }
    assert set(coverage["lean_packet_execution_projection_quantity_types"]) == set(QUANTITY_TYPES)
    assert set(coverage["lean_packet_execution_projection_order_types"]) == set(ORDER_TYPES)
    assert set(coverage["lean_packet_execution_projection_replay_flags"]) == {
        "all_broker_orders_have_fill_readbacks",
        "all_fill_events_bind_signal_metadata",
        "all_lean_targets_have_broker_orders",
        "all_leg_capital_within_budget",
        "all_leg_directions_match_policy_and_allocation",
        "all_leg_expected_quantities_replay_signal_payload",
        "all_leg_market_friction_notional_bound",
        "all_leg_weights_match_handoff_allocation",
        "all_packet_instruments_have_policy_legs",
        "handoff_allocation_bound",
        "paper_only_guard_retained",
        "projection_ready_for_runtime_feedback",
        "replayable",
        "strategy_packet_generation2_bound",
        "strategy_packet_ref_bound",
    }
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
        "alpha_seed_revision_handoff_bound",
        "case_runtime_refs_bound",
        "drives_persona_next_ooda_step",
        "evolved_strategy_packet_refs_bound",
        "experiment_tracking_lineage_bound",
        "fills_drive_next_ooda",
        "handoff_packet_consumed",
        "lean_packet_execution_projection_consumed",
        "multi_persona_proposal_lineage_bound",
        "next_cycle_scheduled",
        "object_store_readback_verified",
        "oss_quality_repair_lineage_bound",
        "openclaw_session_context_bound",
        "openclaw_session_continuity_bound",
        "paper_runtime_guard_retained",
        "portfolio_state_carryover_bound",
        "broker_adapter_carryover_bound",
        "policy_oss_lineage_bound",
        "reflection_oss_lineage_bound",
        "risk_analytics_lineage_bound",
        "runtime_binding_readback_verified",
        "runtime_feedback_consumed",
    }
    assert coverage["experiment_tracking_lineage_handoff_models"] == [
        PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID
    ]
    assert coverage["experiment_tracking_lineage_handoff_backends"] == ["mlflow", "wandb"]
    assert set(coverage["experiment_tracking_lineage_handoff_replay_flags"]) == {
        "evolution_decision_cites_reconciliation",
        "evolution_decision_metadata_carries_experiment_ref",
        "handoff_runtime_bundle_contains_repaired_tracking_refs",
        "lineage_hash_stable_across_packet_handoff_readback",
        "object_store_readback_preserves_tracking_provenance",
        "replayable",
        "runtime_feedback_cites_repaired_tracking_refs",
        "strategy_packet_carries_tracking_provenance",
        "tracker_readback_reconciled",
    }
    assert coverage["policy_oss_lineage_handoff_models"] == [
        PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID
    ]
    assert set(coverage["policy_oss_lineage_handoff_components"]) == {
        "finrl",
        "ray_tune",
        "rllib",
    }
    assert set(coverage["policy_oss_lineage_handoff_artifact_families"]) == {
        "optimizer_result",
        "rl_policy",
    }
    assert set(coverage["policy_oss_lineage_handoff_replay_flags"]) == {
        "all_packet_targets_bind_policy_oss_lineage",
        "evolved_policy_carries_policy_oss_lineage",
        "handoff_runtime_bundle_contains_policy_oss_refs",
        "lineage_hash_stable_across_policy_packet_readback_handoff",
        "materiality_source_matches_policy_lineage",
        "object_store_readback_preserves_policy_oss_lineage",
        "policy_candidate_materiality_passed",
        "replayable",
        "runtime_feedback_cites_policy_oss_lineage",
        "strategy_packet_carries_policy_oss_lineage",
    }
    assert coverage["reflection_oss_lineage_handoff_models"] == [
        PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID
    ]
    assert set(coverage["reflection_oss_lineage_handoff_components"]) == {
        "dspy",
        "imitation",
        "trl",
    }
    assert set(coverage["reflection_oss_lineage_handoff_artifact_families"]) == {
        "imitation_policy",
        "model_artifact",
        "prompt_bundle",
    }
    assert set(coverage["reflection_oss_lineage_handoff_replay_flags"]) == {
        "all_packet_targets_bind_reflection_oss_lineage",
        "evolved_policy_carries_reflection_oss_lineage",
        "handoff_runtime_bundle_contains_reflection_oss_refs",
        "lineage_hash_stable_across_reflection_policy_packet_readback_handoff",
        "materiality_source_matches_reflection_lineage",
        "object_store_readback_preserves_reflection_oss_lineage",
        "reflection_artifact_materiality_passed",
        "replayable",
        "runtime_feedback_cites_reflection_oss_lineage",
        "strategy_packet_carries_reflection_oss_lineage",
    }
    assert coverage["risk_analytics_lineage_handoff_models"] == [
        PERSONA_RISK_ANALYTICS_LINEAGE_HANDOFF_MODEL_ID
    ]
    assert set(coverage["risk_analytics_lineage_handoff_components"]) == {
        "quantlib",
        "statsmodels",
    }
    assert set(coverage["risk_analytics_lineage_handoff_artifact_families"]) == {
        "pricing_report",
        "regime_report",
    }
    assert set(coverage["risk_analytics_lineage_handoff_replay_flags"]) == {
        "all_packet_targets_bind_risk_analytics_lineage",
        "evolved_policy_carries_risk_analytics_lineage",
        "handoff_runtime_bundle_contains_risk_analytics_refs",
        "lineage_hash_stable_across_risk_policy_packet_readback_handoff",
        "object_store_readback_preserves_risk_analytics_lineage",
        "replayable",
        "risk_analytics_role_completed",
        "risk_evaluator_consumes_risk_analytics",
        "runtime_feedback_cites_risk_analytics_lineage",
        "runtime_feedback_state_binds_risk_analytics_lineage",
        "selected_candidates_cite_risk_analytics",
        "strategy_packet_carries_risk_analytics_lineage",
    }
    assert coverage["openclaw_session_handoff_models"] == [
        PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID
    ]
    assert coverage["openclaw_session_handoff_components"] == ["openclaw"]
    assert coverage["openclaw_session_handoff_artifact_families"] == ["openclaw_session"]
    assert coverage["openclaw_session_handoff_session_states"] == ["active"]
    assert set(coverage["openclaw_session_handoff_replay_flags"]) == {
        "handoff_carries_openclaw_session_context",
        "handoff_runtime_bundle_contains_openclaw_session_refs",
        "openclaw_context_hash_stable_across_handoff",
        "openclaw_session_response_completed",
        "persona_reasoning_consumes_openclaw_session_source",
        "replayable",
        "runtime_feedback_cites_openclaw_session",
        "runtime_feedback_state_binds_openclaw_session",
        "selected_candidates_cite_openclaw_session_followup",
    }
    assert coverage["openclaw_session_continuity_models"] == [
        PERSONA_OPENCLAW_SESSION_CONTINUITY_MODEL_ID
    ]
    assert set(coverage["openclaw_session_continuity_statuses"]) == {
        "applied",
        "cold_start",
    }
    assert set(coverage["openclaw_session_continuity_request_actions"]) == {
        "continue_prior_openclaw_session",
        "start_openclaw_session",
    }
    assert set(coverage["openclaw_session_continuity_replay_flags"]) == {
        "candidate_generation_consumes_session_continuity",
        "cold_start_or_previous_session_bound",
        "current_openclaw_session_response_completed",
        "handoff_carries_session_continuity",
        "reasoning_consumes_session_continuity",
        "replayable",
        "runtime_feedback_binds_session_continuity",
        "same_persona_session_continuity",
        "selected_candidate_cites_session_continuity",
    }
    assert coverage["alpha_seed_revision_handoff_models"] == [
        PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
    ]
    assert set(coverage["alpha_seed_revision_handoff_components"]) == {"qlib", "vectorbt"}
    assert set(coverage["alpha_seed_revision_handoff_actions"]) == set(
        ALPHA_SEED_REVISION_ACTION_BY_COMPONENT.values()
    )
    assert set(coverage["alpha_seed_revision_handoff_replay_flags"]) == {
        "all_packet_targets_bind_alpha_seed_revision_handoff",
        "alpha_seed_revision_applied",
        "candidate_generation_consumes_alpha_seed_revision",
        "handoff_carries_alpha_seed_revision_context",
        "handoff_runtime_bundle_contains_alpha_seed_revision_refs",
        "lineage_hash_stable_across_packet_readback_handoff",
        "object_store_readback_preserves_alpha_seed_revision_handoff",
        "persona_reasoning_consumes_alpha_seed_revision",
        "replayable",
        "runtime_feedback_cites_alpha_seed_revision_handoff",
        "runtime_feedback_state_binds_alpha_seed_revision_handoff",
        "selected_candidates_cite_alpha_seed_revision",
        "strategy_packet_carries_alpha_seed_revision_handoff",
    }
    assert coverage["oss_quality_repair_handoff_models"] == [
        PERSONA_OSS_QUALITY_REPAIR_HANDOFF_MODEL_ID
    ]
    assert coverage["oss_quality_repair_handoff_roles"] == sorted(OSS_QUALITY_ROLES)
    assert set(coverage["oss_quality_repair_handoff_components"]) == set(OSS_REQUIRED_COMPONENTS)
    assert set(coverage["oss_quality_repair_handoff_issue_types"]) == set(
        OSS_QUALITY_ISSUE_BY_ROLE.values()
    )
    assert set(coverage["oss_quality_repair_handoff_actions"]) == set(
        OSS_QUALITY_REPAIR_ACTION_BY_ROLE.values()
    )
    assert set(coverage["oss_quality_repair_handoff_downweighted_actions"]) == set(
        OSS_QUALITY_AFFECTED_ACTION_BY_ROLE.values()
    )
    assert set(coverage["oss_quality_repair_handoff_replay_flags"]) == {
        "all_packet_targets_bind_quality_repair_lineage",
        "candidate_generation_consumes_quality_repair",
        "evolved_policy_carries_quality_repair_lineage",
        "handoff_runtime_bundle_contains_quality_repair_refs",
        "lineage_hash_stable_across_quality_policy_packet_readback_handoff",
        "object_store_readback_preserves_quality_repair_lineage",
        "persona_reasoning_consumes_quality_repair",
        "replayable",
        "runtime_feedback_cites_quality_repair_lineage",
        "runtime_feedback_state_binds_quality_repair_lineage",
        "scorer_applies_quality_repair_adjustment_and_penalty",
        "selected_candidates_cite_quality_repair",
        "source_degraded_response_repaired",
        "strategy_packet_carries_quality_repair_lineage",
    }
    assert coverage["evolved_strategy_packet_models"] == [
        LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID
    ]
    assert coverage["evolved_strategy_packet_generations"] == [2]
    assert coverage["evolved_strategy_packet_source_to_validation_paths"] == [
        "holdout:future_holdout"
    ]
    assert set(coverage["evolved_strategy_packet_replay_flags"]) == {
        "execution_projection_consumes_packet_legs_and_orders",
        "handoff_consumes_same_packet",
        "handoff_runtime_bundle_contains_packet_and_proofs",
        "lean_engine_replay_reads_same_packet",
        "packet_binds_evolution_trajectory",
        "packet_binds_no_leakage_protocol",
        "packet_binds_strict_oos_proof",
        "paper_only_guard_retained",
        "replayable",
        "runtime_feedback_consumes_handoff_with_packet",
        "strategy_packet_declares_future_holdout_validation",
        "strategy_packet_is_generation2",
        "strict_oos_generation2_step_matches_packet",
    }
    assert coverage["scheduler_conflict_ooda_models"] == [
        PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID
    ]
    assert set(coverage["scheduler_conflict_ooda_event_types"]) == {
        "broker_adapter_followup",
        "lean_handoff_materialization",
        "lean_runtime_feedback",
        "multi_persona_conflict_resolution",
        "scheduler_next_cycle_dispatch",
        "scheduler_recovery_tick",
    }
    assert set(coverage["scheduler_conflict_ooda_phases"]) == set(AUTONOMOUS_SCHEDULER_PHASES)
    assert set(coverage["scheduler_conflict_ooda_next_ooda_steps"]) == {
        "act",
        "decide",
        "observe",
        "orient",
    }
    assert set(coverage["scheduler_conflict_ooda_next_scheduler_phases"]) == {
        "evolve",
        "reflect",
    }
    assert set(coverage["scheduler_conflict_ooda_replay_flags"]) == {
        "adapter_followup_consumes_schedule",
        "conflict_resolution_consumes_selected_action_and_risk",
        "dispatch_events_strictly_ordered",
        "handoff_consumes_conflict_resolution",
        "handoff_consumes_scheduler_ref",
        "lean_runtime_feedback_consumes_schedule",
        "next_dispatch_consumes_adapter_and_runtime_feedback",
        "no_future_dispatch_ref",
        "paper_only_guard_retained",
        "replayable",
        "resolved_allocation_is_portfolio_complete",
        "runtime_ooda_step_maps_to_scheduler_phase",
        "scheduler_phase_due_times_ordered",
        "scheduler_phase_order_valid",
        "scheduler_recovered_restart_checkpoint",
    }
    assert coverage["cross_cycle_carryover_models"] == [
        PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID
    ]
    assert set(coverage["cross_cycle_carryover_statuses"]) == {"applied", "cold_start"}
    assert set(coverage["cross_cycle_carryover_next_ooda_steps"]) == {"act", "decide", "observe", "orient"}
    assert set(coverage["cross_cycle_carryover_score_adjusted_actions"]) == {
        "feedback-adapt",
        "risk-off",
    }
    assert set(coverage["cross_cycle_carryover_replay_flags"]) == {
        "candidate_generation_consumes_prior_runtime_feedback",
        "cold_start_or_prior_cycle_bound",
        "current_ooda_ledger_available",
        "no_future_current_case_artifact_used_as_prior",
        "previous_lean_handoff_ref_available",
        "previous_ooda_ledger_ref_available",
        "reasoning_consumes_prior_runtime_feedback",
        "replayable",
        "runtime_feedback_ref_available",
        "same_persona_cycle_carryover",
        "scorer_applies_cross_cycle_adjustment",
        "selected_candidate_cites_cross_cycle_state",
    }
    assert coverage["portfolio_state_carryover_models"] == [
        PERSONA_PORTFOLIO_STATE_CARRYOVER_MODEL_ID
    ]
    assert set(coverage["portfolio_state_carryover_statuses"]) == {"applied", "cold_start"}
    portfolio_state_rebalance_actions = set(coverage["portfolio_state_carryover_rebalance_actions"])
    assert {
        "carry_prior_gross_exposure_budget_to_new_portfolio",
        "cold_start_no_prior_positions",
    }.issubset(portfolio_state_rebalance_actions)
    assert portfolio_state_rebalance_actions.issubset({
        "carry_prior_gross_exposure_budget_to_new_portfolio",
        "cold_start_no_prior_positions",
        "rebalance_overlapping_positions_with_carried_exposure",
    })
    assert set(coverage["portfolio_state_carryover_score_adjusted_actions"]) == {
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(coverage["portfolio_state_carryover_replay_flags"]) == {
        "candidate_generation_consumes_prior_positions",
        "cold_start_or_prior_portfolio_state_bound",
        "decision_artifact_replays_portfolio_state",
        "lean_handoff_carries_portfolio_state_ref",
        "prior_positions_available",
        "projection_uses_adjusted_allocation",
        "reasoning_consumes_prior_positions",
        "replayable",
        "resolved_allocation_uses_carried_state",
        "runtime_feedback_binds_portfolio_state",
        "same_persona_portfolio_state",
        "scorecard_replays_portfolio_state_adjustment",
        "scorer_applies_portfolio_state_adjustment",
        "selected_candidate_cites_prior_positions",
    }
    assert coverage["broker_adapter_carryover_models"] == [
        PERSONA_BROKER_ADAPTER_CARRYOVER_MODEL_ID
    ]
    assert set(coverage["broker_adapter_carryover_statuses"]) == {"applied", "cold_start"}
    assert set(coverage["broker_adapter_carryover_actions"]) == set(
        BROKER_ADAPTER_FOLLOWUP_ACTIONS_BY_SCENARIO.values()
    )
    assert set(coverage["broker_adapter_carryover_action_families"]) == {
        "cancel_replace_recovery",
        "limit_repricing",
        "liquidity_sizing",
        "position_reconciliation",
        "risk_control",
    }
    assert coverage["broker_adapter_carryover_next_steps"] == [
        "execution_feedback_review"
    ]
    assert set(coverage["broker_adapter_carryover_score_adjusted_actions"]) == {
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(coverage["broker_adapter_carryover_replay_flags"]) == {
        "candidate_generation_consumes_previous_adapter_response",
        "cold_start_or_previous_adapter_response_bound",
        "conflict_resolution_carries_broker_adapter_response",
        "decision_artifact_replays_broker_adapter_carryover",
        "lean_handoff_carries_broker_adapter_response",
        "reasoning_consumes_previous_adapter_response",
        "replayable",
        "runtime_feedback_binds_broker_adapter_response",
        "same_persona_adapter_response_carryover",
        "scorecard_replays_broker_adapter_adjustment",
        "scorer_applies_broker_adapter_adjustment",
        "selected_candidate_cites_previous_adapter_response",
    }
    assert coverage["persisted_cycle_resume_models"] == [
        PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID
    ]
    assert set(coverage["persisted_cycle_resume_statuses"]) == {"applied", "cold_start"}
    assert coverage["persisted_cycle_resume_steps"] == ["execute_generation2_future_holdout"]
    assert set(coverage["persisted_cycle_resume_next_scheduler_phases"]) == {"evolve", "reflect"}
    assert set(coverage["persisted_cycle_resume_score_adjusted_actions"]) == {
        "feedback-adapt",
        "risk-off",
    }
    assert set(coverage["persisted_cycle_resume_replay_flags"]) == {
        "candidate_generation_consumes_persisted_resume_refs",
        "cold_start_or_persisted_state_bound",
        "cross_cycle_runtime_feedback_bound",
        "no_future_current_case_artifact_used_as_prior",
        "prior_autonomous_schedule_available",
        "prior_restart_checkpoint_available",
        "prior_runtime_object_store_readback_available",
        "reasoning_consumes_persisted_resume_refs",
        "replayable",
        "same_persona_resume_carryover",
        "scheduler_feedback_due_at_preserved",
        "scorer_applies_after_resume_adjustment",
        "selected_candidate_cites_persisted_resume_refs",
    }
    assert coverage["multi_cycle_lineage_models"] == [
        PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID
    ]
    assert set(coverage["multi_cycle_lineage_statuses"]) == {
        "cold_start",
        "lineage_applied",
        "single_prior",
    }
    assert coverage["multi_cycle_lineage_depths"] == [0, 1, 2]
    assert set(coverage["multi_cycle_lineage_trend_signals"]) == {
        "cold_start_no_prior_cycle",
        "latest_runtime_feedback_supersedes_older_cycle_trend",
        "single_prior_runtime_feedback_bootstraps_lineage",
    }
    assert set(coverage["multi_cycle_lineage_score_adjusted_actions"]) == {
        "feedback-adapt",
        "risk-off",
    }
    assert set(coverage["multi_cycle_lineage_replay_flags"]) == {
        "candidate_generation_consumes_lineage_refs",
        "cold_start_or_lineage_bound",
        "cross_cycle_runtime_feedback_bound",
        "decision_artifact_replays_lineage",
        "latest_prior_cycle_bound",
        "lineage_depth_matches_history",
        "no_future_current_case_artifact_used_as_prior",
        "older_prior_cycle_bound",
        "persisted_resume_bound",
        "reasoning_consumes_lineage_refs",
        "replayable",
        "same_persona_lineage",
        "scorer_applies_lineage_adjustment",
        "selected_candidate_cites_lineage_refs",
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
    assert coverage["policy_candidate_materiality_models"] == [
        PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID
    ]
    assert set(coverage["policy_candidate_materiality_components"]) == {
        "finrl",
        "ray_tune",
        "rllib",
    }
    assert set(coverage["policy_candidate_materiality_artifact_families"]) == {
        "optimizer_result",
        "rl_policy",
    }
    assert {
        "best_trial_score",
        "eval_reward_mean",
        "mean_reward_proxy",
        "sharpe",
        "validation_sharpe_proxy",
    }.issubset(set(coverage["policy_candidate_materiality_metric_signal_keys"]))
    assert set(coverage["policy_candidate_materiality_replay_flags"]) == {
        "artifact_family_matches_component",
        "candidate_generation_consumes_policy_oss",
        "component_is_policy_learning_oss",
        "decision_artifact_replays_policy_materiality",
        "evolved_policy_lineage_bound",
        "feedback_scorecard_replays_policy_quality",
        "metrics_drive_nonzero_policy_quality",
        "no_holdout_or_future_leakage_in_policy_artifact",
        "policy_hint_risk_is_recomputed_from_oss_metrics",
        "policy_material_to_selected_score",
        "policy_oss_role_completed",
        "policy_quality_is_recomputed_from_oss_metrics",
        "reasoning_consumes_policy_oss",
        "registry_and_producer_bound",
        "replayable",
        "selected_candidate_cites_policy_oss",
        "selected_policy_uses_policy_hint_risk",
        "selected_scorecard_replays_policy_quality",
    }
    assert coverage["reflection_artifact_materiality_models"] == [
        PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID
    ]
    assert set(coverage["reflection_artifact_materiality_components"]) == {
        "dspy",
        "imitation",
        "trl",
    }
    assert set(coverage["reflection_artifact_materiality_artifact_families"]) == {
        "imitation_policy",
        "model_artifact",
        "prompt_bundle",
    }
    assert {
        "accuracy",
        "action_coverage_ratio",
        "intent_accuracy",
        "training_accuracy",
    }.issubset(set(coverage["reflection_artifact_materiality_metric_signal_keys"]))
    assert set(coverage["reflection_artifact_materiality_replay_flags"]) == {
        "artifact_family_matches_component",
        "candidate_generation_consumes_reflection_oss",
        "component_is_reflection_learning_oss",
        "contrarian_blueprint_consumes_reflection_role",
        "contrarian_candidate_cites_reflection_oss",
        "decision_artifact_replays_reflection_materiality",
        "feedback_blueprint_consumes_reflection_role",
        "feedback_scorecard_replays_reflection_quality",
        "metrics_drive_nonzero_reflection_quality",
        "no_holdout_or_future_leakage_in_reflection_artifact",
        "reasoning_consumes_reflection_oss",
        "reasoning_usage_replays_reflection_quality",
        "reflection_material_to_selected_score",
        "reflection_oss_role_completed",
        "registry_and_producer_bound",
        "replayable",
        "scorer_recomputes_reflection_quality",
        "selected_candidate_cites_reflection_oss",
        "selected_rationale_mentions_reflection",
        "selected_scorecard_replays_reflection_quality",
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
    assert coverage["multi_oss_closed_loop_models"] == [MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID]
    assert set(coverage["multi_oss_closed_loop_roles"]) == {
        "alpha_model",
        "backtest",
        "handoff",
        "policy_candidate",
        "reflection_artifact",
        "risk_analytics",
        "session",
        "tracker",
    }
    assert set(coverage["multi_oss_closed_loop_components"]) == set(OSS_REQUIRED_COMPONENTS)
    assert coverage["multi_oss_closed_loop_expected_role_components"] == expected_oss_role_components
    assert coverage["multi_oss_closed_loop_role_components"] == expected_oss_role_components
    assert coverage["multi_oss_closed_loop_missing_role_components"] == []
    assert coverage["multi_oss_closed_loop_unexpected_role_components"] == []
    assert set(coverage["multi_oss_closed_loop_role_component_case_counts"]) == set(
        expected_oss_role_components
    )
    assert all(
        count > 0
        for count in coverage["multi_oss_closed_loop_role_component_case_counts"].values()
    )
    assert set(coverage["multi_oss_closed_loop_candidate_actions"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(coverage["multi_oss_closed_loop_replay_flags"]) == {
        "all_followup_outputs_consumed_by_candidate_generation",
        "all_followup_outputs_used_by_both_generations",
        "all_followup_responses_completed",
        "all_followups_requested_after_oss_response",
        "all_oss_responses_completed",
        "all_required_roles_present",
        "all_role_components_match_matrix",
        "all_role_score_adjustments_available_to_scorer",
        "all_source_oss_refs_consumed_by_reasoning",
        "all_source_refs_bound_to_followup_requests",
        "feedback_adapt_path_receives_all_oss_feedback",
        "replayable",
        "selected_candidate_cites_all_followup_outputs",
        "selected_case_oss_roles_bound",
    }
    assert coverage["persona_oss_ooda_ledger_models"] == [PERSONA_OSS_OODA_LEDGER_MODEL_ID]
    assert set(coverage["persona_oss_ooda_ledger_phases"]) == {
        "act",
        "decide",
        "observe",
        "orient",
    }
    assert set(coverage["persona_oss_ooda_ledger_event_types"]) == {
        "candidate_generation",
        "candidate_scoring",
        "lean_handoff_packet",
        "oss_response",
        "persona_followup_response",
        "selected_action",
    }
    assert set(coverage["persona_oss_ooda_ledger_actors"]) == {
        "oss",
        "persona",
        "persona+lean_handoff",
        "persona+oss",
    }
    assert set(coverage["persona_oss_ooda_ledger_replay_flags"]) == {
        "actionable_oss_feedback_has_downstream_persona_action",
        "all_events_strictly_ordered",
        "all_followup_outputs_precede_candidate_generation",
        "all_ooda_phases_present",
        "all_oss_responses_precede_persona_followups",
        "all_persona_followups_emit_completed_outputs",
        "candidate_generation_precedes_scoring",
        "lean_handoff_consumes_selected_action",
        "ledger_replayable",
        "no_future_artifact_reference",
        "scoring_precedes_selected_action",
        "selected_action_precedes_lean_handoff",
    }
    assert coverage["oss_disagreement_arbitration_models"] == [
        PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID
    ]
    assert set(coverage["oss_disagreement_types"]) == set(OSS_DISAGREEMENT_TYPES_BY_SCENARIO.values())
    assert set(coverage["oss_disagreement_source_role_pairs"]) == {
        "+".join(source_roles)
        for source_roles in OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE.values()
    }
    assert set(coverage["oss_disagreement_resolution_actions"]) == set(
        OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE.values()
    )
    assert set(coverage["oss_disagreement_candidate_actions"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert coverage["tracking_reconciliation_models"] == [
        PERSONA_TRACKING_RECONCILIATION_MODEL_ID
    ]
    assert set(coverage["tracking_reconciliation_divergence_types"]) == set(
        TRACKING_DIVERGENCE_TYPES_BY_SCENARIO.values()
    )
    assert set(coverage["tracking_reconciliation_repair_actions"]) == set(
        TRACKING_RECONCILIATION_ACTION_BY_TYPE.values()
    )
    assert coverage["tracking_reconciliation_backends"] == ["mlflow", "wandb"]
    assert set(coverage["tracking_reconciliation_replay_flags"]) == {
        "divergence_detected",
        "feedback_adapt_gets_tracking_refs",
        "normalized_experiment_ref_available",
        "repair_action_selected",
        "replayable",
        "scorer_adjustment_available",
        "tracker_completed",
        "tracker_readback_found",
        "vectorbt_tracker_bound",
    }
    assert set(coverage["tracking_reconciliation_candidate_actions"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert coverage["alpha_seed_revision_models"] == [PERSONA_ALPHA_SEED_REVISION_MODEL_ID]
    assert set(coverage["alpha_seed_revision_components"]) == {"qlib", "vectorbt"}
    assert set(coverage["alpha_seed_revision_actions"]) == set(
        ALPHA_SEED_REVISION_ACTION_BY_COMPONENT.values()
    )
    assert set(coverage["alpha_seed_revision_replay_flags"]) == {
        "alpha_revision_generated",
        "downstream_backtest_bound",
        "downstream_tracker_bound",
        "feedback_adapt_gets_alpha_backtest_tracker_refs",
        "no_forbidden_window_sources",
        "policy_candidate_bound",
        "replayable",
        "scorer_adjustment_available",
        "source_alpha_completed",
    }
    assert set(coverage["alpha_seed_revision_candidate_actions"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert coverage["degraded_oss_response_models"] == [
        PERSONA_DEGRADED_OSS_RESPONSE_MODEL_ID
    ]
    assert coverage["degraded_oss_response_roles"] == sorted(OSS_QUALITY_ROLES)
    assert set(coverage["degraded_oss_response_components"]) == set(OSS_REQUIRED_COMPONENTS)
    assert set(coverage["degraded_oss_response_issue_types"]) == set(
        OSS_QUALITY_ISSUE_BY_ROLE.values()
    )
    assert set(coverage["degraded_oss_response_repair_actions"]) == set(
        OSS_QUALITY_REPAIR_ACTION_BY_ROLE.values()
    )
    assert set(coverage["degraded_oss_response_downweighted_actions"]) == set(
        OSS_QUALITY_AFFECTED_ACTION_BY_ROLE.values()
    )
    assert set(coverage["degraded_oss_response_replay_flags"]) == {
        "feedback_candidate_receives_repair_ref",
        "followup_loop_bound_to_quality_repair",
        "persona_repair_request_after_oss_response",
        "persona_repair_response_completed",
        "quality_issue_detected_after_oss_response",
        "repair_adjustment_available_to_scorer",
        "replayable",
        "source_oss_completed_but_degraded",
        "source_quality_downweighted_before_scoring",
        "tracking_reconciliation_bound_to_quality_repair",
    }
    assert coverage["agent_decision_artifact_models"] == [PERSONA_DECISION_ARTIFACT_MODEL_ID]
    assert coverage["agent_candidate_generator_models"] == [PERSONA_CANDIDATE_GENERATOR_MODEL_ID]
    assert coverage["agent_candidate_scorer_models"] == [PERSONA_CANDIDATE_SCORER_MODEL_ID]
    assert coverage["agent_risk_evaluator_models"] == [PERSONA_RISK_EVALUATOR_MODEL_ID]
    assert coverage["agent_decision_artifact_generations"] == [1, 2]
    assert coverage["agent_memory_influence_models"] == [PERSONA_MEMORY_INFLUENCE_MODEL_ID]
    assert coverage["agent_memory_influence_statuses"] == ["applied", "cold_start"]
    assert "feedback-adapt" in coverage["agent_memory_selected_action_hints"]
    assert coverage["agent_memory_counterfactual_models"] == [
        PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID
    ]
    assert coverage["agent_memory_counterfactual_outcomes"] == [
        "cold_start_declared",
        "memory_material_to_selected_score",
    ]
    assert set(coverage["agent_memory_counterfactual_replay_flags"]) == {
        "actual_selection_replayed",
        "candidate_request_includes_memory_when_retrieved",
        "cold_start_zero_memory_adjustments",
        "counterfactual_scores_recomputed",
        "memory_changes_selected_score_when_retrieved",
        "memory_improves_selected_margin_when_retrieved",
        "retrieved_memory_ref_bound",
        "replayable",
        "score_delta_equals_selected_memory_adjustment",
        "selected_action_matches_memory_hint_when_retrieved",
        "selected_candidate_cites_memory_when_retrieved",
    }
    assert coverage["institutional_memory_lineage_models"] == [
        PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID
    ]
    assert set(coverage["institutional_memory_lineage_statuses"]) == {
        "applied",
        "cold_start",
    }
    assert set(coverage["institutional_memory_lineage_score_adjusted_actions"]) == {
        "feedback-adapt",
        "risk-off",
    }
    assert set(coverage["institutional_memory_lineage_replay_flags"]) == {
        "candidate_generation_consumes_institutional_memory",
        "candidate_generation_consumes_institutional_source_evidence",
        "cold_start_or_cross_persona_entry_bound",
        "decision_artifact_replays_institutional_memory",
        "institutional_entry_ref_available",
        "private_persona_memory_not_reused_as_institutional_memory",
        "reasoning_consumes_institutional_memory",
        "reasoning_consumes_institutional_source_evidence",
        "replayable",
        "scorecard_replays_institutional_memory_adjustment",
        "scorer_applies_institutional_memory_adjustment",
        "selected_candidate_cites_institutional_memory",
        "selected_candidate_cites_institutional_source_evidence",
        "source_persona_differs_from_current_persona",
    }
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
    assert coverage["no_leakage_blind_admission_precommit_source_windows"] == [
        "observe->feedback->holdout"
    ]
    assert coverage["no_leakage_blind_admission_precommit_forbidden_windows"] == [
        "future_holdout"
    ]
    assert coverage["no_leakage_blind_admission_precommit_validation_windows"] == [
        "future_holdout"
    ]
    assert coverage["future_blind_window_admission_models"] == [
        FUTURE_BLIND_WINDOW_ADMISSION_MODEL_ID
    ]
    assert coverage["future_blind_window_admission_source_windows"] == [
        "observe->feedback->holdout"
    ]
    assert coverage["future_blind_window_admission_forbidden_windows"] == [
        "future_holdout"
    ]
    assert coverage["future_blind_window_admission_validation_windows"] == [
        "future_holdout"
    ]
    assert coverage["future_blind_window_admission_repair_actions"] == [
        "discard_failed_unseen_future_verdict_and_request_next_future_blind_candidate"
    ]
    assert set(coverage["future_blind_window_admission_replay_flags"]) == {
        "admission_uses_observe_feedback_holdout_only",
        "future_holdout_absent_from_admission",
        "future_holdout_evaluated_only_after_admission",
        "post_admission_failures_are_counted",
        "replayable",
        "second_holdout_selected_windows_strictly_improve",
        "selected_pool_covers_default_case_count",
    }
    assert coverage["strict_oos_evolution_proof_models"] == [STRICT_OOS_EVOLUTION_PROOF_MODEL_ID]
    assert coverage["strict_oos_evolution_source_to_validation_paths"] == [
        "feedback:holdout->holdout:future_holdout"
    ]
    assert set(coverage["strict_oos_evolution_replay_flags"]) == {
        "evolution_decision_executed",
        "evolution_trajectory_passed",
        "future_holdout_hidden_from_all_decisions",
        "generation1_uses_feedback_only_before_holdout",
        "generation2_uses_holdout_only_before_future_holdout",
        "holdout_and_future_holdout_disjoint",
        "no_leakage_protocol_passed",
        "replayable",
        "strict_improvement_on_each_unseen_window",
        "trajectory_agrees_with_oos_steps",
        "strict_proof_binds_blind_admission_precommits",
        "uses_two_distinct_validation_windows",
    }
    assert coverage["blind_future_oos_audit_models"] == [
        BLIND_FUTURE_OOS_AUDIT_MODEL_ID
    ]
    assert coverage["blind_future_oos_admission_source_windows"] == [
        "observe->feedback->holdout"
    ]
    assert coverage["blind_future_oos_forbidden_windows"] == ["future_holdout"]
    assert coverage["blind_future_oos_verdicts"] == ["improved", "regressed"]
    assert set(coverage["blind_future_oos_followup_actions"]) == {
        "promote_blind_holdout_hypothesis_to_paper_candidate",
        "quarantine_oracle_like_evolution_and_request_new_oss_evidence",
    }
    assert set(coverage["blind_future_oos_followup_ooda_steps"]) == {"decide", "orient"}
    assert set(coverage["blind_future_oos_replay_flags"]) == {
        "admission_uses_observe_feedback_holdout_only",
        "blind_verdict_records_pass_or_fail",
        "future_holdout_absent_from_admission",
        "future_verdict_emitted_after_blind_decision",
        "holdout_admission_improves_without_future",
        "persona_followup_action_matches_verdict",
        "replayable",
        "shadow_portfolio_has_three_legs",
        "strict_curated_proof_kept_separate_from_blind_admission",
    }

    plan_signatures: set[str] = set()
    combo_signatures: set[str] = set()
    portfolio_window_signatures: set[str] = set()
    validation_assertion_refs: set[str] = set()
    latest_case_by_persona: dict[str, dict] = {}
    case_history_by_persona: dict[str, list[dict]] = {}
    institutional_writes_by_id: dict[str, dict] = {}
    for case in cases:
        persona_case_history = list(case_history_by_persona.get(case["persona_id"], []))
        assert not case["case_id"].startswith("agent-usability-")
        _assert_unique_planned_validation_cycle(
            case,
            plan_signatures=plan_signatures,
            combo_signatures=combo_signatures,
            portfolio_window_signatures=portfolio_window_signatures,
            validation_assertion_refs=validation_assertion_refs,
        )
        _assert_portfolio_generations(case)
        _assert_agent_decision_traces_are_no_leakage(case)
        _assert_no_leakage_temporal_protocol(case)
        _assert_strict_oos_evolution_proof(case)
        _assert_blind_future_oos_audit(case)
        _assert_memory_and_oss_closed_loop(case)
        _assert_institutional_memory_lineage(case, institutional_writes_by_id)
        _assert_policy_candidate_materiality(case)
        _assert_reflection_artifact_materiality(case)
        _assert_multi_oss_closed_loop_proof(case)
        _assert_persona_oss_ooda_causal_ledger(case)
        _assert_cross_cycle_runtime_carryover(case, latest_case_by_persona)
        _assert_portfolio_state_carryover(case, latest_case_by_persona)
        _assert_broker_adapter_carryover(case, latest_case_by_persona)
        _assert_openclaw_session_continuity(case, latest_case_by_persona)
        _assert_persisted_cycle_resume_carryover(case, latest_case_by_persona)
        _assert_multi_cycle_lineage_carryover(case, persona_case_history)
        _assert_case_specific_upstream_artifacts(case)
        _assert_operational_context(case)
        _assert_experiment_tracking_lineage_handoff(case)
        _assert_policy_oss_lineage_handoff(case)
        _assert_reflection_oss_lineage_handoff(case)
        _assert_risk_analytics_lineage_handoff(case)
        _assert_openclaw_session_handoff(case)
        _assert_alpha_seed_revision_handoff(case)
        _assert_oss_quality_repair_handoff(case)
        _assert_lean_packet_execution_projection(case)
        _assert_evolved_strategy_packet_proof(case)
        _assert_scheduler_conflict_ooda_proof(case)
        _assert_evolution_and_scores(case)
        assert all(case["usable"].values())
        latest_case_by_persona[case["persona_id"]] = case
        case_history_by_persona.setdefault(case["persona_id"], []).append(case)
        for write in case["memory"]["generation_memory_writes"]:
            institutional_writes_by_id[write["institutional_entry_id"]] = {
                **write,
                "case_id": case["case_id"],
                "persona_id": case["persona_id"],
            }


def _assert_unique_planned_validation_cycle(
    case: dict,
    *,
    plan_signatures: set[str],
    combo_signatures: set[str],
    portfolio_window_signatures: set[str],
    validation_assertion_refs: set[str],
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

    backlog = planning["validation_backlog"]
    assert backlog["selected_backlog_keys"] == selected_plan["selected_backlog_keys"]
    assert backlog["queued_new_item_count"] == len(backlog["queued_new_items"])
    assert backlog["fulfilled_prior_item_count"] == len(backlog["fulfilled_prior_items"])
    assert set(selected_plan["fulfilled_prior_backlog_refs"]) == {
        item["backlog_ref"] for item in backlog["fulfilled_prior_items"]
    }
    assert backlog["follow_through_status"] in {
        "cold_start",
        "fulfilled_prior_backlog",
        "no_open_prior_backlog",
    }
    assert backlog["replay"]["queued_items_have_follow_through_keys"] is True
    assert backlog["replay"]["selected_keys_match_plan"] is True
    assert backlog["replay"]["open_prior_items_preserved"] is True
    assert backlog["replay"]["fulfilled_items_match_selected_keys"] is True
    if backlog["open_prior_item_count"]:
        assert backlog["fulfilled_prior_item_count"] > 0
    else:
        assert backlog["fulfilled_prior_item_count"] == 0
    for item in backlog["queued_new_items"]:
        assert item["backlog_axis"] in {
            "order_profile_variant",
            "policy_candidate_variant",
        }
        assert item["follow_through_key"]
        assert item["backlog_ref"].startswith("validation-backlog-")

    assert selected_plan["target_validation_signature"] == case["validation_signature"]
    assert selected_plan["target_combo_signature"] not in combo_signatures
    assert planning["plan_signature"] not in plan_signatures
    assert selected_plan["target_portfolio_window_signature"] not in portfolio_window_signatures
    assert len(set(selected_plan["assertion_labels"])) == len(selected_plan["assertion_labels"])
    assert len(selected_plan["assertion_refs"]) == len(selected_plan["assertion_labels"])
    assert len(set(selected_plan["assertion_refs"])) == len(selected_plan["assertion_refs"])
    for assertion_ref in selected_plan["assertion_refs"]:
        assert assertion_ref.startswith(
            f"validation-assertion://{selected_plan['target_validation_signature']}/"
        )
        assert assertion_ref not in validation_assertion_refs
        validation_assertion_refs.add(assertion_ref)
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
        reflection_ref = (
            f"oss://{input_context['oss_components_by_role']['reflection_artifact']}/"
            f"{input_context['oss_request_ids_by_role']['reflection_artifact']}"
        )

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
        assert reasoning_request["reflection_artifact_ref"] == reflection_ref
        assert reasoning_request["reflection_artifact_component"] == input_context[
            "oss_components_by_role"
        ]["reflection_artifact"]
        assert reasoning_request["reflection_artifact_request_id"] == input_context[
            "oss_request_ids_by_role"
        ]["reflection_artifact"]
        assert reflection_ref in reasoning_request["input_refs"]
        assert reasoning_response["oss_followup_usage"]["loop_ref"] == followup_loop["loop_ref"]
        assert reasoning_response["oss_followup_usage"]["model_id"] == OSS_RESPONSE_FOLLOWUP_LOOP_MODEL_ID
        assert reasoning_response["oss_followup_usage"]["followup_count"] == len(followup_loop["followups"])
        assert reasoning_response["oss_followup_usage"]["candidate_score_adjustments"] == followup_loop[
            "candidate_score_adjustments"
        ]
        assert reasoning_response["reflection_artifact_usage"]["source_oss_ref"] == reflection_ref
        assert reasoning_response["reflection_artifact_usage"]["materiality_model_id"] == (
            PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID
        )
        assert reasoning_response["reflection_artifact_usage"]["reflection_quality"] > 0
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
        assert reflection_ref in candidate_generation["request"]["input_refs"]
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
        assert scorer["scoring_inputs"]["reflection_quality"] == reasoning_response[
            "reflection_artifact_usage"
        ]["reflection_quality"]
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
        assert reflection_ref in trace["selected_candidate"]["evidence_refs"]
        assert "reflection" in trace["selected_candidate"]["rationale"].lower()
        assert scorecards[selected_id]["components"]["reflection_quality"] == scorer[
            "scoring_inputs"
        ]["reflection_quality"]
        assert scorecards[selected_id]["components"]["reflection_quality"] > 0

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
        assert replay["memory_counterfactual_replays_score_delta"] is True
        assert replay["uses_selected_oss_feedback"] is True
        assert replay["uses_policy_candidate_oss_metrics"] is True
        assert replay["uses_reflection_artifact_oss_metrics"] is True
        assert replay["uses_oss_response_followup_loop"] is True
        assert replay["input_hash"]
        assert replay["candidate_hash"]
        assert replay["score_hash"]
        assert replay["selection_hash"]
        _assert_memory_counterfactual_proof(case, trace)

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["persona_decision_artifact_replays_candidate_selection"]["status"] == "passed"
    assert check_by_name["persona_reasoning_response_drives_candidate_generation"]["status"] == "passed"
    assert check_by_name["retrieved_memory_influences_persona_candidate_scoring"]["status"] == "passed"
    assert check_by_name["memory_counterfactual_proves_retrieval_materiality"]["status"] == "passed"
    assert check_by_name["oss_response_followup_loop_drives_persona_scoring"]["status"] == "passed"


def _assert_memory_counterfactual_proof(case: dict, trace: dict) -> None:
    artifact = trace["agent_decision_artifact"]
    proof = artifact["memory_counterfactual"]
    memory_influence = artifact["memory_influence"]
    scorecards = artifact["scorer"]["scorecards"]
    selected_id = trace["selected_candidate_id"]
    selected_card = scorecards[selected_id]

    assert proof["model_id"] == PERSONA_MEMORY_COUNTERFACTUAL_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["generation"] == artifact["generation"]
    assert proof["decision_trace_ref"] == trace["reflection_id"]
    assert proof["proof_ref"] == f"memory-counterfactual://{case['case_id']}/gen{artifact['generation']}"
    assert proof["input_hash"]
    assert proof["selected_candidate_id"] == selected_id
    assert proof["selected_action"] == _candidate_action_from_id(selected_id)
    assert proof["actual_winner_id"] == selected_id
    assert proof["selected_actual_score"] == selected_card["candidate_score"]
    assert proof["selected_memory_adjustment"] == selected_card["components"]["memory_adjustment"]
    assert proof["selected_score_delta_from_memory"] == proof["selected_memory_adjustment"]

    for candidate_id, card in scorecards.items():
        expected_counterfactual = round(
            card["candidate_score"] - card["components"].get("memory_adjustment", 0.0),
            10,
        )
        assert proof["actual_scores"][candidate_id] == card["candidate_score"]
        assert proof["counterfactual_without_memory_scores"][candidate_id] == expected_counterfactual

    replay = proof["replay"]
    assert all(replay.values())
    memory_ref = memory_influence["influence_ref"]
    if memory_ref:
        assert proof["memory_status"] == "retrieved"
        assert proof["outcome"] == "memory_material_to_selected_score"
        assert proof["memory_ref"] == memory_ref
        assert proof["memory_id"] == memory_influence["memory_id"]
        assert proof["memory_source_event_id"] == memory_influence["source_event_id"]
        assert proof["selected_action_hint"] == memory_influence["selected_action_hint"]
        assert proof["selected_action"] == memory_influence["selected_action_hint"]
        assert memory_ref in trace["selected_candidate"]["evidence_refs"]
        assert memory_ref in artifact["candidate_generation"]["request"]["input_refs"]
        assert proof["selected_score_delta_from_memory"] > 0
        assert proof["actual_margin_to_runner_up"] > proof["counterfactual_margin_to_runner_up"]
        assert proof["memory_margin_lift"] > 0
    else:
        assert proof["memory_status"] == "cold_start_declared"
        assert proof["outcome"] == "cold_start_declared"
        assert proof["memory_id"] is None
        assert proof["selected_score_delta_from_memory"] == 0.0
        assert proof["actual_margin_to_runner_up"] == proof["counterfactual_margin_to_runner_up"]
        assert proof["memory_margin_lift"] == 0.0


def _assert_no_leakage_temporal_protocol(case: dict) -> None:
    protocol = case["evolution"]["no_leakage_protocol"]
    assert protocol["model_id"] == NO_LEAKAGE_TEMPORAL_PROTOCOL_MODEL_ID
    assert protocol["case_id"] == case["case_id"]
    assert protocol["persona_id"] == case["persona_id"]
    assert protocol["protocol_path"] == "observe_decide->feedback_reflect->holdout_evolve->future_holdout_verify"
    assert protocol["input_hash"]

    assert len(protocol["window_boundaries"]) == PORTFOLIO_LEG_COUNT
    assert len(protocol["blind_admission_precommit_refs"]) == PORTFOLIO_LEG_COUNT
    assert len(protocol["blind_admission_precommits"]) == PORTFOLIO_LEG_COUNT
    for precommit, boundary in zip(
        protocol["blind_admission_precommits"], protocol["window_boundaries"]
    ):
        assert precommit["precommit_ref"] in protocol["blind_admission_precommit_refs"]
        assert precommit["precommit_ref"].startswith("future-blind-admission-precommit://")
        assert precommit["instrument"] == boundary["instrument"]
        assert precommit["start_index"] == boundary["start_index"]
        assert precommit["source_windows"] == ["observe", "feedback", "holdout"]
        assert precommit["forbidden_windows_not_used"] == ["future_holdout"]
        assert precommit["validation_window"] == "future_holdout"
        assert precommit["future_holdout_period_included"] is False
        assert precommit["future_holdout_hash_excluded"] is True
        assert precommit["precommitted_before_validation_window"] is True
        assert precommit["input_hash"]
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
    assert replay["blind_admission_precommits_bound_to_windows"] is True
    assert replay["blind_admission_precommits_exclude_future_holdout"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["no_leakage_temporal_protocol_replays_window_boundaries"]["status"] == "passed"
    assert case["usability_dimensions"]["no_leakage_temporal_protocol"] == 1.0


def _assert_strict_oos_evolution_proof(case: dict) -> None:
    proof = case["evolution"]["strict_oos_evolution_proof"]
    generation_results = case["generation_results"]

    assert proof["model_id"] == STRICT_OOS_EVOLUTION_PROOF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"strict-oos-evolution://{case['case_id']}"
    assert proof["input_hash"]
    assert proof["blind_admission_precommit_refs"] == case["evolution"][
        "no_leakage_protocol"
    ]["blind_admission_precommit_refs"]
    assert len(proof["blind_admission_precommit_refs"]) == PORTFOLIO_LEG_COUNT
    assert all(
        ref.startswith("future-blind-admission-precommit://")
        for ref in proof["blind_admission_precommit_refs"]
    )
    assert set(proof["blind_admission_precommit_refs"]).issubset(set(proof["evidence_refs"]))

    assert [policy["generation"] for policy in proof["policy_lineage"]] == [0, 1, 2]
    assert [policy["policy_id"] for policy in proof["policy_lineage"]] == [
        result["policy_id"] for result in generation_results
    ]
    assert [policy["policy_version"] for policy in proof["policy_lineage"]] == [
        result["policy_version"] for result in generation_results
    ]

    assert len(proof["window_pairs"]) == PORTFOLIO_LEG_COUNT
    for pair in proof["window_pairs"]:
        assert pair["instrument"] in case["portfolio"]["instruments"]
        assert pair["holdout"]["bar_count"] == HOLDOUT_BARS
        assert pair["future_holdout"]["bar_count"] == FUTURE_HOLDOUT_BARS
        assert pair["holdout"]["end_date"] < pair["future_holdout"]["start_date"]
        assert pair["strictly_after"] is True
        assert pair["disjoint"] is True

    steps = proof["proof_steps"]
    traces = case["reflection"]["agent_decision_traces"]
    assert [step["source_outcome_window"] for step in steps] == ["feedback", "holdout"]
    assert [step["validation_window"] for step in steps] == ["holdout", "future_holdout"]
    assert [step["decision_trace_ref"] for step in steps] == [
        trace["reflection_id"] for trace in traces
    ]

    first_step, second_step = steps
    assert first_step["candidate_policy_id"] == generation_results[1]["policy_id"]
    assert first_step["counterfactual_policy_id"] == generation_results[0]["policy_id"]
    assert first_step["counterfactual_score"] == case["scores"]["baseline_holdout_counterfactual"]
    assert first_step["candidate_score"] == case["scores"]["generation1_holdout"]
    assert first_step["score_improvement"] == case["scores"]["holdout_improvement"]
    assert first_step["visible_windows_before_decision"] == ["observe", "feedback"]
    assert set(first_step["hidden_windows_before_decision"]) == {"holdout", "future_holdout"}

    assert second_step["candidate_policy_id"] == generation_results[2]["policy_id"]
    assert second_step["counterfactual_policy_id"] == generation_results[1]["policy_id"]
    assert second_step["counterfactual_score"] == case["scores"]["generation1_future_counterfactual"]
    assert second_step["candidate_score"] == case["scores"]["generation2_future_holdout"]
    assert second_step["score_improvement"] == case["scores"]["future_generation_improvement"]
    assert second_step["visible_windows_before_decision"] == ["observe", "feedback", "holdout"]
    assert second_step["hidden_windows_before_decision"] == ["future_holdout"]

    for step in steps:
        assert step["score_improvement"] > 0
        assert step["strict_improvement"] is True
        assert step["validation_window_unseen_by_decision"] is True
        assert step["future_window_hidden"] is True

    replay = proof["replay"]
    assert replay["replayable"] is True
    assert replay["uses_two_distinct_validation_windows"] is True
    assert replay["holdout_and_future_holdout_disjoint"] is True
    assert replay["generation1_uses_feedback_only_before_holdout"] is True
    assert replay["generation2_uses_holdout_only_before_future_holdout"] is True
    assert replay["future_holdout_hidden_from_all_decisions"] is True
    assert replay["strict_improvement_on_each_unseen_window"] is True
    assert replay["trajectory_agrees_with_oos_steps"] is True
    assert replay["no_leakage_protocol_passed"] is True
    assert replay["evolution_trajectory_passed"] is True
    assert replay["evolution_decision_executed"] is True
    assert replay["strict_proof_binds_blind_admission_precommits"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["strict_oos_evolution_proof_replays_unseen_windows"]["status"] == "passed"
    assert case["usability_dimensions"]["strict_oos_evolution"] == 1.0


def _assert_blind_future_oos_audit(case: dict) -> None:
    audit = case["evolution"]["blind_future_oos_audit"]
    verdict = audit["future_verdict"]
    followup = audit["persona_followup"]
    admission = audit["admission_contract"]
    replay = audit["replay"]

    assert audit["model_id"] == BLIND_FUTURE_OOS_AUDIT_MODEL_ID
    assert audit["status"] == "passed"
    assert audit["case_id"] == case["case_id"]
    assert audit["persona_id"] == case["persona_id"]
    assert audit["audit_id"] == f"blind-future-oos-audit-{case['case_id']}"
    assert audit["audit_ref"] == f"blind-future-oos://{case['case_id']}"
    assert audit["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][-1][
        "reflection_id"
    ]
    assert audit["strict_oos_proof_ref"] == case["evolution"]["strict_oos_evolution_proof"][
        "proof_ref"
    ]
    assert admission["source_windows"] == ["observe", "feedback", "holdout"]
    assert admission["forbidden_windows_not_used"] == ["future_holdout"]
    assert admission["uses_future_holdout_in_admission"] is False
    assert admission["future_holdout_rows_available_only_for_verdict"] is True
    assert set(admission["criteria"]) == {
        "observe_feedback_direction_available",
        "generation1_beats_baseline_on_holdout",
        "portfolio_leg_count",
    }
    assert len(admission["shadow_window_refs"]) == PORTFOLIO_LEG_COUNT
    assert audit["shadow_signature"] == admission["shadow_signature"]
    assert audit["shadow_portfolio"]["instrument_count"] == PORTFOLIO_LEG_COUNT
    assert len(audit["shadow_portfolio"]["instruments"]) == PORTFOLIO_LEG_COUNT
    assert len(audit["shadow_portfolio"]["start_indices"]) == PORTFOLIO_LEG_COUNT
    assert set(audit["shadow_portfolio"]["feedback_directions"]) == set(
        audit["shadow_portfolio"]["instruments"]
    )
    assert set(audit["shadow_portfolio"]["holdout_directions"]) == set(
        audit["shadow_portfolio"]["instruments"]
    )
    assert set(audit["shadow_portfolio"]["future_directions_visible_after_verdict"]) == set(
        audit["shadow_portfolio"]["instruments"]
    )
    assert audit["generation1_holdout_score"] > audit["baseline_holdout_score"]
    assert audit["holdout_improvement"] > 0
    assert verdict["validation_window"] == "future_holdout"
    assert verdict["verdict"] in {"improved", "regressed"}
    assert verdict["observed_after_blind_decision"] is True
    assert verdict["score_improvement"] == audit["future_improvement"]
    assert verdict["improved"] is (verdict["verdict"] == "improved")
    assert audit["future_improvement"] == round(
        audit["generation2_future_holdout_score"]
        - audit["generation1_future_counterfactual_score"],
        10,
    )
    expected_action = (
        "promote_blind_holdout_hypothesis_to_paper_candidate"
        if verdict["verdict"] == "improved"
        else "quarantine_oracle_like_evolution_and_request_new_oss_evidence"
    )
    expected_ooda_step = "decide" if verdict["verdict"] == "improved" else "orient"
    assert followup["action"] == expected_action
    assert followup["ooda_step"] == expected_ooda_step
    assert followup["verdict"] == verdict["verdict"]
    assert followup["future_verdict_seen_after_decision"] is True
    assert audit["audit_ref"] in followup["evidence_refs"]
    assert f"reflection://{audit['decision_trace_ref']}" in followup["evidence_refs"]
    assert audit["strict_oos_proof_ref"] in followup["evidence_refs"]
    for window_ref in admission["shadow_window_refs"]:
        assert window_ref in followup["evidence_refs"]

    assert replay["replayable"] is True
    assert replay["shadow_portfolio_has_three_legs"] is True
    assert replay["admission_uses_observe_feedback_holdout_only"] is True
    assert replay["future_holdout_absent_from_admission"] is True
    assert replay["holdout_admission_improves_without_future"] is True
    assert replay["future_verdict_emitted_after_blind_decision"] is True
    assert replay["blind_verdict_records_pass_or_fail"] is True
    assert replay["persona_followup_action_matches_verdict"] is True
    assert replay["strict_curated_proof_kept_separate_from_blind_admission"] is True
    assert all(replay.values())
    assert audit["input_hash"]

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["blind_future_oos_audit_drives_persona_followup"]["status"] == "passed"
    assert case["usability_dimensions"]["blind_future_oos_audit"] == 1.0
    assert case["usable"]["blind_future_oos_audit_drives_followup"] is True


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


def _assert_institutional_memory_lineage(
    case: dict,
    institutional_writes_by_id: dict[str, dict],
) -> None:
    memory = case["memory"]
    proof = memory["institutional_memory_lineage"]
    traces = case["reflection"]["agent_decision_traces"]

    assert proof["model_id"] == PERSONA_INSTITUTIONAL_MEMORY_LINEAGE_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"institutional-memory-lineage://{case['case_id']}"
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["institutional_memory_lineage"] == 1.0
    assert case["usable"]["cross_persona_institutional_memory_drives_decision"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["cross_persona_institutional_memory_drives_persona_scoring"]["status"] == "passed"

    if not institutional_writes_by_id:
        assert memory["prior_institutional_memory"] is None
        assert proof["lineage_status"] == "cold_start"
        assert proof["entry_id"] is None
        assert proof["entry_ref"] is None
        assert proof["source_event_id"] is None
        assert proof["contributing_persona_ids"] == []
        assert proof["institutional_refs"] == []
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
    else:
        prior_institutional = memory["prior_institutional_memory"]
        assert prior_institutional
        assert proof["lineage_status"] == "applied"
        assert proof["entry_id"] == prior_institutional["entry_id"]
        assert proof["entry_id"] in institutional_writes_by_id
        previous_write = institutional_writes_by_id[proof["entry_id"]]
        assert previous_write["persona_id"] != case["persona_id"]
        assert proof["entry_ref"] == f"institutional-memory://{proof['entry_id']}"
        assert proof["entry_ref"] == prior_institutional["entry_ref"]
        assert proof["source_event_id"] == previous_write["source_event_id"]
        assert proof["source_event_id"] == prior_institutional["source_event_id"]
        assert proof["contributing_persona_ids"] == prior_institutional["contributing_persona_ids"]
        assert case["persona_id"] not in proof["contributing_persona_ids"]
        assert previous_write["persona_id"] in proof["contributing_persona_ids"]
        assert proof["selected_action_hint"] in {
            "feedback-adapt",
            "risk-off",
            "retain-observe",
            "contrarian-check",
        }
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["score_adjustments"]["risk-off"] > 0.0
        assert proof["score_adjustments"]["retain-observe"] == 0.0
        assert proof["score_adjustments"]["contrarian-check"] == 0.0
        assert proof["cited_evidence_refs"]
        assert proof["institutional_refs"] == [
            proof["entry_ref"],
            *proof["cited_evidence_refs"],
        ]

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        reasoning_response = artifact["persona_reasoning"]["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]
        scorecards = artifact["scorer"]["scorecards"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_card = scorecards[selected_id]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_action"] == selected_action
        assert trace["decision_inputs"]["institutional_memory_entry_ref"] == proof["entry_ref"]
        assert trace["decision_inputs"]["institutional_memory_source_event_id"] == proof["source_event_id"]
        assert trace["decision_inputs"]["institutional_memory_contributing_persona_ids"] == proof[
            "contributing_persona_ids"
        ]
        assert artifact["input_context"]["institutional_memory_status"] == proof["lineage_status"]
        assert artifact["input_context"]["institutional_memory_entry_id"] == proof["entry_id"]
        assert artifact["input_context"]["institutional_memory_entry_ref"] == proof["entry_ref"]
        assert artifact["input_context"]["institutional_memory_source_event_id"] == proof["source_event_id"]
        assert artifact["input_context"]["institutional_memory_contributing_persona_ids"] == proof[
            "contributing_persona_ids"
        ]
        assert reasoning_request["institutional_memory_status"] == proof["lineage_status"]
        assert reasoning_request["institutional_memory_entry_ref"] == proof["entry_ref"]
        assert reasoning_request["institutional_memory_source_event_id"] == proof["source_event_id"]
        assert reasoning_request["institutional_memory_contributing_persona_ids"] == proof[
            "contributing_persona_ids"
        ]
        assert reasoning_response["institutional_memory_usage"]["status"] == proof["lineage_status"]
        assert reasoning_response["institutional_memory_usage"]["entry_ref"] == proof["entry_ref"]
        assert reasoning_response["institutional_memory_usage"]["source_event_id"] == proof["source_event_id"]
        assert reasoning_response["institutional_memory_usage"]["contributing_persona_ids"] == proof[
            "contributing_persona_ids"
        ]
        assert reasoning_response["institutional_memory_usage"]["candidate_score_adjustments"] == proof[
            "score_adjustments"
        ]
        assert scorer_inputs["institutional_memory_influence"]["status"] == proof["lineage_status"]
        assert scorer_inputs["institutional_memory_influence"]["entry_ref"] == proof["entry_ref"]
        assert scorer_inputs["institutional_memory_score_adjustments"] == proof["score_adjustments"]
        assert selected_card["components"]["institutional_memory_adjustment"] == proof[
            "score_adjustments"
        ][selected_action]
        assert artifact["replay"]["uses_cross_persona_institutional_memory_or_declares_cold_start"] is True

        if proof["lineage_status"] == "cold_start":
            assert binding["decision_input_entry_ref"] is None
            assert binding["reasoning_consumes_entry_ref"] is False
            assert binding["candidate_request_consumes_entry_ref"] is False
            assert binding["selected_candidate_cites_entry_ref"] is False
            assert binding["scorer_institutional_memory_adjustment"] == 0.0
            assert selected_card["components"]["institutional_memory_adjustment"] == 0.0
        else:
            expected_refs = set(proof["institutional_refs"])
            assert binding["decision_input_entry_ref"] == proof["entry_ref"]
            assert binding["private_memory_ref"] != proof["entry_ref"]
            assert not str(binding["decision_input_entry_ref"]).startswith("memory://")
            assert binding["reasoning_consumes_entry_ref"] is True
            assert binding["candidate_request_consumes_entry_ref"] is True
            assert binding["selected_candidate_cites_entry_ref"] is True
            assert binding["reasoning_consumes_cited_evidence_refs"] is True
            assert binding["candidate_request_consumes_cited_evidence_refs"] is True
            assert binding["selected_candidate_cites_cited_evidence_refs"] is True
            assert binding["scorer_institutional_memory_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorecard_institutional_memory_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorer_institutional_memory_adjustment"] > 0.0
            assert binding["decision_replay_uses_institutional_memory"] is True
            assert expected_refs.issubset(set(reasoning_request["input_refs"]))
            assert expected_refs.issubset(set(candidate_request["input_refs"]))
            assert expected_refs.issubset(set(trace["selected_candidate"]["evidence_refs"]))


def _assert_policy_candidate_materiality(case: dict) -> None:
    proof = case["oss_feedback"]["policy_candidate_materiality"]
    policy_entry = case["case_upstream_artifacts"]["selected_oss"]["policy_candidate"]
    source_ref = f"oss://{policy_entry['component']}/{policy_entry['request_id']}"
    expected_artifact_family = (
        "rl_policy"
        if policy_entry["component"] in {"finrl", "rllib"}
        else "optimizer_result"
    )

    assert proof["model_id"] == PERSONA_POLICY_CANDIDATE_MATERIALITY_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["proof_ref"] == f"policy-materiality://{case['case_id']}"
    assert proof["component"] == policy_entry["component"]
    assert proof["request_id"] == policy_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["artifact_family"] == expected_artifact_family
    assert proof["expected_artifact_family"] == expected_artifact_family
    assert proof["registry_id"] == policy_entry["registry_id"]
    assert proof["registry_artifact_type"] == policy_entry["registry_artifact_type"]
    assert proof["producer_run_id"] == policy_entry["producer_run_id"]
    assert proof["metric_signal_keys"]
    assert proof["policy_quality"] > 0
    assert set(policy_entry["primary_output_keys"]).issubset(set(proof["primary_output_keys"]))
    assert source_ref in proof["evidence_refs"]
    assert f"registry://{policy_entry['registry_id']}" in proof["evidence_refs"]

    trace_bindings = proof["trace_bindings"]
    traces = case["reflection"]["agent_decision_traces"]
    assert len(trace_bindings) == 2
    assert [binding["generation"] for binding in trace_bindings] == [1, 2]
    for binding, trace in zip(trace_bindings, traces):
        artifact = trace["agent_decision_artifact"]
        generation = artifact["generation"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_candidate = trace["selected_candidate"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_scorecard = artifact["scorer"]["scorecards"][selected_id]

        assert binding["generation"] == generation
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["policy_id"] == case["generation_results"][generation]["policy_id"]
        assert binding["selected_candidate_id"] == selected_id
        assert binding["selected_action"] == selected_action
        assert binding["source_oss_ref"] == source_ref
        assert binding["scoring_input_component"] == policy_entry["component"]
        assert binding["scoring_input_request_id"] == policy_entry["request_id"]
        assert binding["reasoning_consumes_policy_oss"] is True
        assert binding["candidate_generation_consumes_policy_oss"] is True
        assert binding["selected_candidate_cites_policy_oss"] is True
        assert binding["policy_ref_in_decision_evidence"] is True
        assert binding["policy_hint_risk_replay_match"] is True
        assert binding["policy_quality_replay_match"] is True
        assert binding["feedback_scorecard_replays_policy_quality"] is True
        assert binding["selected_scorecard_replays_policy_quality"] is True
        assert binding["selected_candidate_uses_policy_hint_risk"] is True
        assert binding["evolved_policy_uses_policy_hint_risk"] is True
        assert binding["selected_candidate_is_feedback_adapt_policy_candidate"] is True
        assert binding["decision_replay_uses_policy_candidate_oss_metrics"] is True
        assert binding["no_forbidden_window_policy_sources"] is True
        assert source_ref in artifact["persona_reasoning"]["request"]["input_refs"]
        assert source_ref in artifact["candidate_generation"]["request"]["input_refs"]
        assert source_ref in selected_candidate["evidence_refs"]
        assert source_ref in trace["evidence_refs"]
        assert scoring_inputs["policy_quality"] == binding["scoring_policy_quality"]
        assert scoring_inputs["policy_hint_risk"] == binding["scoring_policy_hint_risk"]
        assert selected_scorecard["components"]["policy_quality"] == binding[
            "selected_scorecard_policy_quality"
        ]
        assert selected_scorecard["components"]["policy_quality"] == proof["policy_quality"]
        assert selected_scorecard["components"]["policy_quality"] > 0
        assert selected_candidate["risk_multiplier"] == binding["selected_candidate_risk_multiplier"]
        assert selected_candidate["risk_multiplier"] == scoring_inputs["policy_hint_risk"]
        assert selected_action == "feedback-adapt"
        assert artifact["replay"]["uses_policy_candidate_oss_metrics"] is True

    replay = proof["replay"]
    assert all(replay.values())
    assert case["usable"]["policy_candidate_oss_materiality"] is True
    assert case["usability_dimensions"]["policy_candidate_oss_materiality"] == 1.0
    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["policy_candidate_oss_materiality_drives_evolved_policy"]["status"] == "passed"


def _assert_reflection_artifact_materiality(case: dict) -> None:
    proof = case["oss_feedback"]["reflection_artifact_materiality"]
    reflection_entry = case["case_upstream_artifacts"]["selected_oss"]["reflection_artifact"]
    source_ref = f"oss://{reflection_entry['component']}/{reflection_entry['request_id']}"
    expected_artifact_family = {
        "dspy": "prompt_bundle",
        "imitation": "imitation_policy",
        "trl": "model_artifact",
    }[reflection_entry["component"]]

    assert proof["model_id"] == PERSONA_REFLECTION_ARTIFACT_MATERIALITY_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["proof_ref"] == f"reflection-materiality://{case['case_id']}"
    assert proof["component"] == reflection_entry["component"]
    assert proof["request_id"] == reflection_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["artifact_family"] == expected_artifact_family
    assert proof["expected_artifact_family"] == expected_artifact_family
    assert proof["registry_id"] == reflection_entry["registry_id"]
    assert proof["registry_artifact_type"] == reflection_entry["registry_artifact_type"]
    assert proof["producer_run_id"] == reflection_entry["producer_run_id"]
    assert proof["metric_signal_keys"]
    assert proof["reflection_quality"] > 0
    assert set(reflection_entry["primary_output_keys"]).issubset(set(proof["primary_output_keys"]))
    assert source_ref in proof["evidence_refs"]
    assert f"registry://{reflection_entry['registry_id']}" in proof["evidence_refs"]

    trace_bindings = proof["trace_bindings"]
    traces = case["reflection"]["agent_decision_traces"]
    assert len(trace_bindings) == 2
    assert [binding["generation"] for binding in trace_bindings] == [1, 2]
    for binding, trace in zip(trace_bindings, traces):
        artifact = trace["agent_decision_artifact"]
        generation = artifact["generation"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_candidate = trace["selected_candidate"]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        scorecards = artifact["scorer"]["scorecards"]
        selected_scorecard = scorecards[selected_id]
        feedback_candidate = next(
            candidate
            for candidate in trace["candidates"]
            if _candidate_action_from_id(candidate["candidate_id"]) == "feedback-adapt"
        )
        feedback_scorecard = scorecards[feedback_candidate["candidate_id"]]
        contrarian_candidate = next(
            candidate
            for candidate in trace["candidates"]
            if _candidate_action_from_id(candidate["candidate_id"]) == "contrarian-check"
        )

        assert binding["generation"] == generation
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_candidate_id"] == selected_id
        assert binding["selected_action"] == selected_action
        assert binding["source_oss_ref"] == source_ref
        assert binding["reasoning_request_consumes_reflection_oss"] is True
        assert binding["reasoning_usage_ref_matches"] is True
        assert binding["reasoning_usage_quality"] == proof["reflection_quality"]
        assert binding["reasoning_usage_quality_replay_match"] is True
        assert binding["feedback_blueprint_uses_reflection_role"] is True
        assert binding["contrarian_blueprint_uses_reflection_role"] is True
        assert binding["candidate_generation_consumes_reflection_oss"] is True
        assert binding["selected_candidate_cites_reflection_oss"] is True
        assert binding["contrarian_candidate_cites_reflection_oss"] is True
        assert binding["selected_rationale_mentions_reflection"] is True
        assert binding["scoring_reflection_quality"] == proof["reflection_quality"]
        assert binding["recomputed_reflection_quality"] == proof["reflection_quality"]
        assert binding["scoring_reflection_quality_replay_match"] is True
        assert binding["feedback_scorecard_reflection_quality"] == proof["reflection_quality"]
        assert binding["selected_scorecard_reflection_quality"] == proof["reflection_quality"]
        assert binding["feedback_scorecard_replays_reflection_quality"] is True
        assert binding["selected_scorecard_replays_reflection_quality"] is True
        assert binding["decision_replay_uses_reflection_artifact_metrics"] is True
        assert binding["reflection_ref_in_decision_evidence"] is True
        assert binding["no_forbidden_window_reflection_sources"] is True
        assert source_ref in reasoning_request["input_refs"]
        assert reasoning_request["reflection_artifact_ref"] == source_ref
        assert reasoning_response["reflection_artifact_usage"]["source_oss_ref"] == source_ref
        assert reasoning_response["reflection_artifact_usage"]["reflection_quality"] == proof[
            "reflection_quality"
        ]
        assert source_ref in candidate_request["input_refs"]
        assert source_ref in selected_candidate["evidence_refs"]
        assert source_ref in contrarian_candidate["evidence_refs"]
        assert source_ref in trace["evidence_refs"]
        assert scoring_inputs["reflection_quality"] == proof["reflection_quality"]
        assert feedback_scorecard["components"]["reflection_quality"] == proof["reflection_quality"]
        assert selected_scorecard["components"]["reflection_quality"] == proof["reflection_quality"]
        assert "reflection" in selected_candidate["rationale"].lower()
        assert selected_action == "feedback-adapt"
        assert artifact["replay"]["uses_reflection_artifact_oss_metrics"] is True

    replay = proof["replay"]
    assert all(replay.values())
    assert case["usable"]["reflection_artifact_oss_materiality"] is True
    assert case["usability_dimensions"]["reflection_artifact_oss_materiality"] == 1.0
    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name[
        "reflection_artifact_oss_materiality_drives_persona_reasoning"
    ]["status"] == "passed"


def _assert_multi_oss_closed_loop_proof(case: dict) -> None:
    proof = case["oss_feedback"]["closed_loop_proof"]
    followup_loop = case["oss_feedback"]["response_followup_loop"]
    traces = case["reflection"]["agent_decision_traces"]
    required_roles = [
        "session",
        "alpha_model",
        "backtest",
        "policy_candidate",
        "reflection_artifact",
        "tracker",
        "risk_analytics",
        "handoff",
    ]

    assert proof["model_id"] == MULTI_OSS_CLOSED_LOOP_PROOF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"multi-oss-closed-loop://{case['case_id']}"
    assert proof["input_hash"]
    assert [record["role"] for record in proof["role_records"]] == required_roles
    assert len(proof["trace_bindings"]) == 2

    followup_by_role = {
        followup["role"]: followup for followup in followup_loop["followups"]
    }
    selected_oss = case["case_upstream_artifacts"]["selected_oss"]
    followup_output_refs = {record["followup_output_ref"] for record in proof["role_records"]}
    assert set(followup_loop["candidate_evidence_refs_by_action"]["feedback-adapt"]) == followup_output_refs

    for record in proof["role_records"]:
        role = record["role"]
        component = record["component"]
        request_id = case["oss_feedback"]["request_ids"][role]
        source_ref = f"oss://{component}/{request_id}"
        followup = followup_by_role[role]

        assert component in OSS_REQUIRED_COMPONENTS
        assert record["role_component_cell"] == f"{role}:{component}"
        assert record["expected_components_for_role"] == list(OSS_ROLE_COMPONENT_MATRIX[role])
        assert record["role_component_allowed"] is True
        assert record["source_oss_request_id"] == request_id
        assert record["source_oss_ref"] == source_ref
        assert record["source_status"] == "completed"
        assert record["source_drives_persona_step"] == case["oss_feedback"]["drives_persona_steps"][role]
        assert record["followup_request_id"] == followup["request"]["request_id"]
        assert record["followup_response_id"] == followup["response"]["response_id"]
        assert record["followup_output_ref"] == followup["response"]["output_ref"]
        assert record["followup_candidate_action"] == followup["response"]["candidate_action"]
        assert record["source_ref_bound_to_followup"] is True
        assert record["followup_requested_after_oss_response"] is True
        assert record["followup_completed"] is True
        assert record["followup_drives_persona_step"] == case["oss_feedback"]["drives_persona_steps"][role]
        assert record["used_by_generations"] == [1, 2]
        assert any(value > 0 for value in record["followup_score_adjustments"].values())
        if role in selected_oss:
            selected_entry = selected_oss[role]
            assert record["selected_oss_ref"] == f"oss://{selected_entry['component']}/{selected_entry['request_id']}"
            assert record["selected_oss_bound"] is True
        else:
            assert record["selected_oss_ref"] is None
            assert record["selected_oss_bound"] is True

    for binding, trace in zip(proof["trace_bindings"], traces):
        artifact = trace["agent_decision_artifact"]
        reasoning_refs = set(artifact["persona_reasoning"]["request"]["input_refs"])
        candidate_refs = set(artifact["candidate_generation"]["request"]["input_refs"])
        selected_refs = set(trace["selected_candidate"]["evidence_refs"])
        scorer_adjustments = artifact["scorer"]["scoring_inputs"]["oss_followup_score_adjustments"]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_candidate_id"] == trace["selected_candidate_id"]
        assert binding["selected_action"] == "feedback-adapt"
        assert binding["oss_followup_loop_ref"] == followup_loop["loop_ref"]
        assert binding["reasoning_request_consumes_all_source_oss_refs"] is True
        assert binding["candidate_request_consumes_all_followup_outputs"] is True
        assert binding["selected_candidate_cites_all_followup_outputs"] is True
        assert binding["scorer_has_all_role_adjustments"] is True
        assert set(followup_output_refs).issubset(selected_refs)
        assert [role_binding["role"] for role_binding in binding["role_bindings"]] == required_roles
        for role_binding, record in zip(binding["role_bindings"], proof["role_records"]):
            action = record["followup_candidate_action"]
            expected_adjustment = record["followup_score_adjustments"][action]
            assert record["source_oss_ref"] in reasoning_refs
            assert record["followup_output_ref"] in candidate_refs
            assert record["followup_output_ref"] in selected_refs
            assert scorer_adjustments[action] >= expected_adjustment
            assert role_binding["source_ref_in_reasoning_request"] is True
            assert role_binding["followup_output_in_candidate_request"] is True
            assert role_binding["followup_output_in_selected_evidence"] is True
            assert role_binding["scorer_adjustment_available"] is True

    assert all(proof["replay"].values())
    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["multi_oss_closed_loop_proof_replays_role_bindings"]["status"] == "passed"
    assert case["usability_dimensions"]["multi_oss_closed_loop"] == 1.0
    assert case["usable"]["multi_oss_closed_loop_drives_decision"] is True


def _assert_persona_oss_ooda_causal_ledger(case: dict) -> None:
    ledger = case["oss_feedback"]["ooda_causal_ledger"]
    proof = case["oss_feedback"]["closed_loop_proof"]
    traces = case["reflection"]["agent_decision_traces"]
    lean_handoff = case["operational_context"]["lean_handoff"]
    events = ledger["events"]
    produced_at = {event["output_ref"]: event["sequence"] for event in events}

    assert ledger["model_id"] == PERSONA_OSS_OODA_LEDGER_MODEL_ID
    assert ledger["status"] == "passed"
    assert ledger["case_id"] == case["case_id"]
    assert ledger["persona_id"] == case["persona_id"]
    assert ledger["ledger_ref"] == f"persona-oss-ooda-ledger://{case['case_id']}"
    assert ledger["source_closed_loop_proof_ref"] == proof["proof_ref"]
    assert ledger["oss_followup_loop_ref"] == case["oss_feedback"]["response_followup_loop"]["loop_ref"]
    assert ledger["event_count"] == 22
    assert ledger["input_hash"]
    assert [event["sequence"] for event in events] == list(range(1, 23))
    assert set(ledger["phase_order"]) == {"observe", "orient", "decide", "act"}

    events_by_type = {
        event_type: [event for event in events if event["event_type"] == event_type]
        for event_type in {
            "oss_response",
            "persona_followup_response",
            "candidate_generation",
            "candidate_scoring",
            "selected_action",
            "lean_handoff_packet",
        }
    }
    assert len(events_by_type["oss_response"]) == 8
    assert len(events_by_type["persona_followup_response"]) == 8
    assert len(events_by_type["candidate_generation"]) == 2
    assert len(events_by_type["candidate_scoring"]) == 2
    assert len(events_by_type["selected_action"]) == 1
    assert len(events_by_type["lean_handoff_packet"]) == 1

    for event in events:
        assert event["output_ref"] in ledger["evidence_refs"]
        for input_ref in event["input_refs"]:
            if input_ref in produced_at:
                assert produced_at[input_ref] < event["sequence"]

    role_records = proof["role_records"]
    for source_event, record in zip(events_by_type["oss_response"], role_records):
        assert source_event["ooda_phase"] == "observe"
        assert source_event["actor"] == "oss"
        assert source_event["role"] == record["role"]
        assert source_event["component"] == record["component"]
        assert source_event["output_ref"] == record["source_oss_ref"]
        assert source_event["response_ref"] == record["source_oss_ref"]
        assert source_event["downstream_persona_action"] == record["source_drives_persona_step"]

    for followup_event, record in zip(events_by_type["persona_followup_response"], role_records):
        assert followup_event["ooda_phase"] == "orient"
        assert followup_event["actor"] == "persona+oss"
        assert followup_event["role"] == record["role"]
        assert followup_event["component"] == record["component"]
        assert followup_event["input_refs"] == [record["source_oss_ref"]]
        assert followup_event["output_ref"] == record["followup_output_ref"]
        assert followup_event["downstream_persona_action"] == record["followup_candidate_action"]
        assert produced_at[record["source_oss_ref"]] < followup_event["sequence"]

    followup_output_refs = {record["followup_output_ref"] for record in role_records}
    candidate_refs = []
    scorer_refs = []
    for generation_event, trace in zip(events_by_type["candidate_generation"], traces):
        artifact = trace["agent_decision_artifact"]
        expected_ref = (
            f"candidate-generation://{artifact['candidate_generation']['response']['response_id']}"
        )
        candidate_refs.append(expected_ref)
        assert generation_event["ooda_phase"] == "decide"
        assert generation_event["actor"] == "persona"
        assert generation_event["generation"] == artifact["generation"]
        assert generation_event["output_ref"] == expected_ref
        assert set(generation_event["input_refs"]) == followup_output_refs
        assert all(produced_at[ref] < generation_event["sequence"] for ref in followup_output_refs)

    for scoring_event, trace, candidate_ref in zip(
        events_by_type["candidate_scoring"],
        traces,
        candidate_refs,
    ):
        expected_ref = f"candidate-score://{trace['reflection_id']}"
        scorer_refs.append(expected_ref)
        assert scoring_event["ooda_phase"] == "decide"
        assert scoring_event["actor"] == "persona"
        assert scoring_event["generation"] == trace["agent_decision_artifact"]["generation"]
        assert scoring_event["input_refs"] == [candidate_ref]
        assert scoring_event["output_ref"] == expected_ref
        assert produced_at[candidate_ref] < scoring_event["sequence"]

    selected_event = events_by_type["selected_action"][0]
    final_trace = traces[-1]
    selected_ref = f"selected-action://{case['case_id']}/{final_trace['selected_candidate_id']}"
    assert selected_event["ooda_phase"] == "act"
    assert selected_event["actor"] == "persona"
    assert selected_event["input_refs"] == scorer_refs
    assert selected_event["output_ref"] == selected_ref
    assert selected_event["downstream_persona_action"] == "feedback-adapt"
    assert all(produced_at[ref] < selected_event["sequence"] for ref in scorer_refs)

    handoff_event = events_by_type["lean_handoff_packet"][0]
    handoff_source_ref = f"oss://{lean_handoff['component']}/{lean_handoff['request_id']}"
    assert handoff_event["ooda_phase"] == "act"
    assert handoff_event["actor"] == "persona+lean_handoff"
    assert handoff_event["role"] == "handoff"
    assert handoff_event["component"] == "lean_handoff"
    assert selected_ref in handoff_event["input_refs"]
    assert handoff_source_ref in handoff_event["input_refs"]
    assert handoff_event["output_ref"] == f"lean-handoff://{lean_handoff['packet_id']}"
    assert produced_at[selected_ref] < handoff_event["sequence"]
    assert produced_at[handoff_source_ref] < handoff_event["sequence"]

    assert all(ledger["replay"].values())
    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["persona_oss_ooda_ledger_replays_temporal_causality"]["status"] == "passed"
    assert case["usability_dimensions"]["persona_oss_ooda_causality"] == 1.0
    assert case["usable"]["persona_oss_ooda_causality_replayed"] is True


def _assert_cross_cycle_runtime_carryover(case: dict, latest_case_by_persona: dict[str, dict]) -> None:
    proof = case["cross_cycle"]["runtime_feedback_carryover"]
    traces = case["reflection"]["agent_decision_traces"]
    previous_case = latest_case_by_persona.get(case["persona_id"])

    assert proof["model_id"] == PERSONA_CROSS_CYCLE_CARRYOVER_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"cross-cycle-carryover://{case['case_id']}"
    assert proof["current_ooda_ledger_ref"] == case["oss_feedback"]["ooda_causal_ledger"]["ledger_ref"]
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["cross_cycle_runtime_carryover"] == 1.0
    assert case["usable"]["cross_cycle_runtime_feedback_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["cross_cycle_runtime_feedback_drives_next_case_decision"]["status"] == "passed"

    if previous_case is None:
        assert proof["carryover_status"] == "cold_start"
        assert proof["previous_case_id"] is None
        assert proof["state_ref"] is None
        assert proof["runtime_feedback_ref"] is None
        assert proof["source_runtime_ref"] is None
        assert proof["source_handoff_ref"] is None
        assert proof["previous_ooda_ledger_ref"] is None
        assert proof["next_ooda_step"] is None
        assert proof["next_ooda_action"] is None
        assert proof["evidence_refs"] == []
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
    else:
        previous_feedback = previous_case["operational_context"]["lean_runtime_feedback"]
        previous_recovery = previous_case["operational_context"]["restart_recovery"]
        previous_schedule = previous_case["operational_context"]["autonomous_schedule"]
        previous_ledger = previous_case["oss_feedback"]["ooda_causal_ledger"]
        previous_selected_event = next(
            event
            for event in previous_ledger["events"]
            if event["event_type"] == "selected_action"
        )
        previous_followup = previous_feedback["persona_ooda_followup"]
        previous_runtime_readback = previous_feedback["runtime_feedback"]
        expected_runtime_feedback_ref = f"lean-runtime-feedback://{previous_feedback['feedback_id']}"
        expected_state_ref = f"cross-cycle-runtime://{previous_case['case_id']}->{case['case_id']}"
        expected_portfolio_carry_ref = (
            f"portfolio-state-carryover://{previous_case['case_id']}->{case['case_id']}"
        )
        expected_portfolio_state_ref = f"portfolio-state://{previous_case['case_id']}/generation2"
        previous_openclaw = previous_case["operational_context"]["openclaw_session_handoff"]
        expected_openclaw_continuity_ref = (
            f"openclaw-session-continuity://{previous_case['case_id']}->{case['case_id']}"
        )
        previous_broker_followup = previous_case["operational_context"]["broker_adapter_followup"]
        expected_broker_carryover_ref = "broker-adapter-carryover://{}->{}".format(
            previous_case["case_id"],
            case["case_id"],
        )
        expected_broker_followup_ref = "broker-adapter-followup://{}".format(
            previous_broker_followup["followup_id"]
        )
        expected_broker_source_packet_ref = previous_broker_followup["source_packet_ref"]
        expected_broker_checkpoint_ref = "checkpoint://{}".format(
            previous_broker_followup["restart_checkpoint_ref"]
        )
        expected_broker_schedule_ref = "schedule://{}".format(
            previous_broker_followup["schedule_ref"]
        )
        expected_checkpoint_ref = f"checkpoint://{previous_recovery['checkpoint_id']}"
        expected_schedule_ref = f"schedule://{previous_schedule['schedule_id']}"
        expected_metadata_ref = f"object-store://{previous_runtime_readback['object_store_metadata_key']}"
        expected_artifact_ref = f"object-store://{previous_runtime_readback['object_store_artifact_key']}"
        expected_evidence_refs = [
            expected_state_ref,
            expected_runtime_feedback_ref,
            expected_portfolio_carry_ref,
            expected_portfolio_state_ref,
            expected_openclaw_continuity_ref,
            previous_openclaw["source_oss_ref"],
            previous_openclaw["context_ref"],
            previous_openclaw["session_ref"],
            previous_openclaw["upstream_session_ref"],
            expected_broker_carryover_ref,
            expected_broker_followup_ref,
            expected_broker_source_packet_ref,
            expected_broker_checkpoint_ref,
            expected_broker_schedule_ref,
            previous_feedback["source_runtime_ref"],
            previous_feedback["source_handoff_ref"],
            previous_ledger["ledger_ref"],
            previous_selected_event["output_ref"],
            expected_checkpoint_ref,
            expected_schedule_ref,
            expected_metadata_ref,
            expected_artifact_ref,
        ]

        assert proof["carryover_status"] == "applied"
        assert proof["previous_case_id"] == previous_case["case_id"]
        assert proof["state_ref"] == expected_state_ref
        assert proof["runtime_feedback_ref"] == expected_runtime_feedback_ref
        assert proof["source_runtime_ref"] == previous_feedback["source_runtime_ref"]
        assert proof["source_handoff_ref"] == previous_feedback["source_handoff_ref"]
        assert proof["previous_ooda_ledger_ref"] == previous_ledger["ledger_ref"]
        assert proof["next_ooda_step"] == previous_followup["ooda_step"]
        assert proof["next_ooda_action"] == previous_followup["action"]
        assert proof["evidence_refs"] == expected_evidence_refs
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["score_adjustments"]["risk-off"] > 0.0
        assert proof["score_adjustments"]["retain-observe"] == 0.0
        assert proof["score_adjustments"]["contrarian-check"] == 0.0

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]
        scorecards = artifact["scorer"]["scorecards"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_card = scorecards[selected_id]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_action"] == selected_action
        assert trace["decision_inputs"]["cross_cycle_status"] == proof["carryover_status"]
        assert trace["decision_inputs"]["cross_cycle_state_ref"] == proof["state_ref"]
        assert trace["decision_inputs"]["cross_cycle_runtime_feedback_ref"] == proof["runtime_feedback_ref"]
        assert artifact["input_context"]["cross_cycle_status"] == proof["carryover_status"]
        assert artifact["input_context"]["cross_cycle_state_ref"] == proof["state_ref"]
        assert artifact["input_context"]["cross_cycle_runtime_feedback_ref"] == proof["runtime_feedback_ref"]
        assert artifact["input_context"]["cross_cycle_previous_case_id"] == proof["previous_case_id"]
        assert reasoning_request["cross_cycle_status"] == proof["carryover_status"]
        assert reasoning_request["cross_cycle_state_ref"] == proof["state_ref"]
        assert reasoning_request["cross_cycle_runtime_feedback_ref"] == proof["runtime_feedback_ref"]
        assert reasoning_response["cross_cycle_usage"]["status"] == proof["carryover_status"]
        assert reasoning_response["cross_cycle_usage"]["state_ref"] == proof["state_ref"]
        assert reasoning_response["cross_cycle_usage"]["runtime_feedback_ref"] == proof["runtime_feedback_ref"]
        assert reasoning_response["cross_cycle_usage"]["previous_case_id"] == proof["previous_case_id"]
        assert reasoning_response["cross_cycle_usage"]["next_ooda_step"] == proof["next_ooda_step"]
        assert reasoning_response["cross_cycle_usage"]["candidate_score_adjustments"] == proof["score_adjustments"]
        assert scorer_inputs["cross_cycle_context"]["status"] == proof["carryover_status"]
        assert scorer_inputs["cross_cycle_context"].get("state_ref") == proof["state_ref"]
        assert scorer_inputs["cross_cycle_context"].get("runtime_feedback_ref") == proof["runtime_feedback_ref"]
        assert scorer_inputs["cross_cycle_context"].get("previous_case_id") == proof["previous_case_id"]
        assert scorer_inputs["cross_cycle_score_adjustments"] == proof["score_adjustments"]
        assert selected_card["components"]["cross_cycle_adjustment"] == proof["score_adjustments"][selected_action]
        assert artifact["replay"]["uses_cross_cycle_runtime_feedback_or_declares_cold_start"] is True

        if proof["carryover_status"] == "cold_start":
            assert binding["decision_input_state_ref"] is None
            assert binding["reasoning_consumes_state_ref"] is False
            assert binding["reasoning_consumes_runtime_feedback_ref"] is False
            assert binding["candidate_request_consumes_state_ref"] is False
            assert binding["candidate_request_consumes_runtime_feedback_ref"] is False
            assert binding["selected_candidate_cites_state_ref"] is False
            assert binding["scorer_cross_cycle_adjustment"] == 0.0
            assert selected_card["components"]["cross_cycle_adjustment"] == 0.0
        else:
            state_ref = proof["state_ref"]
            runtime_feedback_ref = proof["runtime_feedback_ref"]
            assert binding["decision_input_state_ref"] == state_ref
            assert binding["reasoning_consumes_state_ref"] is True
            assert binding["reasoning_consumes_runtime_feedback_ref"] is True
            assert binding["candidate_request_consumes_state_ref"] is True
            assert binding["candidate_request_consumes_runtime_feedback_ref"] is True
            assert binding["selected_candidate_cites_state_ref"] is True
            assert binding["scorer_cross_cycle_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorer_cross_cycle_adjustment"] > 0.0
            assert state_ref in reasoning_request["input_refs"]
            assert runtime_feedback_ref in reasoning_request["input_refs"]
            assert state_ref in candidate_request["input_refs"]
            assert runtime_feedback_ref in candidate_request["input_refs"]
            assert state_ref in trace["selected_candidate"]["evidence_refs"]
            assert runtime_feedback_ref in trace["selected_candidate"]["evidence_refs"]
            assert proof["previous_ooda_ledger_ref"] in reasoning_request["input_refs"]
            assert proof["previous_ooda_ledger_ref"] in candidate_request["input_refs"]
            assert proof["source_handoff_ref"] in candidate_request["input_refs"]


def _assert_portfolio_state_carryover(case: dict, latest_case_by_persona: dict[str, dict]) -> None:
    proof = case["cross_cycle"]["portfolio_state_carryover"]
    traces = case["reflection"]["agent_decision_traces"]
    previous_case = latest_case_by_persona.get(case["persona_id"])
    conflict = case["operational_context"]["persona_conflict_resolution"]
    allocation = conflict["resolved_allocation"]
    handoff = case["operational_context"]["lean_handoff"]
    projection = case["operational_context"]["lean_packet_execution_projection"]
    runtime_feedback = case["operational_context"]["lean_runtime_feedback"]

    assert proof["model_id"] == PERSONA_PORTFOLIO_STATE_CARRYOVER_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"portfolio-state-carryover://{case['case_id']}"
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["portfolio_state_carryover"] == 1.0
    assert case["usable"]["portfolio_state_carryover_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["portfolio_state_carryover_drives_next_case_sizing"]["status"] == "passed"

    if previous_case is None:
        assert proof["carryover_status"] == "cold_start"
        assert proof["previous_case_id"] is None
        assert proof["state_ref"] is None
        assert proof["carry_ref"] is None
        assert proof["state_hash"] is None
        assert proof["previous_position_count"] == 0
        assert proof["previous_positions_by_instrument"] == {}
        assert proof["target_capital_budget_scale"] == 1.0
        assert proof["rebalance_action"] == "cold_start_no_prior_positions"
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
        assert allocation["portfolio_state_target_capital_budget_scale"] == 1.0
        assert allocation["portfolio_state_carryover_ref"] is None
        assert allocation["portfolio_state_ref"] is None
        assert handoff["portfolio_state_carryover_ref"] is None
        assert handoff["portfolio_state_ref"] is None
        assert runtime_feedback["state_updates"]["bind_portfolio_state_ref"] is None
        assert runtime_feedback["state_updates"]["bind_portfolio_state_carryover_ref"] is None
    else:
        previous_projection = previous_case["operational_context"]["lean_packet_execution_projection"]
        expected_state_ref = f"portfolio-state://{previous_case['case_id']}/generation2"
        expected_carry_ref = (
            f"portfolio-state-carryover://{previous_case['case_id']}->{case['case_id']}"
        )

        assert proof["carryover_status"] == "applied"
        assert proof["previous_case_id"] == previous_case["case_id"]
        assert proof["state_ref"] == expected_state_ref
        assert proof["carry_ref"] == expected_carry_ref
        assert proof["state_hash"].startswith("portfolio-state-")
        assert proof["previous_position_count"] == PORTFOLIO_LEG_COUNT
        assert set(proof["previous_positions_by_instrument"]) == set(previous_case["portfolio"]["instruments"])
        assert proof["previous_gross_exposure_pct"] > 0.0
        assert proof["previous_abs_market_value"] > 0.0
        assert 0.88 <= proof["target_capital_budget_scale"] < 1.0
        assert proof["rebalance_action"] in {
            "carry_prior_gross_exposure_budget_to_new_portfolio",
            "rebalance_overlapping_positions_with_carried_exposure",
        }
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["score_adjustments"]["risk-off"] > 0.0
        assert proof["score_adjustments"]["retain-observe"] > 0.0
        assert proof["score_adjustments"]["contrarian-check"] == 0.0

        for leg in previous_projection["leg_projections"]:
            position = proof["previous_positions_by_instrument"][leg["instrument"]]
            assert position["position_ref"] == f"{expected_state_ref}/position/{leg['instrument']}"
            assert position["instrument"] == leg["instrument"]
            assert position["execution_symbol"] == leg["execution_symbol"]
            assert position["lean_symbol"] == leg["lean_symbol"]
            assert position["direction"] == leg["direction"]
            assert position["quantity"] == round(abs(leg["fill_quantity"]), 6)
            assert position["signed_quantity"] == round(
                position["quantity"] * leg["direction"],
                6,
            )
            assert position["fill_price"] == round(leg["fill_price"], 6)
            assert position["abs_market_value"] > 0.0
            assert position["resolved_weight"] == leg["resolved_weight"]
            assert position["target_ref"] == leg["target_ref"]
            assert position["order_ref"] == leg["order_ref"]
            assert position["fill_ref"] == leg["fill_ref"]
            assert position["readback_ref"] == leg["readback_ref"]

        assert conflict["portfolio_state_carryover"] == proof["conflict_portfolio_state_carryover"]
        assert allocation["portfolio_state_carryover_ref"] == expected_carry_ref
        assert allocation["portfolio_state_ref"] == expected_state_ref
        assert allocation["portfolio_state_target_capital_budget_scale"] == proof[
            "target_capital_budget_scale"
        ]
        assert proof["resolved_allocation"]["capital_budget_pct"] == allocation["capital_budget_pct"]
        assert proof["resolved_allocation"]["weight_by_instrument"] == allocation["weight_by_instrument"]
        assert allocation["capital_budget_pct"] < 1.0
        assert handoff["portfolio_state_carryover_ref"] == expected_carry_ref
        assert handoff["portfolio_state_ref"] == expected_state_ref
        assert handoff["portfolio_state_hash"] == proof["state_hash"]
        assert handoff["portfolio_state_rebalance_action"] == proof["rebalance_action"]
        assert handoff["portfolio_state_target_capital_budget_scale"] == proof[
            "target_capital_budget_scale"
        ]
        assert expected_carry_ref in handoff["runtime_bundle_refs"]
        assert expected_state_ref in handoff["runtime_bundle_refs"]
        assert projection["capital_budget_pct"] == allocation["capital_budget_pct"]
        assert runtime_feedback["state_updates"]["bind_portfolio_state_ref"] == expected_state_ref
        assert runtime_feedback["state_updates"]["bind_portfolio_state_carryover_ref"] == expected_carry_ref
        assert runtime_feedback["state_updates"]["bind_portfolio_state_rebalance_action"] == proof[
            "rebalance_action"
        ]
        assert runtime_feedback["state_updates"]["bind_portfolio_state_target_capital_budget_scale"] == proof[
            "target_capital_budget_scale"
        ]
        assert runtime_feedback["replay"]["portfolio_state_carryover_bound"] is True

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_candidate = trace["selected_candidate"]
        selected_card = artifact["scorer"]["scorecards"][selected_id]
        scoring_carryover = scorer_inputs["portfolio_state_carryover"]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_action"] == selected_action
        assert trace["decision_inputs"]["portfolio_state_status"] == proof["carryover_status"]
        assert trace["decision_inputs"]["portfolio_state_ref"] == proof["state_ref"]
        assert trace["decision_inputs"]["portfolio_state_carry_ref"] == proof["carry_ref"]
        assert artifact["input_context"]["portfolio_state_status"] == proof["carryover_status"]
        assert artifact["input_context"]["portfolio_state_ref"] == proof["state_ref"]
        assert artifact["input_context"]["portfolio_state_carry_ref"] == proof["carry_ref"]
        assert artifact["input_context"]["previous_portfolio_position_count"] == proof[
            "previous_position_count"
        ]
        assert reasoning_request["portfolio_state_status"] == proof["carryover_status"]
        assert reasoning_request["portfolio_state_ref"] == proof["state_ref"]
        assert reasoning_request["portfolio_state_carry_ref"] == proof["carry_ref"]
        assert reasoning_request["portfolio_rebalance_action"] == proof["rebalance_action"]
        assert reasoning_response["portfolio_state_usage"]["model_id"] == proof["model_id"]
        assert reasoning_response["portfolio_state_usage"]["status"] == proof["carryover_status"]
        assert reasoning_response["portfolio_state_usage"]["state_ref"] == proof["state_ref"]
        assert reasoning_response["portfolio_state_usage"]["carry_ref"] == proof["carry_ref"]
        assert reasoning_response["portfolio_state_usage"]["previous_position_count"] == proof[
            "previous_position_count"
        ]
        assert reasoning_response["portfolio_state_usage"]["candidate_score_adjustments"] == proof[
            "score_adjustments"
        ]
        assert scoring_carryover["model_id"] == proof["model_id"]
        assert scoring_carryover["status"] == proof["carryover_status"]
        assert scoring_carryover["state_ref"] == proof["state_ref"]
        assert scoring_carryover["carry_ref"] == proof["carry_ref"]
        assert scorer_inputs["portfolio_state_score_adjustments"] == proof["score_adjustments"]
        assert selected_card["components"]["portfolio_state_adjustment"] == proof[
            "score_adjustments"
        ][selected_action]
        assert artifact["replay"]["uses_portfolio_state_carryover_or_declares_cold_start"] is True

        if proof["carryover_status"] == "cold_start":
            assert binding["decision_input_portfolio_state_ref"] is None
            assert binding["decision_input_portfolio_carry_ref"] is None
            assert binding["reasoning_consumes_portfolio_state_ref"] is False
            assert binding["reasoning_consumes_portfolio_carry_ref"] is False
            assert binding["candidate_request_consumes_portfolio_state_ref"] is False
            assert binding["candidate_request_consumes_portfolio_carry_ref"] is False
            assert binding["selected_candidate_cites_portfolio_state_ref"] is False
            assert binding["selected_candidate_cites_portfolio_carry_ref"] is False
            assert binding["scorer_portfolio_state_adjustment"] == 0.0
            assert selected_card["components"]["portfolio_state_adjustment"] == 0.0
        else:
            assert binding["decision_input_portfolio_state_ref"] == proof["state_ref"]
            assert binding["decision_input_portfolio_carry_ref"] == proof["carry_ref"]
            assert binding["reasoning_consumes_portfolio_state_ref"] is True
            assert binding["reasoning_consumes_portfolio_carry_ref"] is True
            assert binding["reasoning_usage_ref_matches"] is True
            assert binding["candidate_request_consumes_portfolio_state_ref"] is True
            assert binding["candidate_request_consumes_portfolio_carry_ref"] is True
            assert binding["selected_candidate_cites_portfolio_state_ref"] is True
            assert binding["selected_candidate_cites_portfolio_carry_ref"] is True
            assert binding["scorer_portfolio_state_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorer_portfolio_state_adjustment"] > 0.0
            assert binding["scorecard_portfolio_state_adjustment"] == proof["score_adjustments"][selected_action]
            assert proof["state_ref"] in reasoning_request["input_refs"]
            assert proof["carry_ref"] in reasoning_request["input_refs"]
            assert proof["state_ref"] in candidate_request["input_refs"]
            assert proof["carry_ref"] in candidate_request["input_refs"]
            assert proof["state_ref"] in selected_candidate["evidence_refs"]
            assert proof["carry_ref"] in selected_candidate["evidence_refs"]



def _assert_broker_adapter_carryover(case: dict, latest_case_by_persona: dict[str, dict]) -> None:
    proof = case["cross_cycle"]["broker_adapter_carryover"]
    traces = case["reflection"]["agent_decision_traces"]
    previous_case = latest_case_by_persona.get(case["persona_id"])
    operational = case["operational_context"]
    conflict = operational["persona_conflict_resolution"]
    allocation = conflict["resolved_allocation"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    state_updates = runtime_feedback["state_updates"]

    assert proof["model_id"] == PERSONA_BROKER_ADAPTER_CARRYOVER_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == "broker-adapter-carryover-proof://{}".format(case["case_id"])
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["broker_adapter_carryover"] == 1.0
    assert case["usable"]["broker_adapter_carryover_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["broker_adapter_response_carryover_drives_next_case_ooda"]["status"] == "passed"

    if previous_case is None:
        assert proof["carryover_status"] == "cold_start"
        assert proof["carryover_ref"] is None
        assert proof["previous_case_id"] is None
        assert proof["previous_followup_ref"] is None
        assert proof["previous_source_packet_ref"] is None
        assert proof["previous_action"] is None
        assert not any(
            str(ref).startswith((
                "broker-adapter-carryover://",
                "broker-adapter-followup://",
                "broker-adapter://",
            ))
            for ref in proof["evidence_refs"]
        )
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
        assert allocation["broker_adapter_carryover_ref"] is None
        assert handoff["broker_adapter_carryover_ref"] is None
        assert state_updates["bind_broker_adapter_carryover_ref"] is None
        assert runtime_feedback["replay"]["broker_adapter_carryover_bound"] is True
    else:
        previous_followup = previous_case["operational_context"]["broker_adapter_followup"]
        previous_persona_followup = previous_followup["persona_followup"]
        expected_carryover_ref = "broker-adapter-carryover://{}->{}".format(
            previous_case["case_id"],
            case["case_id"],
        )
        expected_followup_ref = "broker-adapter-followup://{}".format(
            previous_followup["followup_id"]
        )
        expected_source_packet_ref = previous_followup["source_packet_ref"]
        expected_refs = [
            expected_carryover_ref,
            expected_followup_ref,
            expected_source_packet_ref,
        ]

        assert proof["carryover_status"] == "applied"
        assert proof["carryover_ref"] == expected_carryover_ref
        assert proof["previous_case_id"] == previous_case["case_id"]
        assert proof["previous_followup_ref"] == expected_followup_ref
        assert proof["previous_source_packet_ref"] == expected_source_packet_ref
        assert proof["previous_source_packet_hash"] == previous_followup["source_packet_hash"]
        assert proof["previous_action"] == previous_persona_followup["action"]
        assert proof["previous_action_family"] == previous_persona_followup["action_family"]
        assert proof["previous_next_step"] == previous_persona_followup["next_persona_step"]
        assert proof["previous_scenario"] == previous_followup["scenario"]
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["evidence_refs"][:3] == expected_refs

        assert conflict["broker_adapter_carryover"] == proof["conflict_broker_adapter_carryover"]
        assert allocation["broker_adapter_carryover_ref"] == expected_carryover_ref
        assert allocation["previous_broker_adapter_followup_ref"] == expected_followup_ref
        assert allocation["previous_broker_adapter_source_packet_ref"] == expected_source_packet_ref
        assert handoff["broker_adapter_carryover_ref"] == expected_carryover_ref
        assert handoff["previous_broker_adapter_followup_ref"] == expected_followup_ref
        assert handoff["previous_broker_adapter_source_packet_ref"] == expected_source_packet_ref
        assert handoff["previous_broker_adapter_action"] == previous_persona_followup["action"]
        for ref in expected_refs:
            assert ref in handoff["runtime_bundle_refs"]
            assert ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
        assert runtime_feedback["replay"]["broker_adapter_carryover_bound"] is True
        assert state_updates["bind_broker_adapter_carryover_ref"] == expected_carryover_ref
        assert state_updates["bind_previous_broker_adapter_followup_ref"] == expected_followup_ref
        assert state_updates["bind_previous_broker_adapter_source_packet_ref"] == expected_source_packet_ref
        assert state_updates["bind_previous_broker_adapter_action"] == previous_persona_followup["action"]
        assert state_updates["bind_previous_broker_adapter_action_family"] == previous_persona_followup["action_family"]

    binding_by_trace = {binding["trace_id"]: binding for binding in proof["trace_bindings"]}
    for trace in traces:
        artifact = trace["agent_decision_artifact"]
        binding = binding_by_trace[trace["reflection_id"]]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        selected_action = binding["selected_action"]
        selected_card = artifact["scorer"]["scorecards"][trace["selected_candidate_id"]]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]

        assert trace["decision_inputs"]["broker_adapter_carryover_status"] == proof["carryover_status"]
        assert artifact["input_context"]["broker_adapter_carryover_status"] == proof["carryover_status"]
        assert reasoning_request["broker_adapter_carryover_status"] == proof["carryover_status"]
        assert reasoning_response["broker_adapter_carryover_usage"]["model_id"] == proof["model_id"]
        assert scorer_inputs["broker_adapter_carryover"]["model_id"] == proof["model_id"]
        assert scorer_inputs["broker_adapter_score_adjustments"] == proof["score_adjustments"]
        assert selected_card["components"]["broker_adapter_adjustment"] == proof["score_adjustments"][selected_action]
        assert binding["scorecard_broker_adapter_adjustment"] == binding["scorer_broker_adapter_adjustment"]
        assert artifact["replay"]["uses_broker_adapter_carryover_or_declares_cold_start"] is True

        if previous_case is None:
            assert binding["reasoning_consumes_carryover_ref"] is False
            assert binding["candidate_request_consumes_carryover_ref"] is False
            assert binding["selected_candidate_cites_carryover_ref"] is False
            assert binding["scorer_broker_adapter_adjustment"] == 0.0
        else:
            assert proof["carryover_ref"] in reasoning_request["input_refs"]
            assert proof["previous_followup_ref"] in reasoning_request["input_refs"]
            assert proof["previous_source_packet_ref"] in reasoning_request["input_refs"]
            assert proof["carryover_ref"] in candidate_request["input_refs"]
            assert proof["previous_followup_ref"] in candidate_request["input_refs"]
            assert proof["previous_source_packet_ref"] in candidate_request["input_refs"]
            assert binding["reasoning_consumes_carryover_ref"] is True
            assert binding["candidate_request_consumes_carryover_ref"] is True
            assert binding["selected_candidate_cites_carryover_ref"] is True
            assert binding["scorer_broker_adapter_adjustment"] > 0.0

def _assert_openclaw_session_continuity(case: dict, latest_case_by_persona: dict[str, dict]) -> None:
    proof = case["cross_cycle"]["openclaw_session_continuity"]
    traces = case["reflection"]["agent_decision_traces"]
    previous_case = latest_case_by_persona.get(case["persona_id"])
    handoff = case["operational_context"]["lean_handoff"]
    openclaw_handoff = case["operational_context"]["openclaw_session_handoff"]
    runtime_feedback = case["operational_context"]["lean_runtime_feedback"]
    request = proof["openclaw_request"]

    assert proof["model_id"] == PERSONA_OPENCLAW_SESSION_CONTINUITY_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"openclaw-session-continuity://{case['case_id']}"
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["openclaw_session_continuity"] == 1.0
    assert case["usable"]["openclaw_session_continuity_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["openclaw_session_continuity_drives_next_case_request"]["status"] == "passed"

    assert proof["current_source_oss_ref"] == openclaw_handoff["source_oss_ref"]
    assert proof["current_context_ref"] == openclaw_handoff["context_ref"]
    assert proof["current_context_hash"] == openclaw_handoff["context_hash"]
    assert proof["current_session_ref"] == openclaw_handoff["session_ref"]
    assert proof["current_session_id"] == openclaw_handoff["session_id"]
    assert proof["current_upstream_session_ref"] == openclaw_handoff["upstream_session_ref"]
    assert proof["current_upstream_session_id"] == openclaw_handoff["upstream_session_id"]
    assert proof["current_session_state"] == "active"
    assert request["model_id"] == PERSONA_OPENCLAW_SESSION_CONTINUITY_MODEL_ID
    assert request["case_id"] == case["case_id"]
    assert request["persona_id"] == case["persona_id"]
    assert request["requested_before_persona_reasoning"] is True

    if previous_case is None:
        assert proof["continuity_status"] == "cold_start"
        assert proof["continuity_ref"] is None
        assert proof["previous_case_id"] is None
        assert proof["previous_source_oss_ref"] is None
        assert proof["previous_context_ref"] is None
        assert proof["previous_session_ref"] is None
        assert proof["previous_upstream_session_ref"] is None
        assert request["status"] == "cold_start"
        assert request["request_action"] == "start_openclaw_session"
        assert request["continuity_ref"] is None
        assert request["input_refs"] == []
        assert handoff["openclaw_session_continuity_status"] == "cold_start"
        assert handoff["openclaw_session_continuity_ref"] is None
        assert runtime_feedback["state_updates"]["bind_openclaw_session_continuity_ref"] is None
        assert runtime_feedback["state_updates"]["bind_openclaw_previous_session_ref"] is None
    else:
        previous_openclaw = previous_case["operational_context"]["openclaw_session_handoff"]
        previous_feedback = previous_case["operational_context"]["lean_runtime_feedback"]
        previous_schedule = previous_case["operational_context"]["autonomous_schedule"]
        expected_continuity_ref = (
            f"openclaw-session-continuity://{previous_case['case_id']}->{case['case_id']}"
        )
        expected_refs = [
            expected_continuity_ref,
            previous_openclaw["source_oss_ref"],
            previous_openclaw["context_ref"],
            previous_openclaw["session_ref"],
            previous_openclaw["upstream_session_ref"],
        ]

        assert proof["continuity_status"] == "applied"
        assert proof["continuity_ref"] == expected_continuity_ref
        assert proof["previous_case_id"] == previous_case["case_id"]
        assert proof["previous_source_oss_ref"] == previous_openclaw["source_oss_ref"]
        assert proof["previous_context_ref"] == previous_openclaw["context_ref"]
        assert proof["previous_context_hash"] == previous_openclaw["context_hash"]
        assert proof["previous_session_ref"] == previous_openclaw["session_ref"]
        assert proof["previous_session_id"] == previous_openclaw["session_id"]
        assert proof["previous_upstream_session_ref"] == previous_openclaw["upstream_session_ref"]
        assert proof["previous_upstream_session_id"] == previous_openclaw["upstream_session_id"]
        assert request["status"] == "applied"
        assert request["request_action"] == "continue_prior_openclaw_session"
        assert request["continuity_ref"] == expected_continuity_ref
        assert request["previous_session_ref"] == previous_openclaw["session_ref"]
        assert request["previous_context_ref"] == previous_openclaw["context_ref"]
        assert request["previous_upstream_session_ref"] == previous_openclaw["upstream_session_ref"]
        assert set(expected_refs).issubset(set(request["input_refs"]))
        assert f"lean-runtime-feedback://{previous_feedback['feedback_id']}" in request["input_refs"]
        assert f"schedule://{previous_schedule['schedule_id']}" in request["input_refs"]
        assert handoff["openclaw_session_continuity_ref"] == expected_continuity_ref
        assert handoff["openclaw_previous_session_ref"] == previous_openclaw["session_ref"]
        assert all(ref in handoff["runtime_bundle_refs"] for ref in expected_refs)
        assert runtime_feedback["replay"]["openclaw_session_continuity_bound"] is True
        assert runtime_feedback["state_updates"]["bind_openclaw_session_continuity_ref"] == expected_continuity_ref
        assert runtime_feedback["state_updates"]["bind_openclaw_previous_session_ref"] == previous_openclaw["session_ref"]
        assert runtime_feedback["state_updates"]["bind_openclaw_previous_context_ref"] == previous_openclaw["context_ref"]
        assert runtime_feedback["state_updates"]["bind_openclaw_previous_upstream_session_ref"] == previous_openclaw[
            "upstream_session_ref"
        ]
        assert all(ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"] for ref in expected_refs)

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning_refs = artifact["persona_reasoning"]["request"]["input_refs"]
        generation_refs = artifact["candidate_generation"]["request"]["input_refs"]
        selected_refs = trace["selected_candidate"]["evidence_refs"]

        assert binding["generation"] == artifact["generation"]
        assert binding["reasoning_consumes_prior_openclaw_session"] is True
        assert binding["candidate_generation_consumes_prior_openclaw_session"] is True
        assert binding["selected_candidate_cites_prior_openclaw_session"] is True
        assert binding["reasoning_consumes_current_openclaw_source"] is True
        assert binding["candidate_generation_consumes_current_openclaw_session"] is True
        assert binding["selected_candidate_cites_current_openclaw_followup"] is True
        assert proof["current_source_oss_ref"] in reasoning_refs
        assert proof["current_source_oss_ref"] in generation_refs
        assert any(
            str(ref).startswith("followup://")
            and "/session/" in str(ref)
            and "/openclaw/" in str(ref)
            for ref in selected_refs
        )
        if proof["continuity_status"] == "applied":
            prior_refs = [
                proof["continuity_ref"],
                proof["previous_source_oss_ref"],
                proof["previous_context_ref"],
                proof["previous_session_ref"],
                proof["previous_upstream_session_ref"],
            ]
            for ref in prior_refs:
                assert ref in reasoning_refs
                assert ref in generation_refs
                assert ref in selected_refs


def _assert_persisted_cycle_resume_carryover(case: dict, latest_case_by_persona: dict[str, dict]) -> None:
    proof = case["cross_cycle"]["persisted_cycle_resume"]
    traces = case["reflection"]["agent_decision_traces"]
    previous_case = latest_case_by_persona.get(case["persona_id"])

    assert proof["model_id"] == PERSONA_PERSISTED_CYCLE_RESUME_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"persisted-cycle-resume://{case['case_id']}"
    assert proof["source_cross_cycle_proof_ref"] == case["cross_cycle"]["runtime_feedback_carryover"]["proof_ref"]
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["persisted_cycle_resume_carryover"] == 1.0
    assert case["usable"]["persisted_cycle_resume_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["persisted_cycle_resume_replays_after_restart_and_schedule"]["status"] == "passed"

    if previous_case is None:
        assert proof["resume_status"] == "cold_start"
        assert proof["previous_case_id"] is None
        assert proof["state_ref"] is None
        assert proof["runtime_feedback_ref"] is None
        assert proof["restart_checkpoint_ref"] is None
        assert proof["schedule_ref"] is None
        assert proof["object_store_metadata_ref"] is None
        assert proof["object_store_artifact_ref"] is None
        assert proof["resume_step"] is None
        assert proof["persisted_refs"] == []
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
    else:
        previous_feedback = previous_case["operational_context"]["lean_runtime_feedback"]
        previous_recovery = previous_case["operational_context"]["restart_recovery"]
        previous_schedule = previous_case["operational_context"]["autonomous_schedule"]
        previous_runtime_readback = previous_feedback["runtime_feedback"]
        expected_checkpoint_ref = f"checkpoint://{previous_recovery['checkpoint_id']}"
        expected_schedule_ref = f"schedule://{previous_schedule['schedule_id']}"
        expected_metadata_ref = f"object-store://{previous_runtime_readback['object_store_metadata_key']}"
        expected_artifact_ref = f"object-store://{previous_runtime_readback['object_store_artifact_key']}"

        assert proof["resume_status"] == "applied"
        assert proof["previous_case_id"] == previous_case["case_id"]
        assert proof["state_ref"] == f"cross-cycle-runtime://{previous_case['case_id']}->{case['case_id']}"
        assert proof["runtime_feedback_ref"] == f"lean-runtime-feedback://{previous_feedback['feedback_id']}"
        assert proof["restart_checkpoint_ref"] == expected_checkpoint_ref
        assert proof["schedule_ref"] == expected_schedule_ref
        assert proof["next_cycle_due_at"] == previous_schedule["next_cycle_due_at"]
        assert proof["feedback_scheduled_cycle_due_at"] == previous_feedback["state_updates"][
            "schedule_next_cycle_after_feedback"
        ]
        assert proof["next_cycle_due_at"] == proof["feedback_scheduled_cycle_due_at"]
        assert proof["object_store_metadata_ref"] == expected_metadata_ref
        assert proof["object_store_artifact_ref"] == expected_artifact_ref
        assert proof["resume_step"] == previous_recovery["resume_step"]
        assert proof["next_ooda_step"] == previous_feedback["persona_ooda_followup"]["ooda_step"]
        assert proof["next_scheduler_phase"] == previous_feedback["persona_ooda_followup"]["next_scheduler_phase"]
        assert proof["persisted_refs"] == [
            expected_checkpoint_ref,
            expected_schedule_ref,
            expected_metadata_ref,
            expected_artifact_ref,
        ]
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["score_adjustments"]["risk-off"] > 0.0

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning_request = artifact["persona_reasoning"]["request"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorecards = artifact["scorer"]["scorecards"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_card = scorecards[selected_id]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_action"] == selected_action
        assert binding["decision_input_state_ref"] == proof["state_ref"]
        assert selected_card["components"]["cross_cycle_adjustment"] == proof["score_adjustments"][selected_action]

        if proof["resume_status"] == "cold_start":
            assert binding["reasoning_consumes_persisted_refs"] is False
            assert binding["candidate_request_consumes_persisted_refs"] is False
            assert binding["selected_candidate_cites_persisted_refs"] is False
            assert binding["scorer_cross_cycle_adjustment"] == 0.0
            assert selected_card["components"]["cross_cycle_adjustment"] == 0.0
        else:
            persisted_refs = set(proof["persisted_refs"])
            assert binding["reasoning_consumes_persisted_refs"] is True
            assert binding["candidate_request_consumes_persisted_refs"] is True
            assert binding["selected_candidate_cites_persisted_refs"] is True
            assert binding["scorer_cross_cycle_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorer_cross_cycle_adjustment"] > 0.0
            assert persisted_refs.issubset(set(reasoning_request["input_refs"]))
            assert persisted_refs.issubset(set(candidate_request["input_refs"]))
            assert persisted_refs.issubset(set(trace["selected_candidate"]["evidence_refs"]))


def _assert_multi_cycle_lineage_carryover(case: dict, persona_case_history: list[dict]) -> None:
    proof = case["cross_cycle"]["multi_cycle_lineage"]
    traces = case["reflection"]["agent_decision_traces"]
    lineage_cases = persona_case_history[-2:]

    assert proof["model_id"] == PERSONA_MULTI_CYCLE_LINEAGE_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["proof_ref"] == f"multi-cycle-lineage://{case['case_id']}"
    assert proof["source_cross_cycle_proof_ref"] == case["cross_cycle"]["runtime_feedback_carryover"]["proof_ref"]
    assert proof["source_persisted_cycle_resume_ref"] == case["cross_cycle"]["persisted_cycle_resume"]["proof_ref"]
    assert proof["input_hash"]
    assert len(proof["trace_bindings"]) == len(traces) == 2
    assert all(proof["replay"].values())
    assert case["usability_dimensions"]["multi_cycle_lineage_carryover"] == 1.0
    assert case["usable"]["multi_cycle_lineage_drives_next_case"] is True

    check_by_name = {
        check["check"]: check
        for check in case["validation_cycle"]["execution_review"]["checks"]
    }
    assert check_by_name["multi_cycle_lineage_drives_persona_next_case_decision"]["status"] == "passed"

    def lineage_state(previous_case: dict) -> dict:
        feedback = previous_case["operational_context"]["lean_runtime_feedback"]
        recovery = previous_case["operational_context"]["restart_recovery"]
        schedule = previous_case["operational_context"]["autonomous_schedule"]
        ledger = previous_case["oss_feedback"]["ooda_causal_ledger"]
        selected_event = next(
            event
            for event in ledger["events"]
            if event["event_type"] == "selected_action"
        )
        runtime_readback = feedback["runtime_feedback"]
        state_ref = f"multi-cycle-runtime://{previous_case['case_id']}->{case['case_id']}"
        runtime_feedback_ref = f"lean-runtime-feedback://{feedback['feedback_id']}"
        checkpoint_ref = f"checkpoint://{recovery['checkpoint_id']}"
        schedule_ref = f"schedule://{schedule['schedule_id']}"
        metadata_ref = f"object-store://{runtime_readback['object_store_metadata_key']}"
        artifact_ref = f"object-store://{runtime_readback['object_store_artifact_key']}"
        return {
            "case_id": previous_case["case_id"],
            "state_ref": state_ref,
            "runtime_feedback_ref": runtime_feedback_ref,
            "checkpoint_ref": checkpoint_ref,
            "schedule_ref": schedule_ref,
            "metadata_ref": metadata_ref,
            "artifact_ref": artifact_ref,
            "ledger_ref": ledger["ledger_ref"],
            "selected_action_ref": selected_event["output_ref"],
            "next_ooda_step": feedback["persona_ooda_followup"]["ooda_step"],
            "next_scheduler_phase": feedback["persona_ooda_followup"]["next_scheduler_phase"],
            "refs": [
                state_ref,
                runtime_feedback_ref,
                checkpoint_ref,
                schedule_ref,
                metadata_ref,
                artifact_ref,
                ledger["ledger_ref"],
                selected_event["output_ref"],
            ],
        }

    if not lineage_cases:
        assert proof["lineage_status"] == "cold_start"
        assert proof["lineage_ref"] is None
        assert proof["lineage_depth"] == 0
        assert proof["lineage_case_ids"] == []
        assert proof["latest_case_id"] is None
        assert proof["older_case_id"] is None
        assert proof["latest_runtime_feedback_ref"] is None
        assert proof["older_runtime_feedback_ref"] is None
        assert proof["lineage_refs"] == []
        assert proof["trend_signal"] == "cold_start_no_prior_cycle"
        assert all(float(value) == 0.0 for value in proof["score_adjustments"].values())
        expected_refs: list[str] = []
    else:
        latest = lineage_state(lineage_cases[-1])
        older = lineage_state(lineage_cases[-2]) if len(lineage_cases) == 2 else None
        expected_status = "lineage_applied" if older else "single_prior"
        expected_case_ids = [previous_case["case_id"] for previous_case in lineage_cases]
        expected_lineage_ref = f"multi-cycle-lineage://{'->'.join(expected_case_ids)}->{case['case_id']}"
        expected_refs = list(dict.fromkeys([
            expected_lineage_ref,
            *latest["refs"],
            *(older["refs"] if older else []),
        ]))

        assert proof["lineage_status"] == expected_status
        assert proof["lineage_ref"] == expected_lineage_ref
        assert proof["lineage_depth"] == len(lineage_cases)
        assert proof["lineage_case_ids"] == expected_case_ids
        assert proof["latest_case_id"] == latest["case_id"]
        assert proof["older_case_id"] == (older["case_id"] if older else None)
        assert proof["latest_state_ref"] == latest["state_ref"]
        assert proof["older_state_ref"] == (older["state_ref"] if older else None)
        assert proof["latest_runtime_feedback_ref"] == latest["runtime_feedback_ref"]
        assert proof["older_runtime_feedback_ref"] == (older["runtime_feedback_ref"] if older else None)
        assert proof["latest_restart_checkpoint_ref"] == latest["checkpoint_ref"]
        assert proof["older_restart_checkpoint_ref"] == (older["checkpoint_ref"] if older else None)
        assert proof["latest_schedule_ref"] == latest["schedule_ref"]
        assert proof["older_schedule_ref"] == (older["schedule_ref"] if older else None)
        assert proof["latest_object_store_metadata_ref"] == latest["metadata_ref"]
        assert proof["older_object_store_metadata_ref"] == (older["metadata_ref"] if older else None)
        assert proof["latest_object_store_artifact_ref"] == latest["artifact_ref"]
        assert proof["older_object_store_artifact_ref"] == (older["artifact_ref"] if older else None)
        assert proof["latest_next_ooda_step"] == latest["next_ooda_step"]
        assert proof["older_next_ooda_step"] == (older["next_ooda_step"] if older else None)
        assert proof["latest_next_scheduler_phase"] == latest["next_scheduler_phase"]
        assert proof["older_next_scheduler_phase"] == (older["next_scheduler_phase"] if older else None)
        assert proof["lineage_refs"] == expected_refs
        assert proof["score_adjustments"]["feedback-adapt"] > 0.0
        assert proof["score_adjustments"]["risk-off"] > 0.0
        assert proof["score_adjustments"]["retain-observe"] == 0.0
        assert proof["score_adjustments"]["contrarian-check"] == 0.0
        if older:
            assert proof["trend_signal"] == "latest_runtime_feedback_supersedes_older_cycle_trend"
        else:
            assert proof["trend_signal"] == "single_prior_runtime_feedback_bootstraps_lineage"

    binding_by_trace = {
        binding["trace_id"]: binding
        for binding in proof["trace_bindings"]
    }
    assert set(binding_by_trace) == {trace["reflection_id"] for trace in traces}
    for trace in traces:
        binding = binding_by_trace[trace["reflection_id"]]
        artifact = trace["agent_decision_artifact"]
        reasoning = artifact["persona_reasoning"]
        reasoning_request = reasoning["request"]
        reasoning_response = reasoning["response"]
        candidate_request = artifact["candidate_generation"]["request"]
        scorer_inputs = artifact["scorer"]["scoring_inputs"]
        scorecards = artifact["scorer"]["scorecards"]
        selected_id = trace["selected_candidate_id"]
        selected_action = _candidate_action_from_id(selected_id)
        selected_card = scorecards[selected_id]

        assert binding["generation"] == artifact["generation"]
        assert binding["trace_id"] == trace["reflection_id"]
        assert binding["selected_action"] == selected_action
        assert trace["decision_inputs"]["multi_cycle_lineage_status"] == proof["lineage_status"]
        assert trace["decision_inputs"]["multi_cycle_lineage_ref"] == proof["lineage_ref"]
        assert trace["decision_inputs"]["multi_cycle_latest_runtime_feedback_ref"] == proof["latest_runtime_feedback_ref"]
        assert trace["decision_inputs"]["multi_cycle_older_runtime_feedback_ref"] == proof["older_runtime_feedback_ref"]
        assert artifact["input_context"]["multi_cycle_lineage_status"] == proof["lineage_status"]
        assert artifact["input_context"]["multi_cycle_lineage_ref"] == proof["lineage_ref"]
        assert artifact["input_context"]["multi_cycle_lineage_depth"] == proof["lineage_depth"]
        assert artifact["input_context"]["multi_cycle_lineage_case_ids"] == proof["lineage_case_ids"]
        assert artifact["input_context"]["multi_cycle_latest_case_id"] == proof["latest_case_id"]
        assert artifact["input_context"]["multi_cycle_older_case_id"] == proof["older_case_id"]
        assert reasoning_request["multi_cycle_lineage_status"] == proof["lineage_status"]
        assert reasoning_request["multi_cycle_lineage_ref"] == proof["lineage_ref"]
        assert reasoning_request["multi_cycle_latest_runtime_feedback_ref"] == proof["latest_runtime_feedback_ref"]
        assert reasoning_request["multi_cycle_older_runtime_feedback_ref"] == proof["older_runtime_feedback_ref"]
        assert reasoning_response["multi_cycle_lineage_usage"]["status"] == proof["lineage_status"]
        assert reasoning_response["multi_cycle_lineage_usage"]["lineage_ref"] == proof["lineage_ref"]
        assert reasoning_response["multi_cycle_lineage_usage"]["lineage_depth"] == proof["lineage_depth"]
        assert reasoning_response["multi_cycle_lineage_usage"]["lineage_case_ids"] == proof["lineage_case_ids"]
        assert reasoning_response["multi_cycle_lineage_usage"]["latest_runtime_feedback_ref"] == proof[
            "latest_runtime_feedback_ref"
        ]
        assert reasoning_response["multi_cycle_lineage_usage"]["older_runtime_feedback_ref"] == proof[
            "older_runtime_feedback_ref"
        ]
        assert reasoning_response["multi_cycle_lineage_usage"]["trend_signal"] == proof["trend_signal"]
        assert reasoning_response["multi_cycle_lineage_usage"]["candidate_score_adjustments"] == proof[
            "score_adjustments"
        ]
        assert scorer_inputs["multi_cycle_lineage_context"]["status"] == proof["lineage_status"]
        assert scorer_inputs["multi_cycle_lineage_context"].get("lineage_ref") == proof["lineage_ref"]
        assert scorer_inputs["multi_cycle_lineage_context"].get("lineage_case_ids") == proof["lineage_case_ids"]
        assert scorer_inputs["multi_cycle_lineage_score_adjustments"] == proof["score_adjustments"]
        assert selected_card["components"]["multi_cycle_lineage_adjustment"] == proof[
            "score_adjustments"
        ][selected_action]
        assert artifact["replay"]["uses_multi_cycle_lineage_or_declares_cold_start"] is True

        if proof["lineage_status"] == "cold_start":
            assert binding["decision_input_lineage_ref"] is None
            assert binding["reasoning_consumes_lineage_refs"] is False
            assert binding["candidate_request_consumes_lineage_refs"] is False
            assert binding["selected_candidate_cites_lineage_refs"] is False
            assert binding["scorer_multi_cycle_lineage_adjustment"] == 0.0
            assert selected_card["components"]["multi_cycle_lineage_adjustment"] == 0.0
        else:
            expected_ref_set = set(expected_refs)
            assert binding["decision_input_lineage_ref"] == proof["lineage_ref"]
            assert binding["reasoning_consumes_lineage_refs"] is True
            assert binding["candidate_request_consumes_lineage_refs"] is True
            assert binding["selected_candidate_cites_lineage_refs"] is True
            assert binding["scorer_multi_cycle_lineage_adjustment"] == proof["score_adjustments"][selected_action]
            assert binding["scorer_multi_cycle_lineage_adjustment"] > 0.0
            assert binding["decision_replay_uses_lineage"] is True
            assert expected_ref_set.issubset(set(reasoning_request["input_refs"]))
            assert expected_ref_set.issubset(set(candidate_request["input_refs"]))
            assert expected_ref_set.issubset(set(trace["selected_candidate"]["evidence_refs"]))


def _assert_case_specific_upstream_artifacts(case: dict) -> None:
    artifacts = case["case_upstream_artifacts"]
    vectorbt = artifacts["vectorbt"]
    tracker = artifacts["tracker"]
    selected_oss = artifacts["selected_oss"]
    arbitration = artifacts["oss_disagreement_arbitration"]
    reconciliation = artifacts["tracking_reconciliation"]
    alpha_revision = artifacts["alpha_seed_revision"]
    degraded_response = artifacts["degraded_oss_response"]
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
    assert persona_response["next_disagreement_action"] == "arbitrate_multi_oss_disagreement"
    vectorbt_ref = f"oss://vectorbt/{vectorbt['request_id']}"
    tracker_ref = f"oss://{tracker['component']}/{tracker['request_id']}"
    experiment_ref = f"experiment://{tracker['backend']}/{tracker['run_id']}"
    expected_divergence_type = TRACKING_DIVERGENCE_TYPES_BY_SCENARIO[
        case["operational_context"]["scenario"]
    ]
    expected_repair_action = TRACKING_RECONCILIATION_ACTION_BY_TYPE[
        expected_divergence_type
    ]
    expected_alpha_action = ALPHA_SEED_REVISION_ACTION_BY_COMPONENT[
        selected_oss["alpha_model"]["component"]
    ]
    quality_role = degraded_response["role"]
    expected_quality_issue = OSS_QUALITY_ISSUE_BY_ROLE[quality_role]
    expected_quality_repair_action = OSS_QUALITY_REPAIR_ACTION_BY_ROLE[quality_role]
    expected_quality_downweighted_action = OSS_QUALITY_AFFECTED_ACTION_BY_ROLE[
        quality_role
    ]
    assert persona_response["next_tracking_reconciliation_action"] == expected_repair_action
    assert persona_response["next_alpha_seed_action"] == expected_alpha_action
    assert persona_response["next_oss_quality_action"] == expected_quality_repair_action
    assert vectorbt_ref in persona_response["evidence_refs"]
    assert tracker_ref in persona_response["evidence_refs"]
    assert experiment_ref in persona_response["evidence_refs"]
    assert reconciliation["reconciliation_ref"] in persona_response["evidence_refs"]
    assert alpha_revision["revision_ref"] in persona_response["evidence_refs"]
    assert degraded_response["repair_ref"] in persona_response["evidence_refs"]
    for entry in selected_oss.values():
        assert f"oss://{entry['component']}/{entry['request_id']}" in persona_response["evidence_refs"]

    expected_conflict_type = OSS_DISAGREEMENT_TYPES_BY_SCENARIO[case["operational_context"]["scenario"]]
    expected_source_roles = OSS_DISAGREEMENT_SOURCE_ROLES_BY_TYPE[expected_conflict_type]
    expected_resolution_action = OSS_DISAGREEMENT_RESOLUTION_ACTION_BY_TYPE[expected_conflict_type]
    assert arbitration["model_id"] == PERSONA_OSS_DISAGREEMENT_ARBITRATION_MODEL_ID
    assert arbitration["status"] == "resolved"
    assert arbitration["case_id"] == case["case_id"]
    assert arbitration["arbitration_ref"] in persona_response["evidence_refs"]
    assert arbitration["source_feedback_id"] == artifacts["feedback_id"]
    assert arbitration["source_followup_loop_ref"] == case["oss_feedback"]["response_followup_loop"]["loop_ref"]
    assert len(arbitration["conflicts"]) == 1
    conflict = arbitration["conflicts"][0]
    assert conflict["conflict_type"] == expected_conflict_type
    assert tuple(conflict["source_roles"]) == expected_source_roles
    assert conflict["resolution_action"] == expected_resolution_action
    assert conflict["resolution_ref"].startswith(arbitration["arbitration_ref"])
    assert all(ref.startswith("oss://") for ref in conflict["source_refs"])
    assert conflict["observed_signals"]
    assert arbitration["candidate_score_adjustments"][expected_resolution_action] > 0
    assert set(arbitration["candidate_evidence_refs_by_action"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert arbitration["arbitration_ref"] in arbitration["candidate_evidence_refs_by_action"]["feedback-adapt"]
    assert arbitration["persona_arbitration_response"]["next_action"] == "score_candidates_with_arbitrated_oss_weights"
    assert arbitration["persona_arbitration_response"]["preferred_candidate_action"] == "feedback-adapt"
    assert expected_resolution_action in arbitration["persona_arbitration_response"]["resolution_actions"]
    assert all(arbitration["replay"].values())
    assert arbitration["input_hash"]

    assert reconciliation["model_id"] == PERSONA_TRACKING_RECONCILIATION_MODEL_ID
    assert reconciliation["status"] == "reconciled"
    assert reconciliation["case_id"] == case["case_id"]
    assert reconciliation["scenario"] == case["operational_context"]["scenario"]
    assert reconciliation["backend"] == tracker["backend"]
    assert reconciliation["tracker_request_id"] == tracker["request_id"]
    assert reconciliation["run_id"] == tracker["run_id"]
    assert reconciliation["artifact_uri"] == tracker["artifact_uri"]
    assert reconciliation["source_vectorbt_run_id"] == vectorbt["run_id"]
    assert reconciliation["source_feedback_id"] == artifacts["feedback_id"]
    assert reconciliation["divergence"]["divergence_type"] == expected_divergence_type
    assert reconciliation["divergence"]["backend"] == tracker["backend"]
    assert reconciliation["divergence"]["expected"] != reconciliation["divergence"]["readback"]
    assert set(reconciliation["divergence"]["source_refs"]) == {
        vectorbt_ref,
        tracker_ref,
        experiment_ref,
    }
    assert reconciliation["repair"]["action"] == expected_repair_action
    assert reconciliation["repair"]["repair_ref"].startswith(reconciliation["reconciliation_ref"])
    assert reconciliation["repair"]["normalized_experiment_ref"] == experiment_ref
    assert reconciliation["repair"]["next_persona_step"] == "cite_reconciled_experiment_ref"
    assert reconciliation["candidate_score_adjustments"]["feedback-adapt"] > 0
    assert reconciliation["candidate_score_adjustments"]["retain-observe"] > 0
    assert set(reconciliation["candidate_evidence_refs_by_action"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(reconciliation["candidate_evidence_refs_by_action"]["feedback-adapt"]).issuperset(
        {
            reconciliation["reconciliation_ref"],
            reconciliation["repair"]["repair_ref"],
            vectorbt_ref,
            tracker_ref,
            experiment_ref,
        }
    )
    assert (
        reconciliation["persona_reconciliation_response"]["next_action"]
        == "score_candidates_with_reconciled_tracking_readback"
    )
    assert reconciliation["persona_reconciliation_response"]["preferred_candidate_action"] == "feedback-adapt"
    assert expected_repair_action in reconciliation["persona_reconciliation_response"]["repair_actions"]
    assert all(reconciliation["replay"].values())
    assert reconciliation["input_hash"]

    alpha_entry = selected_oss["alpha_model"]
    policy_entry = selected_oss["policy_candidate"]
    reflection_entry = selected_oss["reflection_artifact"]
    risk_entry = selected_oss["risk_analytics"]
    alpha_ref = f"oss://{alpha_entry['component']}/{alpha_entry['request_id']}"
    policy_ref = f"oss://{policy_entry['component']}/{policy_entry['request_id']}"
    reflection_ref = f"oss://{reflection_entry['component']}/{reflection_entry['request_id']}"
    risk_ref = f"oss://{risk_entry['component']}/{risk_entry['request_id']}"
    assert alpha_revision["model_id"] == PERSONA_ALPHA_SEED_REVISION_MODEL_ID
    assert alpha_revision["status"] == "applied"
    assert alpha_revision["case_id"] == case["case_id"]
    assert alpha_revision["alpha_component"] == alpha_entry["component"]
    assert alpha_revision["source_feedback_id"] == artifacts["feedback_id"]
    assert alpha_revision["revision"]["action"] == expected_alpha_action
    assert alpha_revision["revision"]["base_seed_key"] == case["seed_key"]
    assert alpha_revision["revision"]["base_seed_ref"] == f"alpha-seed://{case['seed_key']}"
    assert alpha_revision["revision"]["source_alpha_request_id"] == alpha_entry["request_id"]
    assert alpha_revision["revision"]["source_alpha_artifact_family"] == alpha_entry["artifact_family"]
    assert alpha_revision["revision"]["downstream_vectorbt_request_id"] == vectorbt["request_id"]
    assert alpha_revision["revision"]["downstream_tracker_run_id"] == tracker["run_id"]
    assert alpha_revision["revision"]["downstream_policy_candidate_request_id"] == policy_entry["request_id"]
    assert alpha_revision["revision"]["allowed_windows"] == ["observe", "feedback"]
    assert alpha_revision["revision"]["forbidden_windows_not_used"] == ["holdout", "future_holdout"]
    assert set(alpha_revision["source_refs"]) == {
        f"alpha-seed://{case['seed_key']}",
        alpha_ref,
        vectorbt_ref,
        tracker_ref,
        experiment_ref,
    }
    assert alpha_revision["candidate_score_adjustments"]["feedback-adapt"] > 0
    assert set(alpha_revision["candidate_evidence_refs_by_action"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(alpha_revision["candidate_evidence_refs_by_action"]["feedback-adapt"]).issuperset(
        {
            alpha_revision["revision_ref"],
            f"alpha-seed://{case['seed_key']}",
            alpha_ref,
            vectorbt_ref,
            tracker_ref,
            experiment_ref,
            policy_ref,
        }
    )
    assert risk_ref in alpha_revision["candidate_evidence_refs_by_action"]["risk-off"]
    assert reflection_ref in alpha_revision["candidate_evidence_refs_by_action"]["contrarian-check"]
    assert alpha_revision["persona_alpha_response"]["next_action"] == "score_candidates_with_alpha_seed_revision"
    assert alpha_revision["persona_alpha_response"]["preferred_candidate_action"] == "feedback-adapt"
    assert expected_alpha_action in alpha_revision["persona_alpha_response"]["revision_actions"]
    assert all(alpha_revision["replay"].values())
    assert alpha_revision["input_hash"]

    quality_component = degraded_response["component"]
    quality_request_id = degraded_response["request_id"]
    quality_source_ref = f"oss://{quality_component}/{quality_request_id}"
    quality_persona_response = degraded_response["persona_quality_response"]
    assert degraded_response["model_id"] == PERSONA_DEGRADED_OSS_RESPONSE_MODEL_ID
    assert degraded_response["status"] == "repaired"
    assert degraded_response["case_id"] == case["case_id"]
    assert degraded_response["role"] in OSS_QUALITY_ROLES
    assert quality_role == degraded_response["role"]
    assert degraded_response["source_oss_ref"] == quality_source_ref
    assert degraded_response["issue_type"] == expected_quality_issue
    assert degraded_response["quality_signal"]["source_status"] == "completed"
    assert degraded_response["quality_signal"]["completed_but_degraded"] is True
    assert degraded_response["quality_signal"]["quality_score"] < degraded_response[
        "quality_signal"
    ]["threshold"]
    assert degraded_response["repair_request"]["requested_after_oss_response"] is True
    assert degraded_response["repair_request"]["next_action"] == expected_quality_repair_action
    assert degraded_response["repair_request"]["quality_ref"] == degraded_response["quality_ref"]
    assert degraded_response["repair_request"]["source_oss_ref"] == quality_source_ref
    assert quality_persona_response["status"] == "completed"
    assert quality_persona_response["repair_action"] == expected_quality_repair_action
    assert quality_persona_response["downweighted_candidate_action"] == (
        expected_quality_downweighted_action
    )
    assert quality_persona_response["accepted_for_handoff_after_repair"] is True
    assert quality_persona_response["used_by_generations"] == [1, 2]
    assert degraded_response["candidate_score_adjustments"]["feedback-adapt"] > 0
    assert degraded_response["source_quality_penalty_by_action"][
        expected_quality_downweighted_action
    ] < 0
    assert set(degraded_response["candidate_evidence_refs_by_action"]) == {
        "contrarian-check",
        "feedback-adapt",
        "retain-observe",
        "risk-off",
    }
    assert set(
        degraded_response["candidate_evidence_refs_by_action"]["feedback-adapt"]
    ).issuperset(
        {
            degraded_response["quality_ref"],
            degraded_response["repair_ref"],
            quality_persona_response["output_ref"],
            quality_persona_response["repaired_artifact_ref"],
            quality_source_ref,
        }
    )
    assert all(degraded_response["replay"].values())
    assert degraded_response["input_hash"]

    for trace in case["reflection"]["agent_decision_traces"]:
        artifact = trace["agent_decision_artifact"]
        scoring_inputs = artifact["scorer"]["scoring_inputs"]
        selected_action = _candidate_action_from_id(trace["selected_candidate_id"])
        assert vectorbt_ref in trace["evidence_refs"]
        assert tracker_ref in trace["evidence_refs"]
        assert arbitration["arbitration_ref"] in trace["evidence_refs"]
        assert reconciliation["reconciliation_ref"] in trace["evidence_refs"]
        assert alpha_revision["revision_ref"] in trace["evidence_refs"]
        assert degraded_response["quality_ref"] in trace["evidence_refs"]
        assert degraded_response["repair_ref"] in trace["evidence_refs"]
        assert trace["decision_inputs"]["oss_disagreement_arbitration_ref"] == arbitration["arbitration_ref"]
        assert trace["decision_inputs"]["tracking_reconciliation_ref"] == reconciliation["reconciliation_ref"]
        assert trace["decision_inputs"]["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
        assert trace["decision_inputs"]["oss_quality_ref"] == degraded_response["quality_ref"]
        assert trace["decision_inputs"]["oss_quality_repair_ref"] == degraded_response["repair_ref"]
        assert artifact["input_context"]["oss_disagreement_arbitration_ref"] == arbitration["arbitration_ref"]
        assert artifact["input_context"]["tracking_reconciliation_ref"] == reconciliation["reconciliation_ref"]
        assert artifact["input_context"]["tracking_reconciliation_repair_ref"] == reconciliation["repair"]["repair_ref"]
        assert artifact["input_context"]["tracking_reconciliation_divergence_type"] == expected_divergence_type
        assert artifact["input_context"]["tracking_reconciliation_repair_action"] == expected_repair_action
        assert artifact["input_context"]["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
        assert artifact["input_context"]["alpha_seed_revision_action"] == expected_alpha_action
        assert artifact["input_context"]["alpha_seed_revision_component"] == alpha_entry["component"]
        assert artifact["input_context"]["alpha_seed_revision_key"] == alpha_revision["revision"]["revision_key"]
        assert artifact["input_context"]["oss_quality_ref"] == degraded_response["quality_ref"]
        assert artifact["input_context"]["oss_quality_repair_ref"] == degraded_response["repair_ref"]
        assert artifact["input_context"]["degraded_oss_role"] == quality_role
        assert artifact["input_context"]["degraded_oss_component"] == quality_component
        assert artifact["input_context"]["degraded_oss_issue_type"] == expected_quality_issue
        assert artifact["input_context"]["degraded_oss_repair_action"] == (
            expected_quality_repair_action
        )
        assert artifact["persona_reasoning"]["response"]["oss_disagreement_arbitration_usage"]["arbitration_ref"] == arbitration["arbitration_ref"]
        tracking_usage = artifact["persona_reasoning"]["response"]["tracking_reconciliation_usage"]
        assert tracking_usage["reconciliation_ref"] == reconciliation["reconciliation_ref"]
        assert tracking_usage["model_id"] == PERSONA_TRACKING_RECONCILIATION_MODEL_ID
        assert tracking_usage["divergence_type"] == expected_divergence_type
        assert tracking_usage["repair_action"] == expected_repair_action
        alpha_usage = artifact["persona_reasoning"]["response"]["alpha_seed_revision_usage"]
        assert alpha_usage["revision_ref"] == alpha_revision["revision_ref"]
        assert alpha_usage["model_id"] == PERSONA_ALPHA_SEED_REVISION_MODEL_ID
        assert alpha_usage["alpha_component"] == alpha_entry["component"]
        assert alpha_usage["revision_action"] == expected_alpha_action
        quality_usage = artifact["persona_reasoning"]["response"]["oss_quality_repair_usage"]
        assert quality_usage["quality_ref"] == degraded_response["quality_ref"]
        assert quality_usage["repair_ref"] == degraded_response["repair_ref"]
        assert quality_usage["output_ref"] == quality_persona_response["output_ref"]
        assert quality_usage["model_id"] == PERSONA_DEGRADED_OSS_RESPONSE_MODEL_ID
        assert quality_usage["role"] == quality_role
        assert quality_usage["component"] == quality_component
        assert quality_usage["issue_type"] == expected_quality_issue
        assert quality_usage["repair_action"] == expected_quality_repair_action
        assert quality_usage["downweighted_candidate_action"] == (
            expected_quality_downweighted_action
        )
        assert scoring_inputs["oss_disagreement_arbitration"]["arbitration_id"] == arbitration["arbitration_id"]
        assert scoring_inputs["oss_disagreement_score_adjustments"][expected_resolution_action] > 0
        assert scoring_inputs["tracking_reconciliation"]["reconciliation_id"] == reconciliation["reconciliation_id"]
        assert scoring_inputs["tracking_reconciliation_score_adjustments"]["feedback-adapt"] > 0
        assert scoring_inputs["alpha_seed_revision"]["revision_id"] == alpha_revision["revision_id"]
        assert scoring_inputs["alpha_seed_revision_score_adjustments"]["feedback-adapt"] > 0
        assert scoring_inputs["degraded_oss_response"]["repair_id"] == degraded_response["repair_id"]
        assert scoring_inputs["oss_quality_repair_score_adjustments"][selected_action] > 0
        assert scoring_inputs["source_quality_penalty_by_action"][
            expected_quality_downweighted_action
        ] < 0
        assert artifact["replay"]["uses_oss_disagreement_arbitration"] is True
        assert artifact["replay"]["uses_tracking_reconciliation"] is True
        assert artifact["replay"]["uses_alpha_seed_revision"] is True
        assert artifact["replay"]["uses_degraded_oss_response_repair"] is True
        selected_refs = trace["selected_candidate"]["evidence_refs"]
        assert vectorbt_ref in selected_refs
        assert tracker_ref in selected_refs
        assert set(
            arbitration["candidate_evidence_refs_by_action"]["feedback-adapt"]
        ).issubset(set(selected_refs))
        assert set(
            reconciliation["candidate_evidence_refs_by_action"]["feedback-adapt"]
        ).issubset(set(selected_refs))
        assert set(
            alpha_revision["candidate_evidence_refs_by_action"]["feedback-adapt"]
        ).issubset(set(selected_refs))
        assert set(
            degraded_response["candidate_evidence_refs_by_action"][selected_action]
        ).issubset(set(selected_refs))
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
    assert check_by_name["multi_oss_disagreement_arbitration_drives_persona_scoring"]["status"] == "passed"
    assert check_by_name["tracking_readback_reconciliation_drives_persona_scoring"]["status"] == "passed"
    assert check_by_name["alpha_seed_revision_drives_persona_scoring"]["status"] == "passed"
    assert check_by_name["degraded_oss_response_repair_drives_persona_scoring"]["status"] == "passed"
    assert (
        check_by_name["oss_quality_repair_reaches_lean_handoff_and_runtime_feedback"][
            "status"
        ]
        == "passed"
    )


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
    assert conflict["model_id"] == PERSONA_CONFLICT_RESOLUTION_MODEL_ID
    assert conflict["resolution_ref"] == f"persona-conflict://{case['case_id']}"
    assert conflict["classified_conflicts"]
    assert "weight_conflict" in conflict["conflict_types"]
    assert conflict["open_conflicts"] == []
    assert conflict["decision_trace_ref"] == case["reflection"]["agent_decision_traces"][-1]["reflection_id"]
    assert conflict["selected_action_ref"] == (
        f"selected-action://{case['case_id']}/"
        f"{case['reflection']['agent_decision_traces'][-1]['selected_candidate_id']}"
    )
    assert conflict["oss_risk_ref"].startswith("oss://")
    proposal_lineage = conflict["proposal_lineage"]
    assert proposal_lineage["model_id"] == PERSONA_MULTI_PERSONA_PROPOSAL_LINEAGE_MODEL_ID
    assert proposal_lineage["lineage_ref"] == (
        f"persona-proposal-lineage://{case['case_id']}/generation2"
    )
    assert proposal_lineage["lineage_hash"].startswith("persona-proposal-lineage-")
    assert proposal_lineage["input_hash"] == proposal_lineage["lineage_hash"]
    assert conflict["proposal_lineage_ref"] == proposal_lineage["lineage_ref"]
    assert conflict["proposal_lineage_hash"] == proposal_lineage["lineage_hash"]
    assert proposal_lineage["proposal_count"] == 4
    assert len(proposal_lineage["proposal_records"]) == 4
    assert conflict["proposal_refs"] == proposal_lineage["proposal_refs"]
    assert conflict["proposal_hashes"] == proposal_lineage["proposal_hashes"]
    assert conflict["proposal_persona_ids"] == proposal_lineage["proposal_persona_ids"]
    assert conflict["proposal_roles"] == proposal_lineage["proposal_roles"]
    assert proposal_lineage["resolution_ref"] == conflict["resolution_ref"]
    assert proposal_lineage["selected_action_ref"] == conflict["selected_action_ref"]
    assert set(proposal_lineage["proposal_roles"]) == {
        "alpha_sponsor",
        "execution",
        "macro",
        "risk",
    }
    assert {"p-risk-analyst", "p-execution-lead", "p-macro-observer"}.issubset(
        set(proposal_lineage["proposal_persona_ids"])
    )
    proposal_by_role = {
        proposal["role"]: proposal for proposal in proposal_lineage["proposal_records"]
    }
    for proposal in proposal_lineage["proposal_records"]:
        assert proposal["proposal_ref"].startswith(
            f"persona-proposal://{case['case_id']}/"
        )
        assert proposal["proposal_hash"].startswith("persona-proposal-")
        assert proposal["proposal_ref"] in conflict["evidence_refs"]
        assert proposal["proposal_ref"] in proposal_lineage["proposal_refs"]
        assert proposal_lineage["proposal_hashes"][proposal["proposal_ref"]] == proposal[
            "proposal_hash"
        ]
        assert conflict["selected_action_ref"] in proposal["source_refs"]
        assert proposal["requesting_persona_id"] == case["persona_id"]
        assert proposal["capital_budget_pct"] <= 1.0
        assert set(proposal["direction_by_instrument"]) == set(case["portfolio"]["instruments"])
        assert set(proposal["weight_by_instrument"]) == set(case["portfolio"]["instruments"])
    assert proposal_lineage["lineage_ref"] in conflict["evidence_refs"]
    assert proposal_by_role["risk"]["persona_id"] == "p-risk-analyst"
    assert conflict["oss_risk_ref"] in proposal_by_role["risk"]["source_refs"]
    assert proposal_by_role["execution"]["persona_id"] == "p-execution-lead"
    assert any(
        ref.startswith("tracking-reconciliation://")
        for ref in proposal_by_role["execution"]["source_refs"]
    )
    assert proposal_by_role["macro"]["persona_id"] == "p-macro-observer"
    assert proposal_by_role["alpha_sponsor"]["persona_id"] not in {
        "p-risk-analyst",
        "p-execution-lead",
        "p-macro-observer",
    }
    assert all(proposal_lineage["replay"].values())
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
    assert schedule["schedule_ref"] == f"schedule://{schedule['schedule_id']}"
    assert schedule["trigger_mode"] == "autonomous_daily_paper_loop"
    assert schedule["phase_order_valid"] is True
    assert schedule["phase_due_at_ordered"] is True
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
    assert replay["case_specific_strategy_packet"]["policy_version"] == case["generation_results"][-1][
        "policy_version"
    ]
    assert replay["case_specific_strategy_packet"]["generation"] == 2
    assert replay["case_specific_strategy_packet"]["packet_ref"] == (
        f"lean-strategy-packet://{case['case_id']}/generation2"
    )
    assert replay["case_specific_strategy_packet"]["source_outcome_window"] == "holdout"
    assert replay["case_specific_strategy_packet"]["validation_window"] == "future_holdout"
    assert replay["case_specific_strategy_packet"]["strict_oos_proof_ref"] == (
        case["evolution"]["strict_oos_evolution_proof"]["proof_ref"]
    )
    assert replay["case_specific_strategy_packet"]["no_leakage_protocol_ref"] == (
        f"no-leakage://{case['evolution']['no_leakage_protocol']['protocol_id']}"
    )
    assert replay["case_specific_strategy_packet"]["evolution_trajectory_ref"] == (
        f"trajectory://{case['evolution']['trajectory']['trajectory_id']}"
    )
    assert replay["case_specific_strategy_packet"]["future_holdout_score"] == case["scores"][
        "generation2_future_holdout"
    ]
    assert replay["case_specific_strategy_packet"]["future_holdout_improvement"] == case["scores"][
        "future_generation_improvement"
    ]
    assert replay["case_specific_strategy_packet"]["validation_window_unseen_by_decision"] is True
    assert replay["case_specific_strategy_packet"]["future_window_hidden"] is True
    assert replay["case_specific_strategy_packet"]["strict_oos_replay_passed"] is True
    assert replay["case_specific_strategy_packet"]["no_leakage_replay_passed"] is True
    reconciliation = case["case_upstream_artifacts"]["tracking_reconciliation"]
    tracker = case["case_upstream_artifacts"]["tracker"]
    vectorbt = case["case_upstream_artifacts"]["vectorbt"]
    degraded_response = case["case_upstream_artifacts"]["degraded_oss_response"]
    quality_persona_response = degraded_response["persona_quality_response"]
    quality_source_ref = degraded_response["source_oss_ref"]
    experiment_ref = reconciliation["repair"]["normalized_experiment_ref"]
    tracking_reconciliation_ref = reconciliation["reconciliation_ref"]
    tracking_repair_ref = reconciliation["repair"]["repair_ref"]
    decision_evidence_refs = case["evolution"]["evidence_refs"]
    decision_evidence_ids = {ref["ref_id"] for ref in decision_evidence_refs}
    assert tracking_reconciliation_ref in decision_evidence_ids
    assert case["evolution"]["metadata"]["normalized_experiment_ref"] == experiment_ref
    assert case["evolution"]["metadata"]["tracking_reconciliation_ref"] == tracking_reconciliation_ref
    assert case["evolution"]["metadata"]["tracking_repair_ref"] == tracking_repair_ref
    packet_provenance = replay["case_specific_strategy_packet"]["experiment_tracking_provenance"]
    assert packet_provenance["model_id"] == PERSONA_TRACKING_RECONCILIATION_MODEL_ID
    assert packet_provenance["backend"] == tracker["backend"]
    assert packet_provenance["request_id"] == tracker["request_id"]
    assert packet_provenance["run_id"] == tracker["run_id"]
    assert packet_provenance["artifact_uri"] == tracker["artifact_uri"]
    assert packet_provenance["experiment_ref"] == experiment_ref
    assert packet_provenance["reconciliation_ref"] == tracking_reconciliation_ref
    assert packet_provenance["repair_ref"] == tracking_repair_ref
    assert packet_provenance["repair_action"] == reconciliation["repair"]["action"]
    assert packet_provenance["source_vectorbt_request_id"] == vectorbt["request_id"]
    assert packet_provenance["source_vectorbt_run_id"] == vectorbt["run_id"]
    assert packet_provenance["tracking_reconciliation_input_hash"] == reconciliation["input_hash"]
    assert packet_provenance["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["experiment_tracking_provenance_hash"] == (
        packet_provenance["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["normalized_experiment_ref"] == experiment_ref
    assert replay["case_specific_strategy_packet"]["tracking_reconciliation_ref"] == tracking_reconciliation_ref
    assert replay["case_specific_strategy_packet"]["tracking_repair_ref"] == tracking_repair_ref
    policy_entry = case["case_upstream_artifacts"]["selected_oss"]["policy_candidate"]
    policy_oss_ref = f"oss://{policy_entry['component']}/{policy_entry['request_id']}"
    packet_policy_lineage = replay["case_specific_strategy_packet"]["policy_oss_lineage"]
    assert packet_policy_lineage["model_id"] == PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID
    assert packet_policy_lineage["component"] == policy_entry["component"]
    assert packet_policy_lineage["request_id"] == policy_entry["request_id"]
    assert packet_policy_lineage["source_oss_ref"] == policy_oss_ref
    assert packet_policy_lineage["artifact_family"] == policy_entry["artifact_family"]
    assert packet_policy_lineage["registry_id"] == policy_entry["registry_id"]
    assert packet_policy_lineage["producer_run_id"] == policy_entry["producer_run_id"]
    assert packet_policy_lineage["lineage_ref"].startswith(
        f"policy-oss-lineage://{case['case_id']}/generation2/"
    )
    assert packet_policy_lineage["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["policy_oss_ref"] == policy_oss_ref
    assert replay["case_specific_strategy_packet"]["policy_oss_lineage_ref"] == (
        packet_policy_lineage["lineage_ref"]
    )
    assert replay["case_specific_strategy_packet"]["policy_oss_lineage_hash"] == (
        packet_policy_lineage["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["policy_oss_registry_ref"] == (
        packet_policy_lineage["registry_ref"]
    )
    assert replay["case_specific_strategy_packet"]["policy_oss_component"] == policy_entry["component"]
    assert replay["case_specific_strategy_packet"]["policy_oss_request_id"] == policy_entry["request_id"]
    reflection_entry = case["case_upstream_artifacts"]["selected_oss"]["reflection_artifact"]
    reflection_oss_ref = f"oss://{reflection_entry['component']}/{reflection_entry['request_id']}"
    openclaw_session_request_id = case["oss_feedback"]["request_ids"]["session"]
    openclaw_source_ref = f"oss://openclaw/{openclaw_session_request_id}"
    alpha_revision = case["case_upstream_artifacts"]["alpha_seed_revision"]
    alpha_entry = case["case_upstream_artifacts"]["selected_oss"]["alpha_model"]
    alpha_source_ref = f"oss://{alpha_entry['component']}/{alpha_entry['request_id']}"
    expected_alpha_action = ALPHA_SEED_REVISION_ACTION_BY_COMPONENT[alpha_entry["component"]]
    packet_reflection_lineage = replay["case_specific_strategy_packet"]["reflection_oss_lineage"]
    assert packet_reflection_lineage["model_id"] == PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID
    assert packet_reflection_lineage["component"] == reflection_entry["component"]
    assert packet_reflection_lineage["request_id"] == reflection_entry["request_id"]
    assert packet_reflection_lineage["source_oss_ref"] == reflection_oss_ref
    assert packet_reflection_lineage["artifact_family"] == reflection_entry["artifact_family"]
    assert packet_reflection_lineage["registry_id"] == reflection_entry["registry_id"]
    assert packet_reflection_lineage["producer_run_id"] == reflection_entry["producer_run_id"]
    assert packet_reflection_lineage["lineage_ref"].startswith(
        f"reflection-oss-lineage://{case['case_id']}/generation2/"
    )
    assert packet_reflection_lineage["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["reflection_oss_ref"] == reflection_oss_ref
    assert replay["case_specific_strategy_packet"]["reflection_oss_lineage_ref"] == (
        packet_reflection_lineage["lineage_ref"]
    )
    assert replay["case_specific_strategy_packet"]["reflection_oss_lineage_hash"] == (
        packet_reflection_lineage["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["reflection_oss_registry_ref"] == (
        packet_reflection_lineage["registry_ref"]
    )
    assert replay["case_specific_strategy_packet"]["reflection_oss_component"] == reflection_entry["component"]
    assert replay["case_specific_strategy_packet"]["reflection_oss_request_id"] == reflection_entry["request_id"]
    risk_entry = case["case_upstream_artifacts"]["selected_oss"]["risk_analytics"]
    risk_analytics_ref = f"oss://{risk_entry['component']}/{risk_entry['request_id']}"
    packet_risk_lineage = replay["case_specific_strategy_packet"]["risk_analytics_lineage"]
    assert packet_risk_lineage["model_id"] == PERSONA_RISK_ANALYTICS_LINEAGE_HANDOFF_MODEL_ID
    assert packet_risk_lineage["component"] == risk_entry["component"]
    assert packet_risk_lineage["request_id"] == risk_entry["request_id"]
    assert packet_risk_lineage["source_oss_ref"] == risk_analytics_ref
    assert packet_risk_lineage["artifact_family"] == risk_entry["artifact_family"]
    assert packet_risk_lineage["registry_id"] == risk_entry["registry_id"]
    assert packet_risk_lineage["producer_run_id"] == risk_entry["producer_run_id"]
    assert packet_risk_lineage["lineage_ref"].startswith(
        f"risk-analytics-lineage://{case['case_id']}/generation2/"
    )
    assert packet_risk_lineage["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["risk_analytics_ref"] == risk_analytics_ref
    assert replay["case_specific_strategy_packet"]["risk_analytics_lineage_ref"] == (
        packet_risk_lineage["lineage_ref"]
    )
    assert replay["case_specific_strategy_packet"]["risk_analytics_lineage_hash"] == (
        packet_risk_lineage["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["risk_analytics_registry_ref"] == (
        packet_risk_lineage["registry_ref"]
    )
    assert replay["case_specific_strategy_packet"]["risk_analytics_component"] == risk_entry["component"]
    assert replay["case_specific_strategy_packet"]["risk_analytics_request_id"] == risk_entry["request_id"]
    packet_quality_lineage = replay["case_specific_strategy_packet"]["oss_quality_repair_lineage"]
    assert packet_quality_lineage["model_id"] == PERSONA_OSS_QUALITY_REPAIR_HANDOFF_MODEL_ID
    assert packet_quality_lineage["role"] == degraded_response["role"]
    assert packet_quality_lineage["component"] == degraded_response["component"]
    assert packet_quality_lineage["request_id"] == degraded_response["request_id"]
    assert packet_quality_lineage["source_oss_ref"] == quality_source_ref
    assert packet_quality_lineage["quality_ref"] == degraded_response["quality_ref"]
    assert packet_quality_lineage["repair_ref"] == degraded_response["repair_ref"]
    assert packet_quality_lineage["repaired_artifact_ref"] == quality_persona_response[
        "repaired_artifact_ref"
    ]
    assert packet_quality_lineage["issue_type"] == degraded_response["issue_type"]
    assert packet_quality_lineage["repair_action"] == quality_persona_response["repair_action"]
    assert packet_quality_lineage["downweighted_candidate_action"] == quality_persona_response[
        "downweighted_candidate_action"
    ]
    assert packet_quality_lineage["lineage_ref"].startswith(
        f"oss-quality-repair-lineage://{case['case_id']}/generation2/"
    )
    assert packet_quality_lineage["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["oss_quality_repair_lineage_ref"] == (
        packet_quality_lineage["lineage_ref"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_repair_lineage_hash"] == (
        packet_quality_lineage["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_repair_ref"] == (
        degraded_response["repair_ref"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_ref"] == (
        degraded_response["quality_ref"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_degraded_source_ref"] == (
        quality_source_ref
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_repaired_artifact_ref"] == (
        quality_persona_response["repaired_artifact_ref"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_repair_action"] == (
        quality_persona_response["repair_action"]
    )
    assert replay["case_specific_strategy_packet"]["oss_quality_downweighted_candidate_action"] == (
        quality_persona_response["downweighted_candidate_action"]
    )
    packet_proposal_lineage = replay["case_specific_strategy_packet"][
        "multi_persona_proposal_lineage"
    ]
    assert packet_proposal_lineage == proposal_lineage
    assert replay["case_specific_strategy_packet"]["multi_persona_proposal_lineage_ref"] == (
        proposal_lineage["lineage_ref"]
    )
    assert replay["case_specific_strategy_packet"]["multi_persona_proposal_lineage_hash"] == (
        proposal_lineage["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["multi_persona_proposal_refs"] == (
        proposal_lineage["proposal_refs"]
    )
    assert replay["case_specific_strategy_packet"]["multi_persona_proposal_hashes"] == (
        proposal_lineage["proposal_hashes"]
    )
    assert replay["case_specific_strategy_packet"]["multi_persona_proposal_persona_ids"] == (
        proposal_lineage["proposal_persona_ids"]
    )
    packet_alpha_handoff = replay["case_specific_strategy_packet"]["alpha_seed_revision_handoff"]
    assert packet_alpha_handoff["model_id"] == PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
    assert packet_alpha_handoff["revision_ref"] == alpha_revision["revision_ref"]
    assert packet_alpha_handoff["base_seed_ref"] == f"alpha-seed://{case['seed_key']}"
    assert packet_alpha_handoff["source_oss_ref"] == alpha_source_ref
    assert packet_alpha_handoff["alpha_component"] == alpha_entry["component"]
    assert packet_alpha_handoff["source_alpha_request_id"] == alpha_entry["request_id"]
    assert packet_alpha_handoff["revision_action"] == expected_alpha_action
    assert packet_alpha_handoff["downstream_vectorbt_request_id"] == vectorbt["request_id"]
    assert packet_alpha_handoff["downstream_policy_candidate_request_id"] == (
        case["case_upstream_artifacts"]["selected_oss"]["policy_candidate"]["request_id"]
    )
    assert packet_alpha_handoff["lineage_hash"]
    assert replay["case_specific_strategy_packet"]["alpha_seed_revision_handoff_ref"] == (
        packet_alpha_handoff["handoff_ref"]
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_revision_ref"] == (
        alpha_revision["revision_ref"]
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_source_ref"] == (
        f"alpha-seed://{case['seed_key']}"
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_source_oss_ref"] == (
        alpha_source_ref
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_revision_handoff_hash"] == (
        packet_alpha_handoff["lineage_hash"]
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_revision_action"] == (
        expected_alpha_action
    )
    assert replay["case_specific_strategy_packet"]["alpha_seed_component"] == alpha_entry["component"]
    assert replay["case_specific_strategy_packet"]["alpha_seed_downstream_vectorbt_request_id"] == (
        vectorbt["request_id"]
    )
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

    packet_readback = replay["lean_object_store_packet_readback"]
    assert packet_readback["model_id"] == LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID
    assert packet_readback["status"] == "passed"
    assert packet_readback["packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert packet_readback["packet_hash"] == packet_readback["source_packet_hash"]
    assert packet_readback["artifact_payload_checksum"]
    assert packet_readback["target_count"] == PORTFOLIO_LEG_COUNT
    assert len(packet_readback["target_refs"]) == PORTFOLIO_LEG_COUNT
    assert len(packet_readback["target_signal_ids"]) == PORTFOLIO_LEG_COUNT
    assert len(packet_readback["target_symbols"]) == PORTFOLIO_LEG_COUNT
    assert packet_readback["loaded_signal_id"] == packet_readback["target_signal_ids"][0]
    assert packet_readback["loaded_signal_symbol"] == packet_readback["target_symbols"][0]
    assert packet_readback["loaded_signal_source_target_ref"] == packet_readback["target_refs"][0]
    assert packet_readback["loaded_signal_count"] == PORTFOLIO_LEG_COUNT
    assert packet_readback["loaded_signal_ids"] == packet_readback["target_signal_ids"]
    assert packet_readback["loaded_signal_symbols"] == packet_readback["target_symbols"]
    assert packet_readback["loaded_signal_source_target_refs"] == packet_readback["target_refs"]
    assert packet_readback["executed_packet_target_count"] == PORTFOLIO_LEG_COUNT
    assert packet_readback["executed_packet_target_refs"] == packet_readback["target_refs"]
    assert packet_readback["executed_packet_target_signal_ids"] == packet_readback["target_signal_ids"]
    assert packet_readback["executed_packet_target_symbols"] == packet_readback["target_symbols"]
    assert len(packet_readback["packet_target_executions"]) == PORTFOLIO_LEG_COUNT
    for execution in packet_readback["packet_target_executions"]:
        assert execution["fill_count"] >= 1
        assert all(execution["replay"].values())
    assert packet_readback["normalized_experiment_ref"] == experiment_ref
    assert packet_readback["tracking_reconciliation_ref"] == tracking_reconciliation_ref
    assert packet_readback["tracking_repair_ref"] == tracking_repair_ref
    assert packet_readback["experiment_tracking_provenance_hash"] == packet_provenance["lineage_hash"]
    assert packet_readback["loaded_experiment_tracking_provenance_hash"] == packet_provenance["lineage_hash"]
    assert packet_readback["loaded_experiment_tracking_provenance"] == packet_provenance
    assert packet_readback["policy_oss_ref"] == policy_oss_ref
    assert packet_readback["loaded_policy_oss_ref"] == policy_oss_ref
    assert packet_readback["policy_oss_lineage_ref"] == packet_policy_lineage["lineage_ref"]
    assert packet_readback["loaded_policy_oss_lineage_ref"] == packet_policy_lineage["lineage_ref"]
    assert packet_readback["policy_oss_lineage_hash"] == packet_policy_lineage["lineage_hash"]
    assert packet_readback["loaded_policy_oss_lineage_hash"] == packet_policy_lineage["lineage_hash"]
    assert packet_readback["loaded_policy_oss_lineage"] == packet_policy_lineage
    assert packet_readback["reflection_oss_ref"] == reflection_oss_ref
    assert packet_readback["loaded_reflection_oss_ref"] == reflection_oss_ref
    assert packet_readback["reflection_oss_lineage_ref"] == packet_reflection_lineage["lineage_ref"]
    assert packet_readback["loaded_reflection_oss_lineage_ref"] == packet_reflection_lineage["lineage_ref"]
    assert packet_readback["reflection_oss_lineage_hash"] == packet_reflection_lineage["lineage_hash"]
    assert packet_readback["loaded_reflection_oss_lineage_hash"] == packet_reflection_lineage["lineage_hash"]
    assert packet_readback["loaded_reflection_oss_lineage"] == packet_reflection_lineage
    assert packet_readback["risk_analytics_ref"] == risk_analytics_ref
    assert packet_readback["loaded_risk_analytics_ref"] == risk_analytics_ref
    assert packet_readback["risk_analytics_lineage_ref"] == packet_risk_lineage["lineage_ref"]
    assert packet_readback["loaded_risk_analytics_lineage_ref"] == packet_risk_lineage["lineage_ref"]
    assert packet_readback["risk_analytics_lineage_hash"] == packet_risk_lineage["lineage_hash"]
    assert packet_readback["loaded_risk_analytics_lineage_hash"] == packet_risk_lineage["lineage_hash"]
    assert packet_readback["loaded_risk_analytics_lineage"] == packet_risk_lineage
    assert packet_readback["oss_quality_repair_lineage_ref"] == packet_quality_lineage[
        "lineage_ref"
    ]
    assert packet_readback["loaded_oss_quality_repair_lineage_ref"] == (
        packet_quality_lineage["lineage_ref"]
    )
    assert packet_readback["oss_quality_repair_lineage_hash"] == packet_quality_lineage[
        "lineage_hash"
    ]
    assert packet_readback["loaded_oss_quality_repair_lineage_hash"] == (
        packet_quality_lineage["lineage_hash"]
    )
    assert packet_readback["oss_quality_repair_ref"] == degraded_response["repair_ref"]
    assert packet_readback["loaded_oss_quality_repair_ref"] == degraded_response["repair_ref"]
    assert packet_readback["oss_quality_ref"] == degraded_response["quality_ref"]
    assert packet_readback["loaded_oss_quality_ref"] == degraded_response["quality_ref"]
    assert packet_readback["oss_quality_degraded_source_ref"] == quality_source_ref
    assert packet_readback["loaded_oss_quality_degraded_source_ref"] == quality_source_ref
    assert packet_readback["oss_quality_repaired_artifact_ref"] == (
        quality_persona_response["repaired_artifact_ref"]
    )
    assert packet_readback["loaded_oss_quality_repaired_artifact_ref"] == (
        quality_persona_response["repaired_artifact_ref"]
    )
    assert packet_readback["loaded_oss_quality_repair_lineage"] == packet_quality_lineage
    assert packet_readback["multi_persona_proposal_lineage_ref"] == proposal_lineage[
        "lineage_ref"
    ]
    assert packet_readback["loaded_multi_persona_proposal_lineage_ref"] == (
        proposal_lineage["lineage_ref"]
    )
    assert packet_readback["multi_persona_proposal_lineage_hash"] == proposal_lineage[
        "lineage_hash"
    ]
    assert packet_readback["loaded_multi_persona_proposal_lineage_hash"] == (
        proposal_lineage["lineage_hash"]
    )
    assert packet_readback["multi_persona_proposal_refs"] == proposal_lineage[
        "proposal_refs"
    ]
    assert packet_readback["loaded_multi_persona_proposal_refs"] == proposal_lineage[
        "proposal_refs"
    ]
    assert packet_readback["multi_persona_proposal_persona_ids"] == proposal_lineage[
        "proposal_persona_ids"
    ]
    assert packet_readback["loaded_multi_persona_proposal_persona_ids"] == (
        proposal_lineage["proposal_persona_ids"]
    )
    assert packet_readback["loaded_multi_persona_proposal_lineage"] == proposal_lineage
    assert packet_readback["alpha_seed_revision_handoff_ref"] == packet_alpha_handoff["handoff_ref"]
    assert packet_readback["loaded_alpha_seed_revision_handoff_ref"] == packet_alpha_handoff["handoff_ref"]
    assert packet_readback["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
    assert packet_readback["loaded_alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
    assert packet_readback["alpha_seed_source_ref"] == f"alpha-seed://{case['seed_key']}"
    assert packet_readback["loaded_alpha_seed_source_ref"] == f"alpha-seed://{case['seed_key']}"
    assert packet_readback["alpha_seed_source_oss_ref"] == alpha_source_ref
    assert packet_readback["loaded_alpha_seed_source_oss_ref"] == alpha_source_ref
    assert packet_readback["alpha_seed_revision_handoff_hash"] == packet_alpha_handoff["lineage_hash"]
    assert packet_readback["loaded_alpha_seed_revision_handoff_hash"] == packet_alpha_handoff["lineage_hash"]
    assert packet_readback["loaded_alpha_seed_revision_handoff"] == packet_alpha_handoff
    assert packet_readback["object_store_keys"] == replay["object_store_keys"]
    assert all(packet_readback["replay"].values())
    assert packet_readback["input_hash"]

    assert replay["loaded_signal"]["signal_id"] == packet_readback["loaded_signal_id"]
    assert replay["loaded_signal"]["symbol"] == packet_readback["loaded_signal_symbol"]
    assert replay["loaded_signal"]["source_target_ref"] == packet_readback["loaded_signal_source_target_ref"]
    assert [signal["signal_id"] for signal in replay["loaded_signals"]] == packet_readback["loaded_signal_ids"]
    assert [signal["source_target_ref"] for signal in replay["loaded_signals"]] == packet_readback["target_refs"]
    assert replay["case_specific_packet_targets"][0]["signal_id"] == replay["loaded_signal"]["signal_id"]
    assert replay["case_specific_packet_targets"][0]["execution_symbol"] == replay["loaded_signal"]["symbol"]
    assert [target["target_ref"] for target in replay["case_specific_packet_targets"]] == (
        packet_readback["target_refs"]
    )
    assert [target["signal_id"] for target in replay["case_specific_packet_targets"]] == (
        packet_readback["target_signal_ids"]
    )
    for target in replay["case_specific_packet_targets"]:
        assert target["generation"] == 2
        assert target["instrument"] in case["portfolio"]["instruments"]
        assert target["policy_oss_ref"] == policy_oss_ref
        assert target["policy_oss_lineage_ref"] == packet_policy_lineage["lineage_ref"]
        assert target["policy_oss_lineage_hash"] == packet_policy_lineage["lineage_hash"]
        assert target["policy_oss_component"] == policy_entry["component"]
        assert target["policy_oss_request_id"] == policy_entry["request_id"]
        assert target["reflection_oss_ref"] == reflection_oss_ref
        assert target["reflection_oss_lineage_ref"] == packet_reflection_lineage["lineage_ref"]
        assert target["reflection_oss_lineage_hash"] == packet_reflection_lineage["lineage_hash"]
        assert target["reflection_oss_component"] == reflection_entry["component"]
        assert target["reflection_oss_request_id"] == reflection_entry["request_id"]
        assert target["risk_analytics_ref"] == risk_analytics_ref
        assert target["risk_analytics_lineage_ref"] == packet_risk_lineage["lineage_ref"]
        assert target["risk_analytics_lineage_hash"] == packet_risk_lineage["lineage_hash"]
        assert target["risk_analytics_component"] == risk_entry["component"]
        assert target["risk_analytics_request_id"] == risk_entry["request_id"]
        assert target["oss_quality_repair_lineage_ref"] == packet_quality_lineage[
            "lineage_ref"
        ]
        assert target["oss_quality_repair_lineage_hash"] == packet_quality_lineage[
            "lineage_hash"
        ]
        assert target["oss_quality_repair_ref"] == degraded_response["repair_ref"]
        assert target["oss_quality_ref"] == degraded_response["quality_ref"]
        assert target["oss_quality_degraded_source_ref"] == quality_source_ref
        assert target["oss_quality_repaired_artifact_ref"] == quality_persona_response[
            "repaired_artifact_ref"
        ]
        assert target["oss_quality_repair_action"] == quality_persona_response[
            "repair_action"
        ]
        assert target["oss_quality_downweighted_candidate_action"] == (
            quality_persona_response["downweighted_candidate_action"]
        )
        assert target["multi_persona_proposal_lineage_ref"] == proposal_lineage[
            "lineage_ref"
        ]
        assert target["multi_persona_proposal_lineage_hash"] == proposal_lineage[
            "lineage_hash"
        ]
        assert target["multi_persona_proposal_refs"] == proposal_lineage[
            "proposal_refs"
        ]
        assert target["multi_persona_proposal_persona_ids"] == proposal_lineage[
            "proposal_persona_ids"
        ]
        assert target["alpha_seed_revision_handoff_ref"] == packet_alpha_handoff["handoff_ref"]
        assert target["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
        assert target["alpha_seed_revision_handoff_hash"] == packet_alpha_handoff["lineage_hash"]
        assert target["alpha_seed_source_ref"] == f"alpha-seed://{case['seed_key']}"
        assert target["alpha_seed_source_oss_ref"] == alpha_source_ref
        assert target["alpha_seed_revision_action"] == expected_alpha_action
        assert target["alpha_seed_component"] == alpha_entry["component"]
        assert target["signal"]["metadata"]["strategy_packet_ref"] == packet_readback["packet_ref"]
        assert target["signal"]["metadata"]["packet_target_ref"] == target["target_ref"]
        assert target["signal"]["metadata"]["lean_object_store_readback_model_id"] == (
            LEAN_OBJECT_STORE_PACKET_READBACK_MODEL_ID
        )
        assert target["signal"]["metadata"]["policy_oss_ref"] == policy_oss_ref
        assert target["signal"]["metadata"]["policy_oss_lineage_ref"] == packet_policy_lineage["lineage_ref"]
        assert target["signal"]["metadata"]["policy_oss_lineage_hash"] == packet_policy_lineage["lineage_hash"]
        assert target["signal"]["metadata"]["reflection_oss_ref"] == reflection_oss_ref
        assert target["signal"]["metadata"]["reflection_oss_lineage_ref"] == packet_reflection_lineage["lineage_ref"]
        assert target["signal"]["metadata"]["reflection_oss_lineage_hash"] == packet_reflection_lineage["lineage_hash"]
        assert target["signal"]["metadata"]["risk_analytics_ref"] == risk_analytics_ref
        assert target["signal"]["metadata"]["risk_analytics_lineage_ref"] == packet_risk_lineage["lineage_ref"]
        assert target["signal"]["metadata"]["risk_analytics_lineage_hash"] == packet_risk_lineage["lineage_hash"]
        assert target["signal"]["metadata"]["oss_quality_repair_lineage_ref"] == (
            packet_quality_lineage["lineage_ref"]
        )
        assert target["signal"]["metadata"]["oss_quality_repair_lineage_hash"] == (
            packet_quality_lineage["lineage_hash"]
        )
        assert target["signal"]["metadata"]["oss_quality_repair_ref"] == degraded_response[
            "repair_ref"
        ]
        assert target["signal"]["metadata"]["oss_quality_ref"] == degraded_response[
            "quality_ref"
        ]
        assert target["signal"]["metadata"]["oss_quality_degraded_source_ref"] == (
            quality_source_ref
        )
        assert target["signal"]["metadata"]["oss_quality_repaired_artifact_ref"] == (
            quality_persona_response["repaired_artifact_ref"]
        )
        assert target["signal"]["metadata"]["multi_persona_proposal_lineage_ref"] == (
            proposal_lineage["lineage_ref"]
        )
        assert target["signal"]["metadata"]["multi_persona_proposal_lineage_hash"] == (
            proposal_lineage["lineage_hash"]
        )
        assert target["signal"]["metadata"]["multi_persona_proposal_refs"] == (
            proposal_lineage["proposal_refs"]
        )
        assert target["signal"]["metadata"]["multi_persona_proposal_persona_ids"] == (
            proposal_lineage["proposal_persona_ids"]
        )
        assert target["signal"]["metadata"]["alpha_seed_revision_handoff_ref"] == (
            packet_alpha_handoff["handoff_ref"]
        )
        assert target["signal"]["metadata"]["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
        assert target["signal"]["metadata"]["alpha_seed_revision_handoff_hash"] == (
            packet_alpha_handoff["lineage_hash"]
        )
        assert target["signal"]["metadata"]["source_dataset_ref"] == HISTORICAL_OHLCV_DATASET_ID

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
    assert handoff["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert handoff["policy_generation"] == 2
    assert handoff["policy_version"] == case["generation_results"][-1]["policy_version"]
    assert handoff["strategy_packet_validation_window"] == "future_holdout"
    assert handoff["strategy_packet_source_outcome_window"] == "holdout"
    assert handoff["strict_oos_evolution_proof_ref"] == case["evolution"]["strict_oos_evolution_proof"][
        "proof_ref"
    ]
    assert handoff["no_leakage_protocol_ref"] == (
        f"no-leakage://{case['evolution']['no_leakage_protocol']['protocol_id']}"
    )
    assert handoff["evolution_trajectory_ref"] == f"trajectory://{case['evolution']['trajectory']['trajectory_id']}"
    assert handoff["strategy_packet"] == replay["case_specific_strategy_packet"]
    assert handoff["strategy_packet_hash"].startswith("lean-strategy-packet-")
    assert handoff["strategy_packet_replay_passed"] is True
    assert handoff["future_holdout_score"] == case["scores"]["generation2_future_holdout"]
    assert handoff["future_holdout_improvement"] == case["scores"]["future_generation_improvement"]
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
    assert handoff["normalized_experiment_ref"] == experiment_ref
    assert handoff["tracking_reconciliation_ref"] == tracking_reconciliation_ref
    assert handoff["tracking_repair_ref"] == tracking_repair_ref
    assert handoff["experiment_tracking_provenance"] == packet_provenance
    assert handoff["experiment_tracking_provenance_hash"] == packet_provenance["lineage_hash"]
    assert handoff["policy_oss_lineage"] == packet_policy_lineage
    assert handoff["policy_oss_lineage_hash"] == packet_policy_lineage["lineage_hash"]
    assert handoff["policy_oss_lineage_ref"] == packet_policy_lineage["lineage_ref"]
    assert handoff["policy_oss_ref"] == policy_oss_ref
    assert handoff["policy_oss_registry_ref"] == packet_policy_lineage["registry_ref"]
    assert handoff["policy_oss_component"] == policy_entry["component"]
    assert handoff["policy_oss_request_id"] == policy_entry["request_id"]
    assert handoff["reflection_oss_lineage"] == packet_reflection_lineage
    assert handoff["reflection_oss_lineage_hash"] == packet_reflection_lineage["lineage_hash"]
    assert handoff["reflection_oss_lineage_ref"] == packet_reflection_lineage["lineage_ref"]
    assert handoff["reflection_oss_ref"] == reflection_oss_ref
    assert handoff["reflection_oss_registry_ref"] == packet_reflection_lineage["registry_ref"]
    assert handoff["reflection_oss_component"] == reflection_entry["component"]
    assert handoff["reflection_oss_request_id"] == reflection_entry["request_id"]
    assert handoff["risk_analytics_lineage"] == packet_risk_lineage
    assert handoff["risk_analytics_lineage_hash"] == packet_risk_lineage["lineage_hash"]
    assert handoff["risk_analytics_lineage_ref"] == packet_risk_lineage["lineage_ref"]
    assert handoff["risk_analytics_ref"] == risk_analytics_ref
    assert handoff["risk_analytics_registry_ref"] == packet_risk_lineage["registry_ref"]
    assert handoff["risk_analytics_component"] == risk_entry["component"]
    assert handoff["risk_analytics_request_id"] == risk_entry["request_id"]
    assert handoff["oss_quality_repair_lineage"] == packet_quality_lineage
    assert handoff["oss_quality_repair_lineage_hash"] == packet_quality_lineage[
        "lineage_hash"
    ]
    assert handoff["oss_quality_repair_lineage_ref"] == packet_quality_lineage[
        "lineage_ref"
    ]
    assert handoff["oss_quality_repair_ref"] == degraded_response["repair_ref"]
    assert handoff["oss_quality_ref"] == degraded_response["quality_ref"]
    assert handoff["oss_quality_degraded_source_ref"] == quality_source_ref
    assert handoff["oss_quality_repaired_artifact_ref"] == quality_persona_response[
        "repaired_artifact_ref"
    ]
    assert handoff["oss_quality_repair_action"] == quality_persona_response[
        "repair_action"
    ]
    assert handoff["oss_quality_downweighted_candidate_action"] == (
        quality_persona_response["downweighted_candidate_action"]
    )
    assert handoff["oss_quality_issue_type"] == degraded_response["issue_type"]
    assert handoff["oss_quality_role"] == degraded_response["role"]
    assert handoff["oss_quality_component"] == degraded_response["component"]
    assert handoff["multi_persona_proposal_lineage"] == proposal_lineage
    assert handoff["multi_persona_proposal_lineage_ref"] == proposal_lineage["lineage_ref"]
    assert handoff["multi_persona_proposal_lineage_hash"] == proposal_lineage[
        "lineage_hash"
    ]
    assert handoff["multi_persona_proposal_refs"] == proposal_lineage["proposal_refs"]
    assert handoff["multi_persona_proposal_hashes"] == proposal_lineage[
        "proposal_hashes"
    ]
    assert handoff["multi_persona_proposal_persona_ids"] == proposal_lineage[
        "proposal_persona_ids"
    ]
    openclaw_context = handoff["openclaw_session_context"]
    assert openclaw_context["model_id"] == PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID
    assert openclaw_context["component"] == "openclaw"
    assert openclaw_context["request_id"] == openclaw_session_request_id
    assert openclaw_context["source_oss_ref"] == openclaw_source_ref
    assert openclaw_context["artifact_family"] == "openclaw_session"
    assert openclaw_context["session_state"] == "active"
    assert openclaw_context["session_ref"].startswith("openclaw-session://")
    assert openclaw_context["upstream_session_ref"].startswith(
        "openclaw-upstream-session://"
    )
    assert openclaw_context["context_ref"] == handoff["openclaw_session_context_ref"]
    assert openclaw_context["context_hash"] == handoff["openclaw_session_context_hash"]
    assert openclaw_context["input_hash"] == handoff["openclaw_session_context_hash"]
    assert handoff["openclaw_session_ref"] == openclaw_context["session_ref"]
    assert handoff["openclaw_source_oss_ref"] == openclaw_source_ref
    assert handoff["openclaw_upstream_session_ref"] == openclaw_context[
        "upstream_session_ref"
    ]
    assert handoff["openclaw_session_id"] == openclaw_context["session_id"]
    assert handoff["openclaw_upstream_session_id"] == openclaw_context[
        "upstream_session_id"
    ]
    assert handoff["openclaw_session_state"] == "active"
    assert handoff["openclaw_session_artifact_family"] == "openclaw_session"
    openclaw_continuity = handoff["openclaw_session_continuity"]
    assert openclaw_context["session_continuity"] == openclaw_continuity
    assert handoff["openclaw_session_continuity_status"] == openclaw_continuity["status"]
    assert handoff["openclaw_session_continuity_ref"] == openclaw_continuity["continuity_ref"]
    assert handoff["openclaw_previous_source_oss_ref"] == openclaw_continuity[
        "previous_source_oss_ref"
    ]
    assert handoff["openclaw_previous_context_ref"] == openclaw_continuity[
        "previous_context_ref"
    ]
    assert handoff["openclaw_previous_session_ref"] == openclaw_continuity[
        "previous_session_ref"
    ]
    assert handoff["openclaw_previous_upstream_session_ref"] == openclaw_continuity[
        "previous_upstream_session_ref"
    ]
    openclaw_continuity_refs = [
        ref
        for ref in (
            handoff["openclaw_session_continuity_ref"],
            handoff["openclaw_previous_source_oss_ref"],
            handoff["openclaw_previous_context_ref"],
            handoff["openclaw_previous_session_ref"],
            handoff["openclaw_previous_upstream_session_ref"],
        )
        if ref
    ]
    if openclaw_continuity["status"] == "cold_start":
        assert openclaw_continuity["request_action"] == "start_openclaw_session"
        assert openclaw_continuity_refs == []
    else:
        assert openclaw_continuity["status"] == "applied"
        assert openclaw_continuity["request_action"] == "continue_prior_openclaw_session"
        assert handoff["openclaw_session_continuity_ref"].startswith(
            "openclaw-session-continuity://"
        )
        assert handoff["openclaw_previous_session_ref"].startswith(
            "openclaw-session://"
        )
        assert handoff["openclaw_previous_context_ref"].startswith(
            "openclaw-context://"
        )
    broker_carryover = case["cross_cycle"]["broker_adapter_carryover"]
    assert handoff["broker_adapter_carryover"] == broker_carryover["conflict_broker_adapter_carryover"]
    assert handoff["broker_adapter_carryover_status"] == broker_carryover["carryover_status"]
    assert handoff["broker_adapter_carryover_ref"] == broker_carryover["carryover_ref"]
    assert handoff["previous_broker_adapter_followup_ref"] == broker_carryover["previous_followup_ref"]
    assert handoff["previous_broker_adapter_source_packet_ref"] == broker_carryover[
        "previous_source_packet_ref"
    ]
    broker_carryover_refs = [
        ref
        for ref in (
            handoff["broker_adapter_carryover_ref"],
            handoff["previous_broker_adapter_followup_ref"],
            handoff["previous_broker_adapter_source_packet_ref"],
        )
        if ref
    ]
    if broker_carryover["carryover_status"] == "cold_start":
        assert broker_carryover_refs == []
    else:
        assert handoff["broker_adapter_carryover_ref"].startswith(
            "broker-adapter-carryover://"
        )
        assert handoff["previous_broker_adapter_followup_ref"].startswith(
            "broker-adapter-followup://"
        )
        assert handoff["previous_broker_adapter_source_packet_ref"].startswith(
            "broker-adapter://"
        )
    assert handoff["alpha_seed_revision_handoff"] == packet_alpha_handoff
    assert handoff["alpha_seed_revision_handoff_ref"] == packet_alpha_handoff["handoff_ref"]
    assert handoff["alpha_seed_revision_ref"] == alpha_revision["revision_ref"]
    assert handoff["alpha_seed_source_ref"] == f"alpha-seed://{case['seed_key']}"
    assert handoff["alpha_seed_source_oss_ref"] == alpha_source_ref
    assert handoff["alpha_seed_revision_handoff_hash"] == packet_alpha_handoff["lineage_hash"]
    assert handoff["alpha_seed_revision_action"] == expected_alpha_action
    assert handoff["alpha_seed_component"] == alpha_entry["component"]
    assert handoff["alpha_seed_downstream_vectorbt_request_id"] == vectorbt["request_id"]
    assert handoff["target_stage"] == "paper"
    assert handoff["broker_live_submitted"] is False
    assert set(handoff["portfolio_instruments"]) == set(case["portfolio"]["instruments"])
    assert handoff["persona_conflict_resolution_ref"] == conflict["resolution_ref"]
    assert handoff["resolved_capital_budget_pct"] == allocation["capital_budget_pct"]
    assert handoff["resolved_direction_by_instrument"] == allocation["direction_by_instrument"]
    assert handoff["resolved_weight_by_instrument"] == allocation["weight_by_instrument"]
    assert handoff["schedule_ref"] == schedule["schedule_ref"]
    assert handoff["next_cycle_due_at"] == schedule["next_cycle_due_at"]
    for entry in case["case_upstream_artifacts"]["selected_oss"].values():
        assert f"oss://{entry['component']}/{entry['request_id']}" in handoff["runtime_bundle_refs"]
    assert handoff["strategy_packet_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["strict_oos_evolution_proof_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["no_leakage_protocol_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["evolution_trajectory_ref"] in handoff["runtime_bundle_refs"]
    assert experiment_ref in handoff["runtime_bundle_refs"]
    assert tracking_reconciliation_ref in handoff["runtime_bundle_refs"]
    assert tracking_repair_ref in handoff["runtime_bundle_refs"]
    assert handoff["policy_oss_lineage_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["policy_oss_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["policy_oss_registry_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["reflection_oss_lineage_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["reflection_oss_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["reflection_oss_registry_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["risk_analytics_lineage_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["risk_analytics_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["risk_analytics_registry_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["oss_quality_repair_lineage_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["oss_quality_repair_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["oss_quality_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["oss_quality_degraded_source_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["oss_quality_repaired_artifact_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["multi_persona_proposal_lineage_ref"] in handoff["runtime_bundle_refs"]
    for proposal_ref in proposal_lineage["proposal_refs"]:
        assert proposal_ref in handoff["runtime_bundle_refs"]
    assert handoff["openclaw_session_context_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["openclaw_session_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["openclaw_source_oss_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["openclaw_upstream_session_ref"] in handoff["runtime_bundle_refs"]
    for ref in openclaw_continuity_refs:
        assert ref in handoff["runtime_bundle_refs"]
    for ref in broker_carryover_refs:
        assert ref in handoff["runtime_bundle_refs"]
    assert handoff["alpha_seed_revision_handoff_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["alpha_seed_revision_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["alpha_seed_source_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["alpha_seed_source_oss_ref"] in handoff["runtime_bundle_refs"]
    assert conflict["resolution_ref"] in handoff["runtime_bundle_refs"]
    assert schedule["schedule_ref"] in handoff["runtime_bundle_refs"]
    assert handoff["runtime_bundle_refs"]

    projection = operational["lean_packet_execution_projection"]
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
        "lean_packet_execution_projection",
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
        projection["projection_ref"],
        handoff["strategy_packet_ref"],
        handoff["strict_oos_evolution_proof_ref"],
        handoff["no_leakage_protocol_ref"],
        experiment_ref,
        tracking_reconciliation_ref,
        tracking_repair_ref,
        handoff["policy_oss_lineage_ref"],
        handoff["policy_oss_ref"],
        handoff["policy_oss_registry_ref"],
        handoff["reflection_oss_lineage_ref"],
        handoff["reflection_oss_ref"],
        handoff["reflection_oss_registry_ref"],
        handoff["risk_analytics_lineage_ref"],
        handoff["risk_analytics_ref"],
        handoff["risk_analytics_registry_ref"],
        handoff["oss_quality_repair_lineage_ref"],
        handoff["oss_quality_repair_ref"],
        handoff["oss_quality_ref"],
        handoff["oss_quality_degraded_source_ref"],
        handoff["oss_quality_repaired_artifact_ref"],
        handoff["multi_persona_proposal_lineage_ref"],
        *proposal_lineage["proposal_refs"],
        *[
            ref
            for ref in (
                handoff["portfolio_state_carryover_ref"],
                handoff["portfolio_state_ref"],
            )
            if ref
        ],
        *broker_carryover_refs,
        handoff["openclaw_session_context_ref"],
        handoff["openclaw_session_ref"],
        handoff["openclaw_source_oss_ref"],
        handoff["openclaw_upstream_session_ref"],
        *openclaw_continuity_refs,
        handoff["alpha_seed_revision_handoff_ref"],
        handoff["alpha_seed_revision_ref"],
        handoff["alpha_seed_source_ref"],
        handoff["alpha_seed_source_oss_ref"],
        f"runtime-binding://{runtime_readback['runtime_binding_id']}",
        f"object-store://{runtime_readback['object_store_metadata_key']}",
        f"reflection://{case['reflection']['agent_decision_traces'][-1]['reflection_id']}",
    ]
    assert runtime_feedback["state_updates"]["mark_runtime_feedback_seen"] is True
    assert runtime_feedback["state_updates"]["bind_runtime_context"] == runtime_readback["runtime_binding_id"]
    assert runtime_feedback["state_updates"]["verify_object_store_metadata"] == runtime_readback["object_store_metadata_key"]
    assert runtime_feedback["state_updates"]["bind_evolved_strategy_packet"] == handoff["strategy_packet_ref"]
    assert runtime_feedback["state_updates"]["bind_reconciled_experiment_ref"] == experiment_ref
    assert runtime_feedback["state_updates"]["bind_tracking_reconciliation_ref"] == tracking_reconciliation_ref
    assert runtime_feedback["state_updates"]["bind_tracking_repair_ref"] == tracking_repair_ref
    assert runtime_feedback["state_updates"]["bind_policy_oss_lineage_ref"] == handoff["policy_oss_lineage_ref"]
    assert runtime_feedback["state_updates"]["bind_policy_oss_ref"] == handoff["policy_oss_ref"]
    assert runtime_feedback["state_updates"]["bind_policy_oss_registry_ref"] == handoff["policy_oss_registry_ref"]
    assert runtime_feedback["state_updates"]["bind_reflection_oss_lineage_ref"] == handoff["reflection_oss_lineage_ref"]
    assert runtime_feedback["state_updates"]["bind_reflection_oss_ref"] == handoff["reflection_oss_ref"]
    assert runtime_feedback["state_updates"]["bind_reflection_oss_registry_ref"] == handoff[
        "reflection_oss_registry_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_risk_analytics_lineage_ref"] == handoff[
        "risk_analytics_lineage_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_risk_analytics_ref"] == handoff[
        "risk_analytics_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_risk_analytics_registry_ref"] == handoff[
        "risk_analytics_registry_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_lineage_ref"] == handoff[
        "oss_quality_repair_lineage_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_ref"] == handoff[
        "oss_quality_repair_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_ref"] == handoff[
        "oss_quality_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_degraded_source_ref"] == handoff[
        "oss_quality_degraded_source_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_repaired_artifact_ref"] == handoff[
        "oss_quality_repaired_artifact_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_action"] == (
        quality_persona_response["repair_action"]
    )
    assert runtime_feedback["state_updates"][
        "bind_oss_quality_downweighted_candidate_action"
    ] == quality_persona_response["downweighted_candidate_action"]
    assert runtime_feedback["state_updates"]["bind_multi_persona_proposal_lineage_ref"] == (
        proposal_lineage["lineage_ref"]
    )
    assert runtime_feedback["state_updates"]["bind_multi_persona_proposal_refs"] == (
        proposal_lineage["proposal_refs"]
    )
    assert runtime_feedback["state_updates"]["bind_multi_persona_proposal_persona_ids"] == (
        proposal_lineage["proposal_persona_ids"]
    )
    assert runtime_feedback["state_updates"]["bind_broker_adapter_carryover_ref"] == handoff[
        "broker_adapter_carryover_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_broker_adapter_carryover_status"] == handoff[
        "broker_adapter_carryover_status"
    ]
    assert runtime_feedback["state_updates"]["bind_previous_broker_adapter_followup_ref"] == handoff[
        "previous_broker_adapter_followup_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_previous_broker_adapter_source_packet_ref"] == handoff[
        "previous_broker_adapter_source_packet_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_previous_broker_adapter_action"] == handoff[
        "previous_broker_adapter_action"
    ]
    assert runtime_feedback["state_updates"]["bind_previous_broker_adapter_action_family"] == handoff[
        "previous_broker_adapter_action_family"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_session_context_ref"] == handoff[
        "openclaw_session_context_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_session_ref"] == handoff[
        "openclaw_session_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_source_oss_ref"] == handoff[
        "openclaw_source_oss_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_upstream_session_ref"] == handoff[
        "openclaw_upstream_session_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_session_continuity_ref"] == handoff[
        "openclaw_session_continuity_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_session_continuity_status"] == handoff[
        "openclaw_session_continuity_status"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_previous_source_oss_ref"] == handoff[
        "openclaw_previous_source_oss_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_previous_context_ref"] == handoff[
        "openclaw_previous_context_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_previous_session_ref"] == handoff[
        "openclaw_previous_session_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_previous_upstream_session_ref"] == handoff[
        "openclaw_previous_upstream_session_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_handoff_ref"] == handoff[
        "alpha_seed_revision_handoff_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_ref"] == handoff[
        "alpha_seed_revision_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_source_ref"] == handoff[
        "alpha_seed_source_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_source_oss_ref"] == handoff[
        "alpha_seed_source_oss_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_action"] == expected_alpha_action
    assert runtime_feedback["state_updates"]["bind_lean_packet_execution_projection"] == projection["projection_ref"]
    assert runtime_feedback["state_updates"]["attach_to_handoff_packet"] == handoff["packet_id"]
    assert runtime_feedback["state_updates"]["attach_to_decision_trace"] == case["reflection"]["agent_decision_traces"][-1]["reflection_id"]
    assert runtime_feedback["state_updates"]["schedule_next_cycle_after_feedback"] == schedule["next_cycle_due_at"]
    assert all(runtime_feedback["replay"].values())
    assert runtime_feedback["input_hash"]
    assert case["usability_dimensions"]["lean_packet_execution_projection"] == 1.0
    assert case["usability_dimensions"]["lean_runtime_feedback"] == 1.0
    assert case["usability_dimensions"]["experiment_tracking_lineage_handoff"] == 1.0
    assert case["usability_dimensions"]["policy_oss_lineage_handoff"] == 1.0
    assert case["usability_dimensions"]["reflection_oss_lineage_handoff"] == 1.0
    assert case["usability_dimensions"]["risk_analytics_lineage_handoff"] == 1.0
    assert case["usability_dimensions"]["multi_persona_proposal_lineage"] == 1.0
    assert case["usability_dimensions"]["openclaw_session_handoff"] == 1.0
    assert case["usability_dimensions"]["openclaw_session_continuity"] == 1.0
    assert case["usability_dimensions"]["alpha_seed_revision_handoff"] == 1.0
    assert case["usability_dimensions"]["oss_quality_repair_handoff"] == 1.0
    assert case["usability_dimensions"]["evolved_strategy_packet_handoff"] == 1.0
    assert case["usable"]["lean_packet_execution_projection_replayed"] is True
    assert case["usable"]["experiment_tracking_lineage_reaches_lean_handoff"] is True
    assert case["usable"]["policy_oss_lineage_reaches_lean_handoff"] is True
    assert case["usable"]["reflection_oss_lineage_reaches_lean_handoff"] is True
    assert case["usable"]["risk_analytics_lineage_reaches_lean_handoff"] is True
    assert case["usable"]["multi_persona_proposal_lineage_reaches_runtime"] is True
    assert case["usable"]["openclaw_session_reaches_lean_handoff"] is True
    assert case["usable"]["openclaw_session_continuity_drives_next_case"] is True
    assert case["usable"]["alpha_seed_revision_reaches_lean_handoff"] is True
    assert case["usable"]["oss_quality_repair_reaches_lean_handoff"] is True
    assert case["usable"]["evolved_strategy_packet_reaches_lean_handoff"] is True
    assert check_by_name["lean_packet_execution_projection_replays_packet_legs"]["status"] == "passed"
    assert check_by_name["lean_runtime_feedback_drives_persona_ooda"]["status"] == "passed"
    assert check_by_name["tracking_experiment_lineage_reaches_evolution_and_lean_packet"]["status"] == "passed"
    assert check_by_name["policy_oss_lineage_reaches_evolved_policy_and_lean_packet"]["status"] == "passed"
    assert check_by_name["reflection_oss_lineage_reaches_evolved_policy_and_lean_packet"]["status"] == "passed"
    assert check_by_name["risk_analytics_lineage_reaches_evolved_policy_and_lean_packet"]["status"] == "passed"
    assert check_by_name["multi_persona_proposal_lineage_reaches_runtime_feedback"]["status"] == "passed"
    assert check_by_name["openclaw_session_context_reaches_lean_handoff"]["status"] == "passed"
    assert check_by_name["openclaw_session_continuity_drives_next_case_request"]["status"] == "passed"
    assert check_by_name["alpha_seed_revision_reaches_lean_handoff"]["status"] == "passed"
    assert (
        check_by_name["oss_quality_repair_reaches_lean_handoff_and_runtime_feedback"][
            "status"
        ]
        == "passed"
    )
    assert check_by_name["evolved_strategy_packet_reaches_lean_handoff"]["status"] == "passed"
    assert case["usability_dimensions"]["scheduler_conflict_ooda_dispatch"] == 1.0
    assert case["usable"]["scheduler_conflict_ooda_dispatch_replayed"] is True
    assert check_by_name["scheduler_conflict_ooda_dispatch_replays_next_cycle"]["status"] == "passed"


def _assert_experiment_tracking_lineage_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["experiment_tracking_lineage_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    reconciliation = case["case_upstream_artifacts"]["tracking_reconciliation"]
    tracker = case["case_upstream_artifacts"]["tracker"]

    experiment_ref = reconciliation["repair"]["normalized_experiment_ref"]
    reconciliation_ref = reconciliation["reconciliation_ref"]
    repair_ref = reconciliation["repair"]["repair_ref"]
    lineage_hash = replay["case_specific_strategy_packet"]["experiment_tracking_provenance_hash"]

    assert proof["proof_id"] == f"tracking-experiment-lineage-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"tracking-experiment-lineage://{case['case_id']}"
    assert proof["model_id"] == PERSONA_EXPERIMENT_TRACKING_LINEAGE_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["backend"] == tracker["backend"]
    assert proof["tracker_request_id"] == tracker["request_id"]
    assert proof["tracking_run_id"] == tracker["run_id"]
    assert proof["experiment_ref"] == experiment_ref
    assert proof["tracking_reconciliation_ref"] == reconciliation_ref
    assert proof["tracking_repair_ref"] == repair_ref
    assert proof["tracking_repair_action"] == reconciliation["repair"]["action"]
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {lineage_hash}
    assert {
        experiment_ref,
        reconciliation_ref,
        repair_ref,
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
        proof["object_store_readback_ref"],
    } == set(proof["input_refs"])
    assert any(ref["ref_id"] == reconciliation_ref for ref in proof["decision_evidence_refs"])
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_policy_oss_lineage_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["policy_oss_lineage_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    policy_entry = case["case_upstream_artifacts"]["selected_oss"]["policy_candidate"]
    policy_materiality = case["oss_feedback"]["policy_candidate_materiality"]
    packet_policy_lineage = replay["case_specific_strategy_packet"]["policy_oss_lineage"]
    source_ref = f"oss://{policy_entry['component']}/{policy_entry['request_id']}"
    lineage_ref = packet_policy_lineage["lineage_ref"]
    registry_ref = packet_policy_lineage["registry_ref"]
    lineage_hash = packet_policy_lineage["lineage_hash"]

    assert proof["proof_id"] == f"policy-oss-lineage-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"policy-oss-lineage-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_POLICY_OSS_LINEAGE_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["component"] == policy_entry["component"]
    assert proof["request_id"] == policy_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["lineage_ref"] == lineage_ref
    assert proof["registry_ref"] == registry_ref
    assert proof["artifact_family"] == policy_entry["artifact_family"]
    assert proof["policy_quality"] == policy_materiality["policy_quality"]
    assert proof["policy_hint_risk"] == packet_policy_lineage["policy_hint_risk"]
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {lineage_hash}
    assert set(proof["input_refs"]) == {
        source_ref,
        lineage_ref,
        registry_ref,
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
        proof["object_store_readback_ref"],
    }
    assert source_ref in handoff["runtime_bundle_refs"]
    assert lineage_ref in handoff["runtime_bundle_refs"]
    assert registry_ref in handoff["runtime_bundle_refs"]
    assert source_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert lineage_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert registry_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert packet_readback["loaded_policy_oss_lineage"] == packet_policy_lineage
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_reflection_oss_lineage_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["reflection_oss_lineage_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    reflection_entry = case["case_upstream_artifacts"]["selected_oss"]["reflection_artifact"]
    reflection_materiality = case["oss_feedback"]["reflection_artifact_materiality"]
    packet_reflection_lineage = replay["case_specific_strategy_packet"][
        "reflection_oss_lineage"
    ]
    source_ref = f"oss://{reflection_entry['component']}/{reflection_entry['request_id']}"
    lineage_ref = packet_reflection_lineage["lineage_ref"]
    registry_ref = packet_reflection_lineage["registry_ref"]
    lineage_hash = packet_reflection_lineage["lineage_hash"]

    assert proof["proof_id"] == f"reflection-oss-lineage-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"reflection-oss-lineage-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_REFLECTION_OSS_LINEAGE_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["component"] == reflection_entry["component"]
    assert proof["request_id"] == reflection_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["lineage_ref"] == lineage_ref
    assert proof["registry_ref"] == registry_ref
    assert proof["artifact_family"] == reflection_entry["artifact_family"]
    assert proof["reflection_quality"] == reflection_materiality["reflection_quality"]
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"][
        "packet_ref"
    ]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == (
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    )
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {lineage_hash}
    assert set(proof["input_refs"]) == {
        source_ref,
        lineage_ref,
        registry_ref,
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
        proof["object_store_readback_ref"],
    }
    assert source_ref in handoff["runtime_bundle_refs"]
    assert lineage_ref in handoff["runtime_bundle_refs"]
    assert registry_ref in handoff["runtime_bundle_refs"]
    assert source_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert lineage_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert registry_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert packet_readback["loaded_reflection_oss_lineage"] == packet_reflection_lineage
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_risk_analytics_lineage_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["risk_analytics_lineage_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    risk_entry = case["case_upstream_artifacts"]["selected_oss"]["risk_analytics"]
    packet_risk_lineage = replay["case_specific_strategy_packet"]["risk_analytics_lineage"]
    source_ref = f"oss://{risk_entry['component']}/{risk_entry['request_id']}"
    lineage_ref = packet_risk_lineage["lineage_ref"]
    registry_ref = packet_risk_lineage["registry_ref"]
    lineage_hash = packet_risk_lineage["lineage_hash"]

    assert proof["proof_id"] == f"risk-analytics-lineage-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"risk-analytics-lineage-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_RISK_ANALYTICS_LINEAGE_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["component"] == risk_entry["component"]
    assert proof["request_id"] == risk_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["lineage_ref"] == lineage_ref
    assert proof["registry_ref"] == registry_ref
    assert proof["artifact_family"] == risk_entry["artifact_family"]
    assert proof["expected_artifact_family"] in {"pricing_report", "regime_report"}
    assert proof["risk_materiality_penalty"] == packet_risk_lineage["risk_materiality_penalty"]
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == (
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    )
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {lineage_hash}
    assert set(proof["input_refs"]) == {
        source_ref,
        lineage_ref,
        registry_ref,
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
        proof["object_store_readback_ref"],
    }
    assert [binding["generation"] for binding in proof["trace_bindings"]] == [1, 2]
    assert all(
        binding["candidate_generation_consumes_risk_analytics"]
        and binding["risk_evaluator_source_ref"] == source_ref
        and binding["risk_evaluator_passed"]
        and binding["risk_evaluator_selected_candidate_check_passed"]
        and binding["selected_candidate_cites_risk_analytics"]
        for binding in proof["trace_bindings"]
    )
    assert source_ref in handoff["runtime_bundle_refs"]
    assert lineage_ref in handoff["runtime_bundle_refs"]
    assert registry_ref in handoff["runtime_bundle_refs"]
    assert source_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert lineage_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert registry_ref in runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert runtime_feedback["state_updates"]["bind_risk_analytics_lineage_ref"] == lineage_ref
    assert runtime_feedback["state_updates"]["bind_risk_analytics_ref"] == source_ref
    assert runtime_feedback["state_updates"]["bind_risk_analytics_registry_ref"] == registry_ref
    assert packet_readback["loaded_risk_analytics_lineage"] == packet_risk_lineage
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_openclaw_session_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["openclaw_session_handoff"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    session_request_id = case["oss_feedback"]["request_ids"]["session"]
    source_ref = f"oss://openclaw/{session_request_id}"

    assert proof["proof_id"] == f"openclaw-session-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"openclaw-session-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_OPENCLAW_SESSION_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["component"] == "openclaw"
    assert proof["request_id"] == session_request_id
    assert proof["source_oss_ref"] == source_ref
    assert proof["artifact_family"] == "openclaw_session"
    assert proof["context_ref"] == handoff["openclaw_session_context_ref"]
    assert proof["context_hash"] == handoff["openclaw_session_context_hash"]
    assert proof["session_ref"] == handoff["openclaw_session_ref"]
    assert proof["session_id"] == handoff["openclaw_session_id"]
    assert proof["upstream_session_ref"] == handoff["openclaw_upstream_session_ref"]
    assert proof["upstream_session_id"] == handoff["openclaw_upstream_session_id"]
    assert proof["session_state"] == "active"
    assert proof["strategy_packet_ref"] == handoff["strategy_packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == (
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    )
    assert set(proof["input_refs"]) == {
        source_ref,
        proof["context_ref"],
        proof["session_ref"],
        proof["upstream_session_ref"],
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
    }
    assert len(proof["trace_bindings"]) == 2
    assert all(binding["reasoning_consumes_openclaw_source_ref"] for binding in proof["trace_bindings"])
    assert all(binding["selected_candidate_cites_openclaw_followup"] for binding in proof["trace_bindings"])
    assert source_ref in handoff["runtime_bundle_refs"]
    assert proof["context_ref"] in handoff["runtime_bundle_refs"]
    assert proof["session_ref"] in handoff["runtime_bundle_refs"]
    assert proof["upstream_session_ref"] in handoff["runtime_bundle_refs"]
    evidence_refs = runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert source_ref in evidence_refs
    assert proof["context_ref"] in evidence_refs
    assert proof["session_ref"] in evidence_refs
    assert proof["upstream_session_ref"] in evidence_refs
    assert runtime_feedback["state_updates"]["bind_openclaw_session_context_ref"] == proof[
        "context_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_session_ref"] == proof[
        "session_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_openclaw_source_oss_ref"] == source_ref
    assert runtime_feedback["state_updates"]["bind_openclaw_upstream_session_ref"] == proof[
        "upstream_session_ref"
    ]
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_alpha_seed_revision_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["alpha_seed_revision_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    alpha_revision = case["case_upstream_artifacts"]["alpha_seed_revision"]
    alpha_entry = case["case_upstream_artifacts"]["selected_oss"]["alpha_model"]
    source_ref = f"oss://{alpha_entry['component']}/{alpha_entry['request_id']}"
    base_seed_ref = f"alpha-seed://{case['seed_key']}"
    packet_alpha_handoff = replay["case_specific_strategy_packet"][
        "alpha_seed_revision_handoff"
    ]
    expected_action = ALPHA_SEED_REVISION_ACTION_BY_COMPONENT[alpha_entry["component"]]

    assert proof["proof_id"] == f"alpha-seed-revision-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"alpha-seed-revision-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_ALPHA_SEED_REVISION_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["component"] == alpha_entry["component"]
    assert proof["request_id"] == alpha_entry["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["revision_ref"] == alpha_revision["revision_ref"]
    assert proof["revision_id"] == alpha_revision["revision_id"]
    assert proof["revision_key"] == alpha_revision["revision"]["revision_key"]
    assert proof["base_seed_ref"] == base_seed_ref
    assert proof["revision_action"] == expected_action
    assert proof["handoff_ref"] == packet_alpha_handoff["handoff_ref"]
    assert proof["handoff_hash"] == packet_alpha_handoff["lineage_hash"]
    assert proof["downstream_vectorbt_request_id"] == case["case_upstream_artifacts"]["vectorbt"][
        "request_id"
    ]
    assert proof["downstream_policy_candidate_request_id"] == (
        case["case_upstream_artifacts"]["selected_oss"]["policy_candidate"]["request_id"]
    )
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == (
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    )
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {packet_alpha_handoff["lineage_hash"]}
    assert set(proof["input_refs"]) == {
        source_ref,
        alpha_revision["revision_ref"],
        base_seed_ref,
        packet_alpha_handoff["handoff_ref"],
        proof["strategy_packet_ref"],
        proof["lean_handoff_ref"],
        proof["lean_runtime_feedback_ref"],
        proof["object_store_readback_ref"],
    }
    assert len(proof["trace_bindings"]) == 2
    assert all(
        binding["reasoning_consumes_alpha_seed_revision"]
        and binding["candidate_generation_consumes_alpha_seed_revision"]
        and binding["selected_candidate_cites_alpha_seed_revision"]
        and binding["selected_candidate_cites_alpha_seed_source"]
        for binding in proof["trace_bindings"]
    )
    assert handoff["alpha_seed_revision_handoff"] == packet_alpha_handoff
    assert packet_readback["loaded_alpha_seed_revision_handoff"] == packet_alpha_handoff
    assert source_ref in handoff["runtime_bundle_refs"]
    assert proof["revision_ref"] in handoff["runtime_bundle_refs"]
    assert proof["base_seed_ref"] in handoff["runtime_bundle_refs"]
    assert proof["handoff_ref"] in handoff["runtime_bundle_refs"]
    evidence_refs = runtime_feedback["persona_ooda_followup"]["evidence_refs"]
    assert source_ref in evidence_refs
    assert proof["revision_ref"] in evidence_refs
    assert proof["base_seed_ref"] in evidence_refs
    assert proof["handoff_ref"] in evidence_refs
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_handoff_ref"] == proof[
        "handoff_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_ref"] == proof[
        "revision_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_source_ref"] == proof[
        "base_seed_ref"
    ]
    assert runtime_feedback["state_updates"]["bind_alpha_seed_source_oss_ref"] == source_ref
    assert runtime_feedback["state_updates"]["bind_alpha_seed_revision_action"] == expected_action
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_oss_quality_repair_handoff(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["oss_quality_repair_handoff"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    runtime_feedback = operational["lean_runtime_feedback"]
    packet_readback = replay["lean_object_store_packet_readback"]
    degraded_response = case["case_upstream_artifacts"]["degraded_oss_response"]
    persona_response = degraded_response["persona_quality_response"]
    packet_lineage = replay["case_specific_strategy_packet"][
        "oss_quality_repair_lineage"
    ]
    quality_role = degraded_response["role"]
    source_ref = degraded_response["source_oss_ref"]
    quality_ref = degraded_response["quality_ref"]
    repair_ref = degraded_response["repair_ref"]
    output_ref = persona_response["output_ref"]
    repaired_artifact_ref = persona_response["repaired_artifact_ref"]
    expected_issue = OSS_QUALITY_ISSUE_BY_ROLE[quality_role]
    expected_action = OSS_QUALITY_REPAIR_ACTION_BY_ROLE[quality_role]
    expected_downweighted_action = OSS_QUALITY_AFFECTED_ACTION_BY_ROLE[quality_role]

    assert proof["proof_id"] == f"oss-quality-repair-handoff-{case['case_id']}"
    assert proof["proof_ref"] == f"oss-quality-repair-handoff://{case['case_id']}"
    assert proof["model_id"] == PERSONA_OSS_QUALITY_REPAIR_HANDOFF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["case_id"] == case["case_id"]
    assert proof["persona_id"] == case["persona_id"]
    assert proof["role"] == quality_role
    assert proof["component"] == degraded_response["component"]
    assert proof["request_id"] == degraded_response["request_id"]
    assert proof["source_oss_ref"] == source_ref
    assert proof["quality_ref"] == quality_ref
    assert proof["repair_ref"] == repair_ref
    assert proof["output_ref"] == output_ref
    assert proof["repaired_artifact_ref"] == repaired_artifact_ref
    assert proof["issue_type"] == expected_issue
    assert proof["repair_action"] == expected_action
    assert proof["downweighted_candidate_action"] == expected_downweighted_action
    assert proof["lineage_ref"] == packet_lineage["lineage_ref"]
    assert proof["lineage_hash"] == packet_lineage["lineage_hash"]
    assert proof["strategy_packet_ref"] == replay["case_specific_strategy_packet"]["packet_ref"]
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_runtime_feedback_ref"] == (
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    )
    assert proof["object_store_readback_ref"] == packet_readback["readback_id"]
    assert set(proof["lineage_hashes"].values()) == {packet_lineage["lineage_hash"]}
    assert set(proof["input_refs"]) == {
        source_ref,
        quality_ref,
        repair_ref,
        output_ref,
        repaired_artifact_ref,
        packet_lineage["lineage_ref"],
        replay["case_specific_strategy_packet"]["packet_ref"],
        f"lean-handoff://{handoff['packet_id']}",
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}",
        packet_readback["readback_id"],
    }
    assert len(proof["trace_bindings"]) == len(
        case["reflection"]["agent_decision_traces"]
    )
    for binding in proof["trace_bindings"]:
        assert binding["reasoning_consumes_quality_repair"] is True
        assert binding["candidate_generation_consumes_quality_repair"] is True
        assert binding["selected_candidate_cites_quality_repair"] is True
        assert binding["decision_artifact_replays_quality_repair"] is True
        assert binding["selected_action"] in {
            "feedback-adapt",
            "retain-observe",
            "risk-off",
            "contrarian-check",
        }
        assert float(binding["score_adjustment"]) > 0.0
        assert binding["scorecard_adjustment"] == binding["score_adjustment"]
        assert binding["scorecard_penalty"] == binding["quality_penalty"]
        assert float(binding["affected_action_penalty"]) < 0.0

    assert packet_lineage["model_id"] == PERSONA_OSS_QUALITY_REPAIR_HANDOFF_MODEL_ID
    assert packet_lineage["role"] == quality_role
    assert packet_lineage["component"] == degraded_response["component"]
    assert packet_lineage["request_id"] == degraded_response["request_id"]
    assert packet_lineage["source_oss_ref"] == source_ref
    assert packet_lineage["quality_ref"] == quality_ref
    assert packet_lineage["repair_ref"] == repair_ref
    assert packet_lineage["repaired_artifact_ref"] == repaired_artifact_ref
    assert packet_lineage["issue_type"] == expected_issue
    assert packet_lineage["repair_action"] == expected_action
    assert packet_lineage["downweighted_candidate_action"] == expected_downweighted_action

    packet = replay["case_specific_strategy_packet"]
    assert packet["oss_quality_repair_lineage"] == packet_lineage
    assert packet["oss_quality_repair_lineage_ref"] == packet_lineage["lineage_ref"]
    assert packet["oss_quality_repair_lineage_hash"] == packet_lineage["lineage_hash"]
    assert packet["oss_quality_repair_ref"] == repair_ref
    assert packet["oss_quality_ref"] == quality_ref
    assert packet["oss_quality_degraded_source_ref"] == source_ref
    assert packet["oss_quality_repaired_artifact_ref"] == repaired_artifact_ref
    assert packet["oss_quality_repair_action"] == expected_action
    assert packet["oss_quality_downweighted_candidate_action"] == (
        expected_downweighted_action
    )
    assert handoff["oss_quality_repair_lineage"] == packet_lineage
    assert handoff["oss_quality_repair_lineage_ref"] == packet_lineage["lineage_ref"]
    assert handoff["oss_quality_repair_lineage_hash"] == packet_lineage["lineage_hash"]
    assert handoff["oss_quality_repair_ref"] == repair_ref
    assert handoff["oss_quality_ref"] == quality_ref
    assert handoff["oss_quality_degraded_source_ref"] == source_ref
    assert handoff["oss_quality_repaired_artifact_ref"] == repaired_artifact_ref
    assert handoff["oss_quality_repair_action"] == expected_action
    assert handoff["oss_quality_downweighted_candidate_action"] == (
        expected_downweighted_action
    )

    assert packet_readback["loaded_oss_quality_repair_lineage"] == packet_lineage
    assert packet_readback["oss_quality_repair_lineage_ref"] == packet_lineage[
        "lineage_ref"
    ]
    assert packet_readback["loaded_oss_quality_repair_lineage_ref"] == packet_lineage[
        "lineage_ref"
    ]
    assert packet_readback["oss_quality_repair_lineage_hash"] == packet_lineage[
        "lineage_hash"
    ]
    assert packet_readback["loaded_oss_quality_repair_lineage_hash"] == packet_lineage[
        "lineage_hash"
    ]
    assert packet_readback["oss_quality_repair_ref"] == repair_ref
    assert packet_readback["loaded_oss_quality_repair_ref"] == repair_ref
    assert packet_readback["oss_quality_ref"] == quality_ref
    assert packet_readback["loaded_oss_quality_ref"] == quality_ref
    assert packet_readback["oss_quality_degraded_source_ref"] == source_ref
    assert packet_readback["loaded_oss_quality_degraded_source_ref"] == source_ref
    assert packet_readback["oss_quality_repaired_artifact_ref"] == repaired_artifact_ref
    assert (
        packet_readback["loaded_oss_quality_repaired_artifact_ref"]
        == repaired_artifact_ref
    )

    for target in replay["case_specific_packet_targets"]:
        assert target["oss_quality_repair_lineage_ref"] == packet_lineage["lineage_ref"]
        assert target["oss_quality_repair_lineage_hash"] == packet_lineage["lineage_hash"]
        assert target["oss_quality_repair_ref"] == repair_ref
        assert target["oss_quality_ref"] == quality_ref
        assert target["signal"]["metadata"]["oss_quality_repair_lineage_ref"] == (
            packet_lineage["lineage_ref"]
        )
        assert target["signal"]["metadata"]["oss_quality_repair_ref"] == repair_ref
        assert target["signal"]["metadata"]["oss_quality_ref"] == quality_ref

    runtime_bundle_refs = set(handoff["runtime_bundle_refs"])
    assert {
        source_ref,
        quality_ref,
        repair_ref,
        repaired_artifact_ref,
        packet_lineage["lineage_ref"],
    }.issubset(runtime_bundle_refs)
    evidence_refs = set(runtime_feedback["persona_ooda_followup"]["evidence_refs"])
    assert {
        source_ref,
        quality_ref,
        repair_ref,
        repaired_artifact_ref,
        packet_lineage["lineage_ref"],
    }.issubset(evidence_refs)
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_lineage_ref"] == (
        packet_lineage["lineage_ref"]
    )
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_ref"] == repair_ref
    assert runtime_feedback["state_updates"]["bind_oss_quality_ref"] == quality_ref
    assert runtime_feedback["state_updates"]["bind_oss_quality_degraded_source_ref"] == (
        source_ref
    )
    assert runtime_feedback["state_updates"]["bind_oss_quality_repaired_artifact_ref"] == (
        repaired_artifact_ref
    )
    assert runtime_feedback["state_updates"]["bind_oss_quality_repair_action"] == (
        expected_action
    )
    assert runtime_feedback["state_updates"][
        "bind_oss_quality_downweighted_candidate_action"
    ] == expected_downweighted_action
    assert runtime_feedback["replay"]["oss_quality_repair_lineage_bound"] is True
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_lean_packet_execution_projection(case: dict) -> None:
    operational = case["operational_context"]
    projection = operational["lean_packet_execution_projection"]
    handoff = operational["lean_handoff"]
    replay = operational["lean_engine_replay"]
    lifecycle = operational["broker_lifecycle"]
    conflict = operational["persona_conflict_resolution"]
    final_costs = operational["market_friction"]["generation_costs"][-1]["leg_costs"]
    cost_by_instrument = {cost["instrument"]: cost for cost in final_costs}
    orders_by_id = {order["order_id"]: order for order in lifecycle["orders"]}

    assert projection["projection_id"] == f"lean-packet-execution-{case['case_id']}"
    assert projection["projection_ref"] == f"lean-packet-execution://{case['case_id']}/generation2"
    assert projection["model_id"] == LEAN_PACKET_EXECUTION_PROJECTION_MODEL_ID
    assert projection["status"] == "passed"
    assert projection["case_id"] == case["case_id"]
    assert projection["persona_id"] == case["persona_id"]
    assert projection["strategy_packet_ref"] == handoff["strategy_packet_ref"]
    assert projection["source_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert projection["source_runtime_ref"] == f"lean-engine://{replay['replay_id']}"
    assert projection["policy_id"] == case["generation_results"][-1]["policy_id"]
    assert projection["policy_version"] == case["generation_results"][-1]["policy_version"]
    assert projection["generation"] == 2
    assert projection["target_stage"] == "paper"
    assert projection["portfolio_instruments"] == case["portfolio"]["instruments"]
    assert projection["capital_budget_pct"] == conflict["resolved_allocation"]["capital_budget_pct"]
    assert projection["leg_count"] == PORTFOLIO_LEG_COUNT
    assert projection["order_count"] == PORTFOLIO_LEG_COUNT
    assert projection["fill_count"] == PORTFOLIO_LEG_COUNT
    assert len(projection["leg_projections"]) == PORTFOLIO_LEG_COUNT
    assert projection["input_refs"] == [
        handoff["strategy_packet_ref"],
        f"lean-handoff://{handoff['packet_id']}",
        f"lean-engine://{replay['replay_id']}",
        conflict["resolution_ref"],
        handoff["strict_oos_evolution_proof_ref"],
        handoff["no_leakage_protocol_ref"],
        handoff["evolution_trajectory_ref"],
    ]

    for leg in projection["leg_projections"]:
        order = orders_by_id[leg["broker_order_id"]]
        cost = cost_by_instrument[leg["instrument"]]
        assert leg["generation"] == 2
        assert leg["policy_id"] == projection["policy_id"]
        assert leg["policy_version"] == projection["policy_version"]
        assert leg["instrument"] in case["portfolio"]["instruments"]
        assert leg["execution_symbol"].startswith(leg["lean_symbol"])
        assert leg["lean_symbol"] == order["symbol"]
        assert leg["direction"] == handoff["resolved_direction_by_instrument"][leg["instrument"]]
        assert leg["target_weight"] == handoff["resolved_weight_by_instrument"][leg["instrument"]]
        assert leg["resolved_weight"] == handoff["resolved_weight_by_instrument"][leg["instrument"]]
        assert leg["capital_budget_pct"] == projection["capital_budget_pct"]
        assert leg["quantity_type"] == case["order_profile"]["quantity_type"]
        assert leg["order_type"] == case["order_profile"]["order_type"]
        assert leg["lean_order_call"] in {"SetHoldings", "MarketOrder", "LimitOrder"}
        assert leg["target_ref"] == f"{projection['projection_ref']}/leg/{leg['leg_index']}/target"
        assert leg["order_ref"] == f"paper-order://{leg['broker_order_id']}"
        assert leg["fill_ref"] == f"paper-fill://{leg['broker_fill_event_id']}"
        assert leg["readback_ref"] == f"{leg['order_ref']}/readback"
        assert leg["signal_id"] == leg["expected_signal_id"]
        assert abs(leg["requested_quantity"] - leg["expected_requested_quantity"]) <= 1e-6
        assert leg["fill_quantity"] != 0.0
        assert leg["fill_price"] > 0.0
        assert leg["market_data_ref"].startswith(HISTORICAL_OHLCV_DATASET_ID)
        assert leg["source_dataset_ref"] == HISTORICAL_OHLCV_DATASET_ID
        assert leg["market_friction_notional"] == cost["notional"]
        assert leg["market_friction_total_cost_bps"] == cost["total_cost_bps"]
        assert leg["within_liquidity_cap"] is True
        assert order["generation"] == 2
        assert order["fill_event_id"] == leg["broker_fill_event_id"]
        assert order["terminal_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert order["readback_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert leg["broker_terminal_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert leg["broker_readback_status"] == BROKER_LIFECYCLE_TERMINAL_STATUS
        assert leg["broker_reconciled"] is True
        assert leg["live_broker_submitted"] is False
        assert leg["event_chain"] == [
            "packet_leg_target",
            "lean_target_order",
            "paper_fill_readback",
        ]
        assert set(leg["input_refs"]) == {
            handoff["strategy_packet_ref"],
            f"lean-handoff://{handoff['packet_id']}",
            handoff["strict_oos_evolution_proof_ref"],
            handoff["no_leakage_protocol_ref"],
            handoff["evolution_trajectory_ref"],
            f"lean-engine://{replay['replay_id']}",
            conflict["resolution_ref"],
            leg["order_ref"],
            leg["fill_ref"],
        }

    assert round(sum(leg["target_weight"] for leg in projection["leg_projections"]), 6) == (
        projection["capital_budget_pct"]
    )
    assert all(projection["replay"].values())
    assert projection["input_hash"]


def _assert_evolved_strategy_packet_proof(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["evolved_strategy_packet_proof"]
    replay = operational["lean_engine_replay"]
    handoff = operational["lean_handoff"]
    projection = operational["lean_packet_execution_projection"]
    runtime_feedback = operational["lean_runtime_feedback"]
    strict_oos = case["evolution"]["strict_oos_evolution_proof"]
    no_leakage = case["evolution"]["no_leakage_protocol"]
    trajectory = case["evolution"]["trajectory"]
    strategy_packet = replay["case_specific_strategy_packet"]

    assert proof["model_id"] == LEAN_EVOLVED_STRATEGY_PACKET_PROOF_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["proof_ref"] == f"evolved-strategy-packet://{case['case_id']}"
    assert proof["strategy_packet_ref"] == strategy_packet["packet_ref"]
    assert proof["strategy_packet_ref"] == handoff["strategy_packet_ref"]
    assert proof["policy_id"] == case["generation_results"][-1]["policy_id"]
    assert proof["policy_version"] == case["generation_results"][-1]["policy_version"]
    assert proof["generation"] == 2
    assert proof["source_outcome_window"] == "holdout"
    assert proof["validation_window"] == "future_holdout"
    assert proof["strict_oos_proof_ref"] == strict_oos["proof_ref"]
    assert proof["no_leakage_protocol_ref"] == f"no-leakage://{no_leakage['protocol_id']}"
    assert proof["evolution_trajectory_ref"] == f"trajectory://{trajectory['trajectory_id']}"
    assert proof["lean_engine_replay_ref"] == f"lean-engine://{replay['replay_id']}"
    assert proof["lean_handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["lean_packet_execution_projection_ref"] == projection["projection_ref"]
    assert proof["lean_runtime_feedback_ref"] == f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    assert proof["future_holdout_score"] == case["scores"]["generation2_future_holdout"]
    assert proof["future_holdout_improvement"] == case["scores"]["future_generation_improvement"]
    assert proof["future_holdout_improvement"] > 0
    assert proof["lineage_refs"] == [
        strategy_packet["packet_ref"],
        strict_oos["proof_ref"],
        f"no-leakage://{no_leakage['protocol_id']}",
        f"trajectory://{trajectory['trajectory_id']}",
        f"lean-engine://{replay['replay_id']}",
        f"lean-handoff://{handoff['packet_id']}",
        projection["projection_ref"],
        f"lean-runtime-feedback://{runtime_feedback['feedback_id']}",
    ]
    assert all(proof["replay"].values())
    assert proof["input_hash"]


def _assert_scheduler_conflict_ooda_proof(case: dict) -> None:
    operational = case["operational_context"]
    proof = operational["scheduler_conflict_ooda_proof"]
    conflict = operational["persona_conflict_resolution"]
    schedule = operational["autonomous_schedule"]
    handoff = operational["lean_handoff"]
    adapter_followup = operational["broker_adapter_followup"]
    runtime_feedback = operational["lean_runtime_feedback"]

    assert proof["model_id"] == PERSONA_SCHEDULER_CONFLICT_OODA_MODEL_ID
    assert proof["status"] == "passed"
    assert proof["proof_ref"] == f"scheduler-conflict-ooda://{case['case_id']}"
    assert proof["schedule_ref"] == schedule["schedule_ref"]
    assert proof["conflict_ref"] == conflict["resolution_ref"]
    assert proof["handoff_ref"] == f"lean-handoff://{handoff['packet_id']}"
    assert proof["adapter_followup_ref"] == f"broker-adapter-followup://{adapter_followup['followup_id']}"
    assert proof["runtime_feedback_ref"] == f"lean-runtime-feedback://{runtime_feedback['feedback_id']}"
    assert proof["dispatch_ref"] == f"scheduler-dispatch://{case['case_id']}/next-cycle"
    assert proof["conflict_types"] == conflict["conflict_types"]
    assert proof["next_ooda_step"] == runtime_feedback["persona_ooda_followup"]["ooda_step"]
    assert proof["next_scheduler_phase"] == runtime_feedback["persona_ooda_followup"]["next_scheduler_phase"]
    assert proof["next_cycle_due_at"] == schedule["next_cycle_due_at"]
    assert [event["phase"] for event in proof["phase_events"]] == list(AUTONOMOUS_SCHEDULER_PHASES)
    assert [event["event_type"] for event in proof["dispatch_events"]] == [
        "scheduler_recovery_tick",
        "multi_persona_conflict_resolution",
        "lean_handoff_materialization",
        "broker_adapter_followup",
        "lean_runtime_feedback",
        "scheduler_next_cycle_dispatch",
    ]
    assert [event["sequence"] for event in proof["dispatch_events"]] == [1, 2, 3, 4, 5, 6]
    assert proof["dispatch_events"][1]["output_ref"] == conflict["resolution_ref"]
    assert proof["dispatch_events"][2]["output_ref"] == proof["handoff_ref"]
    assert proof["dispatch_events"][3]["output_ref"] == proof["adapter_followup_ref"]
    assert proof["dispatch_events"][4]["output_ref"] == proof["runtime_feedback_ref"]
    assert proof["dispatch_events"][5]["output_ref"] == proof["dispatch_ref"]
    assert conflict["resolution_ref"] in proof["dispatch_events"][2]["input_refs"]
    assert schedule["schedule_ref"] in proof["dispatch_events"][2]["input_refs"]
    assert proof["adapter_followup_ref"] in proof["dispatch_events"][5]["input_refs"]
    assert proof["runtime_feedback_ref"] in proof["dispatch_events"][5]["input_refs"]
    assert all(proof["replay"].values())
    assert proof["input_hash"]


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

"""E2E: persona agents plan, trade, reflect, and evolve across 3000 cases."""

from __future__ import annotations

from services.persona.agent_usability_validation import (
    DEFAULT_CASE_COUNT,
    GENERATION_COUNT,
    HISTORICAL_OHLCV_DATASET_ID,
    MIN_USABILITY_SCORE,
    ORDER_TYPES,
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


def _assert_evolution_and_scores(case: dict) -> None:
    assert case["scores"]["holdout_improvement"] > 0
    assert case["scores"]["future_generation_improvement"] > 0
    assert case["evolution"]["decision_state"] == "executed"
    assert case["evolution"]["execution_status"] == "succeeded"
    assert case["evolution"]["review_steps"] == ["reviewed", "approved", "executed"]
    assert min(case["usability_dimensions"].values()) >= 0.8
    assert case["overall_usability_score"] >= MIN_USABILITY_SCORE
    assert HISTORICAL_OHLCV_DATASET_ID in case["source_dataset_refs"]

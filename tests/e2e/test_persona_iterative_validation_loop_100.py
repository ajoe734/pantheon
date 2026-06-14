from __future__ import annotations

from services.persona.iterative_validation_loop import (
    ITERATIVE_META_PHASES,
    PRIOR_VALIDATION_SUITES,
    TOTAL_ITERATIVE_VALIDATION_ROUNDS,
    build_iterative_validation_round_plan,
    coverage_digest,
    run_iterative_validation_100,
)
from services.persona.remaining_gap_validation import (
    ALPHA_FAMILIES,
    LEAN_LIFECYCLES,
    LONG_MEMORY_SCENARIOS,
    OPTIMIZATION_SCENARIOS,
    REMAINING_GAP_TYPES,
)


def test_iterative_validation_plan_references_all_previous_results() -> None:
    previous_results = [
        {
            "round_id": "persona-meta-loop-100-001",
            "selected_case": {
                "case_id": "persona-remaining-gap-e2e-0001",
                "gap_type": "lean_order_feedback_recovery",
                "alpha_family": "pure_quant_momentum",
                "broker_adapter": "ibkr",
                "lean_lifecycle": "market_fill_ack_recovered",
                "long_memory_scenario": "newer_memory_preferred",
                "optimization_scenario": "accepted_after_backtest_improves",
            },
            "defects_found": [],
        },
        {
            "round_id": "persona-meta-loop-100-002",
            "selected_case": {
                "case_id": "persona-remaining-gap-e2e-0002",
                "gap_type": "long_term_memory_influence",
                "alpha_family": "pure_quant_reversal",
                "broker_adapter": "ibkr",
                "lean_lifecycle": "market_fill_ack_recovered",
                "long_memory_scenario": "newer_memory_preferred",
                "optimization_scenario": "accepted_after_backtest_improves",
            },
            "defects_found": [],
        },
    ]

    plan = build_iterative_validation_round_plan(3, previous_results)

    assert plan["round_id"] == "persona-meta-loop-100-003"
    assert plan["asked_before_execution"] is True
    assert plan["prior_suites_considered"] == list(PRIOR_VALIDATION_SUITES)
    assert set(plan["questions"]) == {
        "not_yet_verified",
        "deeper_validation",
        "realistic_untested_combination",
    }
    assert all(plan["questions"][key] for key in plan["questions"])
    assert plan["validation_plan"]["references_previous_result_ids"] == [
        "persona-meta-loop-100-001",
        "persona-meta-loop-100-002",
    ]
    assert plan["validation_plan"]["prior_coverage_digest"] == coverage_digest(previous_results)
    assert plan["validation_plan"]["meta_phase_order"] == list(ITERATIVE_META_PHASES)
    assert plan["validation_plan"]["selected_case"]["case_id"] not in {
        "persona-remaining-gap-e2e-0001",
        "persona-remaining-gap-e2e-0002",
    }


def test_iterative_validation_loop_runs_100_reference_aware_e2e_rounds(tmp_path) -> None:
    suite = run_iterative_validation_100(work_dir=tmp_path)
    results = suite["results"]

    assert suite["suite_id"] == "persona-meta-loop-100"
    assert suite["round_count"] == TOTAL_ITERATIVE_VALIDATION_ROUNDS
    assert len(results) == TOTAL_ITERATIVE_VALIDATION_ROUNDS
    assert suite["prior_suites_considered"] == list(PRIOR_VALIDATION_SUITES)
    assert suite["defects_found"] == []
    assert suite["correction_status"] == "no_defect_detected"

    round_ids = [result["round_id"] for result in results]
    selected_case_ids = [result["selected_case"]["case_id"] for result in results]
    iterative_combo_ids = [
        result["validation_plan"]["iterative_combination_id"]
        for result in results
    ]
    assert len(set(round_ids)) == 100
    assert len(set(selected_case_ids)) == 100
    assert len(set(iterative_combo_ids)) == 100

    for index, result in enumerate(results, start=1):
        previous_results = results[: index - 1]
        expected_previous_ids = [previous["round_id"] for previous in previous_results]
        assert result["round_number"] == index
        assert result["asked_before_execution"] is True
        assert result["prior_result_count"] == index - 1
        assert result["previous_round_ids_seen"] == expected_previous_ids
        assert result["validation_plan"]["references_previous_result_ids"] == expected_previous_ids
        assert result["validation_plan"]["prior_coverage_digest"] == coverage_digest(previous_results)
        assert result["validation_plan"]["meta_phase_order"] == list(ITERATIVE_META_PHASES)
        assert result["meta_executed_phase_order"] == list(ITERATIVE_META_PHASES)
        assert result["meta_plan_executed"] is True
        assert result["defects_found"] == []
        assert result["correction_status"] == "no_defect_detected"
        assert result["coverage_after_round"] == coverage_digest(results[:index])

        underlying = result["underlying_proof"]
        assert underlying["case_id"] == result["selected_case"]["case_id"]
        assert underlying["validation_round"]["asked_before_execution"] is True
        assert underlying["validation_round"]["plan_executed"] is True
        assert underlying["validation_round"]["defects_found"] == []

    coverage = suite["coverage"]
    assert coverage == coverage_digest(results)
    assert coverage["round_count"] == 100
    assert coverage["defect_count"] == 0
    assert set(coverage["gap_type_counts"]) == set(REMAINING_GAP_TYPES)
    assert set(coverage["alpha_family_counts"]) == set(ALPHA_FAMILIES)
    assert set(coverage["broker_adapter_counts"]) == {"ibkr", "shioaji", "kraken"}
    assert set(coverage["lean_lifecycle_counts"]) == set(LEAN_LIFECYCLES)
    assert set(coverage["long_memory_scenario_counts"]) == set(LONG_MEMORY_SCENARIOS)
    assert set(coverage["optimization_scenario_counts"]) == set(OPTIMIZATION_SCENARIOS)
    assert all(count > 0 for count in coverage["gap_type_counts"].values())
    assert all(count > 0 for count in coverage["alpha_family_counts"].values())
    assert all(count > 0 for count in coverage["broker_adapter_counts"].values())
    assert all(count > 0 for count in coverage["lean_lifecycle_counts"].values())
    assert all(count > 0 for count in coverage["long_memory_scenario_counts"].values())
    assert all(count > 0 for count in coverage["optimization_scenario_counts"].values())

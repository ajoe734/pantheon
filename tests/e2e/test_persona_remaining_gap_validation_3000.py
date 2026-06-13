from __future__ import annotations

from collections import Counter

import pytest

from services.persona.remaining_gap_validation import (
    LEAN_LIFECYCLES,
    LONG_MEMORY_SCENARIOS,
    OPTIMIZATION_SCENARIOS,
    REMAINING_GAP_TYPES,
    TOTAL_REMAINING_GAP_E2E_CASES,
    build_remaining_gap_case,
    build_validation_round_plan,
    run_remaining_gap_e2e_case,
)


CASES = tuple(
    build_remaining_gap_case(index)
    for index in range(1, TOTAL_REMAINING_GAP_E2E_CASES + 1)
)


def test_remaining_gap_case_manifest_is_unique_and_not_previous_3000() -> None:
    case_ids = [case.case_id for case in CASES]
    round_plans = [build_validation_round_plan(case) for case in CASES]
    combination_ids = [
        plan["validation_plan"]["realistic_combination_id"]
        for plan in round_plans
    ]
    assert len(case_ids) == 3000
    assert len(set(case_ids)) == 3000
    assert len(combination_ids) == 3000
    assert len(set(combination_ids)) == 3000
    assert not any(case_id.startswith("agent-usability-") for case_id in case_ids)
    assert not any(case_id.startswith("persona-cognitive-closed-loop-") for case_id in case_ids)
    assert not any(case_id.startswith("e2e-loop-") for case_id in case_ids)
    assert all(plan["asked_before_execution"] is True for plan in round_plans)
    assert all(
        set(plan["self_questions"]) == {
            "not_yet_verified",
            "deeper_validation",
            "realistic_untested_combination",
        }
        for plan in round_plans
    )
    assert all(
        all(plan["self_questions"][key] for key in plan["self_questions"])
        for plan in round_plans
    )

    assert Counter(case.gap_type for case in CASES) == {
        gap_type: 1000 for gap_type in REMAINING_GAP_TYPES
    }
    assert Counter(case.lean_lifecycle for case in CASES if case.gap_type == "lean_order_feedback_recovery") == {
        lifecycle: 200 for lifecycle in LEAN_LIFECYCLES
    }
    assert Counter(
        case.long_memory_scenario for case in CASES if case.gap_type == "long_term_memory_influence"
    ) == {scenario: 250 for scenario in LONG_MEMORY_SCENARIOS}
    assert Counter(
        case.optimization_scenario for case in CASES if case.gap_type == "optimization_backtest_proof"
    ) == {scenario: 500 for scenario in OPTIMIZATION_SCENARIOS}


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_remaining_gap_case_runs_full_e2e(case, tmp_path) -> None:
    proof = run_remaining_gap_e2e_case(case, work_dir=tmp_path)

    assert proof["case_id"] == case.case_id
    assert proof["gap_type"] == case.gap_type
    assert proof["persona_id"] == case.persona_id
    assert proof["strategy_id"] == case.strategy_id
    assert proof["alpha_family"] == case.alpha_family
    assert proof["phases"]
    assert proof["validation_round"]["round"] == case.ordinal
    assert proof["validation_round"]["asked_before_execution"] is True
    assert proof["validation_round"]["plan_executed"] is True
    assert proof["validation_round"]["executed_phase_order"] == proof["phases"]
    assert proof["validation_round"]["validation_plan"]["phase_order"] == proof["phases"]
    assert proof["validation_round"]["defects_found"] == []
    assert proof["validation_round"]["correction_status"] == "no_defect_detected"

    if case.gap_type == "lean_order_feedback_recovery":
        assert proof["lean"]["first_snapshot_status"] == "ok"
        assert proof["feedback_recovery"]["store_exists"] is True
        assert proof["feedback_recovery"]["stored_event_id"] in proof["feedback_recovery"]["recovered_event_ids"]
        assert proof["feedback_recovery"]["event_type"] == proof["lean"]["expected_event_type"]
        assert proof["feedback_recovery"]["lineage"]["strategy_id"] == case.strategy_id
        order_context = proof["feedback_recovery"]["lineage"]["order_context"]
        assert order_context["adapter_response_status"]
        assert order_context["adapter"]
        assert proof["memory"]["writeback"]["created"] is True
        assert proof["memory"]["persona_read"]["reuse_count"] == 1
        assert proof["memory"]["institutional_read"]["reuse_count"] == 1
        assert proof["safety"] == {
            "submitted_to_broker": False,
            "is_real_order": False,
            "is_real_capital": False,
        }
        if case.lean_lifecycle == "duplicate_retry_idempotent":
            assert order_context["noop_reason"] == "duplicate_signal_id"
            assert order_context["idempotent_replay"] is True
        if case.lean_lifecycle == "binding_mismatch_filtered":
            assert order_context["filter_reason"] == "binding_mismatch"

    if case.gap_type == "long_term_memory_influence":
        memory = proof["long_memory"]
        assert memory["selected_memory_id"] == memory["new_memory_id"]
        assert memory["selected_reuse_count"] == 1
        assert memory["other_persona_retrievable_by_owner"] is True
        assert memory["other_persona_leaked"] is False
        assert memory["institutional_read"]["reuse_count"] == 1
        assert proof["decision"]["changed_by_long_term_memory"] is True
        assert proof["decision"]["source_memory_id"] == memory["selected_memory_id"]
        if case.long_memory_scenario == "superseded_memory_ignored":
            assert memory["old_superseded_by"] == memory["new_memory_id"]
            assert memory["old_memory_id"] not in memory["active_hit_ids"]

    if case.gap_type == "optimization_backtest_proof":
        optimization = proof["optimization"]
        assert optimization["decision_valid"] is True
        assert proof["feedback_recovery"]["stored_event_id"] in proof["feedback_recovery"]["recovered_event_ids"]
        assert proof["memory"]["writeback"]["created"] is True
        assert proof["memory"]["persona_read"]["reuse_count"] == 1
        if case.optimization_scenario == "accepted_after_backtest_improves":
            assert optimization["after_score"] > optimization["before_score"]
            assert optimization["improvement"] > 0
            assert optimization["accepted"] is True
            assert optimization["decision"]["decision_state"] == "executed"
            assert optimization["decision"]["execution_result"]["status"] == "succeeded"
        else:
            assert optimization["after_score"] <= optimization["before_score"]
            assert optimization["improvement"] <= 0
            assert optimization["rejected"] is True
            assert optimization["decision"]["decision_state"] == "rejected"
            assert "execution_result" not in optimization["decision"] or optimization["decision"]["execution_result"] is None

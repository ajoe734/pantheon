from __future__ import annotations

from collections import Counter

import pytest

from services.persona.cognitive_loop_runtime import (
    ALPHA_MODES,
    CLOSED_LOOP_TYPES,
    TOTAL_COGNITIVE_E2E_CASES,
    TOOL_SCENARIOS,
    build_persona_cognitive_case,
    run_persona_cognitive_closed_loop,
)
from services.research.replication.gate_schema import CandidateAdmissionStatus


CASES = tuple(
    build_persona_cognitive_case(index)
    for index in range(1, TOTAL_COGNITIVE_E2E_CASES + 1)
)


def test_persona_cognitive_case_manifest_is_unique_and_balanced() -> None:
    case_ids = [case.case_id for case in CASES]
    assert len(case_ids) == TOTAL_COGNITIVE_E2E_CASES
    assert len(set(case_ids)) == TOTAL_COGNITIVE_E2E_CASES
    assert not set(case_ids).intersection({f"e2e-loop-{index:03d}" for index in range(1, 101)})

    loop_counts = Counter(case.loop_type for case in CASES)
    assert loop_counts == {loop_type: 600 for loop_type in CLOSED_LOOP_TYPES}

    alpha_counts = Counter(case.alpha_mode for case in CASES)
    assert set(alpha_counts) == set(ALPHA_MODES)
    assert all(count == 500 for count in alpha_counts.values())

    tool_counts = Counter(case.expected_tool for case in CASES if case.loop_type == "persona_tool_selection")
    assert tool_counts == {tool: 120 for _, tool in TOOL_SCENARIOS}


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_persona_cognitive_closed_loop_runs_full_e2e(case, tmp_path) -> None:
    proof = run_persona_cognitive_closed_loop(
        case,
        persona_store_path=tmp_path / "persona-memory.json",
        institutional_store_path=tmp_path / "institutional-memory.json",
    )

    assert proof["case_id"] == case.case_id
    assert proof["phases"] == ["observe", "orient", "collaborate", "decide", "act", "learn"]
    assert proof["memory_writeback"]["created"] is True
    assert len(proof["memory_writeback"]["persona_memory_ids"]) == 2
    assert proof["memory_reads"]["primary"]["reuse_count"] == 1
    assert proof["memory_reads"]["collaborator"]["reuse_count"] == 1
    assert proof["memory_reads"]["institutional"]["reuse_count"] == 1
    assert proof["collaboration"]["collaborator_memory_reused"] is True
    assert proof["collaboration"]["consensus"] == "paper_only_governed_action"
    assert proof["decision_changed_by_memory"] is True
    assert proof["baseline_decision"]["action"] != proof["final_decision"]["action"]
    assert proof["learn"]["no_live_side_effects"] is True
    assert proof["tool_execution"]["tool"] == proof["tool_choice"]["selected_tool"]

    if case.loop_type == "memory_changes_decision":
        assert proof["tool_choice"]["selected_tool"] == "persona_memory"
        assert proof["tool_execution"]["memory_context"]["used_to_change_decision"] is True

    if case.loop_type == "uncertainty_triggers_search":
        search = proof["tool_execution"]["search"]
        assert proof["tool_choice"]["selected_tool"] == "governed_search"
        assert search["request_id"] == f"search-{case.case_id}"
        assert search["results"]
        assert search["results"][0]["citations"] == [f"web-evidence:{case.case_id}"]

    if case.loop_type == "performance_triggers_optimization":
        evolution = proof["tool_execution"]["evolution"]
        assert proof["tool_choice"]["selected_tool"] == "evolution_decision"
        assert evolution["is_valid"] is True
        assert evolution["optimization_is_feasible"] is True
        assert evolution["decision"]["action_type"] == "revalidate"
        assert evolution["decision"]["target_stage"] == "paper"

    if case.loop_type == "bad_optimization_rejected":
        gate = proof["tool_execution"]["replication_gate"]
        assert proof["tool_choice"]["selected_tool"] == "replication_gate"
        assert gate["admission_status"] == CandidateAdmissionStatus.REJECTED.value
        assert gate["metadata"]["passed_all_required"] is False
        assert any(
            result["criterion_id"] == "no_live_bypass" and result["passed"] is False
            for result in gate["results"]
        )

    if case.loop_type == "persona_tool_selection":
        assert proof["tool_choice"]["selected_tool"] == case.expected_tool
        if case.expected_tool == "governed_search":
            assert proof["tool_execution"]["search"]["results"]
        elif case.expected_tool == "replication_gate":
            gate = proof["tool_execution"]["replication_gate"]
            assert gate["admission_status"] == CandidateAdmissionStatus.ADMITTED.value
        elif case.expected_tool == "evolution_decision":
            assert proof["tool_execution"]["evolution"]["is_valid"] is True
        elif case.expected_tool == "persona_memory":
            assert proof["tool_execution"]["memory_context"]["used_to_change_decision"] is True
        elif case.expected_tool == "lean_paper_handoff":
            handoff = proof["tool_execution"]["lean_paper_handoff"]
            assert handoff["adapter"] == "lean_paper_runtime"
            assert handoff["target_stage"] == "paper"
            assert handoff["no_live_side_effects"] is True
            assert handoff["accepted_alpha_modes"] == list(ALPHA_MODES)

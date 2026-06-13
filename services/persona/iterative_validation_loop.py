"""Iterative persona validation loop.

This module runs a 100-round ask-plan-execute validation loop on top of the
remaining-gap E2E suite.  Every round derives its next validation target from
all prior round results, then executes a full underlying E2E case.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.persona.remaining_gap_validation import (
    ALPHA_FAMILIES,
    LEAN_LIFECYCLES,
    LONG_MEMORY_SCENARIOS,
    OPTIMIZATION_SCENARIOS,
    REMAINING_GAP_TYPES,
    TOTAL_REMAINING_GAP_E2E_CASES,
    RemainingGapCase,
    RemainingGapValidationError,
    build_remaining_gap_case,
    build_validation_round_plan,
    run_remaining_gap_e2e_case,
)


TOTAL_ITERATIVE_VALIDATION_ROUNDS = 100
ITERATIVE_META_PHASES = (
    "ask_remaining_frontier",
    "plan_next_validation",
    "execute_e2e",
    "compare_with_prior_results",
    "record_iteration",
)
PRIOR_VALIDATION_SUITES = (
    "e2e-loop-001-through-100-lean-memory",
    "agent-trading-reflection-evolution-3000",
    "persona-cognitive-closed-loop-3000",
    "persona-remaining-gap-e2e-3000",
)


def build_iterative_validation_round_plan(
    round_number: int,
    previous_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Ask the next validation questions and plan one iterative round."""

    _validate_round_number(round_number)
    previous_result_ids = [str(result["round_id"]) for result in previous_results]
    prior_digest = coverage_digest(previous_results)
    selected_case = _select_next_case(previous_results)
    underlying_plan = build_validation_round_plan(selected_case)
    selected = _case_summary(selected_case)
    combination_id = (
        f"persona-meta-loop-100-{round_number:03d}|"
        f"{underlying_plan['validation_plan']['realistic_combination_id']}|"
        f"prior_rounds={len(previous_results)}"
    )
    return {
        "round_id": f"persona-meta-loop-100-{round_number:03d}",
        "round_number": round_number,
        "asked_before_execution": True,
        "prior_suites_considered": list(PRIOR_VALIDATION_SUITES),
        "questions": {
            "not_yet_verified": _not_yet_verified_question(selected_case, prior_digest),
            "deeper_validation": _deeper_validation_question(selected_case, previous_result_ids),
            "realistic_untested_combination": _realistic_combination_question(
                selected_case,
                combination_id,
            ),
        },
        "validation_plan": {
            "objective": (
                "Use all prior iterative results to choose the next least-covered realistic "
                "persona validation path, then execute the full E2E proof."
            ),
            "meta_phase_order": list(ITERATIVE_META_PHASES),
            "references_previous_result_ids": previous_result_ids,
            "prior_coverage_digest": prior_digest,
            "selected_case": selected,
            "underlying_validation_plan": underlying_plan,
            "iterative_combination_id": combination_id,
            "fix_policy": (
                "Any missing question, stale prior-reference list, duplicate selected case, "
                "underlying E2E failure, or fantasy-only optimization fails the round until fixed."
            ),
        },
    }


def run_iterative_validation_round(
    round_number: int,
    previous_results: Sequence[Mapping[str, Any]],
    *,
    work_dir: Path,
) -> dict[str, Any]:
    """Run one ask-plan-execute round using all previous round evidence."""

    round_plan = build_iterative_validation_round_plan(round_number, previous_results)
    selected_case = build_remaining_gap_case(
        int(round_plan["validation_plan"]["selected_case"]["ordinal"])
    )
    round_work_dir = work_dir / round_plan["round_id"]
    underlying_proof = run_remaining_gap_e2e_case(selected_case, work_dir=round_work_dir)
    defects = _detect_round_defects(round_plan, underlying_proof, previous_results)
    if defects:
        raise RemainingGapValidationError(
            f"{round_plan['round_id']} defects must be fixed before the loop can continue: {defects}"
        )
    result = {
        "round_id": round_plan["round_id"],
        "round_number": round_number,
        "asked_before_execution": True,
        "questions": round_plan["questions"],
        "validation_plan": round_plan["validation_plan"],
        "meta_executed_phase_order": list(ITERATIVE_META_PHASES),
        "meta_plan_executed": True,
        "prior_result_count": len(previous_results),
        "previous_round_ids_seen": [str(result["round_id"]) for result in previous_results],
        "selected_case": round_plan["validation_plan"]["selected_case"],
        "underlying_proof": underlying_proof,
        "defects_found": [],
        "correction_status": "no_defect_detected",
    }
    result["coverage_after_round"] = coverage_digest([*previous_results, result])
    return result


def run_iterative_validation_100(*, work_dir: Path) -> dict[str, Any]:
    """Run the full 100-round iterative validation loop."""

    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for round_number in range(1, TOTAL_ITERATIVE_VALIDATION_ROUNDS + 1):
        results.append(
            run_iterative_validation_round(
                round_number,
                results,
                work_dir=work_dir,
            )
        )
    return {
        "suite_id": "persona-meta-loop-100",
        "round_count": len(results),
        "prior_suites_considered": list(PRIOR_VALIDATION_SUITES),
        "results": results,
        "coverage": coverage_digest(results),
        "defects_found": [],
        "correction_status": "no_defect_detected",
    }


def coverage_digest(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize all prior results in a stable structure for the next round."""

    selected_cases = [_selected_case_from_result(result) for result in results]
    return {
        "round_count": len(results),
        "round_ids": [str(result["round_id"]) for result in results],
        "selected_case_ids": [case["case_id"] for case in selected_cases],
        "gap_type_counts": _ordered_counts(
            (case["gap_type"] for case in selected_cases),
            REMAINING_GAP_TYPES,
        ),
        "alpha_family_counts": _ordered_counts(
            (case["alpha_family"] for case in selected_cases),
            ALPHA_FAMILIES,
        ),
        "broker_adapter_counts": _ordered_counts(
            (case["broker_adapter"] for case in selected_cases),
            ("ibkr", "shioaji", "kraken"),
        ),
        "lean_lifecycle_counts": _ordered_counts(
            (case["lean_lifecycle"] for case in selected_cases if case["gap_type"] == "lean_order_feedback_recovery"),
            LEAN_LIFECYCLES,
        ),
        "long_memory_scenario_counts": _ordered_counts(
            (
                case["long_memory_scenario"]
                for case in selected_cases
                if case["gap_type"] == "long_term_memory_influence"
            ),
            LONG_MEMORY_SCENARIOS,
        ),
        "optimization_scenario_counts": _ordered_counts(
            (
                case["optimization_scenario"]
                for case in selected_cases
                if case["gap_type"] == "optimization_backtest_proof"
            ),
            OPTIMIZATION_SCENARIOS,
        ),
        "defect_count": sum(len(result.get("defects_found", [])) for result in results),
    }


def _select_next_case(previous_results: Sequence[Mapping[str, Any]]) -> RemainingGapCase:
    digest = coverage_digest(previous_results)
    used_case_ids = set(digest["selected_case_ids"])
    best_case: RemainingGapCase | None = None
    best_score: tuple[int, int, int, int, int] | None = None
    for ordinal in range(1, TOTAL_REMAINING_GAP_E2E_CASES + 1):
        case = build_remaining_gap_case(ordinal)
        if case.case_id in used_case_ids:
            continue
        score = _coverage_score(case, digest)
        if best_score is None or score < best_score:
            best_case = case
            best_score = score
    if best_case is None:
        raise RemainingGapValidationError("No remaining underlying case can be selected")
    return best_case


def _coverage_score(case: RemainingGapCase, digest: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    if case.gap_type == "lean_order_feedback_recovery":
        scenario_count = int(digest["lean_lifecycle_counts"][case.lean_lifecycle])
    elif case.gap_type == "long_term_memory_influence":
        scenario_count = int(digest["long_memory_scenario_counts"][case.long_memory_scenario])
    else:
        scenario_count = int(digest["optimization_scenario_counts"][case.optimization_scenario])
    return (
        int(digest["gap_type_counts"][case.gap_type]),
        int(digest["alpha_family_counts"][case.alpha_family]),
        int(digest["broker_adapter_counts"][case.broker_adapter]),
        scenario_count,
        case.ordinal,
    )


def _detect_round_defects(
    round_plan: Mapping[str, Any],
    underlying_proof: Mapping[str, Any],
    previous_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    defects: list[str] = []
    selected_case = round_plan["validation_plan"]["selected_case"]
    expected_previous_ids = [str(result["round_id"]) for result in previous_results]
    if round_plan["validation_plan"]["references_previous_result_ids"] != expected_previous_ids:
        defects.append("previous result references do not include every prior round in order")
    if underlying_proof["case_id"] != selected_case["case_id"]:
        defects.append("underlying proof case_id does not match selected case")
    if underlying_proof["validation_round"]["plan_executed"] is not True:
        defects.append("underlying E2E plan was not executed")
    if underlying_proof["validation_round"]["defects_found"]:
        defects.append("underlying E2E reported defects")
    used_case_ids = {str(result["selected_case"]["case_id"]) for result in previous_results}
    if selected_case["case_id"] in used_case_ids:
        defects.append("selected underlying case was already used by a previous iterative round")
    if set(round_plan["questions"]) != {
        "not_yet_verified",
        "deeper_validation",
        "realistic_untested_combination",
    }:
        defects.append("round did not ask all required pre-execution questions")
    return defects


def _not_yet_verified_question(case: RemainingGapCase, digest: Mapping[str, Any]) -> str:
    return (
        f"After {digest['round_count']} prior iterative rounds, gap={case.gap_type} has "
        f"count={digest['gap_type_counts'][case.gap_type]}; should we verify this still-low "
        f"frontier via {case.case_id}?"
    )


def _deeper_validation_question(case: RemainingGapCase, previous_result_ids: Sequence[str]) -> str:
    return (
        f"Can round {len(previous_result_ids) + 1} go deeper by checking both the meta-loop "
        f"plan and the underlying {case.gap_type} E2E proof, while referencing all "
        f"{len(previous_result_ids)} previous result ids?"
    )


def _realistic_combination_question(case: RemainingGapCase, combination_id: str) -> str:
    return (
        f"Could production realistically see alpha={case.alpha_family}, adapter={case.broker_adapter}, "
        f"lean={case.lean_lifecycle}, memory={case.long_memory_scenario}, "
        f"optimization={case.optimization_scenario}; and have we executed iterative combo "
        f"{combination_id} before?"
    )


def _selected_case_from_result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    selected = result.get("selected_case")
    if not isinstance(selected, Mapping):
        raise RemainingGapValidationError("Iterative result is missing selected_case")
    return selected


def _case_summary(case: RemainingGapCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "ordinal": case.ordinal,
        "gap_type": case.gap_type,
        "persona_id": case.persona_id,
        "strategy_id": case.strategy_id,
        "alpha_family": case.alpha_family,
        "broker_adapter": case.broker_adapter,
        "lean_lifecycle": case.lean_lifecycle,
        "long_memory_scenario": case.long_memory_scenario,
        "optimization_scenario": case.optimization_scenario,
    }


def _ordered_counts(values: Any, keys: Sequence[str]) -> dict[str, int]:
    counts = Counter(values)
    return {key: int(counts.get(key, 0)) for key in keys}


def _validate_round_number(round_number: int) -> None:
    if round_number < 1 or round_number > TOTAL_ITERATIVE_VALIDATION_ROUNDS:
        raise RemainingGapValidationError(
            f"round_number must be between 1 and {TOTAL_ITERATIVE_VALIDATION_ROUNDS}"
        )

"""E2E: persona agents can trade, reflect, and evolve across 3000 cases."""

from __future__ import annotations

from services.persona.agent_usability_validation import (
    DEFAULT_CASE_COUNT,
    HISTORICAL_OHLCV_DATASET_ID,
    QUANTITY_TYPES,
    run_agent_usability_validations,
)
from services.persona.ooda_cycle_runtime import ALPHA_SEED_SOURCES


def test_persona_agents_trade_reflect_and_evolve_across_3000_historical_cases() -> None:
    run = run_agent_usability_validations(case_count=DEFAULT_CASE_COUNT)
    summary = run.summary
    cases = list(run.cases)

    assert summary["total_cases"] == 3000
    assert len(cases) == 3000
    assert summary["unique_case_count"] == 3000
    assert len({case["case_id"] for case in cases}) == 3000
    assert len({case["case_key"] for case in cases}) == 3000

    assert summary["historical_dataset"]["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
    assert summary["historical_dataset"]["record_count"] == 26800
    assert summary["historical_dataset"]["instrument_count"] == 50
    assert summary["alpha_seed_count"] == len(ALPHA_SEED_SOURCES)
    assert summary["oss_backtest_count"] == len(ALPHA_SEED_SOURCES)
    assert summary["oss_backtest_statuses"] == ["completed"]
    assert summary["oss_backtest_components"] == ["vectorbt"]

    assert summary["baseline_trade_fill_count"] == 3000
    assert summary["evolved_trade_fill_count"] == 3000
    assert summary["reflection_count"] == 3000
    assert summary["learn_memory_writeback_count"] == 3000
    assert summary["evolution_decision_executed_count"] == 3000
    assert summary["evolved_score_non_worse_count"] == 3000
    assert summary["evolved_score_strict_improvement_count"] == 3000
    assert summary["min_score_improvement"] > 0
    assert summary["average_score_improvement"] > 0

    coverage = summary["coverage"]
    assert coverage["covered_persona_ids"] == coverage["persona_ids"]
    assert coverage["covered_seed_keys"] == sorted(source.key for source in ALPHA_SEED_SOURCES)
    assert len(coverage["instruments"]) == 50
    assert set(coverage["baseline_actions"]) == {"BUY", "SELL"}
    assert set(coverage["baseline_directions"]) == {"LONG", "SHORT"}
    assert set(coverage["quantity_types"]) == set(QUANTITY_TYPES)
    assert set(coverage["order_types"]) == {"LIMIT", "MARKET"}
    assert {
        "drawdown_pressure",
        "negative_risk_adjusted_outcome",
        "positive_outcome_scale_review",
    }.issuperset(set(coverage["reflection_triggers"]))
    assert {"retrain", "revalidate"}.issuperset(set(coverage["evolution_action_types"]))

    for case in cases:
        assert case["baseline_trade"]["filled"] is True
        assert case["evolved_trade"]["filled"] is True
        assert case["baseline_trade"]["submitted_to_broker"] is False
        assert case["evolved_trade"]["submitted_to_broker"] is False
        assert case["learn_memory"]["created"] is True
        assert case["learn_memory"]["institutional_entry_id"]
        assert case["learn_memory"]["persona_memory_ids"]
        assert case["reflection"]["telemetry_event_id"] == case["telemetry_event_id"]
        assert case["reflection"]["hypothesis"]
        assert case["reflection"]["next_policy_change"] == "search_direction_and_risk_multiplier"
        assert case["evolution"]["decision_state"] == "executed"
        assert case["evolution"]["execution_status"] == "succeeded"
        assert case["evolution"]["review_steps"] == ["reviewed", "approved", "executed"]
        assert case["usable"] == {
            "traded": True,
            "reflected": True,
            "learned": True,
            "evolved": True,
            "better_or_equal": True,
            "strictly_better": True,
        }
        assert case["scores"]["improvement"] > 0
        assert HISTORICAL_OHLCV_DATASET_ID in case["source_dataset_refs"]

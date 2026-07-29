import pytest

from persona_allocation_policy import (
    build_pm12_allocation_policy_input,
    calculate_paper_simulation_allocations,
    calculate_target_allocations,
    stage_recommendation,
)


def _row(persona_id, stage, tier, current, **extra):
    return {
        "persona_id": persona_id, "stage": stage, "tier": tier, "current_weight": current,
        "ranking_snapshot_id": "rank-q3",
        "capital_scope": "pool", "capital_pool_id": "pool-real", "evidence_refs": [f"ev-{persona_id}"],
        "pnl_score": 1, "sharpe_score": 1, "drawdown_control_score": 1,
        "execution_quality_score": 1, "risk_compliance_score": 1, "improvement_score": 1,
        **extra,
    }


def test_stage_aware_recommendations():
    assert stage_recommendation("paper_running") == "paper_to_canary_review"
    assert stage_recommendation("canary_running") == "canary_to_live_review"
    assert stage_recommendation("live_running") == "allocation_increase_or_retain_review"
    assert stage_recommendation("live_running", hard_risk_breach=True) == "containment"


def test_targets_enforce_stage_tier_caps_smoothing_and_exclusions():
    lines = calculate_target_allocations([
        _row("paper", "paper_running", "s", 0),
        _row("canary", "canary_running", "s", 0.04),
        _row("live-s", "live_running", "s", 0.20),
        _row("live-a", "live_running", "a", 0.10),
        _row("live-b", "live_running", "b", 0.08, missing_required_evidence=True),
    ])
    by_id = {line["persona_id"]: line for line in lines}
    assert by_id["paper"]["target_weight"] == 0
    assert by_id["canary"]["target_weight"] <= 0.05
    assert by_id["live-s"]["target_weight"] <= 0.25
    assert by_id["live-a"]["target_weight"] <= 0.125
    assert "quarterly_increase_cap_25pct" in by_id["live-a"]["cap_reasons"]
    assert by_id["live-b"]["target_weight"] <= by_id["live-b"]["current_weight"]
    assert "missing_required_evidence" in by_id["live-b"]["exclusions"]
    assert by_id["live-a"]["requires_human_approval"] is True
    assert by_id["live-a"]["ranking_snapshot_id"] == "rank-q3"
    assert by_id["live-a"]["evidence_refs"] == ["ev-live-a"]


def test_fresh_real_allocation_entrants_bootstrap_to_stage_tier_caps():
    lines = calculate_target_allocations([
        _row("fresh-canary", "canary_running", "s", 0),
        _row("fresh-live", "live_running", "b", 0),
    ])
    by_id = {line["persona_id"]: line for line in lines}

    assert by_id["fresh-canary"]["target_weight"] == 0.05
    assert by_id["fresh-live"]["target_weight"] == 0.08
    assert by_id["fresh-canary"]["delta"] == 0.05
    assert by_id["fresh-live"]["delta"] == 0.08
    assert "quarterly_increase_cap_25pct" not in by_id["fresh-canary"]["cap_reasons"]
    assert "quarterly_increase_cap_25pct" not in by_id["fresh-live"]["cap_reasons"]


def test_pm12_adapter_uses_overall_score_and_governed_tier_crosswalk():
    row = {
        "persona_id": "persona-alpha",
        "stage": "live_running",
        "tier": "tier-2",
        "overall_score": 70.85,
        "formula_version": "pm12-default-v1",
        "current_weight": 0.04,
        "ranking_snapshot_id": "ranking-quarterly-2026-q3-example",
        "capital_scope": "real",
        "capital_pool_id": "pool-real",
        "evidence_refs": ["ev-alpha"],
    }

    lines = calculate_target_allocations([row])

    assert lines == [
        {
            "ranking_snapshot_id": "ranking-quarterly-2026-q3-example",
            "persona_id": "persona-alpha",
            "stage": "live_running",
            "capital_scope": "real",
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": None,
            "current_weight": 0.04,
            "target_weight": 0.05,
            "delta": 0.01,
            "rank_score": 70.85,
            "capacity_adjusted_score": 70.85,
            "allocation_policy_input": {
                "schema_version": "persona-allocation-policy-input/v1",
                "adapter_version": "pm12-quarterly-overall-tier-v1",
                "policy_version": "persona-real-allocation-v1",
                "source_formula_version": "pm12-default-v1",
                "rank_score_source": "overall_score",
                "rank_score": 70.85,
                "source_tier": "tier-2",
                "allocation_tier": "a",
            },
            "recommendation": "allocation_increase_or_retain_review",
            "cap_reasons": ["live_a_tier_cap", "quarterly_increase_cap_25pct"],
            "exclusions": [],
            "evidence_refs": ["ev-alpha"],
            "eligible": None,
            "exclusion_reasons": [],
            "exclusion_codes": [],
            "requires_human_approval": True,
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("formula_version", "pm12-unknown-v1"),
        ("tier", "tier-5"),
        ("overall_score", float("nan")),
    ],
)
def test_pm12_adapter_rejects_unknown_or_non_finite_semantics(field, value):
    row = {
        "formula_version": "pm12-default-v1",
        "tier": "tier-1",
        "overall_score": 90.0,
        field: value,
    }

    with pytest.raises(ValueError):
        build_pm12_allocation_policy_input(row)


def test_pm12_adapter_rejects_tampered_supplied_schema():
    row = {
        "formula_version": "pm12-default-v1",
        "tier": "tier-2",
        "overall_score": 70.85,
        "allocation_policy_input": {
            "schema_version": "persona-allocation-policy-input/v1",
            "adapter_version": "pm12-quarterly-overall-tier-v1",
            "policy_version": "persona-real-allocation-v1",
            "source_formula_version": "pm12-default-v1",
            "rank_score_source": "overall_score",
            "rank_score": 99.0,
            "source_tier": "tier-2",
            "allocation_tier": "a",
        },
    }

    with pytest.raises(ValueError, match="rank_score"):
        build_pm12_allocation_policy_input(row)


def test_paper_simulation_uses_distinct_policy_and_isolated_ledger_target():
    lines = calculate_paper_simulation_allocations(
        [
            {
                "ranking_snapshot_id": "ranking-quarterly-2026-q3-paper",
                "persona_id": "persona-paper",
                "stage": "paper_running",
                "capital_scope": "paper_ledger",
                "paper_ledger_id": "paper-ledger-persona-paper",
                "capital_pool_id": "pool-persona-paper",
                "capital_sleeve_id": None,
                "binding_id": "binding-persona-paper",
                "current_weight": 0.0,
                "eligible": True,
                "tier": "tier-2",
                "overall_score": 74.5,
                "formula_version": "pm12-default-v1",
                "evidence_refs": ["promotion_decision:cmd-decision"],
                # The quarterly row may carry the real-policy adapter. The
                # paper policy must rebuild it under its own authority.
                "allocation_policy_input": {
                    "policy_version": "persona-real-allocation-v1",
                },
            }
        ]
    )

    assert len(lines) == 1
    line = lines[0]
    assert line["target_weight"] == 1.0
    assert line["delta"] == 1.0
    assert line["capital_scope"] == "paper_ledger"
    assert line["capital_sleeve_id"] is None
    assert line["binding_id"] == "binding-persona-paper"
    assert line["paper_allocation_eligible"] is True
    assert line["live_capital_side_effects"] is False
    assert line["allocation_policy_input"]["policy_version"] == (
        "persona-paper-allocation-simulation-v1"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("stage", "canary_running", "stage=paper_running"),
        ("capital_scope", "real", "capital_scope=paper_ledger"),
        ("capital_sleeve_id", "sleeve-live", "cannot target a capital sleeve"),
        ("eligible", False, "eligible ranking row"),
    ],
)
def test_paper_simulation_fails_closed_outside_paper_authority(
    field,
    value,
    message,
):
    row = {
        "ranking_snapshot_id": "ranking-quarterly-2026-q3-paper",
        "persona_id": "persona-paper",
        "stage": "paper_running",
        "capital_scope": "paper_ledger",
        "paper_ledger_id": "paper-ledger-persona-paper",
        "capital_pool_id": "pool-persona-paper",
        "capital_sleeve_id": None,
        "binding_id": "binding-persona-paper",
        "current_weight": 0.0,
        "eligible": True,
        "tier": "tier-2",
        "overall_score": 74.5,
        "formula_version": "pm12-default-v1",
        field: value,
    }

    with pytest.raises(ValueError, match=message):
        calculate_paper_simulation_allocations([row])

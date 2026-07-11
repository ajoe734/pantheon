from persona_allocation_policy import calculate_target_allocations, stage_recommendation


def _row(persona_id, stage, tier, current, **extra):
    return {
        "persona_id": persona_id, "stage": stage, "tier": tier, "current_weight": current,
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

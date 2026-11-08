"""Unit tests for the first-class RiskPolicy evaluator contract."""
from __future__ import annotations

from services.capital.risk_policy import (
    RiskPolicy,
    RiskPolicyDecision,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
)


def test_risk_policy_rejects_exposure_asset_and_liquidity_breaches() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        gross_limit=1.5,
        forbidden_asset_classes=("crypto",),
        liquidity_constraints={"min_avg_daily_volume": 1_000_000},
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        RiskPolicyEvaluationContext(
            target_type=RiskPolicyTargetType.ALLOCATION_PROPOSAL.value,
            target_id="proposal-001",
            capital_pool_id="pool-001",
            gross_exposure=1.8,
            asset_classes=("crypto",),
            liquidity={"avg_daily_volume": 250_000},
        ),
    )

    assert evaluation.decision == RiskPolicyDecision.REJECTED.value
    assert {check.code for check in evaluation.checks if check.status == "failed"} == {
        "forbidden_asset_class",
        "gross_exposure_limit_exceeded",
        "liquidity_min_adv_breach",
    }


def test_risk_evaluation_propagates_trade_envelope() -> None:
    from services.trade_journey.correlation_envelope import mint_trade_envelope
    incoming = mint_trade_envelope({"tenant_id": "tenant-1", "environment": "paper"}, producer="execution.signal-decision")
    evaluation = RiskPolicyEvaluator().evaluate(
        RiskPolicy(risk_policy_id="risk-main"),
        RiskPolicyEvaluationContext(target_type="order", target_id="order-1", capital_pool_id="pool-1", correlation_envelope=incoming),
    )
    outgoing = evaluation.to_dict()["correlation_envelope"]
    assert outgoing["journey_id"] == incoming["journey_id"]
    assert outgoing["causation_event_id"] == incoming["event_id"]
    assert outgoing["producer"] == "risk.evaluation"


def test_canary_scale_and_kill_switch_are_evaluable_limits() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        max_canary_capital_scale_pct=2.0,
        max_canary_gross_scale_pct=10.0,
        kill_switch_triggers=("operator_emergency_stop",),
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        {
            "target_type": "runtime_launch",
            "target_id": "rt-001",
            "capital_pool_id": "pool-001",
            "stage": "canary",
            "capital_scale_pct": 5.0,
            "gross_scale_pct": 25.0,
            "kill_switch_trigger": "operator_emergency_stop",
        },
    )

    assert evaluation.rejected is True
    assert [check.code for check in evaluation.checks if check.status == "failed"] == [
        "canary_capital_scale_pct_limit_exceeded",
        "canary_gross_scale_pct_limit_exceeded",
        "kill_switch_triggered",
    ]


def test_drawdown_warn_allows_with_conditions() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        drawdown_actions={"warn": 0.03, "risk_off": 0.08, "liquidate": 0.15},
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        {
            "target_type": "runtime_action",
            "target_id": "risk-scan-001",
            "capital_pool_id": "pool-001",
            "drawdown_pct": 0.05,
        },
    )

    assert evaluation.decision == RiskPolicyDecision.ALLOWED_WITH_CONDITIONS.value
    assert evaluation.warnings


def test_homogeneity_and_correlation_limits_are_hard_vetoes() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        max_strategy_family_concentration=0.65,
        max_target_overlap=0.8,
        max_signal_correlation=0.9,
    )

    evaluation = RiskPolicyEvaluator().evaluate(
        policy,
        RiskPolicyEvaluationContext(
            target_type=RiskPolicyTargetType.ALLOCATION_PROPOSAL.value,
            target_id="allocation-gate-review-001",
            capital_pool_id="pool-001",
            strategy_family_concentration={"mega_cap_momentum": 0.9},
            target_overlap=0.95,
            signal_correlation=0.97,
        ),
    )

    assert evaluation.rejected is True
    assert {check.code for check in evaluation.checks if check.status == "failed"} == {
        "strategy_family_concentration_limit_exceeded",
        "target_overlap_limit_exceeded",
        "signal_correlation_limit_exceeded",
    }


def test_runtime_action_future_order_gate_enforces_2x_gross_limit_rejecting_2_13_and_accepting_1_85() -> None:
    policy = RiskPolicy.from_mapping(
        {
            "risk_policy_id": "rp-pool-persona-paper-001",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": 2.0,
            "net_limit": 2.0,
            "max_leverage": 2.0,
            "allowed_asset_classes": ["equity", "future"],
        }
    )
    assert policy.gross_limit == 2.0
    assert policy.max_leverage == 2.0
    assert policy.allowed_asset_classes == ("equity", "future")

    rejected_ctx = RiskPolicyEvaluationContext(
        target_type="runtime_action",
        target_id="dec-qff-2330-short",
        capital_pool_id="pool-persona-paper-001",
        stage="paper",
        gross_exposure=2.13,
        net_exposure=-2.13,
        leverage=2.13,
        asset_classes=["future"],
        decision_id="dec-qff-2330-short",
        instrument_id="ssf:QFF:202611",
    )
    assert rejected_ctx.asset_classes == ("future",)
    assert rejected_ctx.decision_id == "dec-qff-2330-short"
    assert rejected_ctx.instrument_id == "ssf:QFF:202611"

    rejected_eval = RiskPolicyEvaluator().evaluate(policy, rejected_ctx)
    assert rejected_eval.rejected is True
    assert rejected_eval.decision == RiskPolicyDecision.REJECTED.value
    assert {check.code for check in rejected_eval.checks if check.status == "failed"} == {
        "gross_exposure_limit_exceeded",
        "net_exposure_limit_exceeded",
        "leverage_limit_exceeded",
    }

    accepted_ctx = RiskPolicyEvaluationContext.from_mapping(
        {
            "target_type": "runtime_action",
            "decision_id": "dec-srf-0050-long",
            "instrument_id": "ssf:SRF:202611",
            "capital_pool_id": "pool-persona-paper-001",
            "stage": "paper",
            "gross_exposure": 1.85,
            "net_exposure": 1.85,
            "leverage": 1.85,
            "asset_classes": ["future"],
        }
    )
    assert accepted_ctx.target_id == "dec-srf-0050-long"
    assert accepted_ctx.asset_classes == ("future",)
    assert accepted_ctx.decision_id == "dec-srf-0050-long"
    assert accepted_ctx.instrument_id == "ssf:SRF:202611"

    accepted_eval = RiskPolicyEvaluator().evaluate(policy, accepted_ctx)
    assert accepted_eval.allowed is True
    assert accepted_eval.decision == RiskPolicyDecision.ALLOWED.value
    assert accepted_eval.blocking_reasons == ()


def test_stdlib_risk_policy_loader_and_bound_pool_resolver(tmp_path, monkeypatch) -> None:
    import json
    import pytest
    from services.capital.risk_policy import (
        RiskPolicyLoadError,
        load_risk_policy,
        normalize_risk_policy,
        read_capital_pool_record,
        resolve_bound_pool_risk_policy,
    )

    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "rp-paper-futures.json").write_text(
        json.dumps(
            {
                "risk_policy_id": "rp-paper-futures",
                "version": "1.0",
                "status": "active",
                "allowed_stages": ["paper"],
                "allowed_asset_classes": ["equity", "future"],
                "exposure_limits": {
                    "gross_limit": 2.0,
                    "net_limit": 2.0,
                    "max_leverage": 2.0,
                    "max_single_name_weight": 0.5,
                },
            }
        ),
        encoding="utf-8",
    )
    pool_store = tmp_path / "capital_pools.json"
    pool_store.write_text(
        json.dumps(
            {
                "pools": [
                    {
                        "pool_id": "pool-paper-01",
                        "risk_policy_ref": "rp-paper-futures",
                        "status": "active",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))
    monkeypatch.setenv("PANTHEON_CAPITAL_POOL_STORE_PATH", str(pool_store))

    raw = load_risk_policy("rp-paper-futures")
    assert raw["gross_limit"] == 2.0
    assert raw["max_single_name_weight"] == 0.5

    pool_rec = read_capital_pool_record("pool-paper-01")
    assert pool_rec is not None
    assert pool_rec["risk_policy_ref"] == "rp-paper-futures"

    resolved = resolve_bound_pool_risk_policy(capital_pool_id="pool-paper-01")
    assert resolved is not None
    assert resolved.risk_policy_id == "rp-paper-futures"
    assert resolved.gross_limit == 2.0
    assert resolved.max_single_name_weight == 0.5

    norm = normalize_risk_policy({"exposure_limits": {"gross_limit": 1.5}}, default_id="rp-default")
    assert norm.risk_policy_id == "rp-default"
    assert norm.gross_limit == 1.5

    with pytest.raises(RiskPolicyLoadError):
        load_risk_policy("missing-policy-ref")



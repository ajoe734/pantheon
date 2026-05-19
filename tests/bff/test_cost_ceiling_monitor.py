from __future__ import annotations

from decimal import Decimal

import pytest

from services.bff.ha.cost_ceiling_monitor import (
    CostCeilingObservation,
    CostCeilingState,
    decision_to_dict,
    evaluate_cost_ceiling,
    load_cost_ceiling_policy,
    monitor_contract_as_dict,
)


def test_cost_ceiling_policy_loads_production_usd_800_from_sla_targets() -> None:
    policy = load_cost_ceiling_policy("production")
    contract = monitor_contract_as_dict(policy)

    assert policy.monthly_ceiling_usd == Decimal("800.00")
    assert contract["schema_version"] == "bff_ha_cost_ceiling_monitor.v1"
    assert contract["environment"] == "production"
    assert contract["monthly_cost_ceiling_usd"] == 800.0
    assert contract["alarm_path"] == {
        "event_type": "bff_ha.cost_ceiling",
        "severity_channel": "telemetry.alerts",
        "operator_surface": "/bff/alerts",
        "escalation_target": "infra-owner+risk-owner",
        "runbook": "docs/operations/bff_ha_failover_runbook.md",
    }
    assert contract["auto_cap"]["mode"] == "alarm_only_manual_gate"
    assert contract["auto_cap"]["automatic_cap_allowed"] is False
    assert contract["auto_cap"]["cap_applied_by_monitor"] is False
    assert contract["auto_cap"]["manual_gate_required"] is True


def test_cost_ceiling_happy_path_stays_below_alarm_threshold() -> None:
    decision = evaluate_cost_ceiling(
        CostCeilingObservation(
            environment="production",
            billing_month="2026-05",
            month_to_date_usd=Decimal("240.00"),
            projected_month_end_usd=Decimal("520.00"),
        )
    )
    payload = decision_to_dict(decision)

    assert decision.state == CostCeilingState.OK
    assert decision.alarm_required is False
    assert payload["observed_ratio"] == 0.3
    assert payload["projected_ratio"] == 0.65
    assert payload["remaining_budget_usd"] == 560.0
    assert payload["recommended_actions"] == ["continue_monitoring"]


def test_cost_ceiling_warns_at_80_percent_without_auto_cap() -> None:
    decision = evaluate_cost_ceiling(
        {
            "environment": "production",
            "billing_month": "2026-05",
            "month_to_date_usd": 640,
            "projected_month_end_usd": 700,
        }
    )
    payload = decision_to_dict(decision)

    assert decision.state == CostCeilingState.WATCH
    assert decision.severity == "warning"
    assert decision.alarm_required is True
    assert payload["observed_ratio"] == 0.8
    assert payload["alarm_path"]["operator_surface"] == "/bff/alerts"
    assert payload["auto_cap"]["automatic_cap_allowed"] is False
    assert "review_non_critical_ha_scale_out_requests" in payload["recommended_actions"]


def test_cost_ceiling_projected_breach_alarms_and_requires_manual_cap_decision() -> None:
    decision = evaluate_cost_ceiling(
        CostCeilingObservation(
            environment="production",
            billing_month="2026-05",
            month_to_date_usd=Decimal("620.00"),
            projected_month_end_usd=Decimal("850.00"),
        )
    )
    payload = decision_to_dict(decision)

    assert decision.state == CostCeilingState.PROJECTED_BREACH
    assert decision.severity == "warning"
    assert decision.alarm_required is True
    assert payload["projected_ratio"] == 1.0625
    assert "emit_cost_ceiling_alarm" in payload["recommended_actions"]
    assert "request_manual_cost_cap_decision" in payload["recommended_actions"]
    assert payload["auto_cap"]["cap_applied_by_monitor"] is False
    assert "reduce_production_bff_below_sla_replica_floor" in payload["auto_cap"]["forbidden_automatic_actions"]


def test_cost_ceiling_observed_breach_is_critical() -> None:
    decision = evaluate_cost_ceiling(
        CostCeilingObservation(
            environment="production",
            billing_month="2026-05",
            month_to_date_usd=Decimal("801.00"),
        )
    )

    assert decision.state == CostCeilingState.BREACHED
    assert decision.severity == "critical"
    assert decision.alarm_required is True
    assert decision.remaining_budget_usd == Decimal("0.00")


def test_cost_data_unavailable_fails_closed_to_alarm() -> None:
    decision = evaluate_cost_ceiling(
        {
            "environment": "production",
            "billing_month": "2026-05",
            "cost_data_available": False,
        }
    )
    payload = decision_to_dict(decision)

    assert decision.state == CostCeilingState.COST_DATA_UNAVAILABLE
    assert decision.severity == "critical"
    assert decision.alarm_required is True
    assert payload["observed_ratio"] is None
    assert payload["remaining_budget_usd"] is None
    assert "freeze_non_critical_ha_scale_out_requests" in payload["recommended_actions"]
    assert payload["auto_cap"]["automatic_cap_allowed"] is False


def test_cost_ceiling_rejects_invalid_currency_instead_of_silent_conversion() -> None:
    with pytest.raises(ValueError, match="only accepts USD"):
        evaluate_cost_ceiling(
            {
                "environment": "production",
                "billing_month": "2026-05",
                "month_to_date_usd": 100,
                "currency": "TWD",
            }
        )


def test_cost_ceiling_rejects_missing_cost_when_data_is_marked_available() -> None:
    with pytest.raises(ValueError, match="month_to_date_usd is required"):
        evaluate_cost_ceiling(
            {
                "environment": "production",
                "billing_month": "2026-05",
            }
        )

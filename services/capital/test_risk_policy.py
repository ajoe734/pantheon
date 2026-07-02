"""Unit tests for the first-class RiskPolicy evaluator contract."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from services.capital.risk_policy import (
    RiskPolicy,
    RiskGuardrailAction,
    RiskGuardrailEvaluator,
    RiskPolicyDecision,
    RiskPolicyEvaluationContext,
    RiskPolicyEvaluator,
    RiskPolicyTargetType,
)
from services.incident.incident import IncidentCase


ROOT = Path(__file__).resolve().parents[2]
PAPER_LIVE_SCHEMA = ROOT / "services/control-plane/specs/persona_paper_live.schema.json"


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


def _guardrail_context(**overrides):
    base = {
        "persona_id": "persona-live-alpha",
        "runtime_binding_id": "rb-live-alpha",
        "runtime_id": "runtime-live-alpha",
        "capital_pool_id": "pool-live-alpha",
        "deployment_stage": "live",
        "deployment_plan_id": "plan-live-alpha",
        "persona_capital_binding_id": "pcb-live-alpha",
        "artifact_id": "artifact-live-alpha",
        "artifact_version": "1.0.0",
        "trace_id": "trace-risk-001",
        "telemetry_event_ids": ["telemetry-daily-loss-001"],
    }
    base.update(overrides)
    return base


def _risk_guardrail_validator() -> Draft202012Validator:
    with PAPER_LIVE_SCHEMA.open("r", encoding="utf-8") as fh:
        schema = json.load(fh)
    return Draft202012Validator(
        {
            "$schema": schema["$schema"],
            "$defs": schema["$defs"],
            "$ref": "#/$defs/RiskGuardrailEvent",
        }
    )


def test_guardrail_daily_loss_pauses_and_records_incident_evidence() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        pause_rules={"daily_loss_pct": 0.03},
    )

    evaluation = RiskGuardrailEvaluator().evaluate(
        policy,
        _guardrail_context(daily_loss_pct=-0.04),
    )

    assert evaluation.triggered is True
    event = evaluation.events[0]
    assert event.trigger_name == "daily_loss_pct"
    assert event.automatic_action == RiskGuardrailAction.PAUSE_NEW_ORDERS.value
    assert event.observed_value == 0.04
    assert event.threshold == 0.03
    assert event.review_required is True
    assert event.may_promote is False
    assert event.may_increase_allocation is False
    assert event.trace_id == "trace-risk-001"
    assert event.resume_requires_human_review is False
    _risk_guardrail_validator().validate(event.to_dict())

    incident = IncidentCase.from_dict(evaluation.incident_records[0])
    assert incident.status == "open"
    assert incident.severity == "medium"
    assert incident.binding_id == "rb-live-alpha"
    assert incident.deployment_stage == "live"
    assert incident.trace_id == "trace-risk-001"
    assert "automatic_action=pause_new_orders" in (incident.evidence_summary or "")


def test_guardrail_drawdown_risk_off_requires_human_resume_review() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        drawdown_actions={"risk_off": 0.08},
    )

    evaluation = RiskGuardrailEvaluator().evaluate(
        policy,
        _guardrail_context(metadata={"drawdown_pct": 0.11}),
    )

    event = evaluation.events[0]
    assert event.trigger_name == "max_drawdown_pct"
    assert event.automatic_action == RiskGuardrailAction.RISK_OFF.value
    assert event.resume_requires_human_review is True
    assert event.may_promote is False
    assert event.may_increase_allocation is False
    incident = IncidentCase.from_dict(evaluation.incident_records[0])
    assert incident.severity == "high"
    assert "Resume requires human review." in (incident.evidence_summary or "")


def test_guardrail_critical_policy_violation_freezes_and_requires_human_resume_review() -> None:
    evaluation = RiskGuardrailEvaluator().evaluate(
        RiskPolicy(risk_policy_id="risk-main"),
        _guardrail_context(policy_violation_severity="critical"),
    )

    event = evaluation.events[0]
    assert event.trigger_name == "critical_policy_violation"
    assert event.automatic_action == RiskGuardrailAction.FROZEN.value
    assert event.resume_requires_human_review is True
    assert event.review_required is True
    incident = IncidentCase.from_dict(evaluation.incident_records[0])
    assert incident.severity == "critical"
    assert "automatic_action=frozen" in (incident.evidence_summary or "")


def test_guardrail_data_runtime_and_broker_failures_pause_new_orders() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        pause_rules={
            "min_data_freshness_pct": 0.95,
            "max_runtime_heartbeat_age_seconds": 120,
            "broker_error_count": 2,
            "order_reject_rate": 0.10,
            "slippage_bps": 50,
        },
    )

    evaluation = RiskGuardrailEvaluator().evaluate(
        policy,
        _guardrail_context(
            data_freshness_pct=0.72,
            runtime_heartbeat_age_seconds=300,
            broker_error_count=3,
            order_reject_rate=0.18,
            slippage_bps=72,
        ),
    )

    events_by_trigger = {event.trigger_name: event for event in evaluation.events}
    assert {
        "data_freshness_pct",
        "runtime_heartbeat_age_seconds",
        "broker_error_count",
        "order_reject_rate",
        "slippage_bps",
    }.issubset(events_by_trigger)
    assert {
        events_by_trigger[name].automatic_action
        for name in (
            "data_freshness_pct",
            "runtime_heartbeat_age_seconds",
            "broker_error_count",
            "order_reject_rate",
            "slippage_bps",
        )
    } == {RiskGuardrailAction.PAUSE_NEW_ORDERS.value}
    assert all(event.may_promote is False for event in evaluation.events)
    assert all(event.may_increase_allocation is False for event in evaluation.events)


def test_guardrail_exposure_and_correlation_reduce_without_allocation_increase() -> None:
    policy = RiskPolicy(
        risk_policy_id="risk-main",
        pause_rules={"exposure_pct": 0.8},
        max_signal_correlation=0.9,
    )

    evaluation = RiskGuardrailEvaluator().evaluate(
        policy,
        _guardrail_context(exposure_pct=0.93, correlation=0.97),
    )

    events_by_trigger = {event.trigger_name: event for event in evaluation.events}
    assert events_by_trigger["exposure_pct"].automatic_action == (
        RiskGuardrailAction.REDUCE_EXPOSURE.value
    )
    assert events_by_trigger["correlation"].automatic_action == (
        RiskGuardrailAction.REDUCE_EXPOSURE.value
    )
    assert all(event.review_required is True for event in evaluation.events)
    assert all(event.may_promote is False for event in evaluation.events)
    assert all(event.may_increase_allocation is False for event in evaluation.events)

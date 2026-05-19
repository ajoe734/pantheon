from __future__ import annotations

import pytest

from services.broker.live_activation.first_week_observation import (
    DECISION_CONTINUE,
    DECISION_ESCALATE,
    DECISION_FREEZE,
    EXPECTED_FIRST_WEEK_OBSERVATION_CHECKS,
    REPORT_SOURCE,
    FirstWeekObservationReportError,
    build_first_week_observation_report,
    build_first_week_observation_report_or_raise,
)
from services.broker.live_activation.validator import EXPECTED_HARD_FAIL_CONDITIONS


def test_first_week_observation_report_happy_path_is_deterministic() -> None:
    packet = _observation_packet()

    report = build_first_week_observation_report(packet)
    repeated = build_first_week_observation_report(packet)

    assert report.version == "1.0"
    assert report.source == REPORT_SOURCE
    assert report.report_id == repeated.report_id
    assert report.can_continue_live is True
    assert report.decision == DECISION_CONTINUE
    assert report.escalation_required is False
    assert report.observation_window_days == 7
    assert report.blocking_reasons == ()
    assert tuple(check.text for check in report.checks) == EXPECTED_FIRST_WEEK_OBSERVATION_CHECKS
    assert [check.id for check in report.checks] == [
        "first_week_b5_01",
        "first_week_b5_02",
        "first_week_b5_03",
        "first_week_b5_04",
        "first_week_b5_05",
        "first_week_b5_06",
        "first_week_b5_07",
        "first_week_b5_08",
        "first_week_b5_09",
        "first_week_b5_10",
        "first_week_b5_11",
        "first_week_b5_12",
        "first_week_b5_13",
    ]
    assert {check.status for check in report.checks} == {"ready"}
    assert report.to_dict()["passed"] is True


def test_missing_daily_checkin_fails_closed_and_blocks_continue() -> None:
    packet = _observation_packet()
    packet["daily_checkins"] = packet["daily_checkins"][:-1]

    report = build_first_week_observation_report(packet)

    assert report.can_continue_live is False
    assert report.decision == "hold_live_change"
    assert report.checks[2].status == "blocked"
    assert report.checks[2].blocking_reasons == ("daily check-in day 7 is required",)
    with pytest.raises(FirstWeekObservationReportError, match="day 7"):
        build_first_week_observation_report_or_raise(packet)


def test_daily_checkin_without_evidence_ref_fails_closed() -> None:
    packet = _observation_packet()
    del packet["daily_checkins"][0]["evidence_ref"]

    report = build_first_week_observation_report(packet)

    assert report.can_continue_live is False
    assert report.checks[2].status == "blocked"
    assert report.checks[2].blocking_reasons == (
        "daily check-in day 1 evidence reference is required",
    )


def test_active_hard_fail_freezes_or_safe_mode() -> None:
    packet = _observation_packet()
    packet["conditions"]["telemetry_unavailable"] = True

    report = build_first_week_observation_report(packet)

    assert report.can_continue_live is False
    assert report.decision == DECISION_FREEZE
    assert report.checks[12].status == "blocked"
    assert (
        "hard fail condition active: telemetry_unavailable"
        in report.checks[12].blocking_reasons
    )


def test_threshold_breach_escalates_to_risk_owner() -> None:
    packet = _observation_packet()
    packet["risk"]["drawdown_within_threshold"] = False

    report = build_first_week_observation_report(packet)

    assert report.can_continue_live is False
    assert report.decision == DECISION_ESCALATE
    assert report.checks[6].status == "blocked"
    assert report.checks[6].blocking_reasons == (
        "drawdown must stay within threshold",
    )


def test_invalid_criteria_blocks_every_check() -> None:
    report = build_first_week_observation_report(_observation_packet(), criteria={"version": "bad"})

    assert report.can_continue_live is False
    assert report.blocking_reasons
    assert {check.status for check in report.checks} == {"blocked"}
    assert "version must be '1.0'" in report.blocking_reasons


def _observation_packet() -> dict:
    return {
        "live_activation_ref": "activation://broker-live/shioaji-001",
        "deployment_plan_ref": "deployment-plan://live/shioaji-001",
        "runtime_binding_id": "runtime-binding-live-001",
        "capital_pool_id": "pool-live-main",
        "observation_window": {
            "live_started_at": "2026-05-19T00:00:00Z",
            "observed_through_at": "2026-05-26T00:00:00Z",
            "first_week_observation_window_ref": "support/evidence/BLA/first-week.json",
        },
        "daily_checkins": [
            {
                "day": day,
                "status": "complete",
                "evidence_ref": f"support/evidence/BLA/day-{day}.json",
            }
            for day in range(1, 8)
        ],
        "runtime": {
            "runtime_health_ref": "runtime-health://live/shioaji-001/week1",
            "runtime_healthy": True,
        },
        "broker": {
            "broker_session_ref": "shioaji://session/live-ready-001/week1",
            "broker_session_healthy": True,
        },
        "telemetry": {
            "telemetry_readiness_ref": "support/evidence/TEL/week1-readiness.json",
            "runtime_heartbeat_count": 168,
            "pnl_snapshot_count": 7,
        },
        "operations": {
            "audit_retention_ref": "support/evidence/AUDIT/retention.json",
            "audit_replay_ref": "support/evidence/AUDIT/replay.json",
            "audit_event_count": 42,
            "kill_switch_demo_ref": "support/evidence/KILL/demo.json",
            "kill_switch_available": True,
            "safe_mode_ready": True,
            "rollback_target_ref": "artifact://rollback/live-ready-rollback-001",
            "rollback_drill_ref": "support/evidence/ROLLBACK/drill.json",
            "no_go_conditions_ref": "support/evidence/BLA/no-go-conditions.json",
            "open_operator_blockers": [],
            "open_incidents": [],
        },
        "risk": {
            "pnl_within_threshold": True,
            "drawdown_within_threshold": True,
            "exposure_within_threshold": True,
            "liquidity_within_threshold": True,
            "slippage_within_threshold": True,
            "fill_rate_within_threshold": True,
            "order_rejections_within_threshold": True,
            "alpha_drift_within_threshold": True,
            "short_term_drift_detected": False,
            "risk_metrics_ref": "support/evidence/RISK/week1-metrics.json",
            "execution_quality_ref": "support/evidence/BROKER/execution-quality.json",
            "drift_report_ref": "support/evidence/DRIFT/week1.json",
        },
        "conditions": {condition: False for condition in EXPECTED_HARD_FAIL_CONDITIONS},
    }

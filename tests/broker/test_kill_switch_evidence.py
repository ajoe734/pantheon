from __future__ import annotations

import pytest

from services.broker.live_activation.kill_switch_evidence import (
    EVIDENCE_SOURCE,
    KillSwitchDemoEvidenceError,
    collect_kill_switch_demo_evidence,
    collect_kill_switch_demo_evidence_or_raise,
)


def test_kill_switch_demo_evidence_happy_path_is_deterministic() -> None:
    packet = _demo_packet()

    evidence = collect_kill_switch_demo_evidence(packet)
    repeated = collect_kill_switch_demo_evidence(packet)

    assert evidence.version == "1.0"
    assert evidence.source == EVIDENCE_SOURCE
    assert evidence.passed is True
    assert evidence.promotion_eligible is True
    assert evidence.evidence_id == repeated.evidence_id
    assert evidence.demo_stage == "staging_live"
    assert evidence.covered_actions == ("pause",)
    assert evidence.blocking_reasons == ()
    assert evidence.to_dict()["drills"] == [
        {
            "label": "pause-drill",
            "action_type": "pause",
            "ack_status": "acknowledged",
            "evidence_refs": [
                "ks-cmd-001",
                "ks-audit-001",
                "runtime-binding-live-001",
                "pool-live-main",
                "paused",
            ],
            "command_id": "ks-cmd-001",
            "audit_id": "ks-audit-001",
            "capital_pool_id": "pool-live-main",
            "runtime_binding_id": "runtime-binding-live-001",
            "runtime_status_after": "paused",
            "safe_mode_after": "paused",
        }
    ]


def test_fail_closed_ack_is_audit_only_and_blocks_promotion() -> None:
    packet = _demo_packet()
    response = packet["drills"][0]["dispatch_response"]
    response["binding_action"] = None
    response["telemetry_ack"].update(
        {
            "ack_status": "fail_closed",
            "ack_received": False,
            "fail_closed": True,
            "runtime_state_recorded": False,
            "capital_state_recorded": False,
        }
    )

    evidence = collect_kill_switch_demo_evidence(packet)

    assert evidence.passed is False
    assert evidence.promotion_eligible is False
    codes = {issue.code for issue in evidence.errors}
    assert "telemetry_ack_not_acknowledged" in codes
    assert "telemetry_ack_fail_closed" in codes
    assert "required_action_not_covered" in codes
    assert any("audit-only evidence" in reason for reason in evidence.blocking_reasons)
    with pytest.raises(KillSwitchDemoEvidenceError, match="acknowledged"):
        collect_kill_switch_demo_evidence_or_raise(packet)


def test_live_stage_requires_broker_subaccount_and_runtime_links() -> None:
    packet = _demo_packet()
    del packet["broker_subaccount_ref"]
    del packet["deployment_plan_ref"]
    packet["drills"][0]["dispatch_response"]["telemetry_ack"]["runtime_binding_id"] = (
        "runtime-binding-other"
    )

    evidence = collect_kill_switch_demo_evidence(packet)

    assert evidence.passed is False
    codes = {issue.code for issue in evidence.errors}
    assert "missing_broker_subaccount_ref" in codes
    assert "missing_deployment_plan_ref" in codes
    assert "runtime_binding_mismatch" in codes


def test_command_and_ack_identity_mismatch_fails_closed() -> None:
    packet = _demo_packet()
    packet["drills"][0]["dispatch_response"]["telemetry_ack"]["command_id"] = "ks-cmd-other"

    evidence = collect_kill_switch_demo_evidence(packet)

    assert evidence.passed is False
    assert {issue.code for issue in evidence.errors} == {"command_id_mismatch"}


def test_required_action_groups_can_require_additional_drills() -> None:
    packet = _demo_packet()

    evidence = collect_kill_switch_demo_evidence(
        packet,
        required_action_groups=(("pause", "risk_off"), ("replace",)),
    )

    assert evidence.passed is False
    assert {issue.code for issue in evidence.errors} == {"required_action_not_covered"}
    assert evidence.blocking_reasons == ("kill-switch drill must cover one of: replace",)


def _demo_packet() -> dict:
    return {
        "demo_id": "ks-demo-2026-05-19",
        "demo_stage": "staging-live",
        "deployment_plan_ref": "deployment-plan://live/shioaji-001",
        "runtime_binding_id": "runtime-binding-live-001",
        "capital_pool_id": "pool-live-main",
        "operator_id": "operator://primary/live-operator",
        "broker_subaccount_ref": "broker-account://shioaji/live-subaccount-001",
        "drills": [
            {
                "label": "pause-drill",
                "dispatch_response": _runtime_manager_response(),
            }
        ],
    }


def _runtime_manager_response() -> dict:
    return {
        "command": {
            "command_id": "ks-cmd-001",
            "action_type": "pause",
            "capital_pool_id": "pool-live-main",
            "binding_id": "runtime-binding-live-001",
            "bypass_review_queue": True,
        },
        "audit_entry": {
            "audit_id": "ks-audit-001",
            "reason": "operator_emergency_stop",
            "audited_at": "2026-05-19T16:00:00Z",
        },
        "binding_action": {
            "action": "pause",
            "binding": {
                "binding_id": "runtime-binding-live-001",
                "status": "paused",
            },
        },
        "safe_mode_after": "paused",
        "telemetry_ack": {
            "ack_status": "acknowledged",
            "ack_received": True,
            "fail_closed": False,
            "command_id": "ks-cmd-001",
            "audit_id": "ks-audit-001",
            "capital_pool_id": "pool-live-main",
            "action_type": "pause",
            "runtime_binding_id": "runtime-binding-live-001",
            "runtime_status_after": "paused",
            "safe_mode_after": "paused",
            "runtime_state_recorded": True,
            "capital_state_recorded": True,
            "telemetry_event_type": "kill_switch_action",
        },
    }

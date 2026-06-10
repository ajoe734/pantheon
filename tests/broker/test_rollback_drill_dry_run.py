from __future__ import annotations

import pytest

from services.broker.live_activation.rollback_drill_dry_run import (
    EVIDENCE_SOURCE,
    RollbackDrillDryRunError,
    run_rollback_drill_dry_run,
    run_rollback_drill_dry_run_or_raise,
)


def test_rollback_drill_dry_run_happy_path_is_deterministic() -> None:
    packet = _dry_run_packet()

    evidence = run_rollback_drill_dry_run(packet)
    repeated = run_rollback_drill_dry_run(packet)

    assert evidence.version == "1.0"
    assert evidence.source == EVIDENCE_SOURCE
    assert evidence.passed is True
    assert evidence.promotion_eligible is True
    assert evidence.dry_run is True
    assert evidence.evidence_id == repeated.evidence_id
    assert evidence.drill_stage == "staging_live"
    assert evidence.action_type == "pause_then_replace"
    assert evidence.blocking_reasons == ()
    assert evidence.expected_outcome["side_effects_executed"] == []
    assert evidence.expected_outcome["pre_cutover_current_binding_status"] == "paused"
    assert evidence.planned_runtime_manager_request == {
        "current_binding_id": "runtime-binding-live-001",
        "action_type": "pause_then_replace",
        "replacement_plan_id": "deployment-plan://live/shioaji-001-rollback",
        "replacement_plan_status": "approved",
        "replacement_deployment_mode": "paper",
        "replacement_artifact_id": "artifact://fallback/mean-reversion-safe",
        "replacement_artifact_version": "2026.05.19-safe",
        "replacement_persona_capital_binding_id": "persona-capital://safe-mode",
        "replacement_persona_capital_binding_status": "active",
        "replacement_allowed_deployment_scope": "paper",
        "replacement_runtime_id": "runtime://paper-safe-mode",
        "loader_checks_passed": True,
        "opened_by_artifact_id": "artifact://current/live-momentum",
        "dry_run": True,
        "side_effects_allowed": False,
    }
    assert evidence.telemetry_event_preview["execution_mode"] == "dry_run"
    assert evidence.telemetry_event_preview["rollback_parent"] == "runtime-binding-live-001"
    assert evidence.telemetry_event_preview["rollback_action_type"] == "pause_then_replace"
    assert "no_runtime_manager_dispatch" in evidence.safety_guards
    assert "no_broker_api_call" in evidence.safety_guards


def test_rollback_drill_dry_run_refuses_live_side_effect_flags() -> None:
    packet = _dry_run_packet()
    packet["dry_run"] = False
    packet["dispatch_live"] = True

    evidence = run_rollback_drill_dry_run(packet)

    assert evidence.passed is False
    assert evidence.promotion_eligible is False
    assert {issue.code for issue in evidence.errors} == {"live_side_effect_requested"}
    assert any("refuses live dispatch" in reason for reason in evidence.blocking_reasons)
    with pytest.raises(RollbackDrillDryRunError, match="live dispatch"):
        run_rollback_drill_dry_run_or_raise(packet)


def test_rollback_drill_dry_run_fails_closed_for_missing_target_and_inactive_binding() -> None:
    packet = _dry_run_packet()
    del packet["replacement_artifact_id"]
    packet["current_binding_status"] = "paused"

    evidence = run_rollback_drill_dry_run(packet)

    assert evidence.passed is False
    codes = {issue.code for issue in evidence.errors}
    assert "missing_replacement_artifact_id" in codes
    assert "current_binding_not_active" in codes


def test_rollback_drill_dry_run_fails_closed_for_invalid_action_type() -> None:
    packet = _dry_run_packet()
    packet["action_type"] = "freeze"

    evidence = run_rollback_drill_dry_run(packet)

    assert evidence.passed is False
    assert {issue.code for issue in evidence.errors} == {"invalid_action_type"}


def test_canary_or_live_dry_run_requires_broker_subaccount_without_raw_secrets() -> None:
    packet = _dry_run_packet()
    del packet["broker_subaccount_ref"]
    packet["broker_password"] = "raw-secret"

    evidence = run_rollback_drill_dry_run(packet)

    assert evidence.passed is False
    codes = {issue.code for issue in evidence.errors}
    assert "missing_broker_subaccount_ref" in codes
    assert "raw_broker_secret_present" in codes


def _dry_run_packet() -> dict:
    return {
        "drill_id": "rollback-drill-2026-05-19",
        "dry_run": True,
        "drill_stage": "staging-live",
        "deployment_plan_ref": "deployment-plan://live/shioaji-001",
        "current_binding_id": "runtime-binding-live-001",
        "current_binding_status": "active",
        "capital_pool_id": "pool-live-main",
        "operator_id": "operator://primary/live-operator",
        "broker_subaccount_ref": "broker-account://shioaji/live-subaccount-001",
        "action_type": "pause_then_replace",
        "replacement_plan_ref": "deployment-plan://live/shioaji-001-rollback",
        "replacement_deployment_mode": "paper",
        "replacement_allowed_deployment_scope": "paper",
        "replacement_runtime_id": "runtime://paper-safe-mode",
        "replacement_artifact_id": "artifact://fallback/mean-reversion-safe",
        "replacement_artifact_version": "2026.05.19-safe",
        "replacement_persona_capital_binding_id": "persona-capital://safe-mode",
        "opened_by_artifact_id": "artifact://current/live-momentum",
        "loader_checks_passed": True,
    }

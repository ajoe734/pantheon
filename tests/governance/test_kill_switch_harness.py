"""Tests for the EP5 kill-switch demo harness."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.governance.ep5_proof.kill_switch_harness import (
    HARNESS_VERSION,
    KillSwitchHarnessError,
    main,
    run_kill_switch_demo_harness,
)
from services.governance.ep5_proof.packet_generator import (
    PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS,
    PROOF_FLAG_ORDER_ROUTE_MODE,
)


_VALID_REQUEST = {
    "harness_id": "ks-demo-ep5-008",
    "proof_id": "ep5-proof-ks-demo-001",
    "promotion_readiness_packet_id": "prp-ks-demo-001",
    "run_id": "canary-run-ks-demo-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-ks-demo-001",
    "artifact_id": "artifact-alpha-v1",
    "artifact_version": "1.2.3",
    "deployment_plan_id": "dep-canary-ks-demo-001",
    "capital_pool_id": "pool-canary-001",
    "persona_capital_binding_id": "pcb-canary-001",
    "operator_id": "operator-ep5",
    "started_at": "2026-05-20T00:00:00Z",
    "ended_at": "2026-05-20T00:01:00Z",
    "evidence_refs": ["support/evidence/EP5-008-V2/manual-observation.json"],
}


def test_harness_runs_runtime_manager_demo_and_marks_ep5_proof_completed() -> None:
    result = run_kill_switch_demo_harness(_VALID_REQUEST)

    assert result["harness_id"] == "ks-demo-ep5-008"
    assert result["harness_version"] == HARNESS_VERSION
    assert result["status"] == "passed"
    assert result["kill_switch_demo_completed"] is True
    assert result["live_capital_side_effects"] is False

    runtime_response = result["runtime_manager_response"]
    assert runtime_response["command"]["action_type"] == "pause"
    assert runtime_response["command"]["bypass_review_queue"] is True
    assert runtime_response["binding_action"]["binding"]["status"] == "paused"
    assert runtime_response["telemetry_ack"]["ack_status"] == "acknowledged"
    assert runtime_response["telemetry_ack"]["telemetry_event_type"] == "kill_switch_action"

    evidence = result["kill_switch_demo_evidence"]
    assert evidence["passed"] is True
    assert evidence["promotion_eligible"] is True
    assert evidence["demo_stage"] == "canary"
    assert evidence["covered_actions"] == ["pause"]
    assert evidence["blocking_reasons"] == []

    proof_packet = result["proof_packet"]
    assert proof_packet["packet_id"] == "ep5-proof-ks-demo-001"
    assert proof_packet["promotion_readiness_packet_id"] == "prp-ks-demo-001"
    assert proof_packet["proof"]["kill_switch_demo_completed"] is True
    assert proof_packet["proof"]["live_capital_side_effects"] is False
    assert proof_packet["result"]["pass"] is True
    assert any(
        ref.startswith("kill-switch-demo:")
        for ref in proof_packet["result"]["evidence_refs"]
    )

    readiness_packet = result["promotion_readiness_packet"]
    assert readiness_packet["packet_id"] == "prp-ks-demo-001"
    assert readiness_packet["can_proceed"] is True
    assert readiness_packet["flags"][PROOF_FLAG_ORDER_ROUTE_MODE] is True
    assert readiness_packet["flags"][PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS] is False


def test_harness_supports_risk_off_action_as_required_demo_coverage() -> None:
    result = run_kill_switch_demo_harness(
        {
            **_VALID_REQUEST,
            "harness_id": "ks-demo-ep5-risk-off",
            "proof_id": "ep5-proof-ks-risk-off",
            "promotion_readiness_packet_id": "prp-ks-risk-off",
            "run_id": "canary-run-ks-risk-off",
            "action_type": "risk_off",
            "reason": "drawdown_hard_breach",
        }
    )

    assert result["status"] == "passed"
    assert result["runtime_manager_response"]["command"]["action_type"] == "risk_off"
    assert result["kill_switch_demo_evidence"]["covered_actions"] == ["risk_off"]
    assert result["proof_packet"]["proof"]["kill_switch_demo_completed"] is True


def test_live_order_route_is_rejected_before_demo_execution() -> None:
    with pytest.raises(KillSwitchHarnessError, match="order_route_mode"):
        run_kill_switch_demo_harness({**_VALID_REQUEST, "order_route_mode": "live"})


def test_cli_writes_json_output(tmp_path: Path) -> None:
    output = tmp_path / "kill-switch-demo.json"

    exit_code = main(
        [
            "--output",
            str(output),
            "--harness-id",
            "ks-demo-cli",
            "--proof-id",
            "ep5-proof-cli",
            "--promotion-readiness-packet-id",
            "prp-cli",
            "--run-id",
            "canary-run-cli",
        ]
    )

    assert exit_code == 0
    payload = output.read_text()
    assert '"kill_switch_demo_completed": true' in payload
    assert '"packet_id": "ep5-proof-cli"' in payload

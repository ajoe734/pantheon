"""Tests for the EP5 rollback drill harness."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.governance.ep5_proof.packet_generator import (
    PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS,
    PROOF_FLAG_ORDER_ROUTE_MODE,
)
from services.governance.ep5_proof.rollback_drill_harness import (
    HARNESS_VERSION,
    RUNBOOK_REF,
    RollbackDrillHarnessError,
    main,
    run_rollback_drill_harness,
)


_VALID_REQUEST = {
    "harness_id": "rollback-drill-ep5-007",
    "proof_id": "ep5-proof-rollback-drill-001",
    "promotion_readiness_packet_id": "prp-rollback-drill-001",
    "run_id": "canary-run-rollback-drill-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-rollback-current-001",
    "replacement_runtime_id": "rt-rollback-replacement-001",
    "artifact_id": "artifact-alpha-v1",
    "artifact_version": "1.2.3",
    "replacement_artifact_id": "artifact-alpha-safe",
    "replacement_artifact_version": "1.2.2",
    "deployment_plan_id": "dep-canary-rollback-001",
    "replacement_plan_id": "dep-paper-rollback-001",
    "capital_pool_id": "pool-canary-001",
    # Must match: rollback requires the replacement to preserve the
    # current binding's PersonaCapitalBinding identity exactly.
    "persona_capital_binding_id": "pcb-canary-001",
    "replacement_persona_capital_binding_id": "pcb-canary-001",
    "operator_id": "operator-ep5",
    "started_at": "2026-05-20T00:00:00Z",
    "ended_at": "2026-05-20T00:01:00Z",
    "evidence_refs": ["support/evidence/EP5-007-V2/manual-observation.json"],
}


def test_harness_runs_runtime_manager_rollback_and_marks_ep5_proof_completed() -> None:
    result = run_rollback_drill_harness(_VALID_REQUEST)

    assert result["harness_id"] == "rollback-drill-ep5-007"
    assert result["harness_version"] == HARNESS_VERSION
    assert result["runbook_ref"] == RUNBOOK_REF
    assert result["status"] == "passed"
    assert result["rollback_drill_completed"] is True
    assert result["live_capital_side_effects"] is False

    evidence = result["rollback_drill_evidence"]
    assert evidence["passed"] is True
    assert evidence["promotion_eligible"] is True
    assert evidence["dry_run"] is True
    assert evidence["drill_stage"] == "canary"
    assert evidence["action_type"] == "pause_then_replace"
    assert evidence["blocking_reasons"] == []
    assert evidence["expected_outcome"]["side_effects_executed"] == []
    assert "no_runtime_manager_dispatch" in evidence["safety_guards"]
    assert "no_broker_api_call" in evidence["safety_guards"]

    rollback_response = result["runtime_manager_response"]
    old_binding = rollback_response["old_binding"]
    new_binding = rollback_response["new_binding"]
    assert rollback_response["action_type"] == "pause_then_replace"
    assert old_binding["status"] == "retired"
    assert new_binding["status"] == "active"
    assert new_binding["deployment_mode"] == "paper"
    assert new_binding["artifact_id"] == "artifact-alpha-safe"
    assert new_binding["rollback_parent"] == old_binding["binding_id"]
    assert new_binding["rollback_action_type"] == "pause_then_replace"
    assert rollback_response["position_lineage"]["opened_by_artifact_id"] == "artifact-alpha-v1"

    proof_packet = result["proof_packet"]
    assert proof_packet["packet_id"] == "ep5-proof-rollback-drill-001"
    assert proof_packet["promotion_readiness_packet_id"] == "prp-rollback-drill-001"
    assert proof_packet["proof"]["rollback_drill_completed"] is True
    assert proof_packet["proof"]["live_capital_side_effects"] is False
    assert proof_packet["result"]["pass"] is True
    assert any(
        ref.startswith("rollback-drill:")
        for ref in proof_packet["result"]["evidence_refs"]
    )
    assert f"runbook:{RUNBOOK_REF}" in proof_packet["result"]["evidence_refs"]
    assert any(
        ref == f"runtime-manager-rollback:{new_binding['binding_id']}"
        for ref in proof_packet["result"]["evidence_refs"]
    )

    readiness_packet = result["promotion_readiness_packet"]
    assert readiness_packet["packet_id"] == "prp-rollback-drill-001"
    assert readiness_packet["can_proceed"] is True
    assert readiness_packet["flags"][PROOF_FLAG_ORDER_ROUTE_MODE] is True
    assert readiness_packet["flags"][PROOF_FLAG_LIVE_CAPITAL_SIDE_EFFECTS] is False


def test_harness_supports_liquidate_then_replace_paused_replacement() -> None:
    result = run_rollback_drill_harness(
        {
            **_VALID_REQUEST,
            "harness_id": "rollback-drill-ep5-liquidate",
            "proof_id": "ep5-proof-rollback-liquidate",
            "promotion_readiness_packet_id": "prp-rollback-liquidate",
            "run_id": "canary-run-rollback-liquidate",
            "action_type": "liquidate_then_replace",
            "replacement_start_paused": True,
        }
    )

    rollback_response = result["runtime_manager_response"]
    old_binding_id = rollback_response["old_binding"]["binding_id"]
    assert result["status"] == "passed"
    assert rollback_response["action_type"] == "liquidate_then_replace"
    assert rollback_response["new_binding"]["status"] == "paused"
    assert (
        rollback_response["position_lineage"]["current_managed_by_binding_id"]
        == old_binding_id
    )
    assert result["proof_packet"]["proof"]["rollback_drill_completed"] is True


def test_missing_broker_subaccount_ref_fails_closed_before_runtime_rollback() -> None:
    with pytest.raises(RollbackDrillHarnessError, match="broker subaccount"):
        run_rollback_drill_harness({**_VALID_REQUEST, "broker_subaccount_ref": ""})


def test_live_order_route_is_rejected_before_drill_execution() -> None:
    with pytest.raises(RollbackDrillHarnessError, match="order_route_mode"):
        run_rollback_drill_harness({**_VALID_REQUEST, "order_route_mode": "live"})


def test_cli_writes_json_output(tmp_path: Path) -> None:
    output = tmp_path / "rollback-drill.json"

    exit_code = main(
        [
            "--output",
            str(output),
            "--harness-id",
            "rollback-drill-cli",
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
    assert '"rollback_drill_completed": true' in payload
    assert '"packet_id": "ep5-proof-cli"' in payload

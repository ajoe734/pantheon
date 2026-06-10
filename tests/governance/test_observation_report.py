"""Tests for the EP5 canary observation report builder."""
from __future__ import annotations

from pathlib import Path

import pytest

from services.governance.ep5_proof.observation_report import (
    REPORT_VERSION,
    CanaryObservationReportError,
    build_canary_observation_report,
    main,
)


_VALID_REQUEST = {
    "report_id": "canary-observation-ep5-009",
    "proof_id": "ep5-proof-observation-001",
    "promotion_readiness_packet_id": "prp-observation-001",
    "run_id": "canary-run-observation-001",
    "persona_id": "persona-alpha",
    "runtime_id": "rt-observation-001",
    "runtime_binding_id": "rb-observation-001",
    "artifact_id": "artifact-alpha-v1",
    "deployment_plan_id": "dep-canary-observation-001",
    "mode": "validate_only",
    "started_at": "2026-05-20T00:00:00Z",
    "ended_at": "2026-05-20T00:05:00Z",
    "telemetry_refs": [
        "support/evidence/EP5-009-V2/telemetry-heartbeat.json",
        "telemetry:event-fill-observation-001",
    ],
    "audit_refs": ["support/evidence/EP5-009-V2/governance-audit.json"],
    "incident_refs": ["incident:inc-canary-observation-001"],
    "reconciliation_status": "within_threshold",
    "reconciliation_record_ref": "rec-canary-observation-001",
    "drift_report_ref": "support/evidence/EP5-009-V2/drift-report.json",
    "evidence_refs": ["support/evidence/EP5-009-V2/manual-observation.json"],
    "rollback_drill_completed": True,
    "kill_switch_demo_completed": True,
}


def test_report_collects_observation_refs_into_ep5_proof_packet() -> None:
    result = build_canary_observation_report(_VALID_REQUEST)

    assert result["report_id"] == "canary-observation-ep5-009"
    assert result["report_version"] == REPORT_VERSION
    assert result["status"] == "passed"
    assert result["blocking_reasons"] == []

    assert result["telemetry"] == {
        "status": "present",
        "refs": [
            "support/evidence/EP5-009-V2/telemetry-heartbeat.json",
            "telemetry:event-fill-observation-001",
        ],
    }
    assert result["audit"]["refs"] == [
        "support/evidence/EP5-009-V2/governance-audit.json"
    ]
    assert result["incidents"]["refs"] == ["incident:inc-canary-observation-001"]
    assert result["reconciliation"] == {
        "status": "within_threshold",
        "record_ref": "rec-canary-observation-001",
        "drift_report_ref": "support/evidence/EP5-009-V2/drift-report.json",
        "threshold_breached": False,
    }

    evidence_refs = result["evidence_refs"]
    assert evidence_refs == [
        "support/evidence/EP5-009-V2/manual-observation.json",
        "telemetry:support/evidence/EP5-009-V2/telemetry-heartbeat.json",
        "telemetry:event-fill-observation-001",
        "audit:support/evidence/EP5-009-V2/governance-audit.json",
        "incident:inc-canary-observation-001",
        "reconciliation:rec-canary-observation-001",
        "drift-report:support/evidence/EP5-009-V2/drift-report.json",
    ]

    proof_packet = result["proof_packet"]
    assert proof_packet["packet_id"] == "ep5-proof-observation-001"
    assert proof_packet["promotion_readiness_packet_id"] == "prp-observation-001"
    assert proof_packet["status"] == "passed"
    assert proof_packet["proof"]["telemetry_ingested"] is True
    assert proof_packet["proof"]["audit_events_recorded"] is True
    assert proof_packet["proof"]["incident_path_tested"] is True
    assert proof_packet["proof"]["rollback_drill_completed"] is True
    assert proof_packet["proof"]["kill_switch_demo_completed"] is True
    assert proof_packet["result"]["evidence_refs"] == evidence_refs

    readiness_packet = result["promotion_readiness_packet"]
    assert readiness_packet["can_proceed"] is True


def test_clean_observation_without_incident_refs_still_passes() -> None:
    result = build_canary_observation_report(
        {
            **_VALID_REQUEST,
            "incident_refs": [],
            "drift_report_ref": None,
            "reconciliation_status": "passed",
        }
    )

    assert result["status"] == "passed"
    assert result["incidents"] == {
        "status": "not_observed",
        "refs": [],
        "threshold_breached": False,
    }
    assert result["proof_packet"]["proof"]["incident_path_tested"] is False
    assert (
        "drift-report:support/evidence/EP5-009-V2/drift-report.json"
        not in result["evidence_refs"]
    )


def test_missing_telemetry_refs_fail_closed_and_block_proof_packet() -> None:
    result = build_canary_observation_report({**_VALID_REQUEST, "telemetry_refs": []})

    assert result["status"] == "failed"
    assert "MISSING_TELEMETRY_REFS" in result["blocking_reasons"]
    assert "NO_HEARTBEAT_RECEIVED" in result["proof_packet"]["result"]["blocking_reasons"]
    assert "TELEMETRY_NOT_INGESTED" in result["proof_packet"]["result"]["blocking_reasons"]
    assert result["proof_packet"]["result"]["pass"] is False
    assert result["promotion_readiness_packet"]["can_proceed"] is False


def test_missing_audit_refs_fail_closed_without_hiding_core_proof_refs() -> None:
    result = build_canary_observation_report({**_VALID_REQUEST, "audit_refs": []})

    assert result["status"] == "failed"
    assert result["audit"] == {"status": "missing", "refs": []}
    assert result["proof_packet"]["proof"]["audit_events_recorded"] is False
    assert "MISSING_AUDIT_REFS" in result["proof_packet"]["result"]["blocking_reasons"]
    assert result["promotion_readiness_packet"]["can_proceed"] is True


def test_reconciliation_threshold_breach_blocks_even_with_incident_ref() -> None:
    result = build_canary_observation_report(
        {
            **_VALID_REQUEST,
            "reconciliation_status": "threshold_breached",
            "threshold_breached": True,
        }
    )

    assert result["status"] == "failed"
    assert result["reconciliation"]["threshold_breached"] is True
    assert "RECONCILIATION_THRESHOLD_BREACH" in result["blocking_reasons"]
    assert "incident:inc-canary-observation-001" in result["evidence_refs"]
    assert result["proof_packet"]["result"]["pass"] is False


def test_threshold_breach_requires_incident_ref() -> None:
    result = build_canary_observation_report(
        {
            **_VALID_REQUEST,
            "incident_refs": [],
            "reconciliation_status": "drift_detected",
        }
    )

    assert result["status"] == "failed"
    assert "RECONCILIATION_THRESHOLD_BREACH" in result["blocking_reasons"]
    assert "INCIDENT_REF_REQUIRED" in result["blocking_reasons"]
    assert result["proof_packet"]["proof"]["incident_path_tested"] is False


def test_rejects_live_order_route_before_report_build() -> None:
    with pytest.raises(CanaryObservationReportError, match="order_route_mode"):
        build_canary_observation_report({**_VALID_REQUEST, "order_route_mode": "live"})


def test_rejects_empty_ref_entries() -> None:
    with pytest.raises(CanaryObservationReportError, match="telemetry_refs"):
        build_canary_observation_report({**_VALID_REQUEST, "telemetry_refs": [""]})


def test_cli_writes_json_output(tmp_path: Path) -> None:
    output = tmp_path / "observation-report.json"

    exit_code = main(
        [
            "--output",
            str(output),
            "--report-id",
            "canary-observation-cli",
            "--proof-id",
            "ep5-proof-cli",
            "--promotion-readiness-packet-id",
            "prp-cli",
            "--run-id",
            "canary-run-cli",
            "--telemetry-ref",
            "support/evidence/EP5-009-V2/telemetry.json",
            "--audit-ref",
            "support/evidence/EP5-009-V2/audit.json",
            "--reconciliation-record-ref",
            "rec-cli",
        ]
    )

    assert exit_code == 0
    payload = output.read_text()
    assert '"report_id": "canary-observation-cli"' in payload
    assert '"packet_id": "ep5-proof-cli"' in payload
    assert '"reconciliation:rec-cli"' in payload

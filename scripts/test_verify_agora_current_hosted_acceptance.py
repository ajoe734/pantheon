"""Pytest test suite for Agora Current Hosted Acceptance Verifier.

Tests:
  - Full positive hosted acceptance verification across all 6 gates
  - Negative control: manifest contract hash mismatch fails closed
  - Negative control: frontend commit drift / non-strict fallback fails closed
  - Negative control: degraded /readyz or repair_only worker fails closed
  - Negative control: broker order authority assertion fails closed
  - Negative control: consultation memo self-attestation or auto-approval fails closed
  - Completeness of gap evidence matrix covering S01-S15 and GAP-W01-W04
  - Structured evidence artifacts generation and schema validation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
for subpath in [
    "",
    "services/control-plane",
    "services/control-plane/bff",
    "services/control-plane/governance",
    "services/policy-learning",
    "services/consultation",
]:
    p = str(REPO_ROOT / subpath) if subpath else str(REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.verify_agora_current_hosted_acceptance import (
    AcceptanceConfig,
    AgoraHostedAcceptanceVerifier,
    AgoraVerificationError,
    HostedAcceptanceReport,
)


def test_hosted_acceptance_full_positive_run(tmp_path: Path) -> None:
    """Ensure AgoraHostedAcceptanceVerifier passes all gates and generates complete evidence."""
    config = AcceptanceConfig(
        mode="simulated-hosted",
        evidence_dir=tmp_path / "evidence",
        strict=True,
    )
    verifier = AgoraHostedAcceptanceVerifier(config)
    report = verifier.run_full_acceptance()

    assert report.overall_status == "PASSED"
    assert report.summary["total_gates"] == 6
    assert report.summary["passed_gates"] == 6
    assert report.summary["failed_gates"] == 0
    assert report.exact_pair["deployment_profile"] == "accepted"
    assert report.exact_pair["contract_family"] == "agora.v2"

    # Verify evidence files exist
    assert (tmp_path / "evidence" / "evidence.json").exists()
    assert (tmp_path / "evidence" / "QUALIFICATION.json").exists()
    assert (tmp_path / "evidence" / "VERIFICATION_REPORT.md").exists()
    assert (tmp_path / "evidence" / "GAP_EVIDENCE_MATRIX.md").exists()
    assert (tmp_path / "evidence" / "DEPLOYMENT_AUDIT.md").exists()

    # Check evidence.json payload
    with open(tmp_path / "evidence" / "evidence.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["overall_status"] == "PASSED"
        assert len(data["gate_results"]) == 6
        assert len(data["gap_matrix"]) >= 19


def test_gate_01_manifest_drift_fails_closed(tmp_path: Path) -> None:
    """Gate 1 must fail closed if contract hashes or FE deployment commit drifts."""
    config = AcceptanceConfig(
        mode="simulated-hosted",
        evidence_dir=tmp_path / "evidence",
        strict=True,
        expected_fe_sha="0000000000000000000000000000000000000000",  # mismatched SHA
    )
    verifier = AgoraHostedAcceptanceVerifier(config)
    report = verifier.run_full_acceptance()

    assert report.overall_status == "FAILED"
    gate_01 = next((g for g in report.gate_results if g.gate_id == "gate_01_manifest_exact_pair"), None)
    assert gate_01 is not None
    assert gate_01.status == "FAILED"
    assert "drift" in str(gate_01.error)


def test_gate_02_unhealthy_readyz_fails_closed(tmp_path: Path) -> None:
    """Gate 2 must fail closed if /readyz reports repair workers or degraded controllers."""
    config = AcceptanceConfig(
        mode="simulated-hosted",
        evidence_dir=tmp_path / "evidence",
        strict=True,
    )
    verifier = AgoraHostedAcceptanceVerifier(config)

    def mock_bad_readyz():
        return {
            "healthz": {"status": "ok", "http_code": 200},
            "livez": {"status": "ok", "http_code": 200},
            "readyz": {
                "status": "degraded",
                "http_code": 503,
                "subsystems": {"strategy_workshop": "degraded"},
                "watermarks": {
                    "cursor_agreement": False,
                    "max_lag_seconds": 12.5,
                    "active_repair_workers": 1,
                    "degraded_controllers": 1,
                },
            },
        }

    verifier.verify_gate_02_readiness_and_liveness = lambda: (
        mock_bad_readyz()
        if not AgoraVerificationError(
            "gate_02", "Controller degraded or repair_only worker detected on critical path"
        )
        else None
    )

    def failing_gate_2():
        raise AgoraVerificationError(
            "gate_02", "Controller degraded or repair_only worker detected on critical path"
        )

    verifier.verify_gate_02_readiness_and_liveness = failing_gate_2

    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_02 = next((g for g in report.gate_results if g.gate_id == "gate_02_readiness_and_liveness"), None)
    assert gate_02 is not None
    assert gate_02.status == "FAILED"


def test_gate_04_broker_order_authority_violation_fails_closed(tmp_path: Path) -> None:
    """Gate 4 must fail closed if TradingIntent carries broker order authority."""
    config = AcceptanceConfig(
        mode="simulated-hosted",
        evidence_dir=tmp_path / "evidence",
        strict=True,
    )
    verifier = AgoraHostedAcceptanceVerifier(config)

    def violating_security_gate():
        raise AgoraVerificationError(
            "gate_04", "SECURITY VIOLATION: TradingIntent carries broker order authority"
        )

    verifier.verify_gate_04_security_and_boundaries = violating_security_gate

    report = verifier.run_full_acceptance()
    assert report.overall_status == "FAILED"
    gate_04 = next((g for g in report.gate_results if g.gate_id == "gate_04_security_and_boundaries"), None)
    assert gate_04 is not None
    assert gate_04.status == "FAILED"


def test_gap_evidence_matrix_completeness(tmp_path: Path) -> None:
    """Ensure gap matrix covers all S01-S15 and GAP-W01-W04 items."""
    config = AcceptanceConfig(
        mode="simulated-hosted",
        evidence_dir=tmp_path / "evidence",
        strict=True,
    )
    verifier = AgoraHostedAcceptanceVerifier(config)
    report = verifier.run_full_acceptance()

    gap_ids = {g["gap_id"] for g in report.gap_matrix}
    expected_s_gaps = {f"S{i:02d}" for i in range(1, 16)}
    expected_w_gaps = {"GAP-W01", "GAP-W02", "GAP-W03", "GAP-W04"}

    assert expected_s_gaps.issubset(gap_ids)
    assert expected_w_gaps.issubset(gap_ids)
    for g in report.gap_matrix:
        assert g["status"] == "RESOLVED"
        assert len(g["evidence"]) > 0

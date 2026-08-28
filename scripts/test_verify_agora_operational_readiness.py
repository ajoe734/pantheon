"""Pytest test suite for Agora Operational & Data Readiness Verifier (SD-AGC-06).

Tests positive verification stages and fail-closed behavior under simulated violations:
  - Full positive verification across all 7 stages
  - Negative control: missing envelope fails closed
  - Negative control: source/producer snapshot mismatch fails closed
  - Negative control: raw secret leak in config fails closed
  - Negative control: broker order authority claim fails closed
  - Negative control: stale vs empty_fresh vs unavailable conflation fails closed
  - Negative control: invalid mode rejection
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for subpath in [
    "",
    "services/control-plane",
    "services/control-plane/bff",
    "services/source_ingestion",
    "services/execution",
]:
    p = str(REPO_ROOT / subpath) if subpath else str(REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.verify_agora_operational_readiness import (
    AgoraOperationalReadinessVerificationError,
    AgoraOperationalReadinessVerifier,
    OperationalReadinessReport,
)


def test_invalid_mode_rejected() -> None:
    """Verifier must reject invalid execution modes."""
    with pytest.raises(ValueError, match="Unsupported mode 'invalid'"):
        AgoraOperationalReadinessVerifier(mode="invalid", strict=True)


def test_verifier_full_positive_run() -> None:
    """Ensure AgoraOperationalReadinessVerifier completes all 7 stages cleanly."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)
    report = verifier.run_all_stages()

    assert report.overall_status == "PASSED"
    assert report.summary["executed_stages"] == 7
    assert report.summary["passed_stages"] == 7
    assert report.summary["failed_stages"] == 0
    assert report.program_id == "AGORA-PRODUCT-CLOSURE-20260827"
    assert report.task_id == "AGORA-AGC-06-DATA-READINESS-BFF-20260827"
    assert "bound_snapshot_id" in report.lineage


def test_verifier_fails_on_missing_envelope() -> None:
    """Verifier must fail closed if operational readiness response lacks data/meta envelope."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)

    with patch.object(verifier, "_get_readiness_payload", return_value={"corrupted": True}):
        report = verifier.run_all_stages()
        assert report.overall_status == "FAILED"
        assert report.summary["failed_stages"] >= 1
        assert report.stages[0].stage_id == "stage_01_route_contract"
        assert report.stages[0].status == "FAILED"
        assert "missing top-level 'data' or 'meta'" in str(report.stages[0].error).lower()


def test_verifier_fails_on_source_producer_mismatch() -> None:
    """Verifier must fail closed if producer consumed_snapshot_id doesn't match source snapshot."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)

    orig_binding = verifier.verify_source_producer_binding

    def violating_binding():
        raise AgoraOperationalReadinessVerificationError(
            "source_producer_binding",
            "Producer consumed_snapshot_id mismatch: expected mss-1, got mss-2",
        )

    verifier.verify_source_producer_binding = violating_binding

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_2 = next((s for s in report.stages if s.stage_id == "stage_02_source_producer_binding"), None)
    assert stage_2 is not None
    assert stage_2.status == "FAILED"
    assert "mismatch" in str(stage_2.error).lower()


def test_verifier_fails_on_raw_secret_leak() -> None:
    """Verifier must fail closed if raw secret material is detected in config."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)

    def violating_recovery():
        raise AgoraOperationalReadinessVerificationError(
            "bounded_recovery_sequence",
            "VIOLATION: Raw inline secret detected in source config at 'api_key'",
        )

    verifier.verify_bounded_recovery_sequence = violating_recovery

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_4 = next((s for s in report.stages if s.stage_id == "stage_04_bounded_recovery_sequence"), None)
    assert stage_4 is not None
    assert stage_4.status == "FAILED"
    assert "raw inline secret" in str(stage_4.error).lower()


def test_verifier_fails_on_order_authority_claim() -> None:
    """Verifier must fail closed if readiness metadata asserts order route authority."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)

    def violating_invariants():
        raise AgoraOperationalReadinessVerificationError(
            "read_only_negative_invariants",
            "Unexpected no_order_route_proof: live_orders_enabled",
        )

    verifier.verify_read_only_negative_invariants = violating_invariants

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_7 = next((s for s in report.stages if s.stage_id == "stage_07_read_only_negative_invariants"), None)
    assert stage_7 is not None
    assert stage_7.status == "FAILED"
    assert "no_order_route_proof" in str(stage_7.error)


def test_verifier_distinguishes_empty_fresh_and_unavailable() -> None:
    """Verifier proves that empty_fresh and unavailable are distinct."""
    verifier = AgoraOperationalReadinessVerifier(mode="in-process", strict=True)
    res = verifier.verify_distinct_freshness_states()

    assert "stale" in res
    assert "empty_fresh" in res
    assert "unavailable" in res

    assert res["stale"]["source_freshness"] == "stale"
    assert res["empty_fresh"]["source_freshness"] == "empty_fresh"
    assert res["unavailable"]["source_freshness"] == "unavailable"

    # Surface statuses must also distinguish empty_fresh from unavailable
    assert res["empty_fresh"]["surface_status"] == "empty_fresh"
    assert res["unavailable"]["surface_status"] == "unavailable"

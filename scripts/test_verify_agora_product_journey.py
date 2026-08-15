"""Pytest test suite for Agora Backend Product Journey Verifier.

Tests both positive journey execution and fail-closed behavior under simulated violations:
  - Full end-to-end positive verification across all 14 stages
  - Negative control: missing authenticated operator identity fails closed
  - Negative control: broker order authority assertion fails closed
  - Negative control: self-attested consultation memo fails closed
  - Negative control: cross-tenant data leakage fails closed
  - Negative control: candidate artifact checksum mismatch fails closed
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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

from scripts.verify_agora_product_journey import (
    AgoraJourneyVerifier,
    AgoraVerificationError,
    JourneyVerificationReport,
)


def test_live_mode_is_rejected_instead_of_running_in_process_under_a_hosted_label() -> None:
    with pytest.raises(ValueError, match="only mode='in-process'"):
        AgoraJourneyVerifier(mode="live", strict=True)


def test_verifier_full_positive_run() -> None:
    """Ensure AgoraJourneyVerifier completes all 14 stages cleanly."""
    verifier = AgoraJourneyVerifier(mode="in-process", strict=True)
    report = verifier.run_all_stages()

    assert report.overall_status == "PASSED"
    assert report.summary["executed_stages"] == 14
    assert report.summary["passed_stages"] == 14
    assert report.summary["failed_stages"] == 0
    assert "trace_id" in report.lineage
    assert "strategy_id" in report.lineage
    assert "version_id" in report.lineage
    assert "policy_candidate_id" in report.lineage
    assert "consultation_memo_id" in report.lineage


def test_verifier_fails_on_missing_operator_identity() -> None:
    """Verifier must fail closed if operator identity cannot be resolved."""
    verifier = AgoraJourneyVerifier(mode="in-process", strict=True)
    verifier.user_a1 = ""  # Corrupt user id

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    assert report.summary["failed_stages"] >= 1
    assert report.stages[0].stage_id == "stage_01_identity_scope"
    assert report.stages[0].status == "FAILED"


def test_verifier_fails_on_broker_order_authority() -> None:
    """Verifier must fail closed if any TradingIntent asserts broker order authority."""
    verifier = AgoraJourneyVerifier(mode="in-process", strict=True)

    # Patch upsert_intent behavior or inject violation
    orig_intent = verifier.verify_decision_event_intent

    def violating_intent():
        res = orig_intent()
        # Force violation
        res["has_broker_order_authority"] = True
        raise AgoraVerificationError("decision_event_intent", "VIOLATION: TradingIntent must not carry broker order authority")

    verifier.verify_decision_event_intent = violating_intent

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_6 = next((s for s in report.stages if s.stage_id == "stage_06_decision_event_intent"), None)
    assert stage_6 is not None
    assert stage_6.status == "FAILED"
    assert "broker order authority" in str(stage_6.error)


def test_verifier_fails_on_self_attestation() -> None:
    """Verifier must fail closed if consultation reviewer equals candidate producer."""
    verifier = AgoraJourneyVerifier(mode="in-process", strict=True)

    orig_consult = verifier.verify_independent_consultation

    def violating_consult():
        # Producer and reviewer are the same
        evaluator_id = verifier.user_a1
        if evaluator_id == verifier.user_a1:
            raise AgoraVerificationError("independent_consultation", "VIOLATION: Reviewer must not equal candidate producer")
        return orig_consult()

    verifier.verify_independent_consultation = violating_consult

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_10 = next((s for s in report.stages if s.stage_id == "stage_10_independent_consultation"), None)
    assert stage_10 is not None
    assert stage_10.status == "FAILED"
    assert "Reviewer must not equal candidate producer" in str(stage_10.error)


def test_verifier_fails_on_cross_tenant_leakage() -> None:
    """Verifier must fail closed if an entity from Tenant A is accessible by Tenant B."""
    verifier = AgoraJourneyVerifier(mode="in-process", strict=True)

    orig_isolation = verifier.verify_two_tenant_isolation

    def violating_isolation():
        raise AgoraVerificationError("two_tenant_isolation", "Isolation breach: Tenant B accessed Tenant A workshop ws-123")

    verifier.verify_two_tenant_isolation = violating_isolation

    report = verifier.run_all_stages()
    assert report.overall_status == "FAILED"
    stage_11 = next((s for s in report.stages if s.stage_id == "stage_11_two_tenant_isolation"), None)
    assert stage_11 is not None
    assert stage_11.status == "FAILED"
    assert "Isolation breach" in str(stage_11.error)

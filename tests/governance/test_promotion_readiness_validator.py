"""Tests for EP5-002-V2: promotion readiness validator + blocking_reasons.

Covers:
- clean packet → no blocking reasons
- missing evidence (required key never provided)
- evidence with non-passing status (fail/expired/revoked)
- failing gate result
- required approval not recorded
- required approval expired
- required approval revoked
- unsafe flag enabled (fail-closed violation)
- conflicting can_proceed=true with derived blockers
- approval subtree status blocking
"""
from __future__ import annotations

import pytest

from services.governance.promotion_readiness.packet_model import (
    SCHEMA_VERSION,
    PromotionReadinessPacket,
)
from services.governance.promotion_readiness.validator import (
    CODE_APPROVAL_EXPIRED,
    CODE_APPROVAL_NOT_RECORDED,
    CODE_APPROVAL_REVOKED,
    CODE_APPROVAL_STATUS_BLOCKING,
    CODE_CONFLICTING_CAN_PROCEED,
    CODE_EVIDENCE_NOT_PASSING,
    CODE_GATE_NOT_PASSING,
    CODE_MISSING_EVIDENCE,
    CODE_UNSAFE_FLAG_ENABLED,
    is_ready,
    validate_readiness,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_packet(**overrides) -> dict:
    """Return a minimal valid structured packet dict."""
    packet = {
        "packet_id": "test-prp-001",
        "schema_version": SCHEMA_VERSION,
        "reason": "All checks passed",
        "target": {
            "target_type": "deployment",
            "target_id": "m7-canary-001",
            "environment": "canary",
            "deployment_stage": "canary",
        },
        "evidence": {
            "required": ["broker_smoke", "rollback_drill"],
            "provided": [
                {"key": "broker_smoke", "status": "passed", "path": "support/evidence/broker-smoke.json"},
                {"key": "rollback_drill", "status": "passed", "path": "support/evidence/rollback.json"},
            ],
            "missing": [],
            "gate_results": [
                {"gate": "broker_smoke", "status": "passed"},
                {"gate": "rollback_drill", "status": "passed"},
            ],
        },
        "approval": {
            "risk_owner": {
                "required": True,
                "recorded": True,
                "state": "recorded",
                "record_id": "ro-001",
                "actor_id": "risk-owner",
                "evidence_hash": "sha256:ro",
                "signed_at": "2026-05-19T10:00:00Z",
            },
            "operator": {
                "required": True,
                "recorded": True,
                "state": "recorded",
                "record_id": "op-001",
                "actor_id": "operator",
                "evidence_hash": "sha256:op",
                "signed_at": "2026-05-19T10:01:00Z",
            },
            "records": [],
            "status": "approved",
        },
        "flags": {
            "BROKER_PRODUCTION_LIVE_ENABLED": False,
            "CAPITAL_BINDING_LIVE_ENABLED": False,
            "live_capital_side_effects": False,
        },
        "blocking_reasons": [],
        "can_proceed": True,
    }
    packet.update(overrides)
    return packet


def _load(data: dict) -> PromotionReadinessPacket:
    return PromotionReadinessPacket.from_dict(data, validate=False)


def _codes(reasons) -> list[str]:
    return [r.code for r in reasons]


# ---------------------------------------------------------------------------
# 1. Clean packet — no blocking reasons
# ---------------------------------------------------------------------------

def test_clean_packet_no_blocking_reasons():
    packet = _load(_base_packet())
    reasons = validate_readiness(packet)
    assert reasons == [], f"Expected no blocking reasons, got: {reasons}"
    assert is_ready(packet)


# ---------------------------------------------------------------------------
# 2. Missing evidence — required key never supplied
# ---------------------------------------------------------------------------

def test_missing_required_evidence_key():
    data = _base_packet(can_proceed=False, reason="Missing evidence")
    data["evidence"]["required"] = ["broker_smoke", "rollback_drill", "canary_allocation"]
    # canary_allocation is NOT in provided
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_MISSING_EVIDENCE in codes
    missing_reasons = [r for r in reasons if r.code == CODE_MISSING_EVIDENCE]
    missing_keys = [r.details.get("missing_key") for r in missing_reasons]
    assert "canary_allocation" in missing_keys
    assert not is_ready(packet)


# ---------------------------------------------------------------------------
# 3. Evidence with non-passing status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_status", ["fail", "failed", "missing", "expired", "revoked", "pending", "incomplete"])
def test_evidence_non_passing_status(bad_status):
    data = _base_packet(can_proceed=False, reason="Evidence not passing")
    data["evidence"]["provided"][0]["status"] = bad_status  # broker_smoke
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_EVIDENCE_NOT_PASSING in codes
    ev_reason = next(r for r in reasons if r.code == CODE_EVIDENCE_NOT_PASSING)
    assert ev_reason.details["evidence_key"] == "broker_smoke"
    assert ev_reason.details["status"] == bad_status


# ---------------------------------------------------------------------------
# 4. Failing gate result
# ---------------------------------------------------------------------------

def test_gate_result_not_passing():
    data = _base_packet(can_proceed=False, reason="Gate failed")
    data["evidence"]["gate_results"][0]["status"] = "failed"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_GATE_NOT_PASSING in codes
    gate_reason = next(r for r in reasons if r.code == CODE_GATE_NOT_PASSING)
    assert gate_reason.details["gate"] == "broker_smoke"
    assert gate_reason.details["status"] == "failed"


# ---------------------------------------------------------------------------
# 5. Required approval not recorded
# ---------------------------------------------------------------------------

def test_required_approval_not_recorded():
    data = _base_packet(can_proceed=False, reason="Operator approval missing")
    data["approval"]["operator"]["recorded"] = False
    data["approval"]["operator"]["state"] = "pending"
    data["approval"]["status"] = "pending"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_APPROVAL_NOT_RECORDED in codes
    appr_reason = next(r for r in reasons if r.code == CODE_APPROVAL_NOT_RECORDED)
    assert appr_reason.details["role"] == "operator"


# ---------------------------------------------------------------------------
# 6. Required approval expired
# ---------------------------------------------------------------------------

def test_required_approval_expired():
    data = _base_packet(can_proceed=False, reason="Risk owner approval expired")
    data["approval"]["risk_owner"]["state"] = "expired"
    data["approval"]["risk_owner"]["expires_at"] = "2026-01-01T00:00:00Z"
    data["approval"]["status"] = "expired"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_APPROVAL_EXPIRED in codes
    exp_reason = next(r for r in reasons if r.code == CODE_APPROVAL_EXPIRED)
    assert exp_reason.details["role"] == "risk_owner"
    assert exp_reason.details["expires_at"] == "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 7. Required approval revoked
# ---------------------------------------------------------------------------

def test_required_approval_revoked():
    data = _base_packet(can_proceed=False, reason="Operator approval revoked")
    data["approval"]["operator"]["revoked_at"] = "2026-05-10T08:00:00Z"
    data["approval"]["status"] = "revoked"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_APPROVAL_REVOKED in codes
    rev_reason = next(r for r in reasons if r.code == CODE_APPROVAL_REVOKED)
    assert rev_reason.details["role"] == "operator"
    assert rev_reason.details["revoked_at"] == "2026-05-10T08:00:00Z"


# ---------------------------------------------------------------------------
# 8. Unsafe / fail-closed flag enabled
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag_key", [
    "BROKER_PRODUCTION_LIVE_ENABLED",
    "CAPITAL_BINDING_LIVE_ENABLED",
    "live_capital_side_effects",
])
def test_unsafe_flag_enabled(flag_key):
    data = _base_packet(can_proceed=False, reason="Unsafe flag")
    data["flags"][flag_key] = True
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_UNSAFE_FLAG_ENABLED in codes
    flag_reason = next(r for r in reasons if r.code == CODE_UNSAFE_FLAG_ENABLED)
    assert flag_reason.details["flag"] == flag_key


# ---------------------------------------------------------------------------
# 9. Conflicting can_proceed=true with derived blockers
# ---------------------------------------------------------------------------

def test_conflicting_can_proceed_with_missing_evidence():
    data = _base_packet(can_proceed=True, reason="Falsely claims ready")
    data["evidence"]["provided"][0]["status"] = "failed"  # broker_smoke failed
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    # Should surface both the evidence issue and the conflict
    assert CODE_EVIDENCE_NOT_PASSING in codes
    assert CODE_CONFLICTING_CAN_PROCEED in codes


def test_conflicting_can_proceed_with_existing_blocking_reasons():
    data = _base_packet(can_proceed=True, reason="Claims ready but has blockers")
    data["blocking_reasons"] = [{"code": "some_blocker", "message": "something is wrong"}]
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_CONFLICTING_CAN_PROCEED in codes


# ---------------------------------------------------------------------------
# 10. Approval subtree status blocking
# ---------------------------------------------------------------------------

def test_approval_subtree_status_rejected():
    data = _base_packet(can_proceed=False, reason="Approval rejected")
    data["approval"]["status"] = "rejected"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_APPROVAL_STATUS_BLOCKING in codes
    status_reason = next(r for r in reasons if r.code == CODE_APPROVAL_STATUS_BLOCKING)
    assert status_reason.details["approval_status"] == "rejected"


# ---------------------------------------------------------------------------
# 11. Multiple simultaneous blockers
# ---------------------------------------------------------------------------

def test_multiple_blockers_combined():
    """Missing evidence + unsafe flag + not-recorded approval all produce reasons."""
    data = _base_packet(can_proceed=False, reason="Multiple issues")
    data["evidence"]["required"].append("canary_metrics")
    data["flags"]["BROKER_PRODUCTION_LIVE_ENABLED"] = True
    data["approval"]["risk_owner"]["recorded"] = False
    data["approval"]["risk_owner"]["state"] = "pending"
    packet = _load(data)
    reasons = validate_readiness(packet)
    codes = _codes(reasons)
    assert CODE_MISSING_EVIDENCE in codes
    assert CODE_UNSAFE_FLAG_ENABLED in codes
    assert CODE_APPROVAL_NOT_RECORDED in codes
    assert len(reasons) >= 3

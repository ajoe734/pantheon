"""
M7-CANARY-CLOSEOUT validation tests.

Asserts:
1. promotion_readiness_packet.json conforms to PromotionReadinessPacket schema.
2. required_evidence includes the three mandatory keys.
3. provided_evidence references point to existing files.
4. can_proceed is false while approvals are not yet recorded.
5. BROKER_PRODUCTION_LIVE_ENABLED and CAPITAL_BINDING_LIVE_ENABLED remain false in env probe.
"""

import json
import os
import pathlib
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKET_PATH = REPO_ROOT / "support/evidence/M7-CANARY-CLOSEOUT/promotion_readiness_packet.json"

REQUIRED_EVIDENCE_KEYS = {
    "broker_sandbox_smoke_consumed",
    "shioaji_sandbox_evidence_packet_consumed",
    "canary_activation_gate_refs_present",
}

REQUIRED_PACKET_FIELDS = {
    "packet_id",
    "target_type",
    "target_id",
    "environment",
    "required_evidence",
    "provided_evidence",
    "missing_evidence",
    "gate_results",
    "risk_owner_required",
    "operator_required",
    "can_proceed",
    "reason",
}


@pytest.fixture(scope="module")
def packet():
    assert PACKET_PATH.exists(), f"Packet file missing: {PACKET_PATH}"
    with open(PACKET_PATH) as f:
        return json.load(f)


def test_packet_has_required_fields(packet):
    missing = REQUIRED_PACKET_FIELDS - set(packet.keys())
    assert not missing, f"Packet missing required fields: {missing}"


def test_target_type_is_deployment(packet):
    assert packet["target_type"] == "deployment", (
        f"Expected target_type=deployment, got {packet['target_type']!r}"
    )


def test_environment_is_canary(packet):
    assert packet["environment"] == "canary", (
        f"Expected environment=canary, got {packet['environment']!r}"
    )


def test_required_evidence_keys_present(packet):
    actual = set(packet["required_evidence"])
    missing = REQUIRED_EVIDENCE_KEYS - actual
    assert not missing, f"required_evidence missing keys: {missing}"


def test_provided_evidence_paths_exist(packet):
    missing_paths = []
    for entry in packet.get("provided_evidence", []):
        path = REPO_ROOT / entry["path"]
        if not path.exists():
            missing_paths.append(str(entry["path"]))
    assert not missing_paths, f"provided_evidence paths not found on disk: {missing_paths}"


def test_provided_evidence_covers_required_keys(packet):
    provided_keys = {e["key"] for e in packet.get("provided_evidence", [])}
    missing = REQUIRED_EVIDENCE_KEYS - provided_keys
    assert not missing, f"provided_evidence does not cover required keys: {missing}"


def test_missing_evidence_is_empty(packet):
    assert packet.get("missing_evidence") == [], (
        f"missing_evidence should be empty, got: {packet.get('missing_evidence')}"
    )


def test_can_proceed_is_false_while_approvals_pending(packet):
    assert packet["risk_owner_required"] is True, "risk_owner_required must be true"
    assert packet["operator_required"] is True, "operator_required must be true"
    assert packet.get("risk_owner_approval_recorded", True) is False, (
        "risk_owner_approval_recorded must be false in the template packet"
    )
    assert packet.get("operator_approval_recorded", True) is False, (
        "operator_approval_recorded must be false in the template packet"
    )
    assert packet["can_proceed"] is False, (
        "can_proceed must be false until both approvals are recorded"
    )


def test_fail_closed_flags_are_false_in_packet(packet):
    fail_closed = packet.get("fail_closed_assertions", {})
    assert fail_closed.get("BROKER_PRODUCTION_LIVE_ENABLED") is False, (
        "BROKER_PRODUCTION_LIVE_ENABLED must be false in packet fail_closed_assertions"
    )
    assert fail_closed.get("CAPITAL_BINDING_LIVE_ENABLED") is False, (
        "CAPITAL_BINDING_LIVE_ENABLED must be false in packet fail_closed_assertions"
    )


def test_live_broker_flags_absent_from_env():
    """Env probe: neither live-activation flag should be set to a truthy value."""
    broker_live = os.environ.get("BROKER_PRODUCTION_LIVE_ENABLED", "false").lower()
    capital_live = os.environ.get("CAPITAL_BINDING_LIVE_ENABLED", "false").lower()
    assert broker_live in ("false", "0", ""), (
        f"BROKER_PRODUCTION_LIVE_ENABLED is set to a live value: {broker_live!r}"
    )
    assert capital_live in ("false", "0", ""), (
        f"CAPITAL_BINDING_LIVE_ENABLED is set to a live value: {capital_live!r}"
    )


def test_gate_results_contain_required_gates(packet):
    gate_names = {g["gate"] for g in packet.get("gate_results", [])}
    required_gates = {
        "broker_sandbox_smoke",
        "shioaji_sandbox_evidence_packet",
        "canary_activation_gate_refs",
        "risk_owner_approval",
        "operator_approval",
    }
    missing = required_gates - gate_names
    assert not missing, f"gate_results missing gates: {missing}"

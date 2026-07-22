"""
Tests for services/evolution/postmortem_bridge.py

Scenarios
---------
1. low-severity skip           → on_postmortem_published returns None
2. high-severity rollback      → proposed_action == "rollback", cooldown == 72
3. critical-severity freeze    → proposed_action == "freeze", cooldown == 168
4. corrective-flag retrain     → proposed_action == "retrain", cooldown == 24
5. malformed-input fail-fast   → PostmortemBridgeError raised

pytest -q exit 0
"""
from __future__ import annotations

import pytest

from services.evolution.postmortem_bridge import (
    PostmortemBridgeError,
    build_published_postmortem_proposal_request,
    decision_id_for_published_postmortem,
    on_postmortem_published,
    postmortem_bridge_key,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_postmortem(**kwargs):
    """Return a minimal valid postmortem event dict, overridable via kwargs."""
    base = {
        "postmortem_id": "PM-001",
        "incident_id": "INC-001",
        "severity": "low",
        "artifact_id": "strat-alpha-v1",
        "artifact_version": "1.0.0",
        "deployment_stage": "paper",
        "corrective_action_required": False,
    }
    base.update(kwargs)
    return base


def _published_postmortem(**kwargs):
    base = {
        "postmortem_id": "PM-PUB-001",
        "title": "Published postmortem",
        "status": "published",
        "published_at": "2026-06-27T15:30:00Z",
        "incident_id": "INC-PUB-001",
        "binding_id": "binding-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-001",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "artifact_id": "artifact-001",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-001",
        "trace_id": "trace-001",
        "root_cause": "Threshold drift was not caught before deployment.",
        "action_items": [],
        "telemetry_event_ids": ["tel-001"],
        "reconciliation_ids": ["recon-001"],
        "incident_cluster_id": "cluster-drift-001",
    }
    base.update(kwargs)
    return base


def _incident(**kwargs):
    base = {
        "incident_id": "INC-PUB-001",
        "severity": "high",
        "binding_id": "binding-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-001",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "artifact_id": "artifact-001",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-001",
        "trace_id": "trace-001",
        "incident_cluster_id": "cluster-drift-001",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Scenario 1: low-severity skip
# ---------------------------------------------------------------------------

def test_low_severity_skip():
    pm = _base_postmortem(severity="low", corrective_action_required=False)
    result = on_postmortem_published(pm)
    assert result is None


def test_medium_severity_no_corrective_skip():
    pm = _base_postmortem(severity="medium", corrective_action_required=False)
    result = on_postmortem_published(pm)
    assert result is None


# ---------------------------------------------------------------------------
# Scenario 2: high-severity rollback
# ---------------------------------------------------------------------------

def test_high_severity_rollback():
    pm = _base_postmortem(severity="high", corrective_action_required=False)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "rollback"
    assert result["cooldown_window_hours"] == 72
    assert result["source_postmortem_id"] == "PM-001"
    assert result["source_incident_id"] == "INC-001"
    assert result["target_artifact_id"] == "strat-alpha-v1"
    assert result["target_artifact_version"] == "1.0.0"
    assert result["created_by_id"] == "postmortem-bridge"
    assert result["created_by_role"] == "evolution_controller"


def test_high_severity_evidence_refs_include_postmortem_and_incident():
    pm = _base_postmortem(severity="high")
    result = on_postmortem_published(pm)
    assert result is not None
    ref_types = [r["ref_type"] for r in result["evidence_refs"]]
    assert "postmortem" in ref_types
    assert "incident" in ref_types


def test_high_severity_overrides_corrective_flag():
    """When severity is high, rollback takes precedence over corrective retrain."""
    pm = _base_postmortem(severity="high", corrective_action_required=True)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "rollback"


# ---------------------------------------------------------------------------
# Scenario 3: critical-severity freeze
# ---------------------------------------------------------------------------

def test_critical_severity_freeze():
    pm = _base_postmortem(severity="critical", corrective_action_required=False)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "freeze"
    assert result["cooldown_window_hours"] == 168
    assert result["source_postmortem_id"] == "PM-001"
    assert result["created_by_role"] == "evolution_controller"


def test_critical_severity_overrides_corrective_flag():
    """When severity is critical, freeze takes precedence over corrective retrain."""
    pm = _base_postmortem(severity="critical", corrective_action_required=True)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "freeze"


# ---------------------------------------------------------------------------
# Scenario 4: corrective-flag retrain
# ---------------------------------------------------------------------------

def test_corrective_flag_retrain():
    pm = _base_postmortem(severity="low", corrective_action_required=True)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "retrain"
    assert result["cooldown_window_hours"] == 24


def test_corrective_flag_medium_severity_retrain():
    pm = _base_postmortem(severity="medium", corrective_action_required=True)
    result = on_postmortem_published(pm)
    assert result is not None
    assert result["proposed_action"] == "retrain"
    assert result["cooldown_window_hours"] == 24


def test_corrective_flag_forwards_upstream_evidence():
    upstream = [{"ref_type": "telemetry", "ref_id": "TEL-999"}]
    pm = _base_postmortem(
        severity="low",
        corrective_action_required=True,
        evidence_refs=upstream,
    )
    result = on_postmortem_published(pm)
    assert result is not None
    ref_ids = [r["ref_id"] for r in result["evidence_refs"]]
    assert "TEL-999" in ref_ids


# ---------------------------------------------------------------------------
# Scenario 5: malformed-input fail-fast
# ---------------------------------------------------------------------------

def test_malformed_not_a_dict():
    with pytest.raises(PostmortemBridgeError, match="must be a dict"):
        on_postmortem_published("not-a-dict")  # type: ignore[arg-type]


def test_malformed_none_input():
    with pytest.raises(PostmortemBridgeError):
        on_postmortem_published(None)  # type: ignore[arg-type]


def test_malformed_missing_postmortem_id():
    pm = _base_postmortem()
    del pm["postmortem_id"]
    with pytest.raises(PostmortemBridgeError, match="missing required fields"):
        on_postmortem_published(pm)


def test_malformed_missing_incident_id():
    pm = _base_postmortem()
    del pm["incident_id"]
    with pytest.raises(PostmortemBridgeError, match="missing required fields"):
        on_postmortem_published(pm)


def test_malformed_missing_severity():
    pm = _base_postmortem()
    del pm["severity"]
    with pytest.raises(PostmortemBridgeError, match="missing required fields"):
        on_postmortem_published(pm)


def test_malformed_invalid_severity():
    pm = _base_postmortem(severity="extreme")
    with pytest.raises(PostmortemBridgeError, match="Invalid severity"):
        on_postmortem_published(pm)


def test_malformed_missing_artifact_id():
    pm = _base_postmortem()
    del pm["artifact_id"]
    with pytest.raises(PostmortemBridgeError, match="missing required fields"):
        on_postmortem_published(pm)


def test_malformed_missing_artifact_version():
    pm = _base_postmortem()
    del pm["artifact_version"]
    with pytest.raises(PostmortemBridgeError, match="missing required fields"):
        on_postmortem_published(pm)


# ---------------------------------------------------------------------------
# No-mutation guard
# ---------------------------------------------------------------------------

def test_bridge_does_not_mutate_input():
    """Bridge must not modify the caller's dict."""
    pm = _base_postmortem(severity="high")
    original = dict(pm)
    on_postmortem_published(pm)
    assert pm == original


# ---------------------------------------------------------------------------
# Published postmortem admission builder
# ---------------------------------------------------------------------------

def test_published_postmortem_builder_creates_review_gated_request():
    pm = _published_postmortem()
    incident = _incident(severity="high")

    result = build_published_postmortem_proposal_request(pm, incident)

    assert result["decision_id"] == decision_id_for_published_postmortem(pm, incident)
    assert result["target_type"] == "candidate_artifact"
    assert result["target_id"] == "artifact-001"
    assert result["target_version"] == "1.0.0"
    assert result["action_type"] == "flag_for_review"
    assert result["linked_postmortem_id"] == "PM-PUB-001"
    assert result["linked_incident_id"] == "INC-PUB-001"
    assert result["metadata"]["postmortem_bridge_key"] == postmortem_bridge_key(pm, incident)
    assert result["metadata"]["review_gate_state"] == "awaiting_review"
    assert result["metadata"]["proposal_only"] is True
    assert result["metadata"]["broker_order_allowed"] is False
    assert result["metadata"]["bridge_legacy_proposed_action"] == "rollback"
    assert {ref["ref_id"] for ref in result["evidence_refs"]} >= {
        "PM-PUB-001",
        "INC-PUB-001",
        "tel-001",
        "recon-001",
    }


def test_published_postmortem_builder_is_keyed_by_target_and_cluster():
    incident = _incident()
    first_pm = _published_postmortem(postmortem_id="PM-PUB-001")
    duplicate_pm = _published_postmortem(postmortem_id="PM-PUB-002")

    assert postmortem_bridge_key(first_pm, incident) == postmortem_bridge_key(duplicate_pm, incident)
    assert decision_id_for_published_postmortem(first_pm, incident) == decision_id_for_published_postmortem(
        duplicate_pm,
        incident,
    )


def test_published_postmortem_builder_rejects_unpublished_postmortem():
    with pytest.raises(PostmortemBridgeError, match="must be published"):
        build_published_postmortem_proposal_request(
            _published_postmortem(status="approved", published_at=None),
            _incident(),
        )

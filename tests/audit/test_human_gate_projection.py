from __future__ import annotations

import copy

import pytest

from services.audit.projections.human_gate import (
    HUMAN_GATE_AUDIT_SCHEMA_VERSION,
    HumanGateAuditProjectionError,
    project_human_gate_expire,
    project_human_gate_revoke,
    project_human_gate_submit,
)
from services.foundation import ActorType, EnvironmentName, sha256_checksum
from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    compute_evidence_hash,
)


def _evidence() -> tuple[EvidenceReviewed, ...]:
    return (
        EvidenceReviewed(
            key="broker_sandbox_smoke",
            evidence_hash="sha256:broker-smoke",
            source_ref="support/evidence/MGMT-BROKER-002/summary.json",
            status="passed",
        ),
        EvidenceReviewed(
            key="rollback_drill",
            evidence_hash="sha256:rollback-drill",
            source_ref="support/evidence/EP5-007-V2/rollback-drill.json",
            status="passed",
        ),
    )


def _decision(*, status: str = "pending", target_environment: str = "canary") -> HumanGateDecision:
    evidence = _evidence()
    return HumanGateDecision(
        decision_id="hgd-ep5-canary-001",
        target_type="deployment",
        target_id="m7-canary-deployment",
        target_environment=target_environment,
        required_roles=("risk_owner", "operator"),
        evidence_reviewed=evidence,
        evidence_hash=compute_evidence_hash(evidence),
        can_proceed_input=CanProceedInput(
            readiness_packet_ref="prp-a2-1-canary-ready",
            readiness_packet_can_proceed=True,
            required_evidence=("broker_sandbox_smoke", "rollback_drill"),
        ),
        status=status,
    )


def _decision_payload(*, status: str = "pending", target_environment: str = "canary") -> dict:
    return _decision(status=status, target_environment=target_environment).to_dict()


def _state_ref(payload: dict) -> str:
    return f"HumanGateDecision:{payload['decision_id']}@sha256:{sha256_checksum(payload)}"


def test_project_submit_records_trace_correlation_metadata_and_payload_checksum() -> None:
    decision = _decision()

    action = project_human_gate_submit(
        decision,
        trace_id="trace-human-gate-001",
        correlation_id="corr-human-gate-001",
        actor_ref={
            "actor_type": "user",
            "actor_id": "risk-owner-1",
            "roles": ("risk_owner",),
        },
    )

    after_payload = decision.to_dict()
    expected_payload = {
        "schema_version": HUMAN_GATE_AUDIT_SCHEMA_VERSION,
        "change_type": "submit",
        "decision_id": decision.decision_id,
        "target": {
            "type": decision.target_type,
            "id": decision.target_id,
            "environment": decision.target_environment,
        },
        "before": None,
        "after": after_payload,
    }

    assert action.action_type == "human_gate_decision_submitted"
    assert action.target_ref == "HumanGateDecision:hgd-ep5-canary-001"
    assert action.trace_id == "trace-human-gate-001"
    assert action.correlation_id == "corr-human-gate-001"
    assert action.actor_ref.actor_type == ActorType.USER
    assert action.actor_ref.actor_id == "risk-owner-1"
    assert action.environment.name == EnvironmentName.CANARY
    assert action.before_state_ref is None
    assert action.after_state_ref == _state_ref(after_payload)
    assert action.payload_checksum == sha256_checksum(expected_payload)
    assert action.metadata == {
        "projection_schema_version": HUMAN_GATE_AUDIT_SCHEMA_VERSION,
        "source_schema_version": "HumanGateDecision.v1",
        "source": "human_gate_decision",
        "change_type": "submit",
        "decision_id": "hgd-ep5-canary-001",
        "target_type": "deployment",
        "target_id": "m7-canary-deployment",
        "target_environment": "canary",
        "before_status": None,
        "after_status": "pending",
    }


def test_project_revoke_records_before_and_after_state_refs() -> None:
    before_payload = _decision_payload(status="approved")
    after_payload = copy.deepcopy(before_payload)
    after_payload["status"] = "revoked"
    after_payload["reason"] = "risk owner revoked human gate approval"

    action = project_human_gate_revoke(
        before_payload,
        after_payload,
        trace_id="trace-human-gate-revoke",
        correlation_id="corr-human-gate-revoke",
    )

    assert action.action_type == "human_gate_decision_revoked"
    assert action.reason == "risk owner revoked human gate approval"
    assert action.before_state_ref == _state_ref(before_payload)
    assert action.after_state_ref == _state_ref(after_payload)
    assert action.metadata["before_status"] == "approved"
    assert action.metadata["after_status"] == "revoked"


def test_project_expire_accepts_mapping_state_and_aliases_production_environment() -> None:
    before_payload = _decision_payload(status="pending", target_environment="production")
    after_payload = copy.deepcopy(before_payload)
    after_payload["status"] = "expired"
    after_payload["reason"] = "human gate expired before operator signoff"

    action = project_human_gate_expire(
        before_payload,
        after_payload,
        trace_id="trace-human-gate-expire",
        correlation_id="corr-human-gate-expire",
    )

    assert action.action_type == "human_gate_decision_expired"
    assert action.reason == "human gate expired before operator signoff"
    assert action.environment.name == EnvironmentName.LIVE
    assert action.metadata["target_environment"] == "production"
    assert action.metadata["after_status"] == "expired"


def test_project_revoke_fails_closed_when_after_state_is_not_revoked() -> None:
    before_payload = _decision_payload(status="pending")
    after_payload = copy.deepcopy(before_payload)
    after_payload["status"] = "approved"

    with pytest.raises(
        HumanGateAuditProjectionError,
        match="revoke projection requires after.status=revoked",
    ):
        project_human_gate_revoke(
            before_payload,
            after_payload,
            trace_id="trace-human-gate-bad-revoke",
            correlation_id="corr-human-gate-bad-revoke",
        )

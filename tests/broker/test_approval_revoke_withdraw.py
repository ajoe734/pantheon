from __future__ import annotations

import pytest

from services.broker.live_activation.approval_revoke_withdraw import (
    ACTION_REVOKE,
    ACTION_WITHDRAW,
    APPLIED,
    BLOCKED,
    ApprovalLifecycleModelError,
    apply_approval_lifecycle_request,
    apply_approval_lifecycle_request_or_raise,
    revoke_approval,
    withdraw_approval,
)
from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateSignature,
    compute_evidence_hash,
    validate_decision,
)


def test_revoke_risk_owner_approval_uses_human_gate_signature_lifecycle() -> None:
    decision = _approved_live_decision()

    result = revoke_approval(
        decision,
        actor_role="risk_owner",
        actor_id="risk-owner-1",
        reason="broker-live evidence was superseded",
        revoked_at="2026-05-20T02:00:00Z",
        source_ref="support/evidence/BLA-008-V2/revoke.json",
    )

    assert result.status == APPLIED
    assert result.applied is True
    assert result.action == ACTION_REVOKE
    assert result.before_status == "approved"
    assert result.after_status == "pending"
    assert result.after_can_proceed is False
    assert result.signature_id == "sig-risk-live-001"

    updated = result.updated_decision
    assert updated is not None
    assert updated.schema_version == "HumanGateDecision.v1"
    assert updated.decision_id == decision.decision_id
    assert updated.status == "pending"
    assert updated.can_proceed is False
    revoked_signature = next(
        signature
        for signature in updated.signatures
        if signature.signature_id == "sig-risk-live-001"
    )
    assert revoked_signature.revoked_at == "2026-05-20T02:00:00Z"
    assert decision.status == "approved"
    assert decision.can_proceed is True
    assert result.to_dict()["updated_decision"]["schema_version"] == "HumanGateDecision.v1"


def test_withdraw_pending_operator_gate_sets_terminal_human_gate_state() -> None:
    decision = _pending_live_decision()

    result = withdraw_approval(
        decision,
        actor_role="operator",
        actor_id="operator-1",
        reason="operator withdrew before live activation window",
        withdrawn_at="2026-05-20T02:05:00Z",
    )

    assert result.status == APPLIED
    assert result.applied is True
    assert result.action == ACTION_WITHDRAW
    assert result.target_role == "operator"
    assert result.before_status == "pending"
    assert result.after_status == "withdrawn"

    updated = result.updated_decision
    assert updated is not None
    assert updated.status == "withdrawn"
    assert updated.can_proceed is False
    assert updated.reason == "operator withdrew before live activation window"
    assert updated.updated_at == "2026-05-20T02:05:00Z"


def test_mapping_request_round_trips_human_gate_decision_schema() -> None:
    decision = _approved_live_decision()

    result = apply_approval_lifecycle_request(
        decision.to_dict(),
        {
            "action": "revoke",
            "actor_role": "operator",
            "actor_id": "operator-1",
            "reason": "operator revoked broker-live approval",
            "signature_id": "sig-operator-live-001",
            "occurred_at": "2026-05-20T02:10:00Z",
        },
    )

    assert result.applied is True
    payload = result.to_dict()["updated_decision"]
    assert payload["schema_version"] == "HumanGateDecision.v1"
    assert payload["status"] == "pending"
    assert payload["can_proceed"] is False
    assert payload["signatures"][1]["revoked_at"] == "2026-05-20T02:10:00Z"


def test_cross_role_revoke_fails_closed_without_mutating_decision() -> None:
    decision = _approved_live_decision()

    result = revoke_approval(
        decision,
        actor_role="operator",
        actor_id="operator-1",
        target_role="risk_owner",
        signature_id="sig-risk-live-001",
        reason="operator attempted to revoke risk-owner approval",
    )

    assert result.status == BLOCKED
    assert result.applied is False
    assert result.after_status == "approved"
    assert result.after_can_proceed is True
    assert result.updated_decision is decision
    assert result.issues[0].code == "unauthorized_role_scope"
    assert "same role" in result.blocking_reasons[0]
    assert all(signature.revoked_at is None for signature in decision.signatures)


def test_approved_gate_withdraw_fails_closed_and_requires_revoke() -> None:
    decision = _approved_live_decision()

    result = withdraw_approval(
        decision,
        actor_role="operator",
        actor_id="operator-1",
        reason="operator tried to withdraw an approved gate",
    )

    assert result.status == BLOCKED
    assert result.applied is False
    assert result.before_status == "approved"
    assert result.after_status == "approved"
    assert result.issues[0].code == "approved_decision_requires_revoke"
    with pytest.raises(ApprovalLifecycleModelError, match="must be revoked"):
        apply_approval_lifecycle_request_or_raise(
            decision,
            {
                "action": "withdraw",
                "actor_role": "operator",
                "actor_id": "operator-1",
                "reason": "operator tried to withdraw an approved gate",
            },
        )


def test_non_live_human_gate_decision_fails_closed() -> None:
    decision = _pending_live_decision(target_environment="canary")

    result = withdraw_approval(
        decision,
        actor_role="risk_owner",
        actor_id="risk-owner-1",
        reason="canary gate is outside broker-live activation scope",
    )

    assert result.status == BLOCKED
    assert result.applied is False
    assert result.issues[0].code == "non_live_human_gate_decision"
    assert result.updated_decision is decision


def _evidence() -> tuple[EvidenceReviewed, ...]:
    return (
        EvidenceReviewed(
            key="broker_sandbox_smoke",
            evidence_hash="sha256:broker-smoke-live",
            source_ref="support/evidence/BLA-008-V2/broker-smoke.json",
            status="passed",
        ),
        EvidenceReviewed(
            key="rollback_drill",
            evidence_hash="sha256:rollback-drill-live",
            source_ref="support/evidence/BLA-008-V2/rollback-drill.json",
            status="passed",
        ),
    )


def _pending_live_decision(target_environment: str = "live") -> HumanGateDecision:
    evidence = _evidence()
    return validate_decision(
        HumanGateDecision(
            decision_id="hgd-broker-live-001",
            target_type="deployment",
            target_id="broker-live-shioaji-001",
            target_environment=target_environment,
            required_roles=("risk_owner", "operator"),
            evidence_reviewed=evidence,
            evidence_hash=compute_evidence_hash(evidence),
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="readiness://broker-live/BLA-001-V2",
                readiness_packet_can_proceed=True,
                required_evidence=("broker_sandbox_smoke", "rollback_drill"),
            ),
            created_at="2026-05-20T01:00:00Z",
            updated_at="2026-05-20T01:00:00Z",
        )
    )


def _approved_live_decision() -> HumanGateDecision:
    evidence = _evidence()
    evidence_hash = compute_evidence_hash(evidence)
    reviewed = tuple(item.key for item in evidence)
    return validate_decision(
        HumanGateDecision(
            decision_id="hgd-broker-live-001",
            target_type="deployment",
            target_id="broker-live-shioaji-001",
            target_environment="live",
            required_roles=("risk_owner", "operator"),
            evidence_reviewed=evidence,
            evidence_hash=evidence_hash,
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="readiness://broker-live/BLA-001-V2",
                readiness_packet_can_proceed=True,
                required_evidence=("broker_sandbox_smoke", "rollback_drill"),
            ),
            signatures=(
                HumanGateSignature(
                    signature_id="sig-risk-live-001",
                    role="risk_owner",
                    actor_id="risk-owner-1",
                    signed_at="2026-05-20T01:15:00Z",
                    evidence_hash=evidence_hash,
                    evidence_reviewed=reviewed,
                ),
                HumanGateSignature(
                    signature_id="sig-operator-live-001",
                    role="operator",
                    actor_id="operator-1",
                    signed_at="2026-05-20T01:16:00Z",
                    evidence_hash=evidence_hash,
                    evidence_reviewed=reviewed,
                ),
            ),
            created_at="2026-05-20T01:00:00Z",
            updated_at="2026-05-20T01:16:00Z",
        )
    )

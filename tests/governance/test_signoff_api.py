from __future__ import annotations

import pytest

from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateDecisionError,
    compute_evidence_hash,
)
from services.governance.promotion_readiness.signoff_api import (
    HumanGateDecisionStore,
    SignoffAPI,
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


def _decision(*, readiness_can_proceed: bool = True) -> HumanGateDecision:
    evidence = _evidence()
    return HumanGateDecision(
        decision_id="hgd-ep5-canary-001",
        target_type="deployment",
        target_id="m7-canary-deployment",
        target_environment="canary",
        required_roles=("risk_owner", "operator"),
        evidence_reviewed=evidence,
        evidence_hash=compute_evidence_hash(evidence),
        can_proceed_input=CanProceedInput(
            readiness_packet_ref="prp-a2-1-canary-ready",
            readiness_packet_can_proceed=readiness_can_proceed,
            required_evidence=("broker_sandbox_smoke", "rollback_drill"),
            blocking_reasons=(() if readiness_can_proceed else ("rollback_drill_pending",)),
        ),
    )


def test_signoff_api_records_two_role_human_gate_and_allows_proceed():
    api = SignoffAPI(HumanGateDecisionStore())

    created = api.create_decision(_decision())

    assert created.status == "pending"
    assert created.can_proceed is False
    assert created.missing_required_roles() == ("risk_owner", "operator")

    risk_signed = api.append_signature(
        created.decision_id,
        {
            "signature_id": "sig-risk-owner-001",
            "role": "risk_owner",
            "actor_id": "risk-owner-1",
            "signed_at": "2026-05-19T16:20:00Z",
        },
    )

    assert risk_signed.can_proceed is False
    assert risk_signed.missing_required_roles() == ("operator",)

    operator_signed = api.append_signature(
        created.decision_id,
        {
            "signature_id": "sig-operator-001",
            "role": "operator",
            "actor_id": "operator-1",
            "signed_at": "2026-05-19T16:21:00Z",
        },
    )

    assert operator_signed.status == "approved"
    assert operator_signed.can_proceed is True
    assert operator_signed.evidence_hash == compute_evidence_hash(_evidence())
    assert {item["role"] for item in operator_signed.to_dict()["signatures"]} == {
        "risk_owner",
        "operator",
    }


def test_append_signature_rejects_evidence_hash_mismatch():
    api = SignoffAPI(HumanGateDecisionStore())
    created = api.create_decision(_decision())

    with pytest.raises(HumanGateDecisionError, match="evidence_hash"):
        api.append_signature(
            created.decision_id,
            {
                "signature_id": "sig-risk-owner-bad",
                "role": "risk_owner",
                "actor_id": "risk-owner-1",
                "signed_at": "2026-05-19T16:20:00Z",
                "evidence_hash": "sha256:not-the-reviewed-bundle",
            },
        )


def test_readiness_blocker_keeps_decision_fail_closed_after_signatures():
    api = SignoffAPI(HumanGateDecisionStore())
    created = api.create_decision(_decision(readiness_can_proceed=False))

    api.append_signature(
        created.decision_id,
        {
            "signature_id": "sig-risk-owner-001",
            "role": "risk_owner",
            "actor_id": "risk-owner-1",
            "signed_at": "2026-05-19T16:20:00Z",
        },
    )
    signed = api.append_signature(
        created.decision_id,
        {
            "signature_id": "sig-operator-001",
            "role": "operator",
            "actor_id": "operator-1",
            "signed_at": "2026-05-19T16:21:00Z",
        },
    )

    assert signed.status == "blocked"
    assert signed.can_proceed is False
    assert "blocking_reason:rollback_drill_pending" in signed.blocking_summary()


def test_create_decision_rejects_unbound_evidence_hash():
    evidence = _evidence()
    bad = HumanGateDecision(
        decision_id="hgd-bad-hash",
        target_type="deployment",
        target_id="m7-canary-deployment",
        target_environment="canary",
        required_roles=("risk_owner",),
        evidence_reviewed=evidence,
        evidence_hash="sha256:not-the-computed-bundle",
        can_proceed_input=CanProceedInput(
            readiness_packet_ref="prp-a2-1-canary-ready",
            readiness_packet_can_proceed=True,
            required_evidence=("broker_sandbox_smoke",),
        ),
    )

    with pytest.raises(HumanGateDecisionError, match="evidence_hash"):
        SignoffAPI(HumanGateDecisionStore()).create_decision(bad)

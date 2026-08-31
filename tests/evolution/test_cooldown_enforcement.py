from __future__ import annotations

import pytest

from services.evolution.cooldown_enforcement import (
    COOLDOWN_REJECTION_ACTION_TYPE,
    COOLDOWN_OVERRIDE_SCOPE,
    DEFAULT_TENANT_ID,
    CooldownEnforcementError,
    EvolutionProposalCooldownEnforcer,
    enforce_proposal_cooldown,
    proposal_artifact_key,
    record_emitted_proposal,
)
from services.governance.human_gate.decision_model import (
    CanProceedInput,
    EvidenceReviewed,
    HumanGateDecision,
    HumanGateSignature,
    compute_evidence_hash,
    stable_hash,
    validate_decision,
)


def _proposal(**overrides):
    payload = {
        "source_postmortem_id": "pm-tel-hard-001",
        "source_incident_id": "inc-tel-hard-001",
        "proposed_action": "rollback",
        "cooldown_window_hours": 72,
        "target_artifact_id": "artifact-alpha",
        "target_artifact_version": "v1",
        "target_deployment_stage": "canary",
        "created_by_id": "postmortem-bridge",
        "created_by_role": "evolution_controller",
        "rationale": "High-severity incident triggered rollback proposal.",
    }
    payload.update(overrides)
    return payload


def _approved_human_gate(
    *,
    target_id: str = "artifact-alpha",
    override_scope: str = COOLDOWN_OVERRIDE_SCOPE,
    readiness_can_proceed: bool = True,
    signatures: bool = True,
) -> HumanGateDecision:
    evidence = (
        EvidenceReviewed(
            key="cooldown_rejection_audit",
            evidence_hash=stable_hash({"audit": "cooldown-rejection"}),
            source_ref="AuditAction:evolution_proposal_cooldown_rejected",
            status="passed",
        ),
    )
    evidence_hash = compute_evidence_hash(evidence)
    signature_items = (
        HumanGateSignature(
            signature_id="sig-risk-owner-001",
            role="risk_owner",
            actor_id="risk-owner",
            signed_at="2026-05-20T15:01:00Z",
            evidence_hash=evidence_hash,
            evidence_reviewed=("cooldown_rejection_audit",),
        ),
        HumanGateSignature(
            signature_id="sig-operator-001",
            role="operator",
            actor_id="operator",
            signed_at="2026-05-20T15:02:00Z",
            evidence_hash=evidence_hash,
            evidence_reviewed=("cooldown_rejection_audit",),
        ),
    ) if signatures else ()
    return validate_decision(
        HumanGateDecision(
            decision_id="hgd-cooldown-override-001",
            target_type="evolution_cooldown_override",
            target_id=target_id,
            target_environment="canary",
            required_roles=("risk_owner", "operator"),
            evidence_reviewed=evidence,
            evidence_hash=evidence_hash,
            can_proceed_input=CanProceedInput(
                readiness_packet_ref="support/evidence/TEL-HARD-002-V2/cooldown-override.json",
                readiness_packet_can_proceed=readiness_can_proceed,
                required_evidence=("cooldown_rejection_audit",),
            ),
            signatures=signature_items,
            created_at="2026-05-20T15:00:00Z",
            metadata={"override_scope": override_scope},
        )
    )


def test_first_proposal_is_accepted_and_records_policy_cooldown():
    result = enforce_proposal_cooldown(
        _proposal(),
        [],
        emitted_at="2026-05-20T15:00:00Z",
    )

    assert result.accepted is True
    assert result.rejection is None
    assert result.record is not None
    assert result.record.artifact.artifact_id == "artifact-alpha"
    assert result.record.cooldown_window_hours == 72
    assert result.record.cooldown_ends_at == "2026-05-23T15:00:00Z"
    assert any("MGMT-EVO-007" in ref for ref in result.record.policy_refs)


def test_same_artifact_repeated_proposal_is_rejected_with_audit_evidence():
    first = record_emitted_proposal(
        _proposal(),
        emitted_at="2026-05-20T15:00:00Z",
    )

    result = enforce_proposal_cooldown(
        _proposal(source_postmortem_id="pm-tel-hard-002"),
        [first],
        emitted_at="2026-05-20T16:00:00Z",
        trace_id="trace-cooldown-reject",
        correlation_id="corr-cooldown-reject",
    )

    assert result.accepted is False
    assert result.record is None
    assert result.rejection is not None
    assert result.rejection.blocking_record.proposal_id == first.proposal_id
    assert "HumanGateDecision" in result.rejection.reason
    audit = result.rejection.audit_action
    assert audit.action_type == COOLDOWN_REJECTION_ACTION_TYPE
    # target_ref is tenant-prefixed (ProposalArtifactKey.target_ref); pinned
    # via DEFAULT_TENANT_ID rather than a literal so this doesn't drift again
    # if the default tenant id ever changes.
    assert audit.target_ref == f"{DEFAULT_TENANT_ID}/candidate_artifact:artifact-alpha@v1"
    assert audit.trace_id == "trace-cooldown-reject"
    assert audit.correlation_id == "corr-cooldown-reject"
    assert audit.metadata["blocking_proposal_id"] == first.proposal_id
    assert result.rejection.audit_payload["override_required_schema"] == "HumanGateDecision.v1"


def test_approved_human_gate_allows_cooldown_override_and_records_ref():
    first = record_emitted_proposal(
        _proposal(),
        emitted_at="2026-05-20T15:00:00Z",
    )
    override = _approved_human_gate()

    result = enforce_proposal_cooldown(
        _proposal(source_postmortem_id="pm-tel-hard-override"),
        [first],
        emitted_at="2026-05-20T16:00:00Z",
        override_decision=override.to_dict(),
    )

    assert result.accepted is True
    assert result.rejection is None
    assert result.record is not None
    assert result.record.override_decision_ref == f"HumanGateDecision:{override.decision_id}@{override.evidence_hash}"
    assert result.override_decision is not None
    assert result.override_decision.status == "approved"


def test_human_gate_override_requires_explicit_cooldown_scope():
    first = record_emitted_proposal(
        _proposal(),
        emitted_at="2026-05-20T15:00:00Z",
    )
    unrelated_approval = _approved_human_gate().to_dict()
    unrelated_approval.pop("override_scope")

    with pytest.raises(CooldownEnforcementError, match="override_scope must be"):
        enforce_proposal_cooldown(
            _proposal(source_postmortem_id="pm-tel-hard-unrelated-approval"),
            [first],
            emitted_at="2026-05-20T16:00:00Z",
            override_decision=unrelated_approval,
        )


def test_human_gate_override_must_be_approved_and_target_same_artifact():
    first = record_emitted_proposal(
        _proposal(),
        emitted_at="2026-05-20T15:00:00Z",
    )
    pending_override = _approved_human_gate(signatures=False)

    with pytest.raises(CooldownEnforcementError, match="approved and can_proceed=true"):
        enforce_proposal_cooldown(
            _proposal(source_postmortem_id="pm-tel-hard-pending"),
            [first],
            emitted_at="2026-05-20T16:00:00Z",
            override_decision=pending_override,
        )

    wrong_target_override = _approved_human_gate(target_id="artifact-other")
    with pytest.raises(CooldownEnforcementError, match="target_id must match"):
        enforce_proposal_cooldown(
            _proposal(source_postmortem_id="pm-tel-hard-wrong-target"),
            [first],
            emitted_at="2026-05-20T16:00:00Z",
            override_decision=wrong_target_override,
        )


def test_enforcer_keeps_history_and_allows_after_cooldown_elapsed():
    enforcer = EvolutionProposalCooldownEnforcer()
    first = enforcer.admit(_proposal(), emitted_at="2026-05-20T15:00:00Z")
    assert first.accepted is True
    assert len(enforcer.records) == 1

    rejected = enforcer.admit(
        _proposal(source_postmortem_id="pm-tel-hard-blocked"),
        emitted_at="2026-05-20T15:30:00Z",
    )
    assert rejected.accepted is False
    assert len(enforcer.records) == 1

    accepted = enforcer.admit(
        _proposal(source_postmortem_id="pm-tel-hard-after-window"),
        emitted_at="2026-05-23T15:00:00Z",
    )
    assert accepted.accepted is True
    assert len(enforcer.records) == 2


def test_same_artifact_version_matching_is_fail_closed_when_version_missing():
    first = record_emitted_proposal(
        _proposal(target_artifact_version=None),
        emitted_at="2026-05-20T15:00:00Z",
    )

    result = enforce_proposal_cooldown(
        _proposal(target_artifact_version="v2", source_postmortem_id="pm-tel-hard-v2"),
        [first],
        emitted_at="2026-05-20T16:00:00Z",
    )

    assert result.accepted is False
    assert result.rejection is not None
    assert result.rejection.blocking_record.artifact.artifact_version is None


def test_proposal_artifact_key_accepts_governance_proposal_shape():
    artifact = proposal_artifact_key(
        {
            "decision_id": "evolution-proposal-from-sponsor-001",
            "target_type": "candidate_artifact",
            "target_id": "artifact-sponsor",
            "target_version": "v7",
            "action_type": "retrain",
        }
    )

    assert artifact.target_ref == f"{DEFAULT_TENANT_ID}/candidate_artifact:artifact-sponsor@v7"

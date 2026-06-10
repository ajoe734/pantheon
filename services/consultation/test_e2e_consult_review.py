from __future__ import annotations

import sys
from pathlib import Path

_GOVERNANCE_DIR = str(
    Path(__file__).resolve().parents[2] / "services" / "control-plane" / "governance"
)
if _GOVERNANCE_DIR not in sys.path:
    sys.path.insert(0, _GOVERNANCE_DIR)

import pytest

from approval_decision import (
    ActorRole,
    ApprovalDecision,
    ConsultationGateError,
    DecisionOutcome,
    EvidenceRef,
    EvidenceRefType,
    RiskLevel,
    TargetType,
    consultation_gate_required,
    validate_consultation_gate,
    CONSULTATION_HANDOFF_MAX_AGE_SECONDS,
)
from services.consultation.sponsor_decision_bridge import (
    SponsorDecisionBridgeError,
    bridge,
)

from .e2e_fixtures import consult_review_e2e


def test_ask_006_consult_committee_memo_reaches_management_review_handoff() -> None:
    correlation_id = "corr-ask006-consult-review"
    ask_session_id = "ask-006-root-session"
    committee_session_id = "committee-ask006-review"
    memo_id = "memo-ask006-management-review"

    with consult_review_e2e() as e2e:
        ask_session = e2e.create_ask_session(
            session_id=ask_session_id,
            correlation_id=correlation_id,
        )
        assert ask_session["sessionId"] == ask_session_id
        assert ask_session["mode"] == "quick_ask"

        committee = e2e.invoke_committee(
            session_id=committee_session_id,
            linked_request_id=ask_session_id,
        )
        assert committee["sessionId"] == committee_session_id
        assert committee["status"] == "open"
        assert committee["linkedRequestId"] == ask_session_id

        draft_memo = e2e.submit_committee_memo(
            session_id=committee_session_id,
            memo_id=memo_id,
            linked_request_id=ask_session_id,
            correlation_id=correlation_id,
        )
        assert draft_memo["memo_id"] == memo_id
        assert draft_memo["status"] == "draft"

        published_memo = e2e.publish_committee_memo(
            session_id=committee_session_id,
            memo_id=memo_id,
            correlation_id=correlation_id,
        )
        assert published_memo["memo_id"] == memo_id
        assert published_memo["status"] == "published"

        memo_events = [
            event for event in e2e.ask_events() if event["type"] == "consult_memo_published"
        ]
        assert len(memo_events) == 1
        assert memo_events[0]["data"]["correlation_id"] == correlation_id
        assert memo_events[0]["data"]["memo_id"] == memo_id
        assert memo_events[0]["data"]["session_id"] == committee_session_id

        handoff_id = memo_events[0]["data"]["handoff_id"]
        handoffs = e2e.get_json(
            "/bff/agora/handoffs?handoffType=consult_memo_to_management_review"
        )
        assert handoffs["page_info"]["total"] == 1
        handoff = handoffs["items"][0]
        assert handoff["handoffId"] == handoff_id
        assert handoff["handoffType"] == "consult_memo_to_management_review"
        assert handoff["destination"]["app"] == "management"
        assert handoff["destination"]["queue"] == "consult_memo_review"
        assert handoff["payload"]["memoId"] == memo_id
        assert handoff["payload"]["sessionId"] == committee_session_id
        assert handoff["payload"]["correlationId"] == correlation_id


# ---------------------------------------------------------------------------
# Consultation gate tests for allocation_policy approvals
# ---------------------------------------------------------------------------


def _consultation_evidence(
    memo_id: str = "memo-alloc-001",
    handoff_id: str = "gh-alloc-001",
    issued_at: str | None = None,
) -> list[EvidenceRef]:
    storage_ref = {"issued_at": issued_at} if issued_at else None
    return [
        EvidenceRef(ref_type=EvidenceRefType.COMMITTEE_MEMO, ref_id=memo_id),
        EvidenceRef(
            ref_type=EvidenceRefType.SERVICE_HANDOFF,
            ref_id=handoff_id,
            storage_ref=storage_ref,
        ),
    ]


def _high_risk_allocation_decision(decision_id: str = "dec-alloc-hr-001") -> ApprovalDecision:
    d = ApprovalDecision.create_proposed(
        decision_id=decision_id,
        target_type=TargetType.ALLOCATION_POLICY,
        target_id="artifact-alloc-001",
        target_version="1.0.0",
        risk_level=RiskLevel.HIGH,
        capital_pool_id="pool-alpha",
        persona_id="persona-momentum",
    )
    d.accept_review(ActorRole.GOVERNANCE_COMMITTEE, "committee-bot-001")
    return d


def test_consultation_gate_required_for_high_risk_allocation_policy() -> None:
    assert consultation_gate_required(TargetType.ALLOCATION_POLICY, RiskLevel.HIGH) is True
    assert consultation_gate_required(TargetType.ALLOCATION_POLICY, RiskLevel.CRITICAL) is True


def test_consultation_gate_not_required_for_low_risk() -> None:
    assert consultation_gate_required(TargetType.ALLOCATION_POLICY, RiskLevel.LOW) is False
    assert consultation_gate_required(TargetType.ALLOCATION_POLICY, RiskLevel.MEDIUM) is False
    assert consultation_gate_required(TargetType.STRATEGY_SPEC, RiskLevel.HIGH) is False


def test_allocation_policy_high_risk_approve_with_consultation_evidence() -> None:
    d = _high_risk_allocation_decision("dec-alloc-approve-001")
    d.decide(
        DecisionOutcome.APPROVED,
        rationale="Risk committee approved allocation with full consultation evidence.",
        evidence_refs=_consultation_evidence(),
    )
    assert d.decision == DecisionOutcome.APPROVED
    ref_types = [EvidenceRefType(r.ref_type) for r in d.evidence_refs]
    assert EvidenceRefType.COMMITTEE_MEMO in ref_types
    assert EvidenceRefType.SERVICE_HANDOFF in ref_types


def test_allocation_policy_high_risk_approve_with_conditions() -> None:
    d = _high_risk_allocation_decision("dec-alloc-cond-001")
    d.decide(
        DecisionOutcome.APPROVED_WITH_CONDITIONS,
        rationale="Approved with paper-only deployment constraint.",
        conditions=["Deploy to paper stage only for first 72 hours."],
        evidence_refs=_consultation_evidence(),
    )
    assert d.decision == DecisionOutcome.APPROVED_WITH_CONDITIONS
    assert d.conditions == ["Deploy to paper stage only for first 72 hours."]


def test_allocation_policy_high_risk_reject_with_consultation_evidence() -> None:
    d = _high_risk_allocation_decision("dec-alloc-reject-001")
    d.decide(
        DecisionOutcome.REJECTED,
        rationale="Committee rejected due to unresolved concentration risk.",
        evidence_refs=_consultation_evidence(),
    )
    assert d.decision == DecisionOutcome.REJECTED


def test_allocation_policy_high_risk_missing_memo_ref_rejected() -> None:
    d = _high_risk_allocation_decision("dec-alloc-nomemo-001")
    with pytest.raises(ConsultationGateError, match="committee_memo"):
        d.decide(
            DecisionOutcome.APPROVED,
            rationale="Trying to approve without committee memo.",
            evidence_refs=[
                EvidenceRef(ref_type=EvidenceRefType.SERVICE_HANDOFF, ref_id="gh-alloc-001")
            ],
        )


def test_allocation_policy_high_risk_missing_handoff_ref_rejected() -> None:
    d = _high_risk_allocation_decision("dec-alloc-nohandoff-001")
    with pytest.raises(ConsultationGateError, match="service_handoff"):
        d.decide(
            DecisionOutcome.APPROVED,
            rationale="Trying to approve without service handoff.",
            evidence_refs=[
                EvidenceRef(ref_type=EvidenceRefType.COMMITTEE_MEMO, ref_id="memo-alloc-001")
            ],
        )


def test_allocation_policy_high_risk_stale_handoff_rejected() -> None:
    stale_issued_at = "2026-01-01T00:00:00Z"
    evidence = _consultation_evidence(issued_at=stale_issued_at)
    with pytest.raises(ConsultationGateError, match="stale"):
        validate_consultation_gate(evidence, now_iso="2026-06-10T00:00:00Z")


def test_allocation_policy_high_risk_fresh_handoff_passes() -> None:
    fresh_issued_at = "2026-06-10T00:00:00Z"
    evidence = _consultation_evidence(issued_at=fresh_issued_at)
    validate_consultation_gate(evidence, now_iso="2026-06-10T01:00:00Z")


def test_allocation_policy_low_risk_does_not_require_consultation() -> None:
    d = ApprovalDecision.create_proposed(
        decision_id="dec-alloc-low-001",
        target_type=TargetType.ALLOCATION_POLICY,
        target_id="artifact-alloc-low-001",
        target_version="1.0.0",
        risk_level=RiskLevel.LOW,
    )
    d.accept_review(ActorRole.GOVERNANCE_REVIEWER, "reviewer-001")
    d.decide(
        DecisionOutcome.APPROVED,
        rationale="Low-risk allocation approved without consultation.",
        evidence_refs=[EvidenceRef(ref_type=EvidenceRefType.EVALUATOR_RESULT, ref_id="eval-001")],
    )
    assert d.decision == DecisionOutcome.APPROVED


# ---------------------------------------------------------------------------
# Sponsor decision bridge allocation_policy tests
# ---------------------------------------------------------------------------


def _allocation_policy_sponsor_decision(**overrides: object) -> dict:
    payload: dict = {
        "decision_id": "sponsor-alloc-001",
        "type": "approval",
        "sponsor_persona_id": "persona-risk-sponsor",
        "target_type": "allocation_policy",
        "target_id": "artifact-alloc-001",
        "target_version": "1.0.0",
        "sponsor_decision": "approved",
        "risk_level": "high",
        "rationale": "Committee sponsor approved the high-risk allocation policy.",
        "committee_id": "committee-alloc-001",
        "handoff_id": "gh-alloc-001",
        "trace_id": "trace-alloc-001",
        "capital_pool_id": "pool-alpha",
    }
    payload.update(overrides)
    return payload


def test_bridge_allocation_policy_high_risk_injects_consultation_evidence() -> None:
    proposal = bridge(_allocation_policy_sponsor_decision())
    payload = proposal.to_dict()
    ref_types = [r["ref_type"] for r in payload["evidence_refs"]]
    assert "committee_memo" in ref_types
    assert "service_handoff" in ref_types
    memo_ref = next(r for r in payload["evidence_refs"] if r["ref_type"] == "committee_memo")
    assert memo_ref["ref_id"] == "committee-alloc-001"
    handoff_ref = next(r for r in payload["evidence_refs"] if r["ref_type"] == "service_handoff")
    assert handoff_ref["ref_id"] == "gh-alloc-001"


def test_bridge_allocation_policy_high_risk_missing_committee_id_rejected() -> None:
    with pytest.raises(SponsorDecisionBridgeError, match="committee_id"):
        bridge(_allocation_policy_sponsor_decision(committee_id=None))


def test_bridge_allocation_policy_high_risk_missing_handoff_id_rejected() -> None:
    with pytest.raises(SponsorDecisionBridgeError, match="handoff_id"):
        bridge(_allocation_policy_sponsor_decision(handoff_id=None))


def test_bridge_allocation_policy_medium_risk_no_consultation_required() -> None:
    proposal = bridge(_allocation_policy_sponsor_decision(risk_level="medium", committee_id=None, handoff_id=None))
    payload = proposal.to_dict()
    ref_types = [r["ref_type"] for r in payload["evidence_refs"]]
    assert "committee_memo" not in ref_types
    assert "service_handoff" not in ref_types


def test_bridge_allocation_policy_high_risk_mismatched_committee_memo_rejected() -> None:
    payload = _allocation_policy_sponsor_decision(
        committee_id="committee-expected",
        handoff_id="gh-expected",
        evidence_refs=[{"ref_type": "committee_memo", "ref_id": "memo-wrong"}],
    )
    with pytest.raises(SponsorDecisionBridgeError, match="mismatched committee_memo"):
        bridge(payload)


def test_bridge_allocation_policy_high_risk_mismatched_service_handoff_rejected() -> None:
    payload = _allocation_policy_sponsor_decision(
        committee_id="committee-expected",
        handoff_id="gh-expected",
        evidence_refs=[
            {"ref_type": "committee_memo", "ref_id": "committee-expected"},
            {"ref_type": "service_handoff", "ref_id": "gh-wrong"},
        ],
    )
    with pytest.raises(SponsorDecisionBridgeError, match="mismatched service_handoff"):
        bridge(payload)

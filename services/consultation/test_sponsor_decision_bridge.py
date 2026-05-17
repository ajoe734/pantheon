from __future__ import annotations

import pytest

from services.consultation.sponsor_decision_bridge import (
    ApprovalDecisionProposal,
    EvolutionDecisionProposal,
    SponsorDecisionBridgeError,
    bridge,
)


def _base_decision(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision_id": "sponsor-decision-001",
        "type": "approval",
        "sponsor_persona_id": "persona-risk-sponsor",
        "target_type": "model_artifact",
        "target_id": "artifact-alpha-v4",
        "target_version": "4.0.0",
        "sponsor_decision": "conditional",
        "risk_level": "medium",
        "rationale": "Committee sponsor recommends advancing with tighter guardrails.",
        "rationale_ref": "workspace://committee-rationales/sponsor-decision-001",
        "conditions": ["Keep the deployment paper-only for one more heartbeat window."],
        "committee_id": "committee-risk-001",
        "handoff_id": "gh-sponsor-001",
        "trace_id": "trace-sponsor-001",
        "capital_pool_id": "pool-alpha",
        "persona_id": "persona-alpha",
        "evidence_refs": [
            "memo-risk-001",
            {
                "ref_type": "telemetry_summary",
                "ref_id": "telemetry-summary-001",
                "storage_ref": {"backend": "object_store", "path": "/telemetry/summary/001"},
                "note": "Paper stage telemetry remained inside tolerance.",
            },
        ],
        "metadata": {"committee_quorum": "met"},
    }
    payload.update(overrides)
    return payload


def test_bridge_maps_approval_sponsor_decision_to_approval_proposal() -> None:
    proposal = bridge(_base_decision())

    assert isinstance(proposal, ApprovalDecisionProposal)
    payload = proposal.to_dict()
    assert payload["proposal_type"] == "approval_decision"
    assert payload["decision_id"] == "approval-proposal-from-sponsor-decision-001"
    assert payload["decision_state"] == "proposed"
    assert payload["target_type"] == "model_artifact"
    assert payload["target_id"] == "artifact-alpha-v4"
    assert payload["target_version"] == "4.0.0"
    assert payload["risk_level"] == "medium"
    assert payload["rationale"] == "Committee sponsor recommends advancing with tighter guardrails."
    assert payload["capital_pool_id"] == "pool-alpha"
    assert payload["persona_id"] == "persona-alpha"
    assert payload["evidence_refs"][0] == {
        "ref_type": "manual_review_ticket",
        "ref_id": "memo-risk-001",
    }
    assert payload["evidence_refs"][1]["ref_type"] == "telemetry_summary"
    assert payload["metadata"] == {
        "committee_quorum": "met",
        "bridge_source": "consultation_sponsor_decision",
        "source_decision_id": "sponsor-decision-001",
        "source_decision_type": "approval",
        "sponsor_persona_id": "persona-risk-sponsor",
        "committee_id": "committee-risk-001",
        "handoff_id": "gh-sponsor-001",
        "rationale_ref": "workspace://committee-rationales/sponsor-decision-001",
        "trace_id": "trace-sponsor-001",
        "recommended_decision": "approved_with_conditions",
        "conditions": ["Keep the deployment paper-only for one more heartbeat window."],
    }


def test_bridge_maps_evolution_sponsor_decision_to_evolution_proposal() -> None:
    proposal = bridge(
        _base_decision(
            decision_id="sponsor-decision-002",
            type="evolution",
            target_type="candidate_artifact",
            target_id="artifact-live-v2",
            target_version="2.0.0",
            action_type="freeze",
            target_stage="live",
            risk_level="high",
            sponsor_decision="approved",
            linked_incident_id="inc-live-001",
            linked_postmortem_id="pm-live-001",
            threshold_snapshots=[
                {
                    "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md#7.5",
                    "signal_type": "governance_incident",
                    "metric_name": "severity1_incident_count",
                    "comparator": "gte",
                    "observed_value": 1,
                    "threshold_value": 1,
                }
            ],
        )
    )

    assert isinstance(proposal, EvolutionDecisionProposal)
    payload = proposal.to_dict()
    assert payload["proposal_type"] == "evolution_decision"
    assert payload["decision_id"] == "evolution-proposal-from-sponsor-decision-002"
    assert payload["decision_state"] == "proposed"
    assert payload["target_type"] == "candidate_artifact"
    assert payload["action_type"] == "freeze"
    assert payload["target_stage"] == "live"
    assert payload["risk_level"] == "high"
    assert payload["created_by_role"] == "operator"
    assert payload["created_by_id"] == "persona-risk-sponsor"
    assert payload["linked_incident_id"] == "inc-live-001"
    assert payload["linked_postmortem_id"] == "pm-live-001"
    assert payload["threshold_snapshots"][0]["signal_type"] == "governance_incident"
    assert payload["metadata"]["bridge_source"] == "consultation_sponsor_decision"
    assert payload["metadata"]["recommended_decision"] == "approved"


def test_bridge_rejects_missing_sponsor_persona_id() -> None:
    decision = _base_decision()
    decision.pop("sponsor_persona_id")

    with pytest.raises(SponsorDecisionBridgeError, match="sponsor_persona_id is required"):
        bridge(decision)


def test_bridge_does_not_emit_decided_or_store_write_payload() -> None:
    proposal = bridge(_base_decision()).to_dict()

    assert proposal["decision_state"] == "proposed"
    assert "decision" not in proposal
    assert "decided_at" not in proposal
    assert "store_path" not in proposal

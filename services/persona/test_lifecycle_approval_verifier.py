"""Lifecycle decision verification against ApprovalEvidence.require_valid."""
from __future__ import annotations

import pytest

from services.governance.approval_authority import ApprovalEvidence, ApprovalReader
from services.persona.write_owner import HttpGovernanceApprovalVerifier

ARGS = dict(decision_id="d1", persona_id="per1", tenant_id="t1",
            source_state="consultable", target_state="paper_owner")


def _evidence(**over):
    base = dict(
        decision_id="d1", tenant_id="t1", target_type="persona_lifecycle_transition",
        target_id="per1", target_version="1", decision_state="decided",
        decision="approved", actor_id="dec", actor_role="governance_reviewer",
        decided_at="2026-01-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z",
        conditions=[], controller_record_ref="ref", authority_status="authoritative",
        recorded_at="2026-01-01T00:00:00Z", version=1, event_id="e1",
        risk_level="low", owner_user_id="proposer",
        metadata={"subject": {"persona_id": "per1", "from_state": "consultable",
                              "to_state": "paper_owner"},
                  "approvals": [{"actor_id": "dec", "actor_role": "governance_reviewer"}]},
    )
    base.update(over)
    return ApprovalEvidence.model_validate(base)


def _verify(monkeypatch, evidence, **over):
    monkeypatch.setattr(ApprovalReader, "get", lambda self, _id: evidence)
    verifier = HttpGovernanceApprovalVerifier(base_url="http://gov", service_token="x")
    return verifier.verify_persona_lifecycle_decision(**{**ARGS, **over})


def test_exact_approved_decision_verifies(monkeypatch):
    assert _verify(monkeypatch, _evidence()) is True


@pytest.mark.parametrize("over", [
    {"decision": "rejected"},
    {"revoked_at": "2026-02-01T00:00:00Z"},
    {"expires_at": "2020-01-01T00:00:00Z"},
    {"tenant_id": "other"},
    {"target_type": "rebalance_apply"},
])
def test_invalid_decision_rejected(monkeypatch, over):
    assert _verify(monkeypatch, _evidence(**over)) is False


@pytest.mark.parametrize("over", [
    {"persona_id": "per2"}, {"source_state": "research_only"},
    {"target_state": "live_owner"}, {"tenant_id": "other"},
])
def test_mismatched_binding_rejected(monkeypatch, over):
    assert _verify(monkeypatch, _evidence(), **over) is False

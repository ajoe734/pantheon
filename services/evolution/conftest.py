"""Explicit governance transport fixture for lifecycle/receipt regression suites."""
import pytest

from services.governance.test_approval_authority import (
    SnapshotApprovalReader, approval_snapshot,
)


@pytest.fixture
def execution_approvals(monkeypatch):
    from services.evolution import main

    class Reader:
        def get(self, decision_id):
            decision = next(d for d in main.store.list_all()
                            if d.approval_decision_id == decision_id)
            risk = main._enum_value(decision.risk_level)
            role = "governance_committee" if risk in {"high", "critical"} else "governance_reviewer"
            return SnapshotApprovalReader(approval_snapshot(
                decision_id=decision_id, tenant_id=decision.tenant_id,
                risk_level=risk, actor_role=role,
                target_type="evolution_execute", target_id=decision.decision_id,
                target_version=decision.target_version, owner_user_id="test-proposer",
                metadata={
                    "subject": {"proposal_id": decision.decision_id,
                                "proposal_content_digest": main._immutable_decision_fingerprint(decision)},
                    "approvals": [{"actor_id": "unit-reviewer", "actor_role": role}],
                },
            )).get(decision_id)

    monkeypatch.setattr(main, "configured_approval_reader", lambda domain: Reader())

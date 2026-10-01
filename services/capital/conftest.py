"""Default capital_guard collaborators for service tests: healthy runtime, permissive policy, and a
governance reader that approves exactly the action the guard asks about. Its answer is a real
ApprovalEvidence (two distinct deciders) checked by the real ``require_valid``; contract tests
that need a wrong or missing approval pass an explicit snapshot reader instead."""
import pytest

from services.governance.approval_authority import ApprovalInvalid
from services.governance.test_approval_authority import approval_snapshot, SnapshotApprovalReader

VOTES = [{"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
         {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"}]


class _ApprovesWhatIsAsked:
    def get(self, decision_id):
        if not decision_id.strip():
            raise ApprovalInvalid("Exact decision ID required")

        class Bound:
            def require_valid(self, *, expected, **kwargs):
                subject = {k[8:]: v for k, v in expected.items() if k.startswith("subject.")}
                body = approval_snapshot(
                    decision_id=decision_id, tenant_id=expected["tenant_id"],
                    target_type=expected["target_type"], target_id=expected["target_id"],
                    target_version=expected.get("target_version", "1"), owner_user_id="proposer-1",
                    metadata={"subject": subject, "approvals": VOTES})
                return SnapshotApprovalReader(body).get(decision_id).require_valid(expected=expected, **kwargs)
        return Bound()


@pytest.fixture(autouse=True)
def _healthy_guard_collaborators(monkeypatch):
    from services.capital import capital_guard

    monkeypatch.setattr(capital_guard, "read_safe_mode", lambda pool_id: "normal")
    monkeypatch.setattr(capital_guard, "load_risk_policy", lambda ref: {"risk_policy_id": ref})
    monkeypatch.setattr(capital_guard, "configured_approval_reader", lambda domain: _ApprovesWhatIsAsked())

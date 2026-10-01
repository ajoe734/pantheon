"""Default capital_guard collaborators for service tests: healthy runtime, permissive policy,
and a governance reader that accepts any non-empty decision id. Guard tests pass explicit fakes."""
import pytest

from services.governance.approval_authority import ApprovalInvalid


class _AcceptingEvidence:
    def require_valid(self, **_kwargs):
        return self


class _AcceptingReader:
    def get(self, decision_id):
        if not decision_id.strip():
            raise ApprovalInvalid("Exact decision ID required")
        return _AcceptingEvidence()


@pytest.fixture(autouse=True)
def _healthy_guard_collaborators(monkeypatch):
    from services.capital import capital_guard

    monkeypatch.setattr(capital_guard, "read_safe_mode", lambda pool_id: "normal")
    monkeypatch.setattr(capital_guard, "load_risk_policy", lambda ref: {"risk_policy_id": ref})
    monkeypatch.setattr(capital_guard, "configured_approval_reader", lambda domain: _AcceptingReader())

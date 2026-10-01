"""capital_guard: every rule and every fail-closed path, plus route wiring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.capital.capital_guard import CapitalGuard, CapitalGuardError
from services.capital.test_service import (  # noqa: F401  (client fixture)
    _apply_payload, _create_default_pool_and_binding, _rebalance_payload, client,
)
from services.governance.approval_authority import ApprovalInvalid, ApprovalUnavailable

POOL = SimpleNamespace(pool_id="pool-001", tenant_id="tenant-a", metadata={}, risk_policy_ref="risk-main")
KW = dict(pool=POOL, tenant_id="tenant-a", decision_id="dec-1", target_type="rebalance_apply",
          target_id="rb-1", expected={"subject.risk_direction": "increase"}, contexts=[{}])


class _Reader:
    def __init__(self, error=None):
        self.error, self.seen = error, []

    def get(self, decision_id):
        if isinstance(self.error, ApprovalUnavailable):
            raise self.error
        outer = self

        class Evidence:
            def require_valid(self, *, expected):
                outer.seen.append(expected)
                if outer.error:
                    raise outer.error
        return Evidence()


def _guard(*, reader=None, safe_mode=lambda pool_id: "normal", policy=lambda ref: {"risk_policy_id": ref}):
    return CapitalGuard(approval_reader=reader or _Reader(), safe_mode_reader=safe_mode, policy_loader=policy)


def test_allows_when_every_check_passes_and_binds_exact_action():
    reader = _Reader()
    _guard(reader=reader).authorize(**KW)
    assert reader.seen == [{"tenant_id": "tenant-a", "target_type": "rebalance_apply",
                            "target_id": "rb-1", "subject.risk_direction": "increase"}]


@pytest.mark.parametrize("tenant", [None, "", "tenant-b"])
def test_rejects_missing_or_foreign_tenant(tenant):
    with pytest.raises(CapitalGuardError, match="tenant"):
        _guard().authorize(**{**KW, "tenant_id": tenant})


@pytest.mark.parametrize("state", ["guarded", "risk_off", "paused", "recovery_testing", ""])
def test_rejects_while_safe_mode_or_kill_switch_active(state):
    with pytest.raises(CapitalGuardError, match="safe mode"):
        _guard(safe_mode=lambda pool_id: state).authorize(**KW)


def test_fails_closed_when_safe_mode_unreadable():
    def unreadable(pool_id):
        raise OSError("runtime manager down")
    with pytest.raises(CapitalGuardError, match="unreadable"):
        _guard(safe_mode=unreadable).authorize(**KW)


def test_risk_policy_limits_reject():
    policy = {"risk_policy_id": "risk-main", "allowed_stages": ["paper"]}
    with pytest.raises(CapitalGuardError, match="Risk policy rejected"):
        _guard(policy=lambda ref: policy).authorize(**{**KW, "contexts": [{"stage": "live"}]})


def test_fails_closed_when_risk_policy_missing():
    def missing(ref):
        raise FileNotFoundError(ref)
    with pytest.raises(CapitalGuardError, match="Risk policy unavailable"):
        _guard(policy=missing).authorize(**KW)


@pytest.mark.parametrize("error", [
    ApprovalInvalid("insufficient distinct deciders"), ApprovalInvalid("Exact decision ID required"),
    ApprovalUnavailable("Governance read unavailable"), RuntimeError("no credential"),
])
def test_requires_valid_governance_approval(error):
    with pytest.raises(CapitalGuardError, match="approval"):
        _guard(reader=_Reader(error)).authorize(**KW)


def test_routes_call_guard_for_pool_binding_and_rebalance(client, monkeypatch):
    test_client, _ = client
    module = __import__("sys").modules["services.capital.main"]
    calls = []
    real = module.capital_guard.authorize
    monkeypatch.setattr(module.capital_guard, "authorize", lambda **kw: (calls.append(kw["target_type"]), real(**kw))[1])
    _create_default_pool_and_binding(test_client)
    assert test_client.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    assert test_client.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    assert "rebalance_apply" in calls
    suspend = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "suspended"}
    assert test_client.patch("/api/capital-pools/pool-001/status", json=suspend).status_code == 200
    assert "capital_pool_status" not in calls  # decreases never need the guard
    reactivate = {**suspend, "status": "active"}
    assert test_client.patch("/api/capital-pools/pool-001/status", json=reactivate).status_code == 403
    assert test_client.patch(
        "/api/capital-pools/pool-001/status", json={**reactivate, "approval_decision_id": "dec-9"}
    ).status_code == 200
    assert calls[-1] == "capital_pool_status"


def test_binding_activation_and_apply_blocked_by_safe_mode(client, monkeypatch):
    test_client, _ = client
    module = __import__("sys").modules["services.capital.main"]
    _create_default_pool_and_binding(test_client)
    assert test_client.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    monkeypatch.setattr(module.capital_guard, "_safe_mode_reader", lambda pool_id: "risk_off")
    blocked = test_client.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert blocked.status_code == 403 and "safe mode" in blocked.json()["detail"]
    activate = test_client.post(
        "/api/bindings/binding-001/activate",
        json={"actor_id": "a", "actor_role": "persona.admin", "approval_decision_id": "dec-1"},
    )
    assert activate.status_code == 403 and "safe mode" in activate.json()["detail"]

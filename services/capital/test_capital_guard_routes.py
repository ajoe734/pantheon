"""Route regressions: limits are evaluated on the complete resulting pool with every configured limit observed."""
import sys

from services.capital.conftest import _healthy_guard_collaborators  # noqa: F401  (autouse collaborators)
from services.capital.test_service import _apply_payload, _binding_payload, _pool_payload, _rebalance_payload, client  # noqa: F401

_ACT = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "active", "approval_decision_id": "decision-1"}
_BIND = {"actor_id": "persona-admin-1", "actor_role": "persona.admin", "approval_decision_id": "decision-1"}


def _policy(monkeypatch, **limits):
    guard = sys.modules["services.capital.main"].capital_guard
    monkeypatch.setattr(guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, **limits})


def _seed(c, **line):
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/rebalances", json=_rebalance_payload(**line)).status_code == 201


def test_pool_activation_sees_existing_allocations(client, monkeypatch):
    c, _ = client
    _seed(c)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    suspended = {**_ACT, "status": "suspended"}
    assert c.patch("/api/capital-pools/pool-001/status", json=suspended).status_code == 200
    _policy(monkeypatch, gross_limit=0.05)
    assert c.patch("/api/capital-pools/pool-001/status", json=_ACT).status_code == 403
    _policy(monkeypatch, gross_limit=0.5)
    assert c.patch("/api/capital-pools/pool-001/status", json=_ACT).status_code == 200


def test_binding_activation_zero_limit_is_enforced(client, monkeypatch):
    c, _ = client
    _seed(c)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    _policy(monkeypatch, gross_limit=0)
    assert c.post("/api/bindings/binding-001/activate", json=_BIND).status_code == 403


def test_unobservable_sector_limit_rejects_rebalance(client, monkeypatch):
    c, _ = client
    _seed(c)
    _policy(monkeypatch, max_sector_exposure={"technology": 0.01})
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 403


def test_rebalance_limits_include_unchanged_allocations(client, monkeypatch):
    c, _ = client
    _seed(c)
    assert c.post("/api/bindings", json=_binding_payload(binding_id="binding-002", persona_id="persona-beta", capital_sleeve_id="sleeve-beta")).status_code == 201
    _policy(monkeypatch, gross_limit=0.2)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    line = {**_rebalance_payload()["lines"][0], "persona_id": "persona-beta", "capital_sleeve_id": "sleeve-beta"}
    assert c.post("/api/rebalances", json=_rebalance_payload(rebalance_id="rb-002", lines=[line])).status_code == 201
    assert c.post("/api/rebalances/rb-002/apply", json=_apply_payload(rebalance_id="rb-002", command_id="cmd-002")).status_code == 403


def test_paper_running_line_uses_normalized_stage(client, monkeypatch):
    c, _ = client
    _seed(c, lines=[{**_rebalance_payload()["lines"][0], "stage": "paper_running", "capital_scope": "paper_ledger"}])
    _policy(monkeypatch, allowed_stages=["paper"])
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200


def test_live_line_rejected_when_only_paper_allowed(client, monkeypatch):
    c, _ = client
    _seed(c)
    _policy(monkeypatch, allowed_stages=["paper"])
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 403

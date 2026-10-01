"""capital_guard: every rule and every fail-closed path, plus route wiring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.capital.capital_guard import CapitalGuard, CapitalGuardError
from services.capital.test_service import (  # noqa: F401  (client fixture)
    _apply_payload, _binding_payload, _pool_payload, _create_default_pool_and_binding, _rebalance_payload, client,
)
from services.capital.conftest import VOTES
from services.governance.approval_authority import ApprovalInvalid, ApprovalUnavailable
from services.governance.models import ProposeApprovalRequest
from services.governance.test_approval_authority import SnapshotApprovalReader, approval_snapshot

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
    before = len(calls)
    suspend = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "suspended"}
    assert test_client.patch("/api/capital-pools/pool-001/status", json=suspend).status_code == 200
    assert len(calls) == before  # decreases never need the guard
    reactivate = {**suspend, "status": "active"}
    assert test_client.patch("/api/capital-pools/pool-001/status", json=reactivate).status_code == 403
    assert test_client.patch(
        "/api/capital-pools/pool-001/status", json={**reactivate, "approval_decision_id": "dec-9"}
    ).status_code == 200
    assert calls[-1] == "capital_pool_activation"


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


# --- real ApprovalEvidence contracts through the mounted routes -------------------------------

def _main():
    return __import__("sys").modules["services.capital.main"]


def _approve(monkeypatch, *, target_type, target_id, target_version, subject, policy=None):
    reader = SnapshotApprovalReader(approval_snapshot(
        decision_id="dec-real", tenant_id="tenant-test", target_type=target_type, target_id=target_id,
        target_version=target_version, owner_user_id="proposer-1",
        metadata={"subject": subject, "approvals": VOTES}))
    monkeypatch.setattr(_main().capital_guard, "_approval_reader", reader)
    if policy is not None:
        monkeypatch.setattr(_main().capital_guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, **policy})


def test_active_pool_create_is_guarded_before_persistence(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(_main().capital_guard, "_safe_mode_reader", lambda pool_id: "risk_off")
    denied = test_client.post("/api/capital-pools", json=_pool_payload())
    assert denied.status_code == 403
    assert test_client.get("/api/capital-pools/pool-001").status_code == 404
    inactive = test_client.post("/api/capital-pools", json=_pool_payload(status="suspended"))
    assert inactive.status_code == 201  # creating inactive is a decrease and needs no approval


def test_pool_activation_proposal_and_exact_approval(client, monkeypatch):
    test_client, _ = client
    created = test_client.post("/api/capital-pools", json=_pool_payload(status="suspended")).json()
    digest = created["approval_digest"]
    proposal = ProposeApprovalRequest(
        expected_version=0, target_type="capital_pool_activation", target_id="pool-001", target_version=digest,
        tenant_id="tenant-test", owner_user_id="proposer-1",
        subject={"pool_id": "pool-001", "risk_direction": "increase"})
    assert proposal.target_type.value == "capital_pool_activation"
    activate = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "active",
                "approval_decision_id": "dec-real"}
    subject = {"pool_id": "pool-001", "risk_direction": "increase"}
    _approve(monkeypatch, target_type="capital_pool_activation", target_id="pool-001",
             target_version="stale", subject=subject)
    assert test_client.patch("/api/capital-pools/pool-001/status", json=activate).status_code == 403
    _approve(monkeypatch, target_type="capital_pool_activation", target_id="pool-001",
             target_version=digest, subject=subject)
    assert test_client.patch("/api/capital-pools/pool-001/status", json=activate).status_code == 200


def test_binding_activation_binds_semantic_digest_and_policy_facts(client, monkeypatch):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    binding = test_client.post("/api/bindings", json=_binding_payload(budget=900000)).json()
    subject = {"binding_id": "binding-001", "persona_id": "persona-alpha",
               "capital_pool_id": "pool-001", "risk_direction": "increase"}
    activate = {"actor_id": "persona-admin-1", "actor_role": "persona.admin", "approval_decision_id": "dec-real"}
    kw = dict(target_type="capital_binding_activation", target_id="binding-001", subject=subject)
    _approve(monkeypatch, target_version="digest-of-another-binding", **kw)
    assert test_client.post("/api/bindings/binding-001/activate", json=activate).status_code == 403
    _approve(monkeypatch, target_version=binding["approval_digest"], policy={"allowed_stages": ["paper"]}, **kw)
    blocked = test_client.post("/api/bindings/binding-001/activate", json=activate)
    assert blocked.status_code == 403 and "Risk policy rejected" in blocked.json()["detail"]
    _approve(monkeypatch, target_version=binding["approval_digest"], policy={"allowed_stages": ["live"]}, **kw)
    assert test_client.post("/api/bindings/binding-001/activate", json=activate).status_code == 200


def test_configured_limit_without_observation_fails_closed():
    policy = lambda ref: {"risk_policy_id": ref, "gross_limit": 0.5}  # noqa: E731
    with pytest.raises(CapitalGuardError, match="cannot be evaluated"):
        _guard(policy=policy).authorize(**KW)
    _guard(policy=policy).authorize(**{**KW, "contexts": [{"gross_exposure": 0.4}]})
    with pytest.raises(CapitalGuardError, match="Risk policy rejected"):
        _guard(policy=policy).authorize(**{**KW, "contexts": [{"gross_exposure": 0.9}]})


def test_rebalance_approval_binds_owner_plan_digest_not_request_hash(client, monkeypatch):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    plan = test_client.post("/api/rebalances", json=_rebalance_payload(request_hash="any-user-string")).json()
    subject = {"plan_id": "rb-001", "capital_pool_id": "pool-001", "risk_direction": "increase"}
    kw = dict(target_type="rebalance_apply", target_id="rb-001")
    assert plan["plan_digest"] and plan["plan_digest"] != "any-user-string"
    _approve(monkeypatch, target_version=plan["plan_digest"],
             subject={**subject, "plan_digest": "any-user-string"}, **kw)
    assert test_client.post("/api/rebalances/rb-001/apply", json=_apply_payload(approval_ref="dec-real")).status_code == 403
    _approve(monkeypatch, target_version=plan["plan_digest"],
             subject={**subject, "plan_digest": plan["plan_digest"]}, **kw)
    assert test_client.post("/api/rebalances/rb-001/apply", json=_apply_payload(approval_ref="dec-real")).status_code == 200


def test_rebalance_with_unobservable_plan_limit_is_rejected_then_evaluated(client, monkeypatch):
    test_client, _ = client
    _create_default_pool_and_binding(test_client)
    plan = test_client.post("/api/rebalances", json=_rebalance_payload()).json()
    subject = {"plan_id": "rb-001", "plan_digest": plan["plan_digest"],
               "capital_pool_id": "pool-001", "risk_direction": "increase"}
    _approve(monkeypatch, target_type="rebalance_apply", target_id="rb-001",
             target_version=plan["plan_digest"], subject=subject, policy={"gross_limit": 0.0001})
    blocked = test_client.post("/api/rebalances/rb-001/apply", json=_apply_payload(approval_ref="dec-real"))
    assert blocked.status_code == 403 and "Risk policy rejected" in blocked.json()["detail"]


def test_real_provisioning_paper_pool_create_readback_replay_spies_zero_calls(client, monkeypatch):
    test_client, _ = client
    safe_mode_calls, policy_calls, approval_calls = [], [], []
    monkeypatch.setattr(_main().capital_guard, "_safe_mode_reader", lambda pool_id: safe_mode_calls.append(pool_id) or "normal")
    monkeypatch.setattr(_main().capital_guard, "_policy_loader", lambda ref: policy_calls.append(ref) or {"risk_policy_id": ref})
    monkeypatch.setattr(_main().capital_guard, "_approval_reader", type("SpyReader", (), {"get": lambda s, d: approval_calls.append(d)})())

    payload = {
        "actor_id": "control-plane-bff", "actor_role": "admin", "pool_id": "pool-persona-paper-001",
        "name": "CP1 Trader paper pool", "owner_id": "tenant-test", "owner_type": "org",
        "status": "active", "currency": "USD", "single_runtime_enforced": True,
        "metadata": {"internal": True, "execution_context": "paper", "tenant_id": "tenant-test", "persona_id": "persona-cp1"},
    }
    created = test_client.post("/api/capital-pools", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active"
    readback = test_client.get("/api/capital-pools/pool-persona-paper-001")
    assert readback.status_code == 200
    assert readback.json()["status"] == "active"
    assert readback.json()["metadata"]["execution_context"] == "paper"
    assert len(safe_mode_calls) == 0
    assert len(policy_calls) == 0
    assert len(approval_calls) == 0


def test_explicit_paper_policy_enforces_limits_and_rejects(client, monkeypatch):
    test_client, _ = client
    payload = {
        "actor_id": "control-plane-bff", "actor_role": "admin", "pool_id": "pool-paper-strict",
        "name": "Strict paper pool", "owner_id": "tenant-test", "owner_type": "org",
        "status": "active", "currency": "USD", "risk_policy_ref": "paper-strict-policy",
        "metadata": {"internal": True, "execution_context": "paper"},
    }
    monkeypatch.setattr(_main().capital_guard, "_policy_loader", lambda ref: (_ for _ in ()).throw(FileNotFoundError("no policy")))
    res_missing = test_client.post("/api/capital-pools", json=payload)
    assert res_missing.status_code == 403
    assert "Risk policy unavailable" in res_missing.json()["detail"]

    monkeypatch.setattr(_main().capital_guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, "max_sector_exposure": {"tech": 0.2}})
    res_unobs = test_client.post("/api/capital-pools", json=payload)
    assert res_unobs.status_code == 403
    assert "cannot be evaluated" in res_unobs.json()["detail"]

    monkeypatch.setattr(_main().capital_guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, "gross_limit": -1.0})
    res_rejected = test_client.post("/api/capital-pools", json=payload)
    assert res_rejected.status_code == 403
    assert "Risk policy rejected" in res_rejected.json()["detail"]


def test_paper_binding_activation_passes_guard_and_honors_lowerstore_contract(client):
    test_client, _ = client
    test_client.post("/api/capital-pools", json={
        "actor_id": "control-plane-bff", "actor_role": "admin", "pool_id": "pool-paper-binding",
        "name": "Paper pool", "owner_id": "tenant-test", "owner_type": "org", "status": "active",
        "metadata": {"execution_context": "paper"},
    })
    binding = test_client.post("/api/bindings", json={
        "actor_id": "control-plane-bff", "actor_role": "admin", "binding_id": "binding-paper-01",
        "persona_id": "persona-1", "capital_pool_id": "pool-paper-binding", "role": "paper_owner",
        "allowed_deployment_scope": "paper",
    }).json()
    assert binding["status"] == "pending"

    # Lowerstore currently insists any active binding has a decision; until BFF-CAPITAL-FORWARD-001 lands,
    # activating without approval_decision_id passes CapitalGuard but raises 400 from the lower store.
    res_no_approval = test_client.post("/api/bindings/binding-paper-01/activate", json={
        "actor_id": "persona-admin-1", "actor_role": "persona.admin",
    })
    assert res_no_approval.status_code == 400
    assert "approval_decision_id is required" in res_no_approval.json()["detail"]


def test_canary_live_mixed_and_sameweight_upgrade_deny_unapproved_money_effect(client, monkeypatch):
    test_client, _ = client
    test_client.post("/api/capital-pools", json={
        "actor_id": "control-plane-bff", "actor_role": "admin", "pool_id": "pool-paper-upgrades",
        "name": "Paper pool", "owner_id": "tenant-test", "owner_type": "org", "status": "active",
        "metadata": {"execution_context": "paper"},
    })
    test_client.post("/api/bindings", json={
        "actor_id": "control-plane-bff", "actor_role": "admin", "binding_id": "binding-canary-01",
        "persona_id": "persona-c1", "capital_pool_id": "pool-paper-upgrades", "role": "live_owner",
        "allowed_deployment_scope": "canary", "capital_sleeve_id": "sleeve-c1",
    })
    test_client.post("/api/bindings", json={
        "actor_id": "control-plane-bff", "actor_role": "admin", "binding_id": "binding-paper-p",
        "persona_id": "persona-p", "capital_pool_id": "pool-paper-upgrades", "role": "paper_owner",
        "allowed_deployment_scope": "paper",
    })
    monkeypatch.setattr(_main().capital_guard, "_approval_reader", SnapshotApprovalReader(None))
    denied_canary = test_client.post("/api/bindings/binding-canary-01/activate", json={
        "actor_id": "persona-admin-1", "actor_role": "persona.admin", "approval_decision_id": "dec-missing",
    })
    assert denied_canary.status_code == 403
    assert "approval" in denied_canary.json()["detail"].lower()

    base_line = {
        "ranking_snapshot_id": "ranking-q3",
        "allocation_evaluation_id": "allocation-evaluation-q3",
        "allocation_policy_version": "persona-real-allocation-v1",
    }
    paper_line = {
        **base_line, "allocation_line_digest": "dig-p",
        "persona_id": "persona-p", "stage": "paper_running", "capital_scope": "paper_ledger",
        "capital_pool_id": "pool-paper-upgrades", "current_weight": 0.0, "target_weight": 0.5, "delta": 0.5,
    }
    canary_line = {
        **base_line, "allocation_line_digest": "dig-c",
        "persona_id": "persona-c1", "stage": "canary_running", "capital_scope": "pool",
        "capital_pool_id": "pool-paper-upgrades", "capital_sleeve_id": "sleeve-c1",
        "current_weight": 0.0, "target_weight": 0.2, "delta": 0.2,
    }
    assert test_client.post("/api/rebalances", json=_rebalance_payload(
        rebalance_id="rb-mixed", capital_pool_id="pool-paper-upgrades", lines=[paper_line, canary_line],
    )).status_code == 201
    denied_mixed = test_client.post("/api/rebalances/rb-mixed/apply", json=_apply_payload(rebalance_id="rb-mixed", approval_ref="dec-none"))
    assert denied_mixed.status_code == 403


def test_finite_scale_helper():
    from services.capital.capital_guard import _finite_scale
    assert _finite_scale(1.5, "capital_scale_pct") == 1.5
    assert _finite_scale("2.5", "gross_scale_pct") == 2.5
    assert _finite_scale(0, "capital_scale_pct") == 0.0
    for bad in ["NaN", "Infinity", "-Infinity", float("nan"), float("inf"), -float("inf"), "abc", True, False]:
        with pytest.raises(CapitalGuardError, match="must be a finite number"):
            _finite_scale(bad, "capital_scale_pct")


def test_require_risk_policy_rejects_nonfinite_observations():
    guard = _guard(policy=lambda ref: {"risk_policy_id": ref, "gross_limit": 1.0, "max_canary_capital_scale_pct": 5.0})
    with pytest.raises(CapitalGuardError, match="cannot be evaluated"):
        guard.authorize(**{**KW, "contexts": [{"stage": "canary", "gross_exposure": 0.5, "capital_scale_pct": float("nan")}]})
    with pytest.raises(CapitalGuardError, match="cannot be evaluated"):
        guard.authorize(**{**KW, "contexts": [{"stage": "canary", "gross_exposure": float("inf"), "capital_scale_pct": 2.0}]})


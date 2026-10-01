"""Route regressions: limits are evaluated on the complete resulting pool with every configured limit observed."""
import sys
import pytest

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


def test_empty_pool_active_create_with_paper_only_policy(client, monkeypatch):
    c, _ = client
    _policy(monkeypatch, allowed_stages=["paper"])
    resp = c.post("/api/capital-pools", json={**_pool_payload(pool_id="pool-empty-1"), "status": "active", "approval_decision_id": "decision-1"})
    assert resp.status_code == 201


def test_empty_pool_reactivation_with_paper_only_policy(client, monkeypatch):
    c, _ = client
    assert c.post("/api/capital-pools", json={**_pool_payload(pool_id="pool-empty-2"), "status": "suspended"}).status_code == 201
    _policy(monkeypatch, allowed_stages=["paper"])
    assert c.patch("/api/capital-pools/pool-empty-2/status", json=_ACT).status_code == 200


def test_nonempty_pool_reactivation_rejected_on_forbidden_stage(client, monkeypatch):
    c, _ = client
    _seed(c)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    suspended = {**_ACT, "status": "suspended"}
    assert c.patch("/api/capital-pools/pool-001/status", json=suspended).status_code == 200
    _policy(monkeypatch, allowed_stages=["paper"])
    resp = c.patch("/api/capital-pools/pool-001/status", json=_ACT)
    assert resp.status_code == 403
    assert "Risk policy rejected" in resp.json()["detail"]


def test_partial_rebalance_checks_unchanged_live_stage(client, monkeypatch):
    c, _ = client
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    assert c.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="binding-paper",
            persona_id="persona-paper",
            capital_sleeve_id="sleeve-paper",
            role="paper_owner",
            allowed_deployment_scope="paper",
        ),
    ).status_code == 201
    line = {
        **_rebalance_payload()["lines"][0],
        "persona_id": "persona-paper",
        "capital_sleeve_id": "sleeve-paper",
        "stage": "paper_running",
        "capital_scope": "paper_ledger",
    }
    _policy(monkeypatch, allowed_stages=["paper"])
    assert c.post(
        "/api/rebalances",
        json=_rebalance_payload(rebalance_id="rb-paper", lines=[line]),
    ).status_code == 201
    response = c.post(
        "/api/rebalances/rb-paper/apply",
        json=_apply_payload(rebalance_id="rb-paper", command_id="cmd-paper"),
    )
    assert response.status_code == 403, response.text


def test_equal_weight_canary_to_live_requires_guard(client, monkeypatch):
    c, _ = client
    line = {**_rebalance_payload()["lines"][0], "stage": "canary_running"}
    _seed(c, lines=[line])
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    guard = sys.modules["services.capital.main"].capital_guard
    monkeypatch.setattr(guard, "_safe_mode_reader", lambda _: "risk_off")
    upgrade = {**line, "stage": "live_running", "current_weight": 0.12, "target_weight": 0.12, "delta": 0.0}
    assert c.post("/api/rebalances", json=_rebalance_payload(rebalance_id="rb-upgrade", lines=[upgrade])).status_code == 201
    response = c.post("/api/rebalances/rb-upgrade/apply", json=_apply_payload(rebalance_id="rb-upgrade", command_id="cmd-upgrade", approval_ref=None))
    assert response.status_code == 403, response.text


def test_canary_scale_without_observation_fails_closed(client, monkeypatch):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=0.0, max_canary_gross_scale_pct=0.0)
    line = {**_rebalance_payload()["lines"][0], "stage": "canary_running"}
    _seed(c, lines=[line])
    response = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert response.status_code == 403, response.text


def test_canary_scale_with_observation_enforces_limits(client, monkeypatch):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=5.0, max_canary_gross_scale_pct=25.0)
    line_ok = {**_rebalance_payload()["lines"][0], "stage": "canary_running", "capital_scale_pct": 2.0, "gross_scale_pct": 10.0}
    _seed(c, lines=[line_ok])
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200

    line_exceed = {**line_ok, "current_weight": 0.12, "target_weight": 0.15, "delta": 0.03, "capital_scale_pct": 10.0}
    assert c.post("/api/rebalances", json=_rebalance_payload(rebalance_id="rb-exceed", lines=[line_exceed])).status_code == 201
    resp = c.post("/api/rebalances/rb-exceed/apply", json=_apply_payload(rebalance_id="rb-exceed", command_id="cmd-exceed"))
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("field", ["capital_scale_pct", "gross_scale_pct"])
@pytest.mark.parametrize("bad_val", ["NaN", "Infinity", "-Infinity", "malformed", True])
def test_nonfinite_and_malformed_scale_fail_closed_without_allocation_write(client, monkeypatch, field, bad_val):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=5.0, max_canary_gross_scale_pct=25.0)
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    line = {**_rebalance_payload()["lines"][0], "stage": "canary_running", "capital_scale_pct": 2.0, "gross_scale_pct": 10.0, field: bad_val}
    created = c.post("/api/rebalances", json=_rebalance_payload(lines=[line]))
    assert created.status_code == 201, created.text
    applied = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert applied.status_code == 403, applied.text
    allocs = c.get("/api/allocations?capital_pool_id=pool-001").json()
    assert allocs["count"] == 0 and len(allocs["items"]) == 0


@pytest.mark.parametrize("field", ["capital_scale_pct", "gross_scale_pct"])
def test_valid_finite_canary_scales_enforce_policy_limits(client, monkeypatch, field):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=5.0, max_canary_gross_scale_pct=25.0)
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    line_ok = {**_rebalance_payload()["lines"][0], "stage": "canary_running", "capital_scale_pct": 2.0, "gross_scale_pct": 10.0}
    assert c.post("/api/rebalances", json=_rebalance_payload(lines=[line_ok])).status_code == 201
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    excessive_val = 10.0 if field == "capital_scale_pct" else 30.0
    line_exceed = {**line_ok, "current_weight": 0.12, "target_weight": 0.18, "delta": 0.06, field: excessive_val}
    assert c.post("/api/rebalances", json=_rebalance_payload(rebalance_id="rb-exc", lines=[line_exceed])).status_code == 201
    resp = c.post("/api/rebalances/rb-exc/apply", json=_apply_payload(rebalance_id="rb-exc", command_id="cmd-exc"))
    assert resp.status_code == 403, resp.text


def test_binding_activation_keeps_existing_live_stage(client, monkeypatch):
    c, _ = client
    _seed(c)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    assert c.post(
        "/api/bindings",
        json=_binding_payload(
            binding_id="paper-binding", persona_id="paper-persona",
            capital_sleeve_id="paper-sleeve", role="paper_owner", allowed_deployment_scope="paper",
        ),
    ).status_code == 201
    _policy(monkeypatch, allowed_stages=["paper"])
    response = c.post("/api/bindings/paper-binding/activate", json={"actor_id": "persona-admin-1", "actor_role": "persona.admin", "approval_decision_id": "dec-paper"})
    assert response.status_code == 403, response.text


def test_rebalance_cannot_hide_persisted_live_stage(client, monkeypatch):
    c, _ = client
    line = _rebalance_payload()["lines"][0]
    _seed(c)
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    _policy(monkeypatch, allowed_stages=["paper"])
    claimed_paper = {**line, "stage": "paper_running", "current_weight": 0.12, "target_weight": 0.2, "delta": 0.08}
    assert c.post("/api/rebalances", json=_rebalance_payload(rebalance_id="rb-masked", lines=[claimed_paper])).status_code == 201
    response = c.post("/api/rebalances/rb-masked/apply", json=_apply_payload(rebalance_id="rb-masked", command_id="cmd-masked"))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("reverse", [False, True])
def test_canary_limit_checks_every_line_order_invariant(client, monkeypatch, reverse):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=5.0, max_canary_gross_scale_pct=25.0)
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload(binding_id="binding-beta", persona_id="persona-beta", capital_sleeve_id="sleeve-beta")).status_code == 201
    base = _rebalance_payload()["lines"][0]
    excessive = {**base, "stage": "canary_running", "capital_scale_pct": 10.0, "gross_scale_pct": 10.0}
    low = {**base, "persona_id": "persona-beta", "capital_sleeve_id": "sleeve-beta", "stage": "canary_running", "capital_scale_pct": 2.0, "gross_scale_pct": 10.0}
    lines = [low, excessive] if reverse else [excessive, low]
    created = c.post("/api/rebalances", json=_rebalance_payload(lines=lines))
    assert created.status_code == 201, created.text
    response = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("reverse", [False, True])
def test_canary_missing_observation_rejected_independently(client, monkeypatch, reverse):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=5.0, max_canary_gross_scale_pct=25.0)
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload(binding_id="binding-beta", persona_id="persona-beta", capital_sleeve_id="sleeve-beta")).status_code == 201
    base = _rebalance_payload()["lines"][0]
    valid = {**base, "stage": "canary_running", "capital_scale_pct": 2.0, "gross_scale_pct": 10.0}
    missing = {**base, "persona_id": "persona-beta", "capital_sleeve_id": "sleeve-beta", "stage": "canary_running"}
    lines = [missing, valid] if reverse else [valid, missing]
    created = c.post("/api/rebalances", json=_rebalance_payload(lines=lines))
    assert created.status_code == 201, created.text
    response = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert response.status_code == 403, response.text


def test_pool_reactivation_reads_status_under_apply_lock(client, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    c, _ = client
    module = sys.modules["services.capital.main"]
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    monkeypatch.setattr(module.capital_guard, "_safe_mode_reader", lambda _: "risk_off")
    read_active, suspended = threading.Event(), threading.Event()
    real_get = module.CapitalBoundaryService.get_pool
    first = True

    def delayed_get(service, pool_id):
        nonlocal first
        pool = real_get(service, pool_id)
        if first:
            first = False
            read_active.set()
            assert suspended.wait(10), "suspension did not complete"
        return pool

    monkeypatch.setattr(module.CapitalBoundaryService, "get_pool", delayed_get)
    payload = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "active"}
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(c.patch, "/api/capital-pools/pool-001/status", json=payload)
        try:
            assert read_active.wait(10), "active snapshot was not read"
            response = c.patch("/api/capital-pools/pool-001/status", json={**payload, "status": "suspended"})
            assert response.status_code == 200, response.text
        finally:
            suspended.set()
        result = future.result(timeout=10)
    assert result.status_code == 403, result.text


@pytest.mark.parametrize("scale", [0.0, -1.0])
def test_request_scale_claim_cannot_waive_zero_capital_limits(client, monkeypatch, scale):
    c, _ = client
    _policy(monkeypatch, max_canary_capital_scale_pct=0.0, max_canary_gross_scale_pct=0.0)
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    line = {**_rebalance_payload()["lines"][0], "stage": "canary_running", "capital_scale_pct": scale, "gross_scale_pct": scale}
    created = c.post("/api/rebalances", json=_rebalance_payload(lines=[line]))
    assert created.status_code == 201, created.text
    applied = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert applied.status_code == 403, applied.text
    allocs = c.get("/api/allocations?capital_pool_id=pool-001").json()
    assert allocs["count"] == 0 and len(allocs["items"]) == 0


@pytest.mark.parametrize("policy", [None, {"status": "inactive"}, {"gross_limit": "NaN"}, {"gross_limit": "Infinity"}, {"gross_limit": "invalid"}])
def test_empty_pool_must_validate_configured_policy(client, monkeypatch, policy):
    c, _ = client
    def loader(ref):
        if policy is None:
            raise FileNotFoundError(ref)
        return {"risk_policy_id": ref, **policy}
    monkeypatch.setattr(sys.modules["services.capital.main"].capital_guard, "_policy_loader", loader)
    response = c.post("/api/capital-pools", json=_pool_payload())
    assert response.status_code == 403, response.text
    assert c.get("/api/capital-pools/pool-001").status_code == 404


@pytest.mark.parametrize("policy", [
    {"allowed_asset_classes": ["crypto"]},
    {"allowed_strategy_families": ["momentum"]},
])
def test_nonempty_pool_must_reject_unobserved_allowlist(client, monkeypatch, policy):
    c, _ = client
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    assert c.post("/api/rebalances/rb-001/apply", json=_apply_payload()).status_code == 200
    status = {"actor_id": "capital-admin-1", "actor_role": "capital.admin", "status": "suspended"}
    assert c.patch("/api/capital-pools/pool-001/status", json=status).status_code == 200
    monkeypatch.setattr(sys.modules["services.capital.main"].capital_guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, **policy})
    response = c.patch("/api/capital-pools/pool-001/status", json={**status, "status": "active", "approval_decision_id": "decision-1"})
    assert response.status_code == 403, response.text
    assert c.get("/api/capital-pools/pool-001").json()["status"] == "suspended"


@pytest.mark.parametrize("policy", [
    {"forbidden_asset_classes": ["crypto"]},
    {"forbidden_strategy_families": ["momentum"]},
])
def test_rebalance_must_reject_unobserved_denylist(client, monkeypatch, policy):
    c, _ = client
    assert c.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert c.post("/api/bindings", json=_binding_payload()).status_code == 201
    assert c.post("/api/rebalances", json=_rebalance_payload()).status_code == 201
    monkeypatch.setattr(sys.modules["services.capital.main"].capital_guard, "_policy_loader", lambda ref: {"risk_policy_id": ref, **policy})
    response = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
    assert response.status_code == 403, response.text
    allocs = c.get("/api/allocations?capital_pool_id=pool-001").json()
    assert allocs["count"] == 0 and len(allocs["items"]) == 0



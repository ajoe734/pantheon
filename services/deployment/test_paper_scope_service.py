"""Deployment binds a paper-scoped approval to the actual request/plan stage and scale.

Reuses the in-process Deployment TestClient fixture; the approval snapshot is
re-seeded with the owner-stamped authorization_scope.
"""
from __future__ import annotations

import copy
import json
import sys

import pytest

from services.governance.paper_approval_scope import DEV_PAPER_AUTHORIZATION_SCOPE
from services.deployment.test_service import (  # noqa: F401  (fixture)
    _plan_payload, _seed_capital_pool, _seed_persona_binding, client,
)


def _scope_seeded_approval(governance_dir, scoped=True):
    path = governance_dir / "approval_decisions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["approval-001"]["authorization_scope"] = copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE) if scoped else None
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _module():
    return sys.modules["services.deployment.service"]


def _plan_ids(governance_dir):
    path = governance_dir / "deployment_plans.json"
    return set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()


def _saga_state(governance_dir):
    path = governance_dir / "deployment_sagas.json"
    if not path.exists():
        return {"sagas": {}, "outbox": []}
    return json.loads(path.read_text(encoding="utf-8"))


def test_scoped_paper_zero_capital_plan_is_created_in_dev(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    _scope_seeded_approval(governance_dir)
    created = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-paper-scope-001"))
    assert created.status_code == 201, created.text
    assert created.json()["target_stage"] == "paper"
    assert created.json()["scale"]["capital_scale_pct"] == 0


@pytest.mark.parametrize("payload_change,label", [
    ({"target_stage": "canary", "current_stage": "paper"}, "canary"),
    ({"target_stage": "live", "current_stage": "canary"}, "live"),
    ({"scale": {"capital_scale_pct": 1, "gross_scale_pct": 100}}, "capital_scale_pct=1"),
])
def test_scoped_approval_denies_canary_live_or_nonzero_scale_before_persisting(client, monkeypatch, payload_change, label):
    test_client, governance_dir = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    _scope_seeded_approval(governance_dir)
    payload = dict(_plan_payload(plan_id="plan-paper-scope-denied"), **payload_change)
    denied = test_client.post("/api/deployment/plans", json=payload)
    assert denied.status_code == 422, denied.text
    assert "authorization_scope" in denied.json()["detail"]
    assert "plan-paper-scope-denied" not in _plan_ids(governance_dir)
    assert test_client.get("/api/deployment/plans/plan-paper-scope-denied").status_code == 404
    validated = test_client.post("/api/deployment/plans/validate", json=payload)
    assert validated.status_code == 200 and validated.json()["ok"] is False


@pytest.mark.parametrize("environment", [None, "prod"])
def test_scoped_approval_fails_closed_outside_dev(client, monkeypatch, environment):
    test_client, governance_dir = client
    if environment is None:
        monkeypatch.delenv("PANTHEON_ENV", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_ENV", environment)
    _scope_seeded_approval(governance_dir)
    denied = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-paper-scope-env"))
    assert denied.status_code == 422 and "authorization_scope" in denied.json()["detail"]
    assert "plan-paper-scope-env" not in _plan_ids(governance_dir)


def test_dispatch_rechecks_persisted_plan_scale_before_saga_or_outbox(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    _scope_seeded_approval(governance_dir)
    created = test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-paper-scope-dispatch"))
    assert created.status_code == 201, created.text
    # The persisted plan is the authority the dispatcher reads; drift it past the scope.
    plan = _module().planner_service.plan_store.get("plan-paper-scope-dispatch")
    plan.scale.capital_scale_pct = 5.0
    _module().planner_service.plan_store.put(plan)

    denied = test_client.post("/api/deployment/plans/plan-paper-scope-dispatch/dispatch", json={"trace_id": "trace-scope-001"})
    assert denied.status_code == 400, denied.text
    assert "authorization_scope" in denied.json()["detail"]
    state = _saga_state(governance_dir)
    assert state["sagas"] == {} and state["outbox"] == []
    assert _module().orchestration_service.saga_store.list_all() == []

    plan.scale.capital_scale_pct = 0.0
    _module().planner_service.plan_store.put(plan)
    dispatched = test_client.post("/api/deployment/plans/plan-paper-scope-dispatch/dispatch", json={"trace_id": "trace-scope-001"})
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["execution_context"] == "paper"


def test_outbox_replay_rechecks_persisted_plan_before_requeue(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    _scope_seeded_approval(governance_dir)
    assert test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-paper-scope-replay")).status_code == 201
    dispatch = test_client.post("/api/deployment/plans/plan-paper-scope-replay/dispatch", json={"trace_id": "trace-scope-replay"})
    assert dispatch.status_code == 200, dispatch.text
    event_id = dispatch.json()["deployment_saga"]["outbox_event"]["event"]["event_id"]
    failure = test_client.post(f"/api/deployment/outbox/{event_id}/failure", json={
        "consumer_name": "deployment-outbox-consumer", "reason": "runtime-manager 503",
        "retryable": True, "max_attempts": 1, "retry_delay_seconds": 0,
    })
    assert failure.status_code == 200 and failure.json()["status"] == "dead_lettered"

    plan = _module().planner_service.plan_store.get("plan-paper-scope-replay")
    plan.scale.capital_scale_pct = 5.0
    _module().planner_service.plan_store.put(plan)
    denied = test_client.post(f"/api/deployment/outbox/{event_id}/replay", json={"reason": "retry"})
    assert denied.status_code == 400, denied.text
    assert "authorization_scope" in denied.json()["detail"]
    dlq = test_client.get("/api/deployment/outbox", params={"status": "dead_lettered"})
    assert [record["event"]["event_id"] for record in dlq.json()] == [event_id]

    plan.scale.capital_scale_pct = 0.0
    _module().planner_service.plan_store.put(plan)
    replayed = test_client.post(f"/api/deployment/outbox/{event_id}/replay", json={"reason": "retry"})
    assert replayed.status_code == 200 and replayed.json()["replayed"] is True


def test_unscoped_approval_dispatch_is_unchanged(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    _scope_seeded_approval(governance_dir, scoped=False)
    assert test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-legacy-001")).status_code == 201
    dispatched = test_client.post("/api/deployment/plans/plan-legacy-001/dispatch", json={"trace_id": "trace-legacy"})
    assert dispatched.status_code == 200, dispatched.text


def _has_authority(test_client, plan_id):
    projection = test_client.get(f"/api/deployment/projections/{plan_id}")
    assert projection.status_code == 200, projection.text
    return projection.json()["summary"]["has_approval_authority"]


def test_projection_read_model_binds_scoped_authority_to_persisted_plan(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    _scope_seeded_approval(governance_dir)
    assert test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-paper-scope-read")).status_code == 201
    # A valid paper/0% plan in dev reads as authoritative, not as unrestricted-denied.
    assert _has_authority(test_client, "plan-paper-scope-read") is True

    plan = _module().planner_service.plan_store.get("plan-paper-scope-read")
    plan.scale.capital_scale_pct = 5.0
    _module().planner_service.plan_store.put(plan)
    assert _has_authority(test_client, "plan-paper-scope-read") is False

    plan.scale.capital_scale_pct = 0.0
    for stage in ("canary", "live"):
        plan.target_stage = stage
        _module().planner_service.plan_store.put(plan)
        assert _has_authority(test_client, "plan-paper-scope-read") is False

    plan.target_stage = "paper"
    _module().planner_service.plan_store.put(plan)
    assert _has_authority(test_client, "plan-paper-scope-read") is True
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    assert _has_authority(test_client, "plan-paper-scope-read") is False


def test_projection_read_model_unscoped_authority_is_unchanged(client, monkeypatch):
    test_client, governance_dir = client
    monkeypatch.delenv("PANTHEON_ENV", raising=False)
    _scope_seeded_approval(governance_dir, scoped=False)
    assert test_client.post("/api/deployment/plans", json=_plan_payload(plan_id="plan-legacy-read")).status_code == 201
    assert _has_authority(test_client, "plan-legacy-read") is True

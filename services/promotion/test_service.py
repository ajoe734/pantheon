"""
Service-path tests for the promotion service.

Runs in-process via FastAPI TestClient with isolated temp storage.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="promotion_service_")
    env_backup = {
        "PROMOTION_DATA_DIR": os.environ.get("PROMOTION_DATA_DIR"),
        "PORT": os.environ.get("PORT"),
    }
    os.environ["PROMOTION_DATA_DIR"] = tempdir
    os.environ["PORT"] = "18089"

    sys.modules.pop("services.promotion.main", None)
    module = importlib.import_module("services.promotion.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), Path(tempdir)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _approval_payload(**overrides) -> dict:
    payload = {
        "decision_id": "apv-001",
        "target_type": "model_artifact",
        "target_id": "artifact-001",
        "target_version": "1.2.3",
        "risk_level": "medium",
        "capital_pool_id": "pool-001",
        "persona_id": "persona-alpha",
    }
    payload.update(overrides)
    return payload


def _approved_approval(client: TestClient, **overrides) -> dict:
    create = client.post("/api/v1/approvals", json=_approval_payload(**overrides))
    assert create.status_code == 201, create.text

    decide = client.post(
        f"/api/v1/approvals/{create.json()['decision_id']}/decide",
        json={
            "outcome": "approved_with_conditions",
            "rationale": "ready for staged promotion",
            "actor_role": "risk_owner",
            "actor_id": "risk-owner-1",
            "conditions": ["start at paper before canary"],
        },
    )
    assert decide.status_code == 200, decide.text
    return decide.json()


def _deployment_payload(**overrides) -> dict:
    payload = {
        "plan_id": "dp-001",
        "approval_decision_id": "apv-001",
        "artifact_id": "artifact-001",
        "artifact_version": "1.2.3",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-001",
        "capital_pool_id": "pool-001",
        "target_stage": "paper",
        "status": "approved",
        "created_by": "promotion-bot",
        "sponsor_persona_id": "persona-alpha",
        "runtime_config_ref": "runtime://paper/alpha",
        "binding_id": "binding-main",
        "persona_capital_binding_id": "binding-main",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "schedule_window": {
            "start_at": "2026-04-20T10:00:00Z",
            "end_at": "2026-04-20T12:00:00Z",
        },
        "scale": {
            "capital_scale_pct": 0.0,
            "gross_scale_pct": 15.0,
            "ramp_schedule": ["T+0:10", "T+1:25"],
        },
        "rollback": {
            "target_artifact_id": "artifact-000",
            "target_version": "1.2.2",
            "action_type": "pause_then_replace",
            "reason": "fallback candidate",
        },
        "pre_checks": ["governance-approved"],
        "post_checks": ["paper-runtime-healthy"],
        "metadata": {"release_train": "paper-wave-a"},
    }
    payload.update(overrides)
    return payload


def test_health(client):
    test_client, tempdir = client
    response = test_client.get("/__health__")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "promotion-svc",
        "port": 18089,
        "data_dir": str(tempdir),
    }


def test_approval_lifecycle_and_list_filters(client):
    test_client, _ = client
    created = test_client.post("/api/v1/approvals", json=_approval_payload())
    assert created.status_code == 201, created.text
    assert created.json()["decision_state"] == "proposed"

    proposed = test_client.get(
        "/api/v1/approvals",
        params={"target_id": "artifact-001", "decision_state": "proposed"},
    )
    assert proposed.status_code == 200
    assert proposed.json()["count"] == 1
    assert proposed.json()["items"][0]["decision_id"] == "apv-001"

    decided = test_client.post(
        "/api/v1/approvals/apv-001/decide",
        json={
            "outcome": "approved_with_conditions",
            "rationale": "promote with staged guardrails",
            "actor_role": "risk_owner",
            "actor_id": "risk-owner-1",
            "conditions": ["paper soak must pass"],
        },
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["decision_state"] == "decided"
    assert body["decision"] == "approved_with_conditions"
    assert body["conditions"] == ["paper soak must pass"]
    assert body["actor_role"] == "risk_owner"

    filtered = test_client.get(
        "/api/v1/approvals",
        params={"decision_state": "decided", "risk_level": "medium"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1
    assert filtered.json()["items"][0]["decision_id"] == "apv-001"


def test_decide_failure_does_not_persist_partial_under_review_state(client):
    test_client, _ = client
    created = test_client.post(
        "/api/v1/approvals",
        json=_approval_payload(decision_id="apv-invalid", risk_level="low"),
    )
    assert created.status_code == 201, created.text

    failed = test_client.post(
        "/api/v1/approvals/apv-invalid/decide",
        json={
            "outcome": "approved_with_conditions",
            "rationale": "missing condition should fail",
            "actor_role": "automated_gate",
            "actor_id": "gate-1",
            "conditions": [],
        },
    )
    assert failed.status_code == 422
    assert "requires at least one condition" in failed.json()["detail"]

    listing = test_client.get(
        "/api/v1/approvals",
        params={"target_id": "artifact-001", "decision_state": "proposed"},
    )
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    stored = listing.json()["items"][0]
    assert stored["decision_id"] == "apv-invalid"
    assert stored["actor_role"] is None
    assert stored["actor_id"] is None
    assert stored["decision"] is None
    assert stored["decision_state"] == "proposed"


def test_create_deployment_persists_extensions_and_supports_listing_filters(client):
    test_client, _ = client
    _approved_approval(test_client)

    created = test_client.post("/api/v1/deployments", json=_deployment_payload())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["plan_id"] == "dp-001"
    assert body["plan_status"] == "approved"
    assert body["persona_capital_binding_id"] == "binding-main"
    assert body["persona_capital_binding_status"] == "active"
    assert body["allowed_deployment_scope"] == "canary"
    assert body["loader_checks_passed"] is True
    assert body["approval_decision"]["decision_id"] == "apv-001"
    assert body["approval_decision"]["decision"] == "approved_with_conditions"
    assert body["approval_decision"]["decision_state"] == "decided"

    listing = test_client.get(
        "/api/v1/deployments",
        params={
            "status": "approved",
            "capital_pool_id": "pool-001",
            "approval_decision_id": "apv-001",
            "target_stage": "paper",
        },
    )
    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    listed = listing.json()["items"][0]
    assert listed["plan_id"] == "dp-001"
    assert listed["rollback"]["action_type"] == "pause_then_replace"
    assert listed["metadata"]["release_train"] == "paper-wave-a"


def test_create_deployment_requires_decided_matching_approval(client):
    test_client, _ = client
    created = test_client.post("/api/v1/approvals", json=_approval_payload())
    assert created.status_code == 201, created.text

    response = test_client.post("/api/v1/deployments", json=_deployment_payload())
    assert response.status_code == 422
    assert "must be in decided state" in response.json()["detail"]

    _approved_approval(
        test_client,
        decision_id="apv-002",
        target_id="artifact-xyz",
        target_version="9.9.9",
    )
    mismatch = test_client.post(
        "/api/v1/deployments",
        json=_deployment_payload(
            plan_id="dp-002",
            approval_decision_id="apv-002",
        ),
    )
    assert mismatch.status_code == 422
    assert "target_id does not match artifact_id" in mismatch.json()["detail"]

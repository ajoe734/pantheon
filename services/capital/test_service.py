"""
Unit tests for the deployable capital service boundary.
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
    tempdir = tempfile.mkdtemp(prefix="capital_service_")
    env_backup = {
        "CAPITAL_DATA_DIR": os.environ.get("CAPITAL_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
    }
    os.environ["CAPITAL_DATA_DIR"] = tempdir
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = tempdir

    sys.modules.pop("services.capital.main", None)
    module = importlib.import_module("services.capital.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), Path(tempdir)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _pool_payload(**overrides):
    payload = {
        "actor_id": "capital-admin-1",
        "actor_role": "capital.admin",
        "pool_id": "pool-001",
        "name": "Main Pool",
        "owner_id": "fund-001",
        "owner_type": "fund",
        "risk_policy_ref": "risk-main",
    }
    payload.update(overrides)
    return payload


def _binding_payload(**overrides):
    payload = {
        "actor_id": "persona-admin-1",
        "actor_role": "persona.admin",
        "binding_id": "binding-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "role": "live_owner",
        "allowed_deployment_scope": "canary",
    }
    payload.update(overrides)
    return payload


def test_health(client):
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pantheon-capital"


def test_write_authority_matrix(client):
    test_client, _ = client
    response = test_client.get("/api/capital/write-authority")
    assert response.status_code == 200
    matrix = response.json()["matrix"]
    expected = {
        ("CapitalPool", "create"),
        ("CapitalPool", "update_status"),
        ("PersonaCapitalBinding", "create"),
        ("PersonaCapitalBinding", "activate"),
        ("PersonaCapitalBinding", "update_status"),
    }
    found = {(entry["resource_type"], entry["operation"]) for entry in matrix}
    assert expected == found


def test_create_pool_and_list(client):
    test_client, data_dir = client
    create = test_client.post("/api/capital-pools", json=_pool_payload())
    assert create.status_code == 201, create.text
    assert create.json()["single_runtime_enforced"] is True

    listing = test_client.get("/api/capital-pools")
    assert listing.status_code == 200
    assert [pool["pool_id"] for pool in listing.json()] == ["pool-001"]

    persisted = (data_dir / "capital_pools.json").read_text(encoding="utf-8")
    assert "pool-001" in persisted


def test_create_pool_rejects_unauthorized_writer(client):
    test_client, _ = client
    response = test_client.post(
        "/api/capital-pools",
        json=_pool_payload(actor_role="persona.admin"),
    )
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"]


def test_binding_requires_existing_pool(client):
    test_client, _ = client
    response = test_client.post("/api/bindings", json=_binding_payload())
    assert response.status_code == 404
    assert "Pool not found" in response.json()["detail"]


def test_binding_lifecycle_and_admissibility_read_path(client):
    test_client, data_dir = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201

    create = test_client.post("/api/bindings", json=_binding_payload())
    assert create.status_code == 201, create.text
    assert create.json()["status"] == "pending"

    activate = test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-001",
        },
    )
    assert activate.status_code == 200, activate.text
    assert activate.json()["status"] == "active"

    admissibility = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
        },
    )
    assert admissibility.status_code == 200
    body = admissibility.json()
    assert body["permitted"] is True
    assert body["binding_id"] == "binding-001"
    assert body["allowed_deployment_scope"] == "canary"
    assert body["active_live_owner_binding_id"] == "binding-001"

    too_high = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "live",
        },
    )
    assert too_high.status_code == 200
    assert too_high.json()["permitted"] is False

    persisted = (data_dir / "persona_capital_bindings.json").read_text(encoding="utf-8")
    assert "binding-001" in persisted


def test_binding_rejects_role_scope_mismatch(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    response = test_client.post(
        "/api/bindings",
        json=_binding_payload(role="paper_owner", allowed_deployment_scope="canary"),
    )
    assert response.status_code == 400
    assert "deployment ceiling" in response.json()["detail"]


def test_second_live_owner_same_pool_rejected(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload(binding_id="binding-001")).status_code == 201
    assert test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-001",
        },
    ).status_code == 200

    second = test_client.post(
        "/api/bindings",
        json=_binding_payload(binding_id="binding-002", persona_id="persona-beta"),
    )
    assert second.status_code == 201

    activate_second = test_client.post(
        "/api/bindings/binding-002/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-002",
        },
    )
    assert activate_second.status_code == 400
    assert "Single-live-owner rule" in activate_second.json()["detail"]


def test_suspended_pool_blocks_activation_and_admissibility(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload()).status_code == 201

    suspend = test_client.patch(
        "/api/capital-pools/pool-001/status",
        json={
            "actor_id": "capital-admin-1",
            "actor_role": "capital.admin",
            "status": "suspended",
        },
    )
    assert suspend.status_code == 200

    activate = test_client.post(
        "/api/bindings/binding-001/activate",
        json={
            "actor_id": "persona-admin-1",
            "actor_role": "persona.admin",
            "approval_decision_id": "approval-003",
        },
    )
    assert activate.status_code == 400
    assert "must be active" in activate.json()["detail"]

    admissibility = test_client.get(
        "/api/bindings/admissibility",
        params={
            "persona_id": "persona-alpha",
            "capital_pool_id": "pool-001",
            "target_stage": "paper",
        },
    )
    assert admissibility.status_code == 200
    assert admissibility.json()["permitted"] is False
    assert admissibility.json()["pool_status"] == "suspended"


def test_audit_log_records_mutations(client):
    test_client, _ = client
    assert test_client.post("/api/capital-pools", json=_pool_payload()).status_code == 201
    assert test_client.post("/api/bindings", json=_binding_payload()).status_code == 201

    events = test_client.get("/api/capital/audit").json()
    event_types = {event["event_type"] for event in events}
    assert "capital_pool_created" in event_types
    assert "persona_capital_binding_created" in event_types


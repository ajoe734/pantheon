"""
Unit tests for the deployable DeploymentPlan service.

Runs in-process via FastAPI TestClient with isolated temp storage.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _seed_approval_store(path: Path) -> None:
    payload = {
        "approval-001": {
            "decision_id": "approval-001",
            "target_id": "reg-strat-001-1.2.0",
            "target_version": "1.2.0",
            "decision_state": "decided",
            "decision": "approved",
            "capital_pool_id": "pool-001",
            "persona_id": "persona-ops",
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_registry_snapshot(path: Path) -> None:
    payload = {
        "reg-strat-001-1.2.0": {
            "registry_id": "reg-strat-001-1.2.0",
            "artifact_type": "model_artifact",
            "strategy_id": "strat-001",
            "version": "1.2.0",
            "artifact_state": "approved",
            "checksum": "sha256:abc123def4567890",
            "approval_decision_id": "approval-001",
            "approved_at": "2026-04-09T12:00:00Z",
            "lineage": {"source_run_ids": ["replication-run-001"]},
            "deployment_summary": {"current_stage": "none"},
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="deployment_service_")
    governance_dir = Path(tempdir) / "governance"
    governance_dir.mkdir(parents=True, exist_ok=True)
    approval_store = governance_dir / "approval_decisions.json"
    registry_snapshot = Path(tempdir) / "registry_entries.json"
    _seed_approval_store(approval_store)
    _seed_registry_snapshot(registry_snapshot)

    env_backup = {
        "DEPLOYMENT_DATA_DIR": os.environ.get("DEPLOYMENT_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH": os.environ.get(
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        ),
    }
    os.environ["DEPLOYMENT_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"] = str(registry_snapshot)

    sys.modules.pop("services.deployment.service", None)
    module = importlib.import_module("services.deployment.service")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app), governance_dir
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_health(client):
    test_client, _ = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pantheon-deployment"


def test_create_plan_from_snapshots(client):
    test_client, governance_dir = client
    response = test_client.post(
        "/api/deployment/plans",
        json={
            "plan_id": "plan-paper-001",
            "approval_decision_id": "approval-001",
            "registry_id": "reg-strat-001-1.2.0",
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "paper",
            "rollback": {
                "target_artifact_id": "reg-strat-001-1.1.0",
                "target_version": "1.1.0",
                "action_type": "replace",
            },
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["transition_type"] == "activate"
    assert body["runtime_action"] == "deploy_new_binding"
    assert body["scale"]["capital_scale_pct"] == 0.0

    store_payload = json.loads((governance_dir / "deployment_plans.json").read_text(encoding="utf-8"))
    assert "plan-paper-001" in store_payload


def test_validate_rejects_skipped_stage_transition(client):
    test_client, _ = client
    response = test_client.post(
        "/api/deployment/plans/validate",
        json={
            "approval_decision_id": "approval-001",
            "registry_id": "reg-strat-001-1.2.0",
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "current_stage": "paper",
            "target_stage": "live",
            "rollback": {
                "target_artifact_id": "reg-strat-001-1.1.0",
                "target_version": "1.1.0",
                "action_type": "replace",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("Forbidden transition: paper -> live" in error for error in body["errors"])


def test_create_enforces_rollback_linkage(client):
    test_client, _ = client
    response = test_client.post(
        "/api/deployment/plans",
        json={
            "approval_decision_id": "approval-001",
            "registry_id": "reg-strat-001-1.2.0",
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "paper",
        },
    )
    assert response.status_code == 422
    assert "rollback" in response.json()["detail"]


def test_list_and_get(client):
    test_client, _ = client
    create = {
        "approval_decision_id": "approval-001",
        "registry_id": "reg-strat-001-1.2.0",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-ops",
        "target_stage": "paper",
        "rollback": {
            "target_artifact_id": "reg-strat-001-1.1.0",
            "target_version": "1.1.0",
            "action_type": "replace",
        },
    }
    created = test_client.post("/api/deployment/plans", json=create)
    assert created.status_code == 201
    plan_id = created.json()["plan_id"]

    listing = test_client.get("/api/deployment/plans", params={"strategy_id": "strat-001"})
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    detail = test_client.get(f"/api/deployment/plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["plan_id"] == plan_id


def test_duplicate_plan_id_rejected(client):
    test_client, _ = client
    payload = {
        "plan_id": "plan-dup-001",
        "approval_decision_id": "approval-001",
        "registry_id": "reg-strat-001-1.2.0",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-ops",
        "target_stage": "paper",
        "rollback": {
            "target_artifact_id": "reg-strat-001-1.1.0",
            "target_version": "1.1.0",
            "action_type": "replace",
        },
    }
    first = test_client.post("/api/deployment/plans", json=payload)
    assert first.status_code == 201

    second = test_client.post("/api/deployment/plans", json=payload)
    assert second.status_code == 422
    assert "already exists" in second.json()["detail"]


def test_status_transition_updates_read_model(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json={
            "plan_id": "plan-paper-002",
            "approval_decision_id": "approval-001",
            "registry_id": "reg-strat-001-1.2.0",
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "paper",
            "rollback": {
                "target_artifact_id": "reg-strat-001-1.1.0",
                "target_version": "1.1.0",
                "action_type": "replace",
            },
        },
    )
    assert created.status_code == 201

    executing = test_client.post(
        "/api/deployment/plans/plan-paper-002/status",
        json={"status": "executing"},
    )
    assert executing.status_code == 200
    assert executing.json()["status"] == "executing"

    executed = test_client.post(
        "/api/deployment/plans/plan-paper-002/status",
        json={"status": "executed"},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"

    read_model = test_client.get("/api/deployment/strategies/strat-001/read-model")
    assert read_model.status_code == 200
    body = read_model.json()
    assert body["current_stage"] == "paper"
    assert body["latest_plan_id"] == "plan-paper-002"
    assert body["active_plan_id"] is None
    assert body["plan_count"] == 1


def test_invalid_status_transition_rejected(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json={
            "plan_id": "plan-paper-003",
            "approval_decision_id": "approval-001",
            "registry_id": "reg-strat-001-1.2.0",
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "paper",
            "rollback": {
                "target_artifact_id": "reg-strat-001-1.1.0",
                "target_version": "1.1.0",
                "action_type": "replace",
            },
        },
    )
    assert created.status_code == 201

    response = test_client.post(
        "/api/deployment/plans/plan-paper-003/status",
        json={"status": "executed"},
    )
    assert response.status_code == 400
    assert "Invalid plan status transition" in response.json()["detail"]

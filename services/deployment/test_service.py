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
from services.governance.test_approval_authority import approval_snapshot, SnapshotApprovalReader
from fastapi.testclient import TestClient

_AUTH_HEADERS = {
    "Authorization": "Bearer deployment-test:operator,service",
    "X-Tenant-Id": "tenant-deployment-test",
}


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
            "tenant_id": "tenant-deployment-test",
        }
    }
    for record in payload.values():
        record.update(approval_snapshot(**record, target_type='registry_entry', candidate_digest='sha256:abc123def4567890'))
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_registry_snapshot(path: Path) -> None:
    payload = {
        "reg-strat-001-1.2.0": {
            "owner_tenant": "tenant-deployment-test",
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


def _plan_payload(
    *,
    plan_id: str | None = None,
    current_stage: str | None = None,
    target_stage: str = "paper",
    rollback_action: str = "replace",
) -> dict:
    payload = {
        "approval_decision_id": "approval-001",
        "registry_id": "reg-strat-001-1.2.0",
        "capital_pool_id": "pool-001",
        "sponsor_persona_id": "persona-ops",
        "target_stage": target_stage,
        "rollback": {
            "target_artifact_id": "reg-strat-001-1.1.0",
            "target_version": "1.1.0",
            "action_type": rollback_action,
        },
    }
    if plan_id:
        payload["plan_id"] = plan_id
    if current_stage:
        payload["current_stage"] = current_stage
    return payload


def _seed_capital_pool(
    governance_dir: Path,
    *,
    pool_id: str = "pool-001",
    status: str = "active",
    single_runtime_enforced: bool = True,
) -> None:
    (governance_dir / "capital_pools.json").write_text(
        json.dumps(
            [
                {
                    "pool_id": pool_id,
                    "name": "Main Pool",
                    "owner_id": "fund-001",
                    "owner_type": "fund",
                    "status": status,
                    "created_at": "2026-04-10T00:00:00Z",
                    "single_runtime_enforced": single_runtime_enforced,
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


def _seed_persona_binding(
    governance_dir: Path,
    *,
    binding_id: str = "binding-001",
    persona_id: str = "persona-ops",
    capital_pool_id: str = "pool-001",
    role: str = "live_owner",
    allowed_deployment_scope: str = "canary",
    status: str = "active",
) -> None:
    (governance_dir / "persona_capital_bindings.json").write_text(
        json.dumps(
            [
                {
                    "binding_id": binding_id,
                    "persona_id": persona_id,
                    "capital_pool_id": capital_pool_id,
                    "role": role,
                    "allowed_deployment_scope": allowed_deployment_scope,
                    "status": status,
                    "created_at": "2026-04-10T00:00:00Z",
                    "approval_decision_id": "approval-001",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="deployment_service_")
    governance_dir = Path(tempdir) / "governance"
    governance_dir.mkdir(parents=True, exist_ok=True)
    approval_store = governance_dir / "approval_decisions.json"
    registry_snapshot = Path(tempdir) / "registry_entries.json"
    runtime_binding_store = Path(tempdir) / "runtime_bindings.json"
    _seed_approval_store(approval_store)
    _seed_registry_snapshot(registry_snapshot)

    env_backup = {
        "CAPITAL_DATA_DIR": os.environ.get("CAPITAL_DATA_DIR"),
        "DEPLOYMENT_DATA_DIR": os.environ.get("DEPLOYMENT_DATA_DIR"),
        "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
        "PANTHEON_RUNTIME_BINDING_STORE_PATH": os.environ.get("PANTHEON_RUNTIME_BINDING_STORE_PATH"),
        "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH": os.environ.get(
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
        ),
        "PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED": os.environ.get(
            "PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED"
        ),
    }
    os.environ["CAPITAL_DATA_DIR"] = str(governance_dir)
    os.environ["DEPLOYMENT_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(runtime_binding_store)
    os.environ["PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"] = str(registry_snapshot)
    os.environ["PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED"] = "false"

    sys.modules.pop("services.deployment.service", None)
    module = importlib.import_module("services.deployment.service")
    module = importlib.reload(module)

    # Explicit unit transport injection; production selectors never read these fixtures.
    module.planner_service.registry_reader = lambda identity: json.loads(registry_snapshot.read_text())[identity]
    module.planner_service.approval_reader = SnapshotApprovalReader(lambda: json.loads(approval_store.read_text())['approval-001'])
    test_client = TestClient(module.app, headers=_AUTH_HEADERS)
    test_client.registry_snapshot = registry_snapshot
    try:
        yield test_client, governance_dir
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


def test_deployment_boundary_requires_authenticated_actor_and_tenant(client):
    test_client, _ = client

    missing_actor = test_client.get(
        "/api/deployment/plans",
        headers={"Authorization": ""},
    )
    assert missing_actor.status_code == 401

    wrong_role = test_client.get(
        "/api/deployment/plans",
        headers={"Authorization": "Bearer observer:viewer"},
    )
    assert wrong_role.status_code == 403

    missing_tenant = test_client.get(
        "/api/deployment/plans",
        headers={"X-Tenant-Id": ""},
    )
    assert missing_tenant.status_code == 400


def test_deployment_plan_actor_and_tenant_are_server_authoritative(client):
    test_client, _ = client
    payload = _plan_payload(plan_id="plan-tenant-isolation-001")
    payload["created_by"] = "forged-client-actor"

    created = test_client.post("/api/deployment/plans", json=payload)

    assert created.status_code == 201, created.text
    assert created.json()["created_by"] == "deployment-test"
    assert created.json()["tenant_id"] == "tenant-deployment-test"

    hidden = test_client.get(
        "/api/deployment/plans/plan-tenant-isolation-001",
        headers={"X-Tenant-Id": "tenant-other"},
    )
    assert hidden.status_code == 404
    listing = test_client.get(
        "/api/deployment/plans",
        headers={"X-Tenant-Id": "tenant-other"},
    )
    assert listing.status_code == 200
    assert listing.json() == []


def test_pool_runtime_compatibility_allows_active_pool_and_binding(client):
    test_client, governance_dir = client
    _seed_capital_pool(governance_dir)
    _seed_persona_binding(governance_dir, allowed_deployment_scope="canary")

    response = test_client.post(
        "/api/deployment/plans/compatibility-check",
        json={
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "canary",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["pool_found"] is True
    assert body["pool_active"] is True
    assert body["single_runtime_enforced"] is True
    assert body["persona_binding_found"] is True
    assert body["persona_scope_ok"] is True
    assert body["persona_binding_id"] == "binding-001"
    assert body["allowed_deployment_scope"] == "canary"
    assert body["active_runtime_binding_count"] == 0
    assert body["single_runtime_ok"] is True


def test_pool_runtime_compatibility_rejects_suspended_pool_and_scope_gap(client):
    test_client, governance_dir = client
    _seed_capital_pool(governance_dir, status="suspended")
    _seed_persona_binding(governance_dir, allowed_deployment_scope="paper")

    response = test_client.post(
        "/api/deployment/plans/compatibility-check",
        json={
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "canary",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["pool_status"] == "suspended"
    assert body["pool_active"] is False
    assert body["persona_binding_found"] is True
    assert body["persona_scope_ok"] is False
    assert any("must be active" in error for error in body["errors"])
    assert any("permits target_stage" in error for error in body["errors"])


def test_pool_runtime_compatibility_flags_single_runtime_invariant_violation(client):
    test_client, governance_dir = client
    _seed_capital_pool(governance_dir)
    _seed_persona_binding(governance_dir, allowed_deployment_scope="live")
    runtime_store = governance_dir.parent / "runtime_bindings.json"
    runtime_store.write_text(
        json.dumps(
            [
                {
                    "binding_id": "rb-active-001",
                    "runtime_id": "runtime-001",
                    "plan_id": "plan-001",
                    "persona_capital_binding_id": "binding-001",
                    "capital_pool_id": "pool-001",
                    "artifact_id": "reg-strat-001-1.2.0",
                    "artifact_version": "1.2.0",
                    "deployment_mode": "paper",
                    "status": "active",
                    "effective_at": "2026-04-10T00:00:00Z",
                },
                {
                    "binding_id": "rb-active-002",
                    "runtime_id": "runtime-002",
                    "plan_id": "plan-002",
                    "persona_capital_binding_id": "binding-001",
                    "capital_pool_id": "pool-001",
                    "artifact_id": "reg-strat-001-1.2.0",
                    "artifact_version": "1.2.0",
                    "deployment_mode": "canary",
                    "status": "active",
                    "effective_at": "2026-04-10T00:01:00Z",
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    response = test_client.post(
        "/api/deployment/plans/compatibility-check",
        json={
            "capital_pool_id": "pool-001",
            "sponsor_persona_id": "persona-ops",
            "target_stage": "live",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["active_runtime_binding_count"] == 2
    assert body["active_runtime_binding_ids"] == ["rb-active-001", "rb-active-002"]
    assert body["single_runtime_ok"] is False
    assert any("single-runtime policy" in error for error in body["errors"])


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


def test_create_same_stage_replace_plan_from_service_api(client):
    test_client, governance_dir = client
    payload = _plan_payload(
        plan_id="plan-paper-replace-001",
        current_stage="paper",
        target_stage="paper",
    )
    payload["binding_id"] = "rb-paper-current"
    payload["rollback"] = {
        "target_artifact_id": "artifact-paper-baseline",
        "target_version": "1.2.0",
        "action_type": "replace",
    }

    response = test_client.post("/api/deployment/plans", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["current_stage"] == "paper"
    assert body["target_stage"] == "paper"
    assert body["transition_type"] == "replace"
    assert body["runtime_action"] == "replace_binding"
    assert body["binding_id"] == "rb-paper-current"
    persisted = json.loads(
        (governance_dir / "deployment_plans.json").read_text(encoding="utf-8")
    )
    assert persisted["plan-paper-replace-001"]["transition_type"] == "replace"


def test_create_plan_rejects_risk_policy_violation(client):
    test_client, _ = client
    payload = _plan_payload(
        plan_id="plan-canary-risk-reject",
        current_stage="paper",
        target_stage="canary",
    )
    payload["risk_policy"] = {
        "risk_policy_id": "risk-main",
        "max_canary_capital_scale_pct": 2.0,
        "max_canary_gross_scale_pct": 10.0,
    }

    response = test_client.post("/api/deployment/plans", json=payload)

    assert response.status_code == 422
    assert "RiskPolicy" in response.json()["detail"]


def test_create_plan_rejects_deployment_stage_as_artifact_state(client):
    test_client, _ = client
    for artifact_state in ["paper", "canary", "live"]:
        payload = _plan_payload(plan_id=f"plan-invalid-artifact-state-{artifact_state}")
        registry_entry = {
            "registry_id": "reg-strat-001-1.2.0",
            "artifact_type": "model_artifact",
            "strategy_id": "strat-001",
            "version": "1.2.0",
            "artifact_state": artifact_state,
            "checksum": "sha256:abc123def4567890",
            "approval_decision_id": "approval-001",
            "approved_at": "2026-04-09T12:00:00Z",
            "lineage": {"source_run_ids": ["replication-run-001"]},
            "deployment_summary": {"current_stage": "none"},
        }
        registry_entry["owner_tenant"] = "tenant-deployment-test"
        test_client.registry_snapshot.write_text(json.dumps({registry_entry["registry_id"]: registry_entry}))
        response = test_client.post("/api/deployment/plans", json=payload)
        assert response.status_code == 422
        assert "requires artifact_state=approved" in response.json()["detail"]


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


def test_projection_read_model_exposes_derived_plan_view(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-projection-001"),
    )
    assert created.status_code == 201

    response = test_client.get("/api/deployment/projections/plan-projection-001")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["projection_contract"] == "DEP-003"
    assert body["derived_only"] is True
    assert body["plan_id"] == "plan-projection-001"
    assert body["projected_stage"] == "paper"
    assert body["actual_stage"] == "none"
    assert body["plan_status"] == "approved"
    assert body["approval_outcome"] == "approved"
    assert body["approval_state"] == "decided"
    assert body["runtime_binding_id"] is None
    assert body["lifecycle_state"] == "ready_for_dispatch"
    assert body["source_status"]["deployment_plan"] == "canonical"
    assert body["source_status"]["approval_decision"] == "canonical"
    assert body["source_status"]["registry_entry"] == "canonical"
    assert body["source_status"]["runtime_binding"] == "missing"
    assert body["source_status"]["execution_projection"] == "derived"
    assert body["execution_projection"]["metadata"]["deployment_plan_id"] == "plan-projection-001"
    assert body["summary"]["has_approval_authority"] is True
    assert body["summary"]["runtime_backing_present"] is False

    alias = test_client.get("/api/deployment/plans/plan-projection-001/projection")
    assert alias.status_code == 200
    assert alias.json()["plan_id"] == "plan-projection-001"

    listing = test_client.get("/api/deployment/projections", params={"strategy_id": "strat-001"})
    assert listing.status_code == 200
    assert [item["plan_id"] for item in listing.json()] == ["plan-projection-001"]


def test_projection_read_model_joins_runtime_and_saga_state(client):
    test_client, governance_dir = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-projection-runtime-001"),
    )
    assert created.status_code == 201
    dispatch = test_client.post(
        "/api/deployment/plans/plan-projection-runtime-001/dispatch",
        json={"trace_id": "trace-projection-runtime-001"},
    )
    assert dispatch.status_code == 200, dispatch.text

    runtime_store = governance_dir.parent / "runtime_bindings.json"
    runtime_store.write_text(
        json.dumps(
            [
                {
                    "binding_id": "rb-projection-runtime-001",
                    "runtime_id": "runtime-projection-001",
                    "plan_id": "plan-projection-runtime-001",
                    "capital_pool_id": "pool-001",
                    "artifact_id": "reg-strat-001-1.2.0",
                    "artifact_version": "1.2.0",
                    "deployment_mode": "paper",
                    "status": "active",
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    response = test_client.get("/api/deployment/projections/plan-projection-runtime-001")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["runtime_binding_id"] == "rb-projection-runtime-001"
    assert body["runtime_id"] == "runtime-projection-001"
    assert body["runtime_status"] == "active"
    assert body["actual_stage"] == "paper"
    assert body["deployment_saga_id"] == "deployment-saga-plan-projection-runtime-001"
    assert body["deployment_saga_status"] == "awaiting_binding"
    assert body["lifecycle_state"] == "active"
    assert body["source_status"]["runtime_binding"] == "canonical"
    assert body["source_status"]["deployment_saga"] == "canonical"
    assert body["summary"]["runtime_backing_present"] is True


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


def test_dispatch_bootstraps_saga_and_persists_outbox(client):
    test_client, governance_dir = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-dispatch-001"),
    )
    assert created.status_code == 201

    dispatch = test_client.post(
        "/api/deployment/plans/plan-paper-dispatch-001/dispatch",
        json={
            "trace_id": "trace-dispatch-001",
            "workflow_id": "pantheon.deploy",
            "source_task_id": "BP5-SVC-005",
        },
    )
    assert dispatch.status_code == 200, dispatch.text
    body = dispatch.json()
    assert body["deployment_contract"] == "DEP-001"
    assert body["consistency_contract"] == "DEP-002"
    assert body["execution_context"] == "paper"
    assert body["replayed"] is False
    assert body["deployment_saga"]["saga"]["status"] == "awaiting_binding"
    assert body["deployment_saga"]["outbox_event"]["event"]["event_type"] == "runtime.binding.requested"
    assert body["deployment_saga"]["outbox_event"]["event"]["sequence_no"] == 1
    assert body["execution_projection"]["metadata"]["deployment_plan_id"] == "plan-paper-dispatch-001"

    persisted = json.loads((governance_dir / "deployment_sagas.json").read_text(encoding="utf-8"))
    assert "deployment-saga-plan-paper-dispatch-001" in persisted["sagas"]
    assert len(persisted["outbox"]) == 1


def test_dispatch_records_foundation_context_and_replays_idempotently(client):
    test_client, governance_dir = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-foundation-001"),
    )
    assert created.status_code == 201

    body = {
        "trace_id": "trace-deploy-foundation-001",
        "correlation_id": "corr-deploy-foundation-001",
        "idempotency_key": "idmp-deploy-foundation-001",
        "actor_id": "deployment-worker-001",
        "workflow_id": "pantheon.deploy.foundation",
        "source_task_id": "SD-FND-004",
        "metadata": {"change_ticket": "chg-001"},
    }
    first = test_client.post(
        "/api/deployment/plans/plan-paper-foundation-001/dispatch",
        json=body,
    )
    second = test_client.post(
        "/api/deployment/plans/plan-paper-foundation-001/dispatch",
        json=body,
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["replayed"] is True

    saga = first.json()["deployment_saga"]["saga"]
    foundation = saga["metadata"]["foundation"]
    assert foundation["trace_context"]["trace_id"] == "trace-deploy-foundation-001"
    assert foundation["trace_context"]["correlation_id"] == "corr-deploy-foundation-001"
    assert foundation["command_envelope"]["command_type"] == "deployment.dispatch_plan"
    assert foundation["idempotency_record"]["idempotency_key"] == "idmp-deploy-foundation-001"
    assert foundation["idempotency_record"]["status"] == "succeeded"
    assert foundation["policy_decision"]["decision"] == "allow"
    assert foundation["audit_action"]["trace_id"] == "trace-deploy-foundation-001"
    assert first.json()["deployment_saga"]["outbox_event"]["event"]["trace_id"] == "trace-deploy-foundation-001"

    persisted = json.loads((governance_dir / "deployment_sagas.json").read_text(encoding="utf-8"))
    persisted_foundation = persisted["sagas"]["deployment-saga-plan-paper-foundation-001"]["metadata"]["foundation"]
    assert persisted_foundation["audit_action"]["policy_decision_ref"] == foundation["policy_decision"]["decision_id"]
    assert len(persisted["outbox"]) == 1


def test_dispatch_rejects_idempotency_key_reuse_with_foundation_error(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-foundation-conflict"),
    )
    assert created.status_code == 201

    first = test_client.post(
        "/api/deployment/plans/plan-paper-foundation-conflict/dispatch",
        json={
            "trace_id": "trace-deploy-conflict-001",
            "idempotency_key": "idmp-deploy-conflict-001",
            "metadata": {"change_ticket": "chg-original"},
        },
    )
    assert first.status_code == 200, first.text

    conflict = test_client.post(
        "/api/deployment/plans/plan-paper-foundation-conflict/dispatch",
        json={
            "trace_id": "trace-deploy-conflict-002",
            "idempotency_key": "idmp-deploy-conflict-001",
            "metadata": {"change_ticket": "chg-mutated"},
        },
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["foundation_error"]["error_kind"] == "idempotency_conflict"
    assert detail["foundation_error"]["trace"]["trace_id"] == "trace-deploy-conflict-002"
    assert detail["audit_action"]["action_type"] == "deployment.dispatch.idempotency_conflict"


def test_dispatch_unapproved_plan_returns_foundation_policy_error(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json={**_plan_payload(plan_id="plan-paper-foundation-deny"), "status": "draft"},
    )
    assert created.status_code == 201

    denied = test_client.post(
        "/api/deployment/plans/plan-paper-foundation-deny/dispatch",
        json={
            "trace_id": "trace-deploy-deny-001",
            "idempotency_key": "idmp-deploy-deny-001",
        },
    )
    assert denied.status_code == 403, denied.text
    detail = denied.json()["detail"]
    assert detail["foundation_error"]["error_kind"] == "policy_denial"
    assert detail["foundation_error"]["trace"]["trace_id"] == "trace-deploy-deny-001"
    assert detail["policy_decision"]["decision"] == "deny"
    assert detail["audit_action"]["policy_decision_ref"] == detail["policy_decision"]["decision_id"]


def test_dispatch_is_idempotent_for_existing_saga(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-dispatch-002"),
    )
    assert created.status_code == 201

    first = test_client.post(
        "/api/deployment/plans/plan-paper-dispatch-002/dispatch",
        json={"trace_id": "trace-dispatch-002"},
    )
    assert first.status_code == 200

    second = test_client.post(
        "/api/deployment/plans/plan-paper-dispatch-002/dispatch",
        json={"trace_id": "trace-dispatch-002-retry"},
    )
    assert second.status_code == 200
    assert second.json()["replayed"] is True

    outbox = test_client.get("/api/deployment/outbox")
    assert outbox.status_code == 200
    assert len(outbox.json()) == 1
    assert outbox.json()[0]["event"]["sequence_no"] == 1


def test_outbox_claim_ack_requires_active_authenticated_lease(
    client, monkeypatch
):
    test_client, _ = client
    monkeypatch.setenv("PANTHEON_DEPLOYMENT_OUTBOX_LEASE_REQUIRED", "true")
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-lease-001"),
    )
    assert created.status_code == 201
    dispatch = test_client.post(
        "/api/deployment/plans/plan-paper-lease-001/dispatch",
        json={"trace_id": "trace-lease-001"},
    )
    assert dispatch.status_code == 200, dispatch.text
    event_id = dispatch.json()["deployment_saga"]["outbox_event"]["event"]["event_id"]

    claimed = test_client.post(
        "/api/deployment/outbox/claim",
        json={
            "consumer_name": "deployment-consumer-a",
            "lease_seconds": 60,
            "limit": 1,
        },
    )
    assert claimed.status_code == 200, claimed.text
    assert len(claimed.json()) == 1
    claim = claimed.json()[0]
    assert claim["event"]["event_id"] == event_id
    assert claim["tenant_id"] == "tenant-deployment-test"
    assert claim["status"] == "pending"
    assert claim["lease_status"] == "active"
    assert claim["claim_token"]

    competing = test_client.post(
        "/api/deployment/outbox/claim",
        json={
            "consumer_name": "deployment-consumer-b",
            "lease_seconds": 60,
            "limit": 1,
        },
    )
    assert competing.status_code == 200
    assert competing.json() == []

    stale = test_client.post(
        f"/api/deployment/outbox/{event_id}/consume",
        json={
            "consumer_name": "deployment-consumer-a",
            "claim_token": "not-the-active-token",
        },
    )
    assert stale.status_code == 409

    acknowledged = test_client.post(
        f"/api/deployment/outbox/{event_id}/consume",
        json={
            "consumer_name": "deployment-consumer-a",
            "claim_token": claim["claim_token"],
        },
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["status"] == "applied"

    health = test_client.get("/api/deployment/outbox/lease-health")
    assert health.status_code == 200
    assert health.json()["active_claim_count"] == 0
    assert health.json()["acknowledged_claim_count"] == 1
    outbox = test_client.get(
        "/api/deployment/outbox",
        params={"status": "published"},
    )
    assert [record["event"]["event_id"] for record in outbox.json()] == [event_id]


def test_saga_progress_and_inbox_replay_receipts(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-saga-001"),
    )
    assert created.status_code == 201

    dispatch = test_client.post(
        "/api/deployment/plans/plan-paper-saga-001/dispatch",
        json={"trace_id": "trace-saga-001"},
    )
    assert dispatch.status_code == 200
    saga_id = dispatch.json()["deployment_saga"]["saga"]["saga_id"]
    seq1_event_id = dispatch.json()["deployment_saga"]["outbox_event"]["event"]["event_id"]

    binding = test_client.post(
        f"/api/deployment/sagas/{saga_id}/binding-created",
        json={"binding_id": "binding-001", "runtime_id": "runtime-001"},
    )
    assert binding.status_code == 200
    assert binding.json()["event"]["sequence_no"] == 2
    binding_plan = test_client.get("/api/deployment/plans/plan-paper-saga-001")
    assert binding_plan.status_code == 200
    assert binding_plan.json()["binding_id"] == "binding-001"
    assert binding_plan.json()["status"] == "executing"
    assert binding_plan.json()["current_stage"] == "none"
    assert binding_plan.json()["metadata"]["runtime_lifecycle"] == {
        "binding_id": "binding-001",
        "runtime_id": "runtime-001",
    }

    active = test_client.post(
        f"/api/deployment/sagas/{saga_id}/runtime-active",
        json={"binding_id": "binding-001", "runtime_id": "runtime-001"},
    )
    assert active.status_code == 200
    seq3_event_id = active.json()["event"]["event_id"]
    active_plan = test_client.get("/api/deployment/plans/plan-paper-saga-001")
    assert active_plan.status_code == 200
    active_plan_body = active_plan.json()
    assert active_plan_body["binding_id"] == "binding-001"
    assert active_plan_body["status"] == "executed"
    assert active_plan_body["current_stage"] == "paper"
    assert active_plan_body["target_stage"] == "paper"
    assert active_plan_body["metadata"]["runtime_lifecycle"] == {
        "binding_id": "binding-001",
        "runtime_id": "runtime-001",
        "runtime_status": "active",
        "activated_stage": "paper",
    }

    projection = test_client.get("/api/deployment/projections/plan-paper-saga-001")
    assert projection.status_code == 200
    assert projection.json()["current_stage"] == "paper"
    assert projection.json()["actual_stage"] == "paper"
    assert projection.json()["plan_status"] == "executed"

    seq1 = test_client.post(
        f"/api/deployment/outbox/{seq1_event_id}/consume",
        json={"consumer_name": "runtime-projector"},
    )
    assert seq1.status_code == 200
    assert seq1.json()["status"] == "applied"

    duplicate = test_client.post(
        f"/api/deployment/outbox/{seq1_event_id}/consume",
        json={"consumer_name": "runtime-projector"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    out_of_order = test_client.post(
        f"/api/deployment/outbox/{seq3_event_id}/consume",
        json={"consumer_name": "runtime-projector"},
    )
    assert out_of_order.status_code == 200
    assert out_of_order.json()["status"] == "out_of_order"

    outbox = test_client.get("/api/deployment/outbox", params={"aggregate_id": saga_id})
    seq2_event_id = next(
        record["event"]["event_id"]
        for record in outbox.json()
        if record["event"]["sequence_no"] == 2
    )

    seq2 = test_client.post(
        f"/api/deployment/outbox/{seq2_event_id}/consume",
        json={"consumer_name": "runtime-projector"},
    )
    assert seq2.status_code == 200
    assert seq2.json()["status"] == "applied"

    seq3 = test_client.post(
        f"/api/deployment/outbox/{seq3_event_id}/consume",
        json={"consumer_name": "runtime-projector"},
    )
    assert seq3.status_code == 200
    assert seq3.json()["status"] == "applied"

    receipts = test_client.get(
        "/api/deployment/inbox",
        params={"consumer_name": "runtime-projector", "aggregate_id": saga_id},
    )
    assert receipts.status_code == 200
    assert [receipt["status"] for receipt in receipts.json()] == [
        "applied",
        "duplicate",
        "out_of_order",
        "applied",
        "applied",
    ]


def test_saga_progress_exposes_dlq_block_and_idempotent_replay(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(plan_id="plan-paper-dlq-001"),
    )
    assert created.status_code == 201

    dispatch = test_client.post(
        "/api/deployment/plans/plan-paper-dlq-001/dispatch",
        json={"trace_id": "trace-dlq-001"},
    )
    assert dispatch.status_code == 200, dispatch.text
    saga_id = dispatch.json()["deployment_saga"]["saga"]["saga_id"]
    event_id = dispatch.json()["deployment_saga"]["outbox_event"]["event"]["event_id"]

    initial_progress = test_client.get(f"/api/deployment/sagas/{saga_id}/progress")
    assert initial_progress.status_code == 200
    assert initial_progress.json()["progress_status"] == "pending"
    assert initial_progress.json()["pending_event_count"] == 1

    failure = test_client.post(
        f"/api/deployment/outbox/{event_id}/failure",
        json={
            "consumer_name": "deployment-outbox-consumer",
            "reason": "runtime-manager 503 after retry budget",
            "retryable": True,
            "max_attempts": 1,
            "retry_delay_seconds": 0,
        },
    )
    assert failure.status_code == 200, failure.text
    assert failure.json()["status"] == "dead_lettered"
    assert failure.json()["delivery_attempts"] == 1
    assert failure.json()["blocked_reason"] == "runtime-manager 503 after retry budget"

    blocked = test_client.get(f"/api/deployment/plans/plan-paper-dlq-001/saga-progress")
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["progress_status"] == "blocked"
    assert blocked_body["blocked_reason"] == "runtime-manager 503 after retry budget"
    assert blocked_body["dlq_count"] == 1
    assert blocked_body["retry_state"][0]["status"] == "dead_lettered"

    projection = test_client.get("/api/deployment/projections/plan-paper-dlq-001")
    assert projection.status_code == 200
    projection_body = projection.json()
    assert projection_body["deployment_saga_progress"]["progress_status"] == "blocked"
    assert projection_body["summary"]["saga_progress_status"] == "blocked"
    assert projection_body["summary"]["blocked_reason"] == "runtime-manager 503 after retry budget"

    dlq = test_client.get("/api/deployment/outbox", params={"status": "dead_lettered"})
    assert dlq.status_code == 200
    assert [record["event"]["event_id"] for record in dlq.json()] == [event_id]

    replayed = test_client.post(
        f"/api/deployment/outbox/{event_id}/replay",
        json={"reason": "operator retry after runtime-manager recovered"},
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replayed"] is True
    assert replayed.json()["event"]["status"] == "pending"
    assert replayed.json()["event"]["replay_count"] == 1

    replayed_again = test_client.post(
        f"/api/deployment/outbox/{event_id}/replay",
        json={"reason": "operator retried same DLQ replay command"},
    )
    assert replayed_again.status_code == 200
    assert replayed_again.json()["replayed"] is False
    assert replayed_again.json()["event"]["status"] == "pending"
    assert replayed_again.json()["event"]["replay_count"] == 1


def test_post_activation_failure_uses_plan_rollback_action_and_finalize(client):
    test_client, _ = client
    created = test_client.post(
        "/api/deployment/plans",
        json=_plan_payload(
            plan_id="plan-live-saga-001",
            current_stage="canary",
            target_stage="live",
            rollback_action="pause_then_replace",
        ),
    )
    assert created.status_code == 201

    dispatch = test_client.post(
        "/api/deployment/plans/plan-live-saga-001/dispatch",
        json={"trace_id": "trace-live-saga-001"},
    )
    assert dispatch.status_code == 200
    saga_id = dispatch.json()["deployment_saga"]["saga"]["saga_id"]

    binding = test_client.post(
        f"/api/deployment/sagas/{saga_id}/binding-created",
        json={"binding_id": "binding-live-001", "runtime_id": "runtime-live-001"},
    )
    assert binding.status_code == 200

    active = test_client.post(
        f"/api/deployment/sagas/{saga_id}/runtime-active",
        json={"binding_id": "binding-live-001", "runtime_id": "runtime-live-001"},
    )
    assert active.status_code == 200

    failure = test_client.post(
        f"/api/deployment/sagas/{saga_id}/failure",
        json={
            "reason": "severe mismatch after cutover",
            "failed_step": "runtime_active",
        },
    )
    assert failure.status_code == 200
    assert failure.json()["command_type"] == "request_rollback"
    assert failure.json()["runtime_action"] == "pause_then_replace"

    finalized = test_client.post(
        f"/api/deployment/sagas/{saga_id}/compensation/finalize",
        json={"note": "rollback request issued"},
    )
    assert finalized.status_code == 200
    assert finalized.json()["event"]["event_type"] == "deployment.saga.failed"

    saga = test_client.get(f"/api/deployment/sagas/{saga_id}")
    assert saga.status_code == 200
    assert saga.json()["status"] == "failed"
    assert saga.json()["compensation"]["command_type"] == "request_rollback"

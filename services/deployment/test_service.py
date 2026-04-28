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

    active = test_client.post(
        f"/api/deployment/sagas/{saga_id}/runtime-active",
        json={"binding_id": "binding-001", "runtime_id": "runtime-001"},
    )
    assert active.status_code == 200
    seq3_event_id = active.json()["event"]["event_id"]

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

"""
DEP-001-RB rebaseline coverage for the deployable DeploymentPlan service.

The existing service owns the DEP-001 create / validate / read surface. These
tests pin the 2026-05-15 rebaseline acceptance: an approved artifact, governed
by a decided ApprovalDecision, can create paper / canary / live / frozen plans.
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
    "Authorization": "Bearer dep001-test:operator,service",
    "X-Tenant-Id": "tenant-dep001-test",
}


def _approved_decision() -> dict:
    return approval_snapshot(**{
        "decision_id": "approval-dep001-rb-001",
        "target_id": "reg-dep001-rb-1.2.0",
        "target_version": "1.2.0",
        "decision_state": "decided",
        "decision": "approved",
        "capital_pool_id": "pool-dep001-rb",
        "persona_id": "persona-dep001-rb",
        "tenant_id": "tenant-dep001-test",
        "candidate_digest": "sha256:dep001rbabc123",
    })


def _seed_approval_store(path: Path) -> None:
    path.write_text(
        json.dumps({"approval-dep001-rb-001": _approved_decision()}, indent=2),
        encoding="utf-8",
    )


def _registry_entry(current_stage: str, *, artifact_state: str = "approved") -> dict:
    return {
        "owner_tenant": "tenant-dep001-test",
        "registry_id": "reg-dep001-rb-1.2.0",
        "artifact_type": "model_artifact",
        "strategy_id": "strat-dep001-rb",
        "version": "1.2.0",
        "artifact_state": artifact_state,
        "checksum": "sha256:dep001rbabc123",
        "approval_decision_id": "approval-dep001-rb-001",
        "approved_at": "2026-05-16T05:00:00Z",
        "lineage": {"source_run_ids": ["dep001-rb-smoke"]},
        "deployment_summary": {"current_stage": current_stage},
    }


def _rollback_ref() -> dict:
    return {
        "target_artifact_id": "reg-dep001-rb-1.1.0",
        "target_version": "1.1.0",
        "action_type": "replace",
        "reason": "DEP-001-RB approved baseline rollback target",
    }


def _request_payload(
    *,
    registry_owner,
    plan_id: str,
    current_stage: str,
    target_stage: str,
    artifact_state: str = "approved",
) -> dict:
    registry_owner.update(_registry_entry(current_stage, artifact_state=artifact_state))
    payload = {
        "plan_id": plan_id,
        "approval_decision_id": "approval-dep001-rb-001",
        "registry_id": "reg-dep001-rb-1.2.0",
        "capital_pool_id": "pool-dep001-rb",
        "sponsor_persona_id": "persona-dep001-rb",
        "target_stage": target_stage,
        "created_by": "dep001-rb-test",
    }
    if target_stage != "frozen":
        payload["rollback"] = _rollback_ref()
    return payload


@pytest.fixture()
def dep001_client():
    with tempfile.TemporaryDirectory(prefix="dep001_rb_service_") as tempdir:
        governance_dir = Path(tempdir) / "governance"
        governance_dir.mkdir(parents=True, exist_ok=True)
        _seed_approval_store(governance_dir / "approval_decisions.json")

        env_backup = {
            "DEPLOYMENT_DATA_DIR": os.environ.get("DEPLOYMENT_DATA_DIR"),
            "PANTHEON_GOVERNANCE_DATA_DIR": os.environ.get("PANTHEON_GOVERNANCE_DATA_DIR"),
            "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH": os.environ.get(
                "PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH"
            ),
            "PANTHEON_RUNTIME_BINDING_STORE_PATH": os.environ.get(
                "PANTHEON_RUNTIME_BINDING_STORE_PATH"
            ),
        }
        os.environ["DEPLOYMENT_DATA_DIR"] = str(governance_dir)
        os.environ["PANTHEON_GOVERNANCE_DATA_DIR"] = str(governance_dir)
        os.environ.pop("PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH", None)
        os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(Path(tempdir) / "runtime_bindings.json")

        sys.modules.pop("services.deployment.service", None)
        module = importlib.import_module("services.deployment.service")
        module = importlib.reload(module)

        test_client = TestClient(module.app, headers=_AUTH_HEADERS)
        test_client.registry_owner = {}
        test_client.approval_owner = _approved_decision()
        module.planner_service.registry_reader = lambda identity: dict(test_client.registry_owner)
        module.planner_service.approval_reader = SnapshotApprovalReader(lambda: test_client.approval_owner)
        try:
            yield test_client
        finally:
            for key, value in env_backup.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@pytest.mark.parametrize(
    (
        "current_stage",
        "target_stage",
        "transition_type",
        "runtime_action",
        "scale",
    ),
    [
        ("none", "paper", "activate", "deploy_new_binding", (0.0, 100.0)),
        ("paper", "canary", "promote", "replace_binding", (5.0, 25.0)),
        ("canary", "live", "promote", "replace_binding", (100.0, 100.0)),
        ("live", "frozen", "freeze", "freeze_binding", (0.0, 0.0)),
    ],
)
def test_approved_artifact_can_create_rebaseline_stage_plans(
    dep001_client,
    current_stage: str,
    target_stage: str,
    transition_type: str,
    runtime_action: str,
    scale: tuple[float, float],
):
    plan_id = f"plan-dep001-rb-{target_stage}"

    create = dep001_client.post(
        "/api/deployment/plans",
        json=_request_payload(
            registry_owner=dep001_client.registry_owner,
            plan_id=plan_id,
            current_stage=current_stage,
            target_stage=target_stage,
        ),
    )

    assert create.status_code == 201, create.text
    body = create.json()
    assert body["plan_id"] == plan_id
    assert body["artifact_id"] == "reg-dep001-rb-1.2.0"
    assert body["artifact_version"] == "1.2.0"
    assert body["approval_decision_id"] == "approval-dep001-rb-001"
    assert body["capital_pool_id"] == "pool-dep001-rb"
    assert body["current_stage"] == current_stage
    assert body["target_stage"] == target_stage
    assert body["transition_type"] == transition_type
    assert body["runtime_action"] == runtime_action
    assert body["status"] == "approved"
    assert body["scale"]["capital_scale_pct"] == scale[0]
    assert body["scale"]["gross_scale_pct"] == scale[1]

    readback = dep001_client.get(f"/api/deployment/plans/{plan_id}")
    assert readback.status_code == 200
    assert readback.json()["plan_id"] == plan_id


def test_deployment_plan_service_requires_approved_artifact_state(dep001_client):
    response = dep001_client.post(
        "/api/deployment/plans",
        json=_request_payload(
            registry_owner=dep001_client.registry_owner,
            plan_id="plan-dep001-rb-candidate",
            current_stage="none",
            target_stage="paper",
            artifact_state="candidate",
        ),
    )

    assert response.status_code == 422
    assert "requires artifact_state=approved" in response.json()["detail"]


def test_deployment_plan_service_requires_decided_approval_authority(dep001_client):
    payload = _request_payload(
        registry_owner=dep001_client.registry_owner,
        plan_id="plan-dep001-rb-undecided",
        current_stage="none",
        target_stage="paper",
    )
    dep001_client.approval_owner = {
        **_approved_decision(),
        "decision_state": "under_review",
        "decision": None,
    }

    response = dep001_client.post("/api/deployment/plans", json=payload)

    assert response.status_code == 422
    assert "approval_decision must be in decided state" in response.json()["detail"]

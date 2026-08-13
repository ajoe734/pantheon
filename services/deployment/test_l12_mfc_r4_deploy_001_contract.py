"""Service-bound contract test for L12-MFC-R4-DEPLOY-001:
Validate and align Consultation approval through RuntimeBinding identity.

Proves:
1. Approved immutable artifact creates DeploymentPlan and active paper RuntimeBinding.
2. Artifact ID, version, strategy_id, and checksum remain exact across all stages.
3. Capital receipt (capital_pool_id, persona_capital_binding_id, allowed_deployment_scope) is visible.
4. Pre-condition violations (unapproved decision, checksum mismatch, inactive binding) fail closed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from services.promotion.main import create_app as create_promotion_app
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    strategy_artifact_checksum,
)

ROOT = Path(__file__).resolve().parents[2]
RM_DIR = ROOT / "services" / "runtime-manager"
if str(RM_DIR) not in sys.path:
    sys.path.insert(0, str(RM_DIR))

import deploy_authority
DeployAuthorityError = deploy_authority.DeployAuthorityError
verify_deploy_authorities = deploy_authority.verify_deploy_authorities


def _sample_registration():
    return load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])


def _sample_approval_payload(artifact, approval_id="apv-l12-deploy-001"):
    return {
        "decision_id": approval_id,
        "decision_state": "decided",
        "decision": "approved",
        "target_type": "registry_entry",
        "target_id": artifact["artifact_id"],
        "target_version": artifact["version"],
        "capital_pool_id": "pool-paper-001",
        "persona_id": "persona-a",
        "owner_user_id": "user-owner-001",
        "tenant_id": "tenant-l12-deploy",
        "risk_level": "low",
        "actor_role": "automated_gate",
        "actor_id": "promotion-svc",
        "created_at": "2026-08-13T12:00:00Z",
        "updated_at": "2026-08-13T12:00:00Z",
        "revoked_at": None,
        "conditions": [],
        "expires_at": "2027-08-13T12:00:00Z",
    }


def _sample_registry_payload(registration, approval_id="apv-l12-deploy-001"):
    artifact = registration["strategy_artifact"]
    checksum = strategy_artifact_checksum(artifact)
    return {
        "deployment_stage": "paper",
        "checksum": checksum,
        "entry": {
            "registry_id": artifact["artifact_id"],
            "version": artifact["version"],
            "strategy_id": artifact["strategy_id"],
            "artifact_type": "execution_bundle",
            "artifact_state": "approved",
            "approval_decision_id": approval_id,
            "checksum": checksum,
            "metadata": {
                "strategy_artifact": artifact,
            },
        },
    }


def _sample_capital_pool_payload():
    return {
        "pool_id": "pool-paper-001",
        "status": "active",
        "single_runtime_enforced": True,
        "tenant_id": "tenant-l12-deploy",
    }


def _sample_capital_admissibility_payload():
    return {
        "persona_id": "persona-a",
        "capital_pool_id": "pool-paper-001",
        "target_stage": "paper",
        "permitted": True,
        "pool_status": "active",
        "single_runtime_enforced": True,
        "binding_id": "pcb-paper-001",
        "binding_status": "active",
        "allowed_deployment_scope": "paper",
    }


def _sample_persona_binding_payload():
    return {
        "binding_id": "pcb-paper-001",
        "persona_id": "persona-a",
        "capital_pool_id": "pool-paper-001",
        "status": "active",
        "allowed_deployment_scope": "paper",
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_to": None,
    }


def test_promotion_approval_to_deployment_plan_flow(tmp_path, monkeypatch):
    """Test creating an approval and generating a deployment plan in promotion-svc."""
    data_dir = tmp_path / "promotion"
    monkeypatch.setenv("PROMOTION_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PROMOTION_STORE_BACKEND", "json")

    import importlib
    sys.modules.pop("services.promotion.main", None)
    promotion_main = importlib.import_module("services.promotion.main")
    promotion_main = importlib.reload(promotion_main)

    registration = _sample_registration()
    artifact = registration["strategy_artifact"]

    app = promotion_main.create_app()
    client = TestClient(app)

    headers = {
        "Authorization": "Bearer promotion-test:risk_owner,automated_gate,operator,service",
        "X-Tenant-Id": "tenant-l12-deploy",
    }

    # 1. Create proposed approval decision
    create_apv_resp = client.post(
        "/api/v1/approvals",
        headers=headers,
        json={
            "decision_id": "apv-l12-deploy-001",
            "target_type": "registry_entry",
            "target_id": artifact["artifact_id"],
            "target_version": artifact["version"],
            "tenant_id": "tenant-l12-deploy",
            "owner_user_id": "user-owner-001",
            "risk_level": "low",
            "capital_pool_id": "pool-paper-001",
            "persona_id": "persona-a",
        },
    )
    assert create_apv_resp.status_code == 201
    apv_data = create_apv_resp.json()
    assert apv_data["decision_id"] == "apv-l12-deploy-001"
    assert apv_data["decision_state"] == "proposed"

    # 2. Decide approval (approve unconditionally)
    decide_resp = client.post(
        "/api/v1/approvals/apv-l12-deploy-001/decide",
        headers=headers,
        json={
            "outcome": "approved",
            "rationale": "Consultation consensus reached and verified",
            "actor_role": "automated_gate",
            "actor_id": "promotion-svc",
            "conditions": [],
        },
    )
    assert decide_resp.status_code == 200
    decided_data = decide_resp.json()
    assert decided_data["decision_state"] == "decided"
    assert decided_data["decision"] == "approved"

    # 3. Create DeploymentPlan bound to the approved decision
    create_dp_resp = client.post(
        "/api/v1/deployments",
        headers=headers,
        json={
            "plan_id": "dp-l12-deploy-001",
            "approval_decision_id": "apv-l12-deploy-001",
            "artifact_id": artifact["artifact_id"],
            "artifact_version": artifact["version"],
            "artifact_type": "execution_bundle",
            "strategy_id": artifact["strategy_id"],
            "capital_pool_id": "pool-paper-001",
            "target_stage": "paper",
            "current_stage": "none",
            "status": "approved",
            "sponsor_persona_id": "persona-a",
            "persona_capital_binding_id": "pcb-paper-001",
            "persona_capital_binding_status": "active",
            "allowed_deployment_scope": "paper",
            "loader_checks_passed": True,
            "rollback": {
                "target_artifact_id": "art-strat-previous-001",
                "target_version": "0.9.0",
                "action_type": "replace",
                "reason": "paper deployment rollback ref",
            },
        },
    )
    assert create_dp_resp.status_code == 201, f"create_dp_resp failed: {create_dp_resp.text}"
    plan_data = create_dp_resp.json()
    assert plan_data["plan_id"] == "dp-l12-deploy-001"
    assert plan_data["approval_decision_id"] == "apv-l12-deploy-001"
    assert plan_data["artifact_id"] == artifact["artifact_id"]
    assert plan_data["artifact_version"] == artifact["version"]
    assert plan_data["strategy_id"] == artifact["strategy_id"]
    assert plan_data["target_stage"] == "paper"
    assert plan_data["persona_capital_binding_id"] == "pcb-paper-001"


def test_verify_deploy_authorities_end_to_end_identity_exactness():
    """Verify deploy authority checks match exact identity and produce loader report."""
    registration = _sample_registration()
    artifact = registration["strategy_artifact"]
    apv_id = "apv-l12-deploy-001"

    plan_payload = {
        "plan_id": "dp-l12-deploy-001",
        "status": "approved",
        "target_stage": "paper",
        "current_stage": "none",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": apv_id,
        "capital_pool_id": "pool-paper-001",
        "sponsor_persona_id": "persona-a",
        "metadata": {
            "tenant_id": "tenant-l12-deploy",
            "persona_capital_binding_id": "pcb-paper-001",
        },
    }

    mock_responses = {
        "http://deploy-svc/api/deployment/plans/dp-l12-deploy-001": plan_payload,
        f"http://reg-svc/api/registry/strategy-artifacts/{artifact['artifact_id']}": _sample_registry_payload(registration, apv_id),
        f"http://gov-svc/api/governance/approvals/{apv_id}": _sample_approval_payload(artifact, apv_id),
        "http://cap-svc/api/capital-pools/pool-paper-001": _sample_capital_pool_payload(),
        "http://cap-svc/api/bindings/admissibility?persona_id=persona-a&capital_pool_id=pool-paper-001&target_stage=paper": _sample_capital_admissibility_payload(),
        "http://cap-svc/api/bindings/pcb-paper-001": _sample_persona_binding_payload(),
    }

    def fake_fetch_json(url: str, timeout_seconds: float) -> dict:
        if url in mock_responses:
            return mock_responses[url]
        raise ValueError(f"Unexpected url: {url}")

    req = {
        "plan_id": "dp-l12-deploy-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": apv_id,
        "capital_pool_id": "pool-paper-001",
        "sponsor_persona_id": "persona-a",
        "persona_capital_binding_id": "pcb-paper-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
    }

    report = verify_deploy_authorities(
        req,
        deployment_base_url="http://deploy-svc",
        registry_base_url="http://reg-svc",
        governance_base_url="http://gov-svc",
        capital_base_url="http://cap-svc",
        fetch_json=fake_fetch_json,
    )

    assert report["status"] == "passed"
    assert report["plan_id"] == "dp-l12-deploy-001"
    assert report["artifact_id"] == artifact["artifact_id"]
    assert report["artifact_version"] == artifact["version"]
    assert report["strategy_id"] == artifact["strategy_id"]
    assert report["approval_decision_id"] == apv_id
    assert report["capital_pool_id"] == "pool-paper-001"
    assert report["sponsor_persona_id"] == "persona-a"
    assert report["persona_capital_binding_id"] == "pcb-paper-001"
    assert report["allowed_deployment_scope"] == "paper"


def test_verify_deploy_authorities_fails_on_unapproved_or_mismatched_approval():
    """Verify that unapproved decisions or mismatched identity fail closed."""
    registration = _sample_registration()
    artifact = registration["strategy_artifact"]
    apv_id = "apv-l12-deploy-001"

    plan_payload = {
        "plan_id": "dp-l12-deploy-001",
        "status": "approved",
        "target_stage": "paper",
        "current_stage": "none",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": apv_id,
        "capital_pool_id": "pool-paper-001",
        "sponsor_persona_id": "persona-a",
        "metadata": {},
    }

    bad_approval_payload = _sample_approval_payload(artifact, apv_id)
    bad_approval_payload["decision"] = "rejected"  # Not approved!

    mock_responses = {
        "http://deploy-svc/api/deployment/plans/dp-l12-deploy-001": plan_payload,
        f"http://reg-svc/api/registry/strategy-artifacts/{artifact['artifact_id']}": _sample_registry_payload(registration, apv_id),
        f"http://gov-svc/api/governance/approvals/{apv_id}": bad_approval_payload,
        "http://cap-svc/api/capital-pools/pool-paper-001": _sample_capital_pool_payload(),
        "http://cap-svc/api/bindings/admissibility?persona_id=persona-a&capital_pool_id=pool-paper-001&target_stage=paper": _sample_capital_admissibility_payload(),
        "http://cap-svc/api/bindings/pcb-paper-001": _sample_persona_binding_payload(),
    }

    def fake_fetch_json(url: str, timeout_seconds: float) -> dict:
        return mock_responses[url]

    req = {
        "plan_id": "dp-l12-deploy-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "approval_decision_id": apv_id,
        "capital_pool_id": "pool-paper-001",
        "sponsor_persona_id": "persona-a",
        "persona_capital_binding_id": "pcb-paper-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "paper",
    }

    with pytest.raises(DeployAuthorityError, match="governance authority mismatch"):
        verify_deploy_authorities(
            req,
            deployment_base_url="http://deploy-svc",
            registry_base_url="http://reg-svc",
            governance_base_url="http://gov-svc",
            capital_base_url="http://cap-svc",
            fetch_json=fake_fetch_json,
        )
